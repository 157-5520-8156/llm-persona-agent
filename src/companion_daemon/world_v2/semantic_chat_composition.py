"""Production composition for CharacterInterior and compute routing.

The Module keeps one small Interface for platform composition roots while it
owns model selection, Flash / Thinking routing, endpoint timing, source review,
and model lifecycle. CharacterInterior is the only protagonist semantic author;
the text endpoint predicts only whether another user bubble is likely.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
import logging
from types import SimpleNamespace
from typing import Literal
from urllib.parse import urlsplit

from companion_daemon.character import load_character
from companion_daemon.config import Settings
from companion_daemon.llm import (
    DeepSeekChatModel,
    OpenAICompatibleChatModel,
    ProviderCapacityGate,
    text_endpoint_capacity_marker_path,
)

from .character_interior import CharacterInterior
from .character_interior.production import compose_production_character_interior
from .character_interior.turn_store import _CharacterInteriorTurnStore
from .companion_identity import CompanionIdentityFrame
from .expression_draft import (
    ExpressionDraftCapabilities,
    PRODUCTION_TEXT_ONLY_EXPRESSION_CAPABILITIES,
)
from .model_completion import ChatCompletionModel
from .model_authority_identity import (
    possible_provider_lanes,
    provider_lane_sets_are_independent,
    semantic_authority_id,
)
from .semantic_compute_router import SemanticComputeRouter
from .source_closure_lane import SourceClosureReselectionLane
from .text_turn_endpoint import (
    ChatSemanticEndpointModel,
    TextTurnEndpointController,
)


_LOG = logging.getLogger(__name__)
_CANDIDATE_INVENTORY_CONTRACT = "candidate-external-proposition-inventory.5"
_CANDIDATE_COVERAGE_CONTRACT = "candidate-external-proposition-coverage.5"
_FULL_SOURCE_REVIEW_CONTRACT = "source-closure-review.7"
_REPORT_RELATIVE_REVIEW_CONTRACT = "report-relative-entailment-adjudication.3"
_LIFE_SOURCE_REVIEW_CONTRACTS = (
    "life-development-source-closure-review.1",
    "life-development-novel-origin-review.2",
)


def unavailable_life_source_authority_health() -> dict[str, object]:
    """Return a fresh backward-compatible snapshot for missing composition.

    Hosts expose this shape before semantic composition is available. Returning
    new containers prevents one health consumer from mutating a later response.
    """

    return {
        "status": "unavailable",
        "warning": True,
        "warning_reasons": ["life_source_authority.composition_unavailable"],
        "runtime_isolated": False,
        "runtime_isolation": "unavailable",
        "reviewer_model": None,
        "contracts": {
            contract: {
                "schema_installed": False,
                "parser_fail_closed": True,
                "release_qualified": False,
            }
            for contract in _LIFE_SOURCE_REVIEW_CONTRACTS
        },
        "last_transport_winner": None,
        "route_suppression": {},
        "transport_runtime": None,
    }


def _validated_test_only_provider_capture_authority(
    *,
    settings: Settings,
    authority_id: str | None,
) -> str | None:
    """Validate the identity hand-off used by the isolated provider harness.

    The real author route is identified by its provider endpoint and checkpoint.
    A loopback hash capture necessarily changes that endpoint, so the acceptance
    process may carry the already-derived identity explicitly.  This seam is
    deliberately restricted to an IPv4 loopback endpoint and to the exact
    release-pinned DeepSeek checkpoint; production configuration cannot use it
    to claim an arbitrary authority.
    """

    if authority_id is None:
        return None
    try:
        parsed = urlsplit(settings.deepseek_base_url.rstrip("/"))
        port = parsed.port
    except ValueError as exc:
        raise ValueError(
            "test-only provider capture authority requires a valid loopback DeepSeek endpoint"
        ) from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "test-only provider capture authority requires an exact IPv4 loopback DeepSeek endpoint"
        )
    expected = semantic_authority_id(
        SimpleNamespace(
            provider="deepseek",
            base_url="https://api.deepseek.com",
            model=settings.deepseek_model,
        )
    )
    if expected is None or authority_id != expected:
        raise ValueError(
            "test-only provider capture authority does not match the configured DeepSeek checkpoint"
        )
    return authority_id


def _apply_test_only_provider_capture_authority(
    model: object | None,
    authority_id: str | None,
    *,
    settings: Settings,
) -> None:
    """Attach the validated capture identity before independence checks.

    Only auto-created models reach this helper.  Rechecking their route and
    checkpoint here prevents a future provider-construction change from
    accidentally inheriting the primary model's authority label.
    """

    if authority_id is None or model is None:
        return
    if (
        getattr(model, "provider", None) != "deepseek"
        or str(getattr(model, "base_url", "")).rstrip("/")
        != settings.deepseek_base_url.rstrip("/")
        or str(getattr(model, "model", "")).strip()
        != settings.deepseek_model
    ):
        raise ValueError("test-only provider capture authority requires a DeepSeek model")
    object.__setattr__(model, "semantic_authority_id", authority_id)
    # The exact loopback capture is also allowed to receive a SHA-256 of the
    # adapter-verified logical request identity.  The capture proxy strips the
    # header before forwarding upstream; ordinary production models never set
    # this private flag and therefore never emit local audit metadata.
    object.__setattr__(model, "_test_only_capture_exact_request_identity", True)


def _model_identity(model: object | None) -> str | None:
    """Return the smallest deployment-visible model identity.

    This is an audit label, not a security principal.  Explicit provider
    dependencies remain the authority; the label only lets composition reject
    an obvious author-as-reviewer route and explain the deployed state.
    """

    if model is None:
        return None
    value = str(getattr(model, "model", "")).strip()
    return value[:256] if value else type(model).__name__[:256]


def _possible_provider_lanes(model: object | None) -> tuple[object, ...]:
    """Expand every provider that may produce bytes for one semantic role."""

    return possible_provider_lanes(model)


def _provider_lane_sets_are_independent(
    left: object | None,
    right: object | None,
) -> bool:
    """Prove that two roles have no shared possible provider authority."""

    return provider_lane_sets_are_independent(left, right)


def _reviewer_is_independent(*, author: object, reviewer: object | None) -> bool:
    """Reject self-review across every possible author and reviewer lane.

    A production ``FailoverChatModel`` has implicit failover disabled: its
    primary remains the role author and its separately exposed fallback can be
    the review authority.  If implicit failover is enabled, either branch may
    have authored the candidate and neither branch is an independent reviewer.
    """

    return _provider_lane_sets_are_independent(author, reviewer)


def _uses_implicit_character_failover(model: object | None) -> bool:
    """Whether one author port can silently switch to a second provider."""

    if model is None:
        return False
    origin = getattr(model, "authority_origin", model)
    return (
        getattr(origin, "primary", None) is not None
        and getattr(origin, "fallback", None) is not None
        and bool(getattr(origin, "implicit_failover", True))
    )


def _supports_strict_output_contract(
    model: object | None,
    contract: str,
) -> bool:
    """Query an explicit transport capability without guessing from identity."""

    if model is None:
        return False
    try:
        checker = getattr(model, "supports_strict_output_contract", None)
        return callable(checker) and checker(contract) is True
    except Exception:
        # A malformed capability declaration proves nothing. The caller keeps
        # its existing fallback instead of risking an unsupported strict wire.
        return False


def _installs_strict_output_contract(
    model: object | None,
    contract: str,
) -> bool:
    """Prove that every possible transport lane installs the exact schema."""

    if model is None:
        return False
    checker = getattr(model, "installs_strict_output_contract", None)
    try:
        if callable(checker):
            return checker(contract) is True
        primary = getattr(model, "primary", None)
        secondary = getattr(model, "secondary", None)
        if primary is not None and secondary is not None:
            return _installs_strict_output_contract(
                primary,
                contract,
            ) and _installs_strict_output_contract(secondary, contract)
    except Exception:
        return False
    return False


def _shares_known_reviewer_runtime(left: object | None, right: object | None) -> bool:
    """Reject mutable runtime objects known to couple two reviewer roles."""

    if left is None or right is None:
        return False
    if left is right:
        return True
    for attribute in ("circuit_breaker", "capacity_gate"):
        left_state = getattr(left, attribute, None)
        right_state = getattr(right, attribute, None)
        if left_state is not None and left_state is right_state:
            return True
    left_lanes = tuple(
        lane
        for lane_name in ("primary", "secondary")
        if (lane := getattr(left, lane_name, None)) is not None
    )
    right_lanes = tuple(
        lane
        for lane_name in ("primary", "secondary")
        if (lane := getattr(right, lane_name, None)) is not None
    )
    if any(
        left_lane is right_lane or _shares_known_reviewer_runtime(left_lane, right_lane)
        for left_lane in left_lanes
        for right_lane in right_lanes
    ):
        return True
    return False


def _provider_roles_are_pairwise_independent(
    *,
    author: object,
    inventory: object | None,
    reviewer: object | None,
) -> bool:
    """Require three distinct semantic authorities before enabling Inventory V5."""

    return (
        inventory is not None
        and _provider_lane_sets_are_independent(author, inventory)
        and _provider_lane_sets_are_independent(author, reviewer)
        and _provider_lane_sets_are_independent(inventory, reviewer)
    )


def _candidate_review_capability(
    *,
    authors: tuple[object, ...],
    inventory: object | None,
    reviewer: object | None,
) -> tuple[bool, bool, bool]:
    """Describe one candidate lane without invoking any provider."""

    return (
        _supports_strict_output_contract(
            inventory,
            _CANDIDATE_INVENTORY_CONTRACT,
        ),
        _supports_strict_output_contract(
            reviewer,
            _CANDIDATE_COVERAGE_CONTRACT,
        ),
        bool(authors)
        and all(
            _provider_roles_are_pairwise_independent(
                author=author,
                inventory=inventory,
                reviewer=reviewer,
            )
            for author in authors
        ),
    )


@dataclass(frozen=True, slots=True)
class ProactiveSourceAuthorityDeployment:
    """Auditable deployment state for proactive visible-fact closure.

    Missing source authority does not decide that the character must stay
    silent. Source-free subjective expression remains available; visible
    external propositions fail closed when the compact guard is unavailable.
    No legacy Inventory/full-review shape is reported as an active chat route.
    """

    status: Literal["ready", "correlated_guard", "fact_effects_fail_closed"]
    author_model: str
    reviewer_model: str | None
    candidate_inventory_model: str | None
    requested_candidate_inventory_model: str | None = None
    inventory_capability_evidence: object | None = None
    inventory_route_evidence: tuple[object, ...] = ()
    inventory_runtime_model: object | None = None
    visible_source_review_runtime_model: object | None = None
    inventory_call_timeout_seconds: float | None = None
    warning_reasons: tuple[str, ...] = ()
    source_review_authority: object | None = None
    ordinary_candidate_review_capability: tuple[bool, bool, bool] = (
        False,
        False,
        False,
    )
    recovery_candidate_review_capability: tuple[bool, bool, bool] = (
        False,
        False,
        False,
    )
    reselection_candidate_review_capability: tuple[bool, bool, bool] = (
        False,
        False,
        False,
    )
    inventory_transport_routes: tuple[str, ...] = ()

    @property
    def independent_reviewer(self) -> bool:
        return self.status == "ready" and _supports_strict_output_contract(
            self.visible_source_review_runtime_model,
            "visible-beat-source-verdict.1",
        )

    def health_snapshot(self) -> dict[str, object]:
        def capability_snapshot(
            value: tuple[bool, bool, bool],
        ) -> dict[str, bool]:
            inventory_v5, coverage_v5, roles_independent = value
            return {
                "inventory_v5": inventory_v5,
                "coverage_v5": coverage_v5,
                "roles_independent": roles_independent,
            }

        runtime_reader = getattr(
            self.inventory_runtime_model,
            "strict_output_runtime_snapshot",
            None,
        )
        inventory_runtime = (
            runtime_reader()
            if callable(runtime_reader)
            else {
                "status": "unavailable",
                "successful_calls": 0,
                "failed_calls": 0,
                "last_checked_at": None,
                "last_failure_code": None,
            }
        )
        selective_reader = getattr(
            self.visible_source_review_runtime_model,
            "health_snapshot",
            None,
        )
        compact_guard_installed = _supports_strict_output_contract(
            self.visible_source_review_runtime_model,
            "visible-beat-source-verdict.1",
        )
        selective_runtime = (
            selective_reader()
            if callable(selective_reader)
            else None
        )
        warning_reasons = list(self.warning_reasons)
        if (
            not compact_guard_installed
            and self.status != "fact_effects_fail_closed"
        ):
            warning_reasons.append("visible_source_review.compact_guard_unavailable")
        runtime_status = str(inventory_runtime.get("status") or "unavailable")
        if runtime_status == "qualified_unprobed":
            warning_reasons.append("source_inventory.qualified_unprobed")
        elif runtime_status == "degraded":
            warning_reasons.append("source_inventory.full_source_closure_fallback_active")
        elif runtime_status == "runtime_failed":
            warning_reasons.append("source_inventory.runtime_failed")
        warning_reasons = list(dict.fromkeys(warning_reasons))
        redundancy_state = (
            "redundant"
            if self.source_review_authority is not None
            else "single_lane"
            if self.status == "ready"
            else "unavailable"
        )
        inventory_provider_count = len(
            {
                str(getattr(evidence, "provider", "")).casefold()
                for evidence in self.inventory_route_evidence
                if getattr(evidence, "provider", None)
            }
        )
        inventory_qualification_state = "unavailable"
        active_source_review_protocol = "unavailable"
        visible_review_strategy = "unavailable"
        if compact_guard_installed:
            relation = (
                selective_runtime.get("semantic_authority_relation")
                if selective_runtime is not None
                else "correlated_same_checkpoint"
                if self.status == "correlated_guard"
                else "independent"
            )
            visible_review_strategy = "visible_beat_verdict"
            active_source_review_protocol = "visible_beat_source_verdict.1"
            if relation == "correlated_same_checkpoint":
                # Legacy V7 objects may still exist for non-chat/life paths,
                # but the compact chat guard cannot fail over to them.  Do not
                # misreport inactive objects as redundancy for this lane.
                redundancy_state = "single_active_correlated_lane"
                warning_reasons.append(
                    "source_review_authority.correlated_same_checkpoint"
                )
        else:
            redundancy_state = "unavailable"
        reported_status = (
            self.status if compact_guard_installed else "fact_effects_fail_closed"
        )
        return {
            "status": reported_status,
            "warning": bool(warning_reasons),
            "warning_reasons": warning_reasons,
            "independent_reviewer": self.independent_reviewer,
            "fact_effects_available": compact_guard_installed
            and self.status in {"ready", "correlated_guard"},
            "source_guard_relation": (
                "independent"
                if compact_guard_installed and self.status == "ready"
                else "correlated_same_checkpoint"
                if compact_guard_installed and self.status == "correlated_guard"
                else "unavailable"
            ),
            "subjective_expression_available": True,
            "author_model": self.author_model,
            "reviewer_model": self.reviewer_model,
            "candidate_inventory_model": self.candidate_inventory_model,
            "requested_candidate_inventory_model": (self.requested_candidate_inventory_model),
            "inventory_capability_evidence": (
                self.inventory_capability_evidence.health_snapshot()
                if self.inventory_capability_evidence is not None
                and hasattr(self.inventory_capability_evidence, "health_snapshot")
                else None
            ),
            "inventory_runtime": inventory_runtime,
            "inventory_call_timeout_seconds": self.inventory_call_timeout_seconds,
            "visible_review_strategy": visible_review_strategy,
            "inventory_qualification_state": inventory_qualification_state,
            "active_source_review_protocol": active_source_review_protocol,
            "source_review_qualification_transition": (
                f"{inventory_qualification_state} -> {active_source_review_protocol}"
            ),
            "selective_source_review": {
                "enabled": compact_guard_installed,
                "runtime": selective_runtime,
            },
            "candidate_review_capabilities": {
                "ordinary": capability_snapshot(self.ordinary_candidate_review_capability),
                "recovery": capability_snapshot(self.recovery_candidate_review_capability),
                "reselection": capability_snapshot(self.reselection_candidate_review_capability),
            },
            "inventory_transport": {
                "route_count": len(self.inventory_transport_routes),
                "routes": self.inventory_transport_routes,
                "single_transport": len(self.inventory_transport_routes) == 1,
                "provider_count": inventory_provider_count,
                "single_provider": (
                    bool(self.inventory_route_evidence) and inventory_provider_count == 1
                ),
                "capability_evidence": [
                    evidence.health_snapshot()
                    for evidence in self.inventory_route_evidence
                    if hasattr(evidence, "health_snapshot")
                ],
                "attempt_timeout_seconds": getattr(
                    self.inventory_runtime_model,
                    "inventory_attempt_timeout_seconds",
                    None,
                ),
                "secondary_reserved_seconds": getattr(
                    self.inventory_runtime_model,
                    "inventory_secondary_reserved_seconds",
                    None,
                ),
            },
            "redundancy_state": redundancy_state,
            "source_review_authority": (
                self.source_review_authority.health_snapshot()
                if self.source_review_authority is not None
                and hasattr(self.source_review_authority, "health_snapshot")
                else None
            ),
        }


@dataclass(slots=True)
class SemanticChatComposition:
    """The complete capability-free semantic/model side of one chat host."""

    # Objective extraction/World-author lanes may reuse this provider, but no
    # host receives the protagonist author itself.  Every protagonist decision
    # is reachable only through ``character_interior``.
    world_support_model: ChatCompletionModel
    character_author_model_id: str
    expression_episode_observer_model: ChatCompletionModel | None
    source_closure_model: ChatCompletionModel | None
    recovery_source_closure_model: ChatCompletionModel | None
    source_closure_reselection_lane: SourceClosureReselectionLane | None
    proactive_source_closure_model: ChatCompletionModel | None
    life_source_closure_model: ChatCompletionModel | None
    life_source_runtime_isolation: str
    known_source_inventory: ChatCompletionModel | None
    proactive_source_authority: ProactiveSourceAuthorityDeployment
    character_interior: CharacterInterior
    router: SemanticComputeRouter
    identity_frame: CompanionIdentityFrame
    local_provider_capacity: ProviderCapacityGate | None
    text_endpoint_controller: TextTurnEndpointController | None
    _owned_models: tuple[object, ...] = ()
    # Close-only resources promise that ``aclose`` itself reaches quiescence.
    # Task owners may return from bounded close while retaining provider
    # leases, so they are tracked separately and expose an explicit waiter.
    _owned_closeables: tuple[object, ...] = ()
    _owned_task_owners: tuple[object, ...] = ()
    _close_task: asyncio.Task[None] | None = None
    _deferred_model_close_task: asyncio.Task[None] | None = None
    _models_closed: bool = False

    def proactive_source_authority_health(self) -> dict[str, object]:
        """Return read-only deployment evidence without invoking a model."""

        return self.proactive_source_authority.health_snapshot()

    def character_interior_health(self) -> dict[str, object]:
        """Expose the sole protagonist-author topology without private state."""

        return self.character_interior.runtime_health()

    def life_source_authority_health(self) -> dict[str, object]:
        """Report Life reviewer transport state without overstating qualification."""

        reviewer = self.life_source_closure_model
        contracts = {
            contract: {
                "schema_installed": _installs_strict_output_contract(
                    reviewer,
                    contract,
                ),
                "parser_fail_closed": True,
                "release_qualified": _supports_strict_output_contract(
                    reviewer,
                    contract,
                ),
            }
            for contract in _LIFE_SOURCE_REVIEW_CONTRACTS
        }
        runtime_isolation = self.life_source_runtime_isolation
        runtime_isolated = runtime_isolation in {
            "verified_fork",
            "dedicated_life_only",
        }
        transport_runtime: dict[str, object] | None = None
        health_reader = getattr(reviewer, "health_snapshot", None)
        if callable(health_reader):
            try:
                raw_health = health_reader()
                if isinstance(raw_health, dict):
                    transport_runtime = dict(raw_health)
            except Exception:
                _LOG.warning("Life source-review health snapshot failed", exc_info=True)

        route_suppression: object = {}
        last_transport_winner: dict[str, object] | None = None
        if transport_runtime is not None:
            raw_suppression = transport_runtime.get("route_suppression")
            if isinstance(raw_suppression, dict):
                route_suppression = raw_suppression
            winner_lane = transport_runtime.get("last_winner_lane")
            lane_models = transport_runtime.get("lane_models")
            lane_providers = transport_runtime.get("lane_providers")
            if isinstance(winner_lane, str) and winner_lane:
                last_transport_winner = {
                    "lane": winner_lane,
                    "model": (
                        lane_models.get(winner_lane) if isinstance(lane_models, dict) else None
                    ),
                    "provider": (
                        lane_providers.get(winner_lane)
                        if isinstance(lane_providers, dict)
                        else None
                    ),
                }

        all_qualified = all(
            bool(contract_health["release_qualified"]) for contract_health in contracts.values()
        )
        warning_reasons: list[str] = []
        if reviewer is None:
            status = "unavailable"
            warning_reasons.append("life_source_authority.reviewer_unavailable")
        elif runtime_isolation == "caller_provided_distinct_unverified":
            status = (
                "operational_isolation_unverified" if all_qualified else "operational_unqualified"
            )
            warning_reasons.append("life_source_authority.runtime_isolation_unverified")
            if not all_qualified:
                warning_reasons.append("life_source_authority.release_qualification_unavailable")
        elif not runtime_isolated:
            status = "unsafe_shared_runtime"
            warning_reasons.append("life_source_authority.runtime_not_isolated")
        elif not all_qualified:
            status = "operational_unqualified"
            warning_reasons.append("life_source_authority.release_qualification_unavailable")
        else:
            status = "ready"
        return {
            "status": status,
            "warning": bool(warning_reasons),
            "warning_reasons": warning_reasons,
            "runtime_isolated": runtime_isolated,
            "runtime_isolation": runtime_isolation,
            "reviewer_model": _model_identity(reviewer),
            "contracts": contracts,
            "last_transport_winner": last_transport_winner,
            "route_suppression": route_suppression,
            "transport_runtime": transport_runtime,
        }

    async def aclose(self) -> None:
        close_task = self._close_task
        if close_task is None:
            close_task = asyncio.create_task(
                self._aclose_owned(),
                name="world-v2-semantic-chat-close",
            )
            self._close_task = close_task
        await asyncio.shield(close_task)

    async def _aclose_owned(self) -> None:
        close_results = await asyncio.gather(
            *(
                close()
                for owner in (
                    *self._owned_closeables,
                    *self._owned_task_owners,
                )
                if callable(close := getattr(owner, "aclose", None))
            ),
            return_exceptions=True,
        )
        waiters = tuple(
            waiter()
            for owner in self._owned_task_owners
            if getattr(owner, "shutdown_pending_task_count", 0) > 0
            if callable(waiter := getattr(owner, "wait_for_shutdown_quiescence", None))
        )
        if waiters:
            deferred = asyncio.create_task(
                self._close_models_after_quiescence(waiters),
                name="world-v2-semantic-chat-deferred-model-close",
            )
            self._deferred_model_close_task = deferred
            deferred.add_done_callback(self._observe_deferred_model_close)
        else:
            await self._close_owned_models()
        for result in close_results:
            if isinstance(result, BaseException):
                raise result

    async def _close_models_after_quiescence(
        self,
        waiters: tuple[Awaitable[None], ...],
    ) -> None:
        await asyncio.gather(*waiters)
        await self._close_owned_models()

    async def _close_owned_models(self) -> None:
        if self._models_closed:
            return
        for model in self._owned_models:
            close = getattr(model, "aclose", None)
            if callable(close):
                await close()
        self._models_closed = True

    @staticmethod
    def _observe_deferred_model_close(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    @property
    def shutdown_pending_task_count(self) -> int:
        """Dependencies retained by advisory or reviewer tasks after bounded close."""

        deferred = self._deferred_model_close_task
        if deferred is None or deferred.done():
            return 0
        owner_count = sum(
            int(getattr(owner, "shutdown_pending_task_count", 0))
            for owner in self._owned_task_owners
        )
        return max(1, owner_count)

    async def wait_for_shutdown_quiescence(self) -> None:
        """Wait until reviewer leases end and their clients have closed."""

        close_task = self._close_task
        if close_task is not None:
            await asyncio.shield(close_task)
        deferred = self._deferred_model_close_task
        if deferred is not None:
            await asyncio.shield(deferred)


def build_semantic_chat_composition(
    *,
    settings: Settings,
    flash_model: ChatCompletionModel | None = None,
    thinking_model: ChatCompletionModel | None = None,
    world_support_model: ChatCompletionModel | None = None,
    source_closure_model: ChatCompletionModel | None = None,
    life_source_closure_model: ChatCompletionModel | None = None,
    expression_episode_observer_model: ChatCompletionModel | None = None,
    model_id_prefix: str,
    expression_capabilities: ExpressionDraftCapabilities = (
        PRODUCTION_TEXT_ONLY_EXPRESSION_CAPABILITIES
    ),
    usage_observer: object | None = None,
    character_interior_turn_store: _CharacterInteriorTurnStore | None = None,
    character_interior_turn_owner_id: str = "character-interior:production",
    test_only_provider_capture_authority_id: str | None = None,
    **_unused: object,
) -> SemanticChatComposition:
    """Build one explicitly supplied or provider-backed Character author.

    Explicitly supplied models are caller-owned.  With provider settings, the
    Module owns a Flash client and, when enabled, a separate bounded Thinking
    client. Invalid output may receive the same CharacterInterior author's one
    constrained reselection; terminal technical failure leaves through the
    normal retry lifecycle and cannot become a second semantic role path.
    """

    if not model_id_prefix:
        raise ValueError("semantic chat composition requires a model id prefix")
    if expression_capabilities.private_turn_state_mode != "required":
        raise ValueError(
            "production expression requires a final PrivateTurnState; "
            "legacy_optional is historical replay/test only"
        )
    if flash_model is None and not settings.deepseek_api_key:
        raise ValueError(
            "production CharacterInterior requires an explicit character model "
            "or DEEPSEEK_API_KEY; fixture prose cannot be installed implicitly"
        )
    if any(_uses_implicit_character_failover(model) for model in (flash_model, thinking_model)):
        raise ValueError(
            "CharacterInterior cannot install an implicit backup character author; "
            "provider failure must enter its durable technical-failure lifecycle"
        )
    test_only_provider_capture_authority_id = (
        _validated_test_only_provider_capture_authority(
            settings=settings,
            authority_id=test_only_provider_capture_authority_id,
        )
    )
    if test_only_provider_capture_authority_id is not None and (
        flash_model is not None or thinking_model is not None
    ):
        raise ValueError(
            "test-only provider capture authority cannot be combined with caller-supplied "
            "character models"
        )
    if (
        test_only_provider_capture_authority_id is not None
        and settings.deepseek_character_thinking_enabled
        and settings.deepseek_character_thinking_model != settings.deepseek_model
    ):
        raise ValueError(
            "test-only provider capture authority requires the thinking character route "
            "to use the configured DeepSeek checkpoint"
        )
    # Source review is an explicit deployment capability. It is a hard
    # boundary for visible World-bound expression facts; the redundant-route
    # switch must not silently turn that boundary off. An explicitly injected
    # reviewer still must be independent of every author.
    owned: list[object] = []
    owned_closeables: list[object] = []
    owned_task_owners: list[object] = []
    if character_interior_turn_store is not None:
        owned_closeables.append(character_interior_turn_store)

    auto_flash = flash_model is None
    if flash_model is None:
        if settings.deepseek_api_key:
            provider_flash = DeepSeekChatModel(
                api_key=settings.deepseek_api_key,
                base_url=settings.deepseek_base_url,
                model=settings.deepseek_model,
                thinking_enabled=False,
                max_completion_tokens=4_096,
                usage_observer=usage_observer,
            )
            flash_model = provider_flash
            owned.append(provider_flash)
        else:  # pragma: no cover - guarded above, kept for type narrowing
            raise RuntimeError("character author is unavailable")
    if (
        thinking_model is None
        and auto_flash
        and settings.deepseek_api_key
        and settings.deepseek_character_thinking_enabled
    ):
        provider_thinking = DeepSeekChatModel(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_character_thinking_model,
            thinking_enabled=True,
            reasoning_effort=settings.deepseek_character_thinking_reasoning_effort,
            usage_observer=usage_observer,
        )
        thinking_model = provider_thinking
        owned.append(provider_thinking)
    _apply_test_only_provider_capture_authority(
        flash_model,
        test_only_provider_capture_authority_id,
        settings=settings,
    )
    _apply_test_only_provider_capture_authority(
        thinking_model,
        test_only_provider_capture_authority_id,
        settings=settings,
    )

    local_endpoint_model: ChatCompletionModel | None = None
    local_provider_capacity: ProviderCapacityGate | None = None
    if settings.world_v2_text_endpoint_enabled:
        # The endpoint deployment is a serial inference worker. Its sole role
        # is turn-boundary likelihood; it never enters CharacterInterior or
        # compiles Appraisal/Affect/Relationship semantics.
        local_provider_capacity = ProviderCapacityGate(
            marker_path=text_endpoint_capacity_marker_path(),
        )
        local_endpoint_model = OpenAICompatibleChatModel(
            api_key=settings.world_v2_text_endpoint_api_key,
            base_url=settings.world_v2_text_endpoint_base_url,
            model=settings.world_v2_text_endpoint_model,
            reasoning_effort="none",
            max_completion_tokens=96,
            capacity_gate=local_provider_capacity,
        )
        owned.append(local_endpoint_model)

    character = load_character(str(settings.character_path))
    aliases_raw = character.identity.get("nicknames", ())
    aliases = (
        tuple(str(item) for item in aliases_raw if str(item).strip())
        if isinstance(aliases_raw, list)
        else ()
    )
    identity_frame = CompanionIdentityFrame(
        companion_name=character.name,
        companion_aliases=aliases,
        counterpart_name=settings.primary_user_id,
        stable_identity_facts=tuple(character.canonical_facts),
        shared_history_facts=tuple(character.shared_history_facts),
        counterpart_history_facts=tuple(character.counterpart_history_facts),
        personality_frame=character.personality,
        values=tuple(character.values),
        speech_frame=character.speech,
        style_rules=tuple(character.style_rules),
        boundaries=tuple(character.boundaries),
        base_prompt=character.base_prompt,
        appearance=character.appearance,
        background=character.background,
        daily_life=tuple(character.daily_life),
        first_message=character.first_message,
    )
    del source_closure_model, life_source_closure_model, _unused
    background_model = world_support_model
    if (
        background_model is None
        and auto_flash
        and settings.deepseek_api_key
        and isinstance(flash_model, DeepSeekChatModel)
    ):
        background_model = DeepSeekChatModel(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            model=settings.deepseek_model,
            thinking_enabled=False,
            max_completion_tokens=4_096,
            usage_observer=usage_observer,
        )
        _apply_test_only_provider_capture_authority(
            background_model,
            test_only_provider_capture_authority_id,
            settings=settings,
        )
        owned.append(background_model)
    if background_model is None:
        background_model = flash_model
    proactive_source_authority = ProactiveSourceAuthorityDeployment(
        status="fact_effects_fail_closed",
        author_model=_model_identity(getattr(flash_model, "primary", flash_model)) or "unknown",
        reviewer_model=None,
        candidate_inventory_model=None,
        warning_reasons=("one_shot.model_review_lanes_removed",),
    )
    character_interior = compose_production_character_interior(
        flash_model=flash_model,
        thinking_model=thinking_model,
        source_closure_model=None,
        report_relative_source_closure_model=None,
        source_closure_reselection_lane=None,
        expression_episode_observer_model=expression_episode_observer_model,
        flash_model_id=str(getattr(flash_model, "model", f"{model_id_prefix}-flash")),
        thinking_model_id=(
            str(getattr(thinking_model, "model", f"{model_id_prefix}-thinking"))
            if thinking_model is not None
            else None
        ),
        expression_capabilities=expression_capabilities,
        identity_frame=identity_frame,
        review_claim_free_candidates=False,
        turn_store=character_interior_turn_store,
        turn_owner_id=character_interior_turn_owner_id,
    )
    return SemanticChatComposition(
        world_support_model=background_model,
        character_author_model_id=(
            _model_identity(getattr(flash_model, "primary", flash_model)) or "unknown"
        ),
        expression_episode_observer_model=expression_episode_observer_model,
        source_closure_model=None,
        recovery_source_closure_model=None,
        source_closure_reselection_lane=None,
        proactive_source_closure_model=None,
        life_source_closure_model=None,
        life_source_runtime_isolation="unavailable",
        known_source_inventory=None,
        proactive_source_authority=proactive_source_authority,
        character_interior=character_interior,
        router=SemanticComputeRouter(thinking_available=thinking_model is not None),
        identity_frame=identity_frame,
        local_provider_capacity=local_provider_capacity,
        text_endpoint_controller=(
            TextTurnEndpointController(
                model=ChatSemanticEndpointModel(local_endpoint_model),
                timeout_seconds=settings.world_v2_text_endpoint_timeout_seconds,
            )
            if local_endpoint_model is not None
            else None
        ),
        _owned_models=tuple(owned),
        _owned_closeables=tuple(owned_closeables),
        _owned_task_owners=tuple(owned_task_owners),
    )




__all__ = [
    "ProactiveSourceAuthorityDeployment",
    "SemanticChatComposition",
    "build_semantic_chat_composition",
    "unavailable_life_source_authority_health",
]
