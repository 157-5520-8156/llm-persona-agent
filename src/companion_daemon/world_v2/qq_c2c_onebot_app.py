"""NapCat/OneBot HTTP ingress for the normalized World v2 QQ C2C lane.

The module owns only provider-envelope validation and lifecycle scheduling. It
does not import the legacy engine, conversation turn, or coalescer modules.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from companion_daemon.config import Settings
from companion_daemon.llm import FakeCompanionModel
from companion_daemon.ledger_storage_health import ledger_storage_snapshot
from companion_daemon.onebot_adapter import (
    event_token_is_valid,
    get_onebot_friend_msg_history,
)

from .platform_action_executor import MediaProviderTransport
from .dashboard_runtime_observation import DashboardRuntimeObservationSampler
from .model_completion import ChatCompletionModel
from .process_health import compile_process_health, ledger_path_is_writable
from .production_latency_health import production_latency_health_snapshot
from .production_reliability_metrics import reliability_snapshot
from .durable_reliability import durable_reliability_snapshot
from .production_turn_application import MediaPreviewDeployment
from .qq_attachment_archive import QQOneBotAttachmentArchiver
from .qq_c2c_host import QQC2CHost, build_qq_c2c_host, qq_c2c_world_id
from .qq_media_deployment import build_qq_media_preview_deployment
from .qq_perception_deployment import build_qq_perception_deployment
from .qq_history_backfill import (
    DEFAULT_BACKFILL_COUNT,
    backfill_missed_private_messages,
)
from .qq_ingress_policy import normalize_onebot_qq_ingress
from .world_v2_dashboard_ui import (
    DASHBOARD_APP_JS,
    DASHBOARD_HTML,
    DASHBOARD_SESSION_COOKIE,
    DASHBOARD_SESSION_TTL_SECONDS,
    DashboardSessionCodec,
    LOGIN_HTML,
    UNAVAILABLE_HTML,
)


logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DAEMON_STATIC = Path(__file__).resolve().parents[1] / "static"
_LOCAL_DASHBOARD_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_local_dashboard_request(request: Request) -> bool:
    client = request.client
    host = client.host.strip().lower() if client is not None else ""
    return host in _LOCAL_DASHBOARD_HOSTS


def _require_safe_dashboard_host(request: Request) -> JSONResponse | None:
    if not _is_local_dashboard_request(request):
        return JSONResponse({"error": "Dashboard is loopback-only"}, status_code=403)
    host = (request.url.hostname or "").strip().lower()
    if host not in _LOCAL_DASHBOARD_HOSTS:
        return JSONResponse({"error": "invalid Dashboard host"}, status_code=403)
    return None


def _http_origin_coordinate(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


def _same_dashboard_origin(request: Request) -> bool:
    supplied = _http_origin_coordinate(request.headers.get("origin", ""))
    expected = _http_origin_coordinate(str(request.base_url))
    return supplied is not None and supplied == expected


def _dashboard_session_codec(asgi_app: FastAPI, settings: Settings) -> DashboardSessionCodec | None:
    token = (settings.world_v2_dashboard_operator_token or "").strip()
    secret = getattr(asgi_app.state, "dashboard_session_secret", None)
    if not token or not isinstance(secret, bytes):
        return None
    return DashboardSessionCodec(operator_token=token, instance_secret=secret)


def _dashboard_session_is_valid(request: Request, settings: Settings) -> bool:
    codec = _dashboard_session_codec(request.app, settings)
    return codec is not None and codec.verify(request.cookies.get(DASHBOARD_SESSION_COOKIE))


def _mount_dashboard_static_files(app: FastAPI) -> None:
    """Serve the already-built room/dashboard assets from this World owner process."""

    app.mount("/assets", StaticFiles(directory=_REPO_ROOT / "assets"), name="assets")
    app.mount("/dashboard-static", StaticFiles(directory=_DAEMON_STATIC), name="dashboard-static")
    app.mount(
        "/pixel-home",
        StaticFiles(directory=_REPO_ROOT / "prototypes" / "pixel-home"),
        name="pixel-home",
    )


def _dashboard_host_probe(
    host: object,
    method_name: str,
) -> Callable[[], Mapping[str, object]]:
    """Adapt optional test/host diagnostics without weakening production capture."""

    def capture() -> Mapping[str, object]:
        method = getattr(host, method_name, None)
        if not callable(method):
            raise RuntimeError("dashboard runtime probe is not installed")
        payload = method()
        if not isinstance(payload, Mapping):
            raise RuntimeError("dashboard runtime probe returned an invalid payload")
        return payload

    return capture


def _dashboard_latency_probe(host: object) -> dict[str, object]:
    method = getattr(host, "latency_samples", None)
    if not callable(method):
        raise RuntimeError("dashboard latency probe is not installed")
    return production_latency_health_snapshot(method())


@dataclass
class QQC2CSchedulerDiagnostics:
    """Process-local evidence that the QQ scheduler is actually making progress."""

    interval_seconds: float
    task: asyncio.Task[None] | None = None
    passes_started: int = 0
    passes_completed: int = 0
    failures: int = 0
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    last_success_at: datetime | None = None
    last_duration_ms: int | None = None
    last_error: str | None = None

    def snapshot(
        self, *, now: datetime, world: dict[str, object] | None = None
    ) -> dict[str, object]:
        task_running = self.task is not None and not self.task.done()
        stale_after_seconds = max(60.0, self.interval_seconds * 4)
        stale = (
            self.last_completed_at is not None
            and (now - self.last_completed_at).total_seconds() > stale_after_seconds
        )
        if not task_running:
            status = "stopped"
        elif self.last_completed_at is None:
            status = "starting"
        elif stale:
            status = "stale"
        elif self.last_error is not None and (
            self.last_success_at is None or self.last_completed_at > self.last_success_at
        ):
            status = "failing"
        else:
            status = "running"
        world = world or {}
        raw_initiative_warning_reasons = world.get("initiative_warning_reasons", [])
        initiative_warning_reasons = [
            item
            for item in (
                raw_initiative_warning_reasons
                if isinstance(raw_initiative_warning_reasons, (list, tuple))
                else ()
            )
            if isinstance(item, str) and item != "consideration_overdue"
        ]
        next_consideration_at = world.get("initiative_next_consideration_at")
        due_at = None
        if isinstance(next_consideration_at, str):
            try:
                parsed_due_at = datetime.fromisoformat(next_consideration_at)
            except ValueError:
                parsed_due_at = None
            if (
                parsed_due_at is not None
                and parsed_due_at.tzinfo is not None
                and parsed_due_at.utcoffset() is not None
            ):
                due_at = parsed_due_at
        # "Two scheduler cycles" is an adapter liveness promise, so its
        # threshold must come from this process's actual scheduler cadence.
        # The application projection has no authority to invent a platform
        # polling interval, and wall time lets this warning fire even when a
        # failed scheduler has stopped advancing the World clock itself.
        last_considered_at = None
        last_considered_raw = world.get("initiative_last_considered_at")
        if isinstance(last_considered_raw, str):
            try:
                parsed_last_considered = datetime.fromisoformat(last_considered_raw)
            except ValueError:
                parsed_last_considered = None
            if (
                parsed_last_considered is not None
                and parsed_last_considered.tzinfo is not None
                and parsed_last_considered.utcoffset() is not None
            ):
                last_considered_at = parsed_last_considered
        consideration_in_flight = world.get("initiative_state") in {
            "considering",
            "action_pending",
        }
        due_unconsumed = (
            due_at is not None
            and now - due_at > timedelta(seconds=self.interval_seconds * 2)
            and not consideration_in_flight
            and (last_considered_at is None or last_considered_at < due_at)
        )
        if due_unconsumed:
            initiative_warning_reasons.append("consideration_overdue")
        unexplained_initiative_warning = (
            bool(world.get("initiative_warning", False)) and not raw_initiative_warning_reasons
        )
        return {
            "status": status,
            "task_running": task_running,
            "interval_seconds": self.interval_seconds,
            "passes_started": self.passes_started,
            "passes_completed": self.passes_completed,
            "failures": self.failures,
            "last_started_at": (self.last_started_at.isoformat() if self.last_started_at else None),
            "last_completed_at": (
                self.last_completed_at.isoformat() if self.last_completed_at else None
            ),
            "last_success_at": (self.last_success_at.isoformat() if self.last_success_at else None),
            "last_duration_ms": self.last_duration_ms,
            "last_error": self.last_error,
            "last_ledger_event_created_at": world.get("last_ledger_event_created_at"),
            "last_ledger_sequence": world.get("last_ledger_sequence"),
            "overdue_declared_due_kinds": (
                ["social.initiative.cadence"] if due_unconsumed else []
            ),
            "initiative": {
                "last_status": world.get("initiative_last_status"),
                "last_reason": world.get("initiative_last_reason"),
                "pending_opportunity_count": world.get("pending_proactive_opportunity_count", 0),
                "pending_process_count": world.get("pending_proactive_process_count", 0),
                "pending_action_count": world.get("pending_proactive_action_count", 0),
                "spontaneous_candidate_due": world.get("spontaneous_candidate_due", False),
                "state": world.get("initiative_state", "waiting_context"),
                "last_considered_at": world.get("initiative_last_considered_at"),
                "last_model_decision": world.get("initiative_last_model_decision"),
                "last_decision_reason": world.get("initiative_last_decision_reason"),
                "last_impulse_summary": world.get("initiative_last_impulse_summary"),
                "last_grounding_outcome": world.get("initiative_last_grounding_outcome"),
                "grounding_corrected_count": world.get("initiative_grounding_corrected_count", 0),
                "grounding_rejected_count": world.get("initiative_grounding_rejected_count", 0),
                "stimulus_source_count": world.get("initiative_stimulus_source_count", 0),
                "stimulus_merge_window_seconds": world.get(
                    "initiative_stimulus_merge_window_seconds", 600
                ),
                "pending_expectation_count": world.get("initiative_pending_expectation_count", 0),
                "expectation_status_counts": world.get("initiative_expectation_status_counts", {}),
                "next_consideration_at": world.get("initiative_next_consideration_at"),
                "cadence_reason_codes": world.get("initiative_cadence_reason_codes", []),
                "consecutive_technical_failures": world.get(
                    "initiative_consecutive_technical_failures", 0
                ),
                "retry_ordinal": world.get("initiative_retry_ordinal", 0),
                "last_failure_code": world.get("initiative_last_failure_code"),
                "reliability_24h": world.get(
                    "initiative_reliability_24h",
                    {
                        "window_hours": 24,
                        "as_of": None,
                        "attempt_count": 0,
                        "consideration_count": 0,
                        "technical_failure_attempt_count": 0,
                        "technical_failure_consideration_count": 0,
                        "model_silent_count": 0,
                        "grounding_rejected_count": 0,
                        "authorized_count": 0,
                        "delivered_count": 0,
                        "delivery_pending_count": 0,
                        "delivery_non_delivered_terminal_count": 0,
                        "model_decision_success_rate": None,
                        "technical_failure_rate": None,
                        "technical_failure_attempt_rate": None,
                        "visible_authorization_rate": None,
                        "visible_delivery_rate": None,
                        "delivery_success_rate": None,
                        "technical_failure_codes": {},
                        "warning": False,
                        "warning_reasons": [],
                    },
                ),
                "warning": bool(initiative_warning_reasons) or unexplained_initiative_warning,
                "warning_reasons": initiative_warning_reasons,
            },
            "world_activity": {
                "life_event_count": world.get("life_event_count", 0),
                "occurrence_count": world.get("occurrence_count", 0),
                "experience_count": world.get("experience_count", 0),
                "starved": world.get("starved", False),
                "last_lived_at": world.get("last_lived_at"),
            },
            "private_impression": world.get("private_impression", {}),
            "expression_episode": world.get("expression_episode", {}),
            "expression_retry": world.get("expression_retry", {}),
            "character_interior": world.get(
                "character_interior",
                {
                    "status": "unavailable",
                    "installed": False,
                    "semantic_author_count": 0,
                    "primary_author_model": None,
                    "primary_author_route": None,
                    "parallel_character_author_conflicts": 0,
                    "legacy_interface_invocations": 0,
                    "dual_write_conflicts": 0,
                },
            ),
            # Keep the legacy ``world_activity`` contract stable while
            # exposing the per-mechanism evidence needed to diagnose a live
            # companion.  These values are read-only projection counts; they
            # do not claim that a model used a slice merely because it exists.
            "mechanisms": world.get("mechanisms", {}),
            "recall_semantic": world.get(
                "recall_semantic",
                {"enabled": False},
            ),
        }


def create_qq_c2c_onebot_app(
    *,
    adapter: str,
    settings: Settings,
    use_fake_model: bool = False,
    _test_only_model: ChatCompletionModel | None = None,
    _test_only_world_support_model: ChatCompletionModel | None = None,
    _test_only_source_closure_model: ChatCompletionModel | None = None,
    _test_only_life_source_closure_model: ChatCompletionModel | None = None,
    _test_only_provider_capture_authority_id: str | None = None,
    scheduler_interval_seconds: float = 15.0,
    media_preview: MediaPreviewDeployment | None = None,
    media_transport: MediaProviderTransport | None = None,
) -> FastAPI:
    """Create the opt-in v2 OneBot service for exactly one private QQ user.

    ``NAPCAT_ALLOWED_PRIVATE_USER_IDS`` is intentionally required to contain
    one id.  A missing or multi-user allowlist would create ambiguous target
    ownership and must not silently map several relationships into one world.
    """

    if adapter not in {"napcat", "onebot"}:
        raise ValueError(f"unsupported OneBot adapter: {adapter}")
    if scheduler_interval_seconds <= 0:
        raise ValueError("QQ C2C v2 scheduler interval must be positive")
    if (media_preview is None) != (media_transport is None):
        raise ValueError(
            "QQ media preview deployment and durable transport must be supplied together"
        )
    recipient_ids = tuple(
        item.strip() for item in settings.napcat_allowed_private_user_ids.split(",") if item.strip()
    )
    if len(recipient_ids) != 1:
        raise ValueError(
            "World v2 QQ C2C requires exactly one NAPCAT_ALLOWED_PRIVATE_USER_IDS entry"
        )
    recipient_id = recipient_ids[0]
    test_authorities_injected = any(
        model is not None
        for model in (
            _test_only_model,
            _test_only_world_support_model,
            _test_only_source_closure_model,
            _test_only_life_source_closure_model,
        )
    )
    if use_fake_model and test_authorities_injected:
        raise ValueError("fake-model mode cannot also inject test authorities")
    if use_fake_model and _test_only_provider_capture_authority_id is not None:
        raise ValueError("fake-model mode cannot use a provider capture authority")
    if test_authorities_injected:
        if _test_only_model is None or _test_only_source_closure_model is None:
            raise ValueError(
                "test authority injection requires a character author and source reviewer"
            )
        strict_checker = getattr(
            _test_only_source_closure_model,
            "supports_strict_output_contract",
            None,
        )
        if not callable(strict_checker) or not strict_checker("visible-beat-source-verdict.1"):
            raise ValueError("test source reviewer requires the compact visible-Beat contract")
    if (
        not use_fake_model
        and _test_only_model is None
        and not (settings.deepseek_api_key or "").strip()
    ):
        raise RuntimeError(
            "World v2 production QQ C2C requires a configured real character provider; "
            "set DEEPSEEK_API_KEY or explicitly enable the fake-model test mode"
        )
    media_bundle = None
    if media_preview is None and not use_fake_model:
        # The production entry composes its own complete media deployment
        # from Settings.  Missing credentials, an explicit off-switch, or an
        # unprovisioned enforcement grant chain disable the lane with one log
        # line inside the factory; an explicit injected deployment wins.
        # Delivery is world-owned (selection Acceptance + composed
        # guardrails); there is no human approval step.
        media_bundle = build_qq_media_preview_deployment(
            settings=settings, world_id=qq_c2c_world_id(settings.primary_user_id)
        )
        if media_bundle is not None:
            media_preview = media_bundle.deployment
            media_transport = media_bundle.transport
    access_token = (
        settings.napcat_access_token if adapter == "napcat" else settings.onebot_access_token
    ) or None
    perception_bundle = None
    if not use_fake_model:
        # Perception is likewise composed from Settings: a zero budget,
        # missing credentials, or an unprovisioned perception enforcement
        # chain disables the lane with one log line inside the factory.
        perception_bundle = build_qq_perception_deployment(
            settings=settings,
            world_id=qq_c2c_world_id(settings.primary_user_id),
            api_url=(settings.napcat_api_url if adapter == "napcat" else settings.onebot_api_url),
            access_token=access_token,
        )
    host = build_qq_c2c_host(
        settings=settings,
        recipient_id=recipient_id,
        model=FakeCompanionModel() if use_fake_model else _test_only_model,
        world_support_model=_test_only_world_support_model,
        source_closure_model=_test_only_source_closure_model,
        life_source_closure_model=_test_only_life_source_closure_model,
        media_preview=media_preview,
        media_transport=media_transport,
        perception_input_source=(
            perception_bundle.input_source if perception_bundle is not None else None
        ),
        perception_transport=(
            perception_bundle.transport if perception_bundle is not None else None
        ),
        perception_budget_limit=(
            perception_bundle.budget_limit if perception_bundle is not None else 0
        ),
        test_only_provider_capture_authority_id=(_test_only_provider_capture_authority_id),
        scheduler_interval_seconds=scheduler_interval_seconds,
    )
    scheduler = QQC2CSchedulerDiagnostics(interval_seconds=scheduler_interval_seconds)
    dashboard_runtime_sampler = DashboardRuntimeObservationSampler(
        scheduler=lambda: scheduler.snapshot(now=datetime.now(UTC)),
        character_interior=_dashboard_host_probe(host, "dashboard_character_interior_health"),
        local_provider_capacity=_dashboard_host_probe(host, "local_provider_capacity_health"),
        text_endpoint=_dashboard_host_probe(host, "text_endpoint_health"),
        proactive_source_authority=_dashboard_host_probe(host, "proactive_source_authority_health"),
        life_source_authority=_dashboard_host_probe(host, "life_source_authority_health"),
        external_perception_upstream=_dashboard_host_probe(
            host, "external_world_perception_health"
        ),
        model_usage_budget=_dashboard_host_probe(host, "usage_budget_health"),
        process_latency=lambda: _dashboard_latency_probe(host),
        storage=lambda: ledger_storage_snapshot(settings.database_path),
        expression_episode=_dashboard_host_probe(host, "dashboard_expression_episode_health"),
        semantic_recall=_dashboard_host_probe(host, "dashboard_semantic_recall_health"),
    )

    api_url = settings.napcat_api_url if adapter == "napcat" else settings.onebot_api_url

    async def _fetch_recent_history() -> list[dict[str, object]]:
        return await get_onebot_friend_msg_history(
            api_url,
            user_id=recipient_id,
            count=DEFAULT_BACKFILL_COUNT,
            access_token=access_token,
        )

    def _start_attachment_archive(raw_event: dict[str, object]) -> asyncio.Task | None:
        """Pull inbound image bytes concurrently with the ingress composure wait.

        The archiver owns its failures (a miss degrades to "no bytes to
        perceive"); this hook only decides *when* it runs so a provider
        download never delays accepting the message itself.
        """

        if perception_bundle is None:
            return None
        if not QQOneBotAttachmentArchiver.image_segments(raw_event):
            return None
        return asyncio.create_task(
            perception_bundle.archiver.archive_from_event(raw_event),
            name="world-v2-qq-attachment-archive",
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Restart-window compensation: messages the user sent while this
        # process was down exist only in provider history.  Replay them
        # through the ordinary dedup ingress before live traffic resumes;
        # the pass runs as a background task so startup and push ingress
        # never block on a slow or absent provider history API.
        backfill_task = asyncio.create_task(
            backfill_missed_private_messages(
                host=host,
                fetch_history=_fetch_recent_history,
                recipient_id=recipient_id,
                archive_event=(
                    perception_bundle.archiver.archive_from_event
                    if perception_bundle is not None
                    else None
                ),
            ),
            name="world-v2-qq-c2c-history-backfill",
        )
        task = asyncio.create_task(
            _scheduler_loop(
                host,
                interval_seconds=scheduler_interval_seconds,
                diagnostics=scheduler,
            ),
            name="world-v2-qq-c2c-scheduler",
        )
        scheduler.task = task
        try:
            yield
        finally:
            backfill_task.cancel()
            task.cancel()
            await asyncio.gather(backfill_task, task, return_exceptions=True)
            await host.aclose()
            wait_for_quiescence = getattr(
                host,
                "wait_for_shutdown_quiescence",
                None,
            )
            if callable(wait_for_quiescence):
                await wait_for_quiescence()
            if media_bundle is not None:
                media_bundle.transport.close()
            if perception_bundle is not None:
                perception_bundle.close()

    app = FastAPI(title=f"Girl-Agent {adapter.title()} World v2 C2C", lifespan=lifespan)
    app.state.qq_c2c_host = host
    app.state.dashboard_runtime_sampler = dashboard_runtime_sampler
    app.state.dashboard_session_secret = secrets.token_bytes(32)
    _mount_dashboard_static_files(app)

    @app.post("/onebot/event")
    async def onebot_event(
        request: Request,
        authorization: str | None = Header(None),
        x_signature: str | None = Header(None),
    ):
        if not _event_request_is_authorized(
            request,
            access_token,
            authorization=authorization,
            x_signature=x_signature,
            accept_unauthenticated_local=settings.napcat_accept_unauthenticated_local_events,
        ):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        raw_event = await request.json()
        if raw_event.get("post_type") == "message" and raw_event.get("message_type") == "group":
            return {"status": "ignored_group_v2_unsupported"}
        try:
            fragment = normalize_onebot_qq_ingress(raw_event)
        except (TypeError, ValueError):
            return JSONResponse({"status": "rejected_invalid_qq_ingress"}, status_code=400)
        if fragment is None:
            return {"status": "ignored_qq_shape_v2_unsupported"}
        if fragment.recipient_id != recipient_id:
            return {"status": "ignored_private"}
        archive_task = _start_attachment_archive(raw_event)
        try:
            result = await host.inbound_fragment(fragment)
        finally:
            if archive_task is not None:
                await archive_task
        return {
            "status": result.status,
            "world_action_id": result.action_id,
            "canonical_user_id": result.canonical_user_id,
        }

    @app.get("/health")
    async def health():
        # Report-only: this handler never exits the process.  launchd KeepAlive
        # watches the PID, not this JSON, so a non-running status must not 5xx.
        diagnostics_error: str | None = None
        world: dict[str, object] = {}
        try:
            world = await host.world_health_diagnostics()
        except Exception as exc:  # health must stay available
            diagnostics_error = type(exc).__name__
        scheduler_view = scheduler.snapshot(now=datetime.now(UTC), world=world)
        try:
            scheduler_view["local_provider_capacity"] = host.local_provider_capacity_health()
            scheduler_view["text_turn_endpoint"] = host.text_endpoint_health()
            scheduler_view["proactive_source_authority"] = host.proactive_source_authority_health()
            scheduler_view["life_source_authority"] = host.life_source_authority_health()
            scheduler_view["budget"] = host.usage_budget_health()
            external_perception_health = host.external_world_perception_health()
            downstream = world.get("external_perception_downstream")
            if isinstance(downstream, dict):
                external_perception_health["downstream"] = downstream
            scheduler_view["external_world_perception"] = external_perception_health
            scheduler_view["performance"] = production_latency_health_snapshot(
                host.latency_samples()
            )
            # Rolling process-local reliability counters (24h window): provider
            # dispatch ACKs are reported separately from strongly evidenced
            # visible replies, alongside failsafe engagements and repairs.  The
            # ledger stays the durable audit; this makes the failsafe rate
            # checkable at a glance without a ledger scan.
            scheduler_view["reliability"] = reliability_snapshot()
        except Exception as exc:  # health must stay available
            diagnostics_error = diagnostics_error or type(exc).__name__
        try:
            scheduler_view["reliability_ledger"] = durable_reliability_snapshot(
                settings.database_path
            )
        except Exception as exc:  # health must stay available
            scheduler_view["reliability_ledger"] = {
                "source": "ledger",
                "error": type(exc).__name__,
            }
        try:
            scheduler_view["storage"] = ledger_storage_snapshot(settings.database_path)
        except Exception as exc:  # health must stay available
            scheduler_view["storage"] = {"status": "error", "error": type(exc).__name__}
        writable = ledger_path_is_writable(settings.database_path)
        storage = scheduler_view.get("storage")
        if isinstance(storage, dict):
            storage["writable"] = writable
        interior = scheduler_view.get("character_interior")
        verdict = compile_process_health(
            healthy_status="running",
            character_interior=interior if isinstance(interior, dict) else None,
            budget=scheduler_view.get("budget")
            if isinstance(scheduler_view.get("budget"), dict)
            else None,
            scheduler_status=(
                str(scheduler_view.get("status"))
                if isinstance(scheduler_view.get("status"), str)
                else None
            ),
            storage=storage if isinstance(storage, dict) else None,
            recall_semantic=scheduler_view.get("recall_semantic")
            if isinstance(scheduler_view.get("recall_semantic"), dict)
            else None,
            external_perception=scheduler_view.get("external_world_perception")
            if isinstance(scheduler_view.get("external_world_perception"), dict)
            else None,
            initiative=scheduler_view.get("initiative")
            if isinstance(scheduler_view.get("initiative"), dict)
            else None,
            scheduler=scheduler_view,
            world_activity=scheduler_view.get("world_activity")
            if isinstance(scheduler_view.get("world_activity"), dict)
            else None,
            private_impression=scheduler_view.get("private_impression")
            if isinstance(scheduler_view.get("private_impression"), dict)
            else None,
            ledger_writable=writable,
            diagnostics_error=diagnostics_error,
        )
        return {
            "status": verdict.status,
            "reason": verdict.reason,
            "reasons": list(verdict.reasons),
            "adapter": adapter,
            "world_v2": True,
            "mode": "c2c-normalized-ingress",
            "scheduler": scheduler_view,
        }

    def _read_only_operator_access(
        *,
        token: str | None,
        configured: str | None,
        disabled_error: str,
    ) -> JSONResponse | None:
        """Gate one read-only operator surface behind its composition secret.

        Each surface stays disabled until its composition-specific credential
        exists, and a wrong token is rejected without leaking owner state.  The
        surface is deliberately observation-only — delivery is decided by the
        world's own selection/acceptance chain and its composed guardrails.
        """

        expected = (configured or "").strip()
        if not expected:
            return JSONResponse(
                {"error": disabled_error},
                status_code=503,
            )
        if not token or not secrets.compare_digest(token, expected):
            return JSONResponse({"error": "invalid operator token"}, status_code=403)
        return None

    @app.get("/internal/world-v2/dashboard/operator-snapshot")
    async def dashboard_operator_snapshot(
        request: Request,
        if_none_match: str | None = Header(None),
        x_world_v2_internal_token: str | None = Header(None),
    ):
        denied = _read_only_operator_access(
            token=x_world_v2_internal_token,
            configured=settings.world_v2_dashboard_operator_token,
            disabled_error=(
                "dashboard snapshot is disabled until its read-only operator token is configured"
            ),
        )
        if denied is not None:
            return denied
        if request.query_params:
            return JSONResponse(
                {"error": "dashboard snapshot does not accept query parameters"},
                status_code=400,
            )
        runtime_observation = await dashboard_runtime_sampler.capture()
        snapshot = await host.dashboard_home_snapshot(runtime_observation)
        etag = f'"{snapshot.snapshot_hash}"'
        headers = {"Cache-Control": "private, no-store", "ETag": etag}
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(content=snapshot.to_payload(), headers=headers)

    def _owner_dashboard_access(
        request: Request,
        token: str | None,
    ) -> JSONResponse | HTMLResponse | None:
        host_denied = _require_safe_dashboard_host(request)
        if host_denied is not None:
            return host_denied
        if _dashboard_session_is_valid(request, settings):
            return None
        return _read_only_operator_access(
            token=token,
            configured=settings.world_v2_dashboard_operator_token,
            disabled_error=(
                "dashboard snapshot is disabled until its read-only operator token is configured"
            ),
        )

    @app.get("/world-v2/room")
    def world_v2_public_room():
        """Return the public-only Room DTO from the already-open QQ World."""

        try:
            return host.dashboard_room().to_payload()
        except PermissionError:
            return JSONResponse(
                {"error": "World v2 room projection denied"},
                status_code=403,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)

    @app.get("/world-v2/dashboard")
    def world_v2_dashboard_public(
        if_none_match: str | None = Header(None),
        x_world_v2_internal_token: str | None = Header(None),
    ):
        denied = _read_only_operator_access(
            token=x_world_v2_internal_token,
            configured=settings.world_v2_dashboard_operator_token,
            disabled_error=(
                "World v2 Dashboard is disabled until its read-only token is configured"
            ),
        )
        if denied is not None:
            return denied
        try:
            payload = host.dashboard_public().to_payload()
        except PermissionError:
            return JSONResponse(
                {"error": "World v2 dashboard projection denied"},
                status_code=403,
            )
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        etag = f'"{payload["projection_hash"]}"'
        headers = {"Cache-Control": "no-store", "ETag": etag}
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(content=payload, headers=headers)

    @app.get("/dashboard", response_class=HTMLResponse)
    def owner_dashboard(request: Request):
        host_denied = _require_safe_dashboard_host(request)
        if host_denied is not None:
            return host_denied
        codec = _dashboard_session_codec(request.app, settings)
        if codec is None:
            return HTMLResponse(
                UNAVAILABLE_HTML,
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if not _dashboard_session_is_valid(request, settings):
            return HTMLResponse(LOGIN_HTML, headers={"Cache-Control": "no-store"})
        return HTMLResponse(DASHBOARD_HTML, headers={"Cache-Control": "no-store"})

    @app.post("/world-v2/dashboard/session")
    async def world_v2_dashboard_login(request: Request):
        host_denied = _require_safe_dashboard_host(request)
        if host_denied is not None:
            return host_denied
        if not _same_dashboard_origin(request):
            return JSONResponse({"error": "invalid Dashboard origin"}, status_code=403)
        codec = _dashboard_session_codec(request.app, settings)
        if codec is None:
            return HTMLResponse(
                UNAVAILABLE_HTML,
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/x-www-form-urlencoded"
        ):
            return HTMLResponse(LOGIN_HTML, status_code=415, headers={"Cache-Control": "no-store"})
        body = await request.body()
        if len(body) > 4096:
            return HTMLResponse(LOGIN_HTML, status_code=413, headers={"Cache-Control": "no-store"})
        try:
            submitted = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                strict_parsing=True,
            ).get("operator_token", [""])[0]
        except (UnicodeDecodeError, ValueError):
            submitted = ""
        configured = (settings.world_v2_dashboard_operator_token or "").strip()
        if not submitted or not secrets.compare_digest(submitted, configured):
            return HTMLResponse(LOGIN_HTML, status_code=401, headers={"Cache-Control": "no-store"})
        response = Response(
            status_code=303, headers={"Location": "/dashboard", "Cache-Control": "no-store"}
        )
        response.set_cookie(
            DASHBOARD_SESSION_COOKIE,
            codec.issue(),
            max_age=DASHBOARD_SESSION_TTL_SECONDS,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            path="/",
        )
        return response

    @app.post("/world-v2/dashboard/logout")
    def world_v2_dashboard_logout(request: Request):
        host_denied = _require_safe_dashboard_host(request)
        if host_denied is not None:
            return host_denied
        if not _same_dashboard_origin(request):
            return JSONResponse({"error": "invalid Dashboard origin"}, status_code=403)
        response = Response(
            status_code=303, headers={"Location": "/dashboard", "Cache-Control": "no-store"}
        )
        response.delete_cookie(DASHBOARD_SESSION_COOKIE, path="/", httponly=True, samesite="strict")
        return response

    @app.get("/world-v2/dashboard/app.js")
    def world_v2_dashboard_script():
        return Response(
            DASHBOARD_APP_JS,
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/world-v2/dashboard/home")
    async def world_v2_dashboard_home(
        request: Request,
        if_none_match: str | None = Header(None),
        x_world_v2_internal_token: str | None = Header(None),
    ):
        denied = _owner_dashboard_access(request, x_world_v2_internal_token)
        if denied is not None:
            return denied
        if request.query_params:
            return JSONResponse(
                {"error": "dashboard home does not accept query parameters"},
                status_code=400,
            )
        runtime_observation = await dashboard_runtime_sampler.capture()
        snapshot = await host.dashboard_home_snapshot(runtime_observation)
        etag = f'"{snapshot.snapshot_hash}"'
        headers = {"Cache-Control": "private, no-store", "ETag": etag}
        if if_none_match == etag:
            return Response(status_code=304, headers=headers)
        return JSONResponse(content=snapshot.to_payload(), headers=headers)

    @app.get("/internal/world-v2/media/previews")
    async def media_previews(
        x_world_v2_internal_token: str | None = Header(None),
    ):
        denied = _read_only_operator_access(
            token=x_world_v2_internal_token,
            configured=settings.delivery_reconciliation_token,
            disabled_error=(
                "media observation surface is disabled until an operator token is configured"
            ),
        )
        if denied is not None:
            return denied
        observer = host.media_preview_operator()
        return {"previews": list(observer.queue(materialize=True))}

    @app.get("/internal/world-v2/media/previews/{preview_id}/image")
    async def media_preview_image(
        preview_id: str,
        x_world_v2_internal_token: str | None = Header(None),
    ):
        denied = _read_only_operator_access(
            token=x_world_v2_internal_token,
            configured=settings.delivery_reconciliation_token,
            disabled_error=(
                "media observation surface is disabled until an operator token is configured"
            ),
        )
        if denied is not None:
            return denied
        observer = host.media_preview_operator()
        row = next(
            (item for item in observer.queue(materialize=True) if item["preview_id"] == preview_id),
            None,
        )
        if row is None or not row.get("image_path"):
            return JSONResponse({"error": "preview image is unavailable"}, status_code=404)
        return FileResponse(str(row["image_path"]), media_type="image/png")

    return app


async def _scheduler_loop(
    host: QQC2CHost,
    *,
    interval_seconds: float,
    diagnostics: QQC2CSchedulerDiagnostics,
) -> None:
    """Bounded recovery loop; each pass resumes from the durable v2 clock."""

    # One model-backed background unit can fan out into a Life/NPC/Memory
    # settlement and therefore take seconds or minutes.  Starting with the
    # historical budget of eight units made a restart spend an unbounded
    # foreground-looking burst on backlog recovery: /health remained in
    # ``starting`` and the user-visible lane competed with stale cognition.
    # Durable claims already provide fairness across wakes, so one unit per
    # pass is the safer production quantum.  Action recovery remains at its
    # normal bounded budget and inbound turns can preempt this lane.
    background_units_per_pass = 1
    while True:
        started_at = datetime.now(UTC)
        started = time.monotonic()
        diagnostics.passes_started += 1
        diagnostics.last_started_at = started_at
        try:
            result = await host.scheduler_once(
                observed_at=datetime.now(UTC),
                max_action_units=8,
                max_background_units=background_units_per_pass,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            diagnostics.failures += 1
            diagnostics.last_error = type(exc).__name__
            logger.exception("World v2 QQ C2C scheduler pass failed")
        else:
            raw_background_statuses = getattr(result, "background_statuses", ())
            background_statuses = (
                raw_background_statuses
                if isinstance(raw_background_statuses, (list, tuple))
                else ()
            )
            technical_failures = tuple(
                status
                for status in background_statuses
                if isinstance(status, str) and status.startswith("technical_failure:")
            )
            if technical_failures:
                diagnostics.failures += 1
                diagnostics.last_error = technical_failures[-1]
                logger.error(
                    "World v2 QQ C2C scheduler pass recorded technical background failure: %s",
                    technical_failures[-1],
                )
            else:
                diagnostics.last_success_at = datetime.now(UTC)
                diagnostics.last_error = None
        diagnostics.passes_completed += 1
        diagnostics.last_completed_at = datetime.now(UTC)
        diagnostics.last_duration_ms = round((time.monotonic() - started) * 1_000)
        try:
            # Passive WAL compaction is scheduler upkeep, never reply work.
            # The ledger self-throttles by WAL size and a minimum interval,
            # and yields immediately to any active writer.
            result = await host.maintain_wal_once()
            if result is not None and getattr(result, "status", "skipped") != "skipped":
                logger.info(
                    "world v2 QQ WAL maintenance status=%s before_bytes=%s after_bytes=%s "
                    "log_frames=%s checkpointed_frames=%s",
                    result.status,
                    result.wal_bytes_before,
                    result.wal_bytes_after,
                    result.log_frames,
                    result.checkpointed_frames,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("World v2 QQ WAL maintenance failed")
        await asyncio.sleep(interval_seconds)


def _event_request_is_authorized(
    request: Request,
    expected_token: str | None,
    *,
    authorization: str | None,
    x_signature: str | None,
    accept_unauthenticated_local: bool,
) -> bool:
    if event_token_is_valid(expected_token, authorization=authorization, x_signature=x_signature):
        return True
    client_host = request.client.host if request.client else None
    return bool(accept_unauthenticated_local and client_host in {"127.0.0.1", "::1"})


__all__ = ["QQC2CSchedulerDiagnostics", "create_qq_c2c_onebot_app"]
