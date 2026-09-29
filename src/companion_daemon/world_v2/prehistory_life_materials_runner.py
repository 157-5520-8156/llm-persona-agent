"""Provider runner for source-bound prehistory extraction."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import ValidationError

from .character_prehistory import PrehistoryArchiveDocument, digest
from .prehistory_authoring import PrehistoryAuthoringBrief, PrehistoryCreationReview
from .prehistory_life_materials import (
    LifeMaterialsDraft, export_actor_archive,
    faithful_revision_request, ingestion_request, normalize_ingested_material_metadata,
    validate_faithful_revision, validate_ingested_materials,
)
from .prehistory_story_links import (
    PrehistoryStoryLinkArtifact,
    PrehistoryStoryLinkDraft,
    PrehistoryStoryLinkResponse,
    apply_story_links,
    normalize_story_link_response,
    story_link_request,
    validate_story_links,
)

MAX_PREHISTORY_OUTPUT_TOKENS = 32_768
MAX_STORY_LINK_OUTPUT_TOKENS = 8_192
PREHISTORY_PROVIDER_TIMEOUT_SECONDS = 180.0


class ProviderOutputTruncated(RuntimeError):
    """A complete API response reached its declared max-token boundary."""


def _response_finish_reason(response: dict[str, Any]) -> str | None:
    choices = response.get("choices") if isinstance(response, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    reason = choices[0].get("finish_reason")
    return reason if isinstance(reason, str) else None


def _captured_finish_reason(attempt_dir: Path) -> str | None:
    captured = attempt_dir / "provider-response.json"
    if not captured.exists():
        return None
    try:
        wrapper = json.loads(captured.read_text(encoding="utf-8"))
        body = wrapper.get("body_utf8") if isinstance(wrapper, dict) else None
        response = json.loads(body) if isinstance(body, str) else None
        return _response_finish_reason(response) if isinstance(response, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _record_usage_audit(manifest: dict[str, Any], saved: dict[str, Any], attempt_no: int) -> None:
    """Keep each captured attempt's usage even when parsing/extraction fails."""
    audit = saved.get("usage_audit")
    if not isinstance(audit, dict):
        audit = provider_usage_audit(
            str(manifest.get("model", "deepseek-flash")),
            saved.get("usage") if isinstance(saved.get("usage"), dict) else {},
            saved.get("usage_observer") if isinstance(saved.get("usage_observer"), dict) else None,
        )
        saved["usage_audit"] = audit
    rows = [row for row in manifest.get("usage_audit", [])
            if not isinstance(row, dict) or row.get("attempt") != attempt_no]
    rows.append({**audit, "attempt": attempt_no})
    manifest["usage_audit"] = rows


def _billing_state(manifest: dict[str, Any], run_dir: Path, last_attempt: int) -> str:
    audits = manifest.get("usage_audit", [])
    requests_emitted = any(
        (run_dir / f"attempt-{attempt:02d}" / "provider-request.json").is_file()
        for attempt in range(1, last_attempt + 1)
    )
    if not audits:
        return "unknown_if_request_was_emitted" if requests_emitted else "no_provider_request_emitted"
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("cost"), dict)
        or item["cost"].get("billing_state") != "estimated_from_provider_tokens"
        for item in audits
    ):
        return "unknown_if_request_was_emitted" if requests_emitted else "no_provider_request_emitted"
    return "estimated_from_provider_tokens"


def _preserve_pre_request_failure(attempt_dir: Path) -> None:
    """Keep a prior local failure while safely reusing an unissued attempt slot."""
    failure = attempt_dir / "failure.json"
    if not failure.exists() or (attempt_dir / "provider-request.json").exists():
        return
    destination = attempt_dir / "pre-request-failure.json"
    ordinal = 2
    while destination.exists():
        destination = attempt_dir / f"pre-request-failure-{ordinal:02d}.json"
        ordinal += 1
    os.replace(failure, destination)


def _private_write(path: Path, payload: bytes, *, exclusive: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC), 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _private_json(path: Path, value: object, *, exclusive: bool = True) -> None:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode("utf-8") + b"\n"
    _private_write(path, data, exclusive=exclusive)


class _AttemptCaptureTransport:
    """Capture request/response bodies while hiding authorization headers."""

    def __init__(self, inner, attempt_dir: Path):
        self.inner = inner
        self.attempt_dir = attempt_dir

    async def __aenter__(self):
        enter = getattr(self.inner, "__aenter__", None)
        if callable(enter):
            await enter()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        del exc_type, exc_value, traceback
        await self.aclose()

    async def handle_async_request(self, request):
        request_body = request.content
        safe_headers = {key.lower(): value for key, value in request.headers.items() if key.lower() != "authorization"}
        parsed_url = urlsplit(str(request.url))
        safe_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, "", ""))
        _private_json(self.attempt_dir / "provider-request.json", {
            "method": request.method, "url": safe_url, "headers": safe_headers,
            "body_utf8": request_body.decode("utf-8", errors="replace"),
            "body_sha256": hashlib.sha256(request_body).hexdigest(),
        })
        response = await self.inner.handle_async_request(request)
        response_body = await response.aread()
        _private_json(self.attempt_dir / "provider-response.json", {
            "status_code": response.status_code,
            "headers": {key.lower(): value for key, value in response.headers.items()
                        if key.lower() in {"content-type", "date", "x-request-id", "retry-after"}},
            "body_utf8": response_body.decode("utf-8", errors="replace"),
            "body_sha256": hashlib.sha256(response_body).hexdigest(),
        })
        headers = {key: value for key, value in response.headers.items()
                   if key.lower() not in {"content-length", "content-encoding", "transfer-encoding"}}
        return type(response)(
            status_code=response.status_code, headers=headers, content=response_body,
            extensions=response.extensions, request=request,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


def _response_content(raw_response: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract a normal Chat Completions JSON result from captured wire bytes."""
    body = raw_response.get("body_utf8")
    if not isinstance(body, str):
        raise ValueError("captured provider response has no body")
    payload = json.loads(body)
    choices = payload.get("choices") if isinstance(payload, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content:
        raise ValueError("provider response has no JSON content")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    return content, usage


def provider_usage_audit(model_name: str, usage: dict[str, Any], observer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Keep provider token usage separate from a local price estimate."""
    from companion_daemon.usage_metrics import estimate_model_cost_usd

    observer = observer or {}
    prompt = int(usage.get("prompt_tokens", usage.get("input_tokens", observer.get("prompt_tokens", 0))) or 0)
    completion_tokens = int(usage.get("completion_tokens", usage.get("output_tokens", observer.get("completion_tokens", 0))) or 0)
    cache_hit = int(usage.get("prompt_cache_hit_tokens", usage.get("cache_hit_tokens", observer.get("cache_hit_tokens", 0))) or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens", usage.get("cache_miss_tokens", observer.get("cache_miss_tokens", 0))) or 0)
    reasoning_tokens = int(usage.get("reasoning_tokens", observer.get("reasoning_tokens", 0)) or 0)
    if prompt <= 0 or completion_tokens <= 0:
        return {"provider_usage": usage, "usage_observer": observer or None,
                "cost": {"estimated_cost_usd": None, "estimated_cost_cny": None,
                         "pricing_version": None, "billing_state": "unknown",
                         "reason": "provider_token_usage_missing_or_incomplete"}}
    try:
        usd, pricing_version = estimate_model_cost_usd(
            model=model_name, prompt_tokens=prompt, completion_tokens=completion_tokens,
            cache_hit_tokens=cache_hit, cache_miss_tokens=cache_miss,
            reasoning_tokens=reasoning_tokens,
        )
        estimated_cost = {"estimated_cost_usd": usd, "estimated_cost_cny": round(usd * 7.2, 8),
                          "pricing_version": pricing_version,
                          "billing_state": "estimated_from_provider_tokens"}
    except (KeyError, ValueError):
        estimated_cost = {"estimated_cost_usd": None, "estimated_cost_cny": None,
                          "pricing_version": None, "billing_state": "unknown"}
    return {"provider_usage": usage, "usage_observer": observer or None, "cost": estimated_cost}


async def _captured_completion(
    *, api_key: str, base_url: str, model_name: str, messages: list[dict[str, str]], attempt_dir: Path,
    usage_observer=None, max_completion_tokens: int = MAX_PREHISTORY_OUTPUT_TOKENS,
) -> tuple[str, dict[str, Any]]:
    """One call through the existing DeepSeek adapter with durable wire capture."""
    import httpx
    from companion_daemon.llm import DeepSeekChatModel

    attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(attempt_dir, 0o700)
    transport = _AttemptCaptureTransport(httpx.AsyncHTTPTransport(retries=0), attempt_dir)
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(PREHISTORY_PROVIDER_TIMEOUT_SECONDS, connect=15.0),
        trust_env=False,
        transport=transport,
    )
    model = DeepSeekChatModel(api_key=api_key, base_url=base_url, model=model_name,
                              thinking_enabled=False, max_completion_tokens=max_completion_tokens,
                              usage_observer=usage_observer, transport=transport, client=client)
    previous_debug_ledger_flag = os.environ.get("COMPANION_DISABLE_DEBUG_USAGE_LEDGER")
    os.environ["COMPANION_DISABLE_DEBUG_USAGE_LEDGER"] = "1"
    try:
        content, usage = await model.complete_json_with_usage(messages, temperature=0.1)
        return content, usage
    finally:
        if previous_debug_ledger_flag is None:
            os.environ.pop("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", None)
        else:
            os.environ["COMPANION_DISABLE_DEBUG_USAGE_LEDGER"] = previous_debug_ledger_flag
        await model.client.aclose()


async def run_faithful_ingestion(
    *, brief: PrehistoryAuthoringBrief, narrative_text: str, actor_ref: str, archive_id: str,
    run_dir: Path, api_key: str, base_url: str = "https://api.deepseek.com", model_name: str = "deepseek-flash",
    completion=None, max_repairs: int = 1,
    previous_materials: LifeMaterialsDraft | None = None,
    previous_archive: PrehistoryArchiveDocument | None = None,
    revision_review: PrehistoryCreationReview | None = None,
) -> tuple[LifeMaterialsDraft, PrehistoryArchiveDocument, dict[str, Any]]:
    """Call the author model, capture each attempt, validate, and export.

    Transport failures are never retried automatically because billing may be
    uncertain. One same-model repair is allowed only after a completed response
    fails local structural or source binding checks. A saved completed response
    can be parsed again without calling the provider.
    """
    from .character_prehistory import digest as canonical_digest
    from .prehistory_authoring import PrehistoryAuthoringBrief

    brief = PrehistoryAuthoringBrief.model_validate_json(brief.model_dump_json())
    if not 0 <= max_repairs <= 1:
        raise ValueError("at most one constrained repair is supported")
    run_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(run_dir, 0o700)
    revision_args = (previous_materials, previous_archive, revision_review)
    if any(value is not None for value in revision_args) and not all(value is not None for value in revision_args):
        raise ValueError("faithful revision requires previous materials, archive and a bound rejected review together")
    if revision_review is None:
        packet = ingestion_request(brief, narrative_text)
    else:
        packet = faithful_revision_request(
            brief, narrative_text, actor_ref=actor_ref, archive_id=archive_id,
            previous_materials=previous_materials, previous_archive=previous_archive,
            review=revision_review,
        )
    source_hash = hashlib.sha256(narrative_text.encode("utf-8")).hexdigest()
    manifest_path = run_dir / "manifest.json"
    expected_manifest = {
        "contract": "prehistory-life-materials-run.1", "source_sha256": source_hash,
        "brief_hash": digest(brief), "model": model_name, "mode": packet["mode"],
        "request_contract": packet["contract"], "actor_ref": actor_ref,
        "archive_id": archive_id, "base_url": base_url.rstrip("/"), "status": "started",
    }
    if manifest_path.exists():
        current = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("contract", "source_sha256", "brief_hash", "model", "mode", "request_contract",
                    "actor_ref", "archive_id", "base_url"):
            if current.get(key) != expected_manifest[key]:
                raise ValueError("existing run directory is bound to different source, brief or model")
        if current.get("status") == "completed":
            materials = LifeMaterialsDraft.model_validate_json((run_dir / "materials.json").read_text(encoding="utf-8"))
            if revision_review is not None:
                archive = validate_faithful_revision(
                    brief, narrative_text, actor_ref=actor_ref, archive_id=archive_id,
                    previous_materials=previous_materials, previous_archive=previous_archive,
                    review=revision_review, revised_materials=materials,
                )
            else:
                validate_ingested_materials(brief, narrative_text, materials)
                archive = export_actor_archive(materials, actor_ref, archive_id)
            return materials, archive, current
        if current.get("status") == "output_truncated":
            raise RuntimeError("previous ingestion output was truncated; inspect captured evidence instead of retrying")
        if current.get("status") == "provider_or_runner_failed":
            last_attempt = int(current.get("attempts", 1))
            if (run_dir / f"attempt-{last_attempt:02d}" / "provider-request.json").exists():
                raise RuntimeError("previous ingestion request may have been billed; inspect captured evidence instead of retrying")
        manifest = current
    else:
        if any(run_dir.iterdir()):
            raise ValueError("run directory is non-empty but has no ingestion manifest")
        manifest = expected_manifest
        _private_json(manifest_path, manifest)
        _private_json(run_dir / "ingestion-request.json", packet)

    usage_rows: list[dict[str, Any]] = []
    def observe(usage):
        usage_rows.append({
            "provider": getattr(usage, "provider", "deepseek"),
            "model": getattr(usage, "model", model_name),
            "purpose": getattr(usage, "purpose", ""),
            "status": getattr(usage, "status", "unknown"),
            "latency_ms": getattr(usage, "latency_ms", None),
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "reasoning_tokens": getattr(usage, "reasoning_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "cache_hit_tokens": getattr(usage, "cache_hit_tokens", 0),
            "cache_miss_tokens": getattr(usage, "cache_miss_tokens", 0),
            "cost_cny": getattr(usage, "cost_cny", None),
            "error_type": type(getattr(usage, "error", None)).__name__ if getattr(usage, "error", None) else None,
            "billing_state": getattr(usage, "billing_state", "unknown"),
        })

    prior = []
    for attempt_no in (1, 2):
        attempt_dir = run_dir / f"attempt-{attempt_no:02d}"
        raw_response_path = attempt_dir / "provider-response.json"
        attempt_result_path = attempt_dir / "attempt-result.json"
        try:
            if attempt_result_path.exists():
                saved = json.loads(attempt_result_path.read_text(encoding="utf-8"))
                if "finish_reason" not in saved:
                    saved["finish_reason"] = _captured_finish_reason(attempt_dir)
                if saved.get("status") in {"response_captured", "validation_failed"}:
                    raw = saved["response_content"]
                    usage = saved.get("usage") or {}
                    if saved.get("status") == "validation_failed":
                        if not saved.get("usage_audit"):
                            saved["usage_audit"] = provider_usage_audit(
                                model_name, usage,
                                saved.get("usage_observer") if isinstance(saved.get("usage_observer"), dict) else None,
                            )
                        _record_usage_audit(manifest, saved, attempt_no)
                        _private_json(attempt_result_path, saved, exclusive=False)
                        manifest.update(status="repair_pending", attempts=attempt_no,
                                        billing_state=_billing_state(manifest, run_dir, attempt_no))
                        _private_json(manifest_path, manifest, exclusive=False)
                        prior.append({"attempt": attempt_no,
                                      "validation_errors": saved.get("validation_errors", []),
                                      "response_content": raw, "usage": usage})
                        continue
                else:
                    raise RuntimeError("previous provider attempt ended in failure; inspect its captured evidence")
            elif raw_response_path.exists():
                response_obj = json.loads(raw_response_path.read_text(encoding="utf-8"))
                raw, usage = _response_content(response_obj)
                saved = {"status": "response_captured", "response_content": raw, "usage": usage,
                         "finish_reason": _response_finish_reason(response_obj),
                         "resumed_from_wire_capture": True}
                _private_json(attempt_result_path, saved)
            else:
                if (attempt_dir / "provider-request.json").exists():
                    raise RuntimeError("provider request was emitted without a captured response; billing is unknown and automatic retry is disabled")
                attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                _preserve_pre_request_failure(attempt_dir)
                user_payload = {
                    "contract": packet["contract"], "mode": packet["mode"],
                    "source": packet["source"], "brief_hash": packet["brief_hash"],
                    "protagonist_binding": packet["protagonist_binding"],
                }
                if "prior_candidate" in packet:
                    user_payload["prior_candidate"] = packet["prior_candidate"]
                    user_payload["revision_feedback"] = packet["revision_feedback"]
                if attempt_no > 1:
                    user_payload.update(
                        repair_errors=prior[-1].get("validation_errors"),
                        previous_response=prior[-1].get("response_content"),
                    )
                messages = [
                    {"role": "system", "content": json.dumps({"instruction": packet["instruction"],
                     "brief": brief.model_dump(mode="json"),
                     "protagonist_binding": packet["protagonist_binding"],
                     "output_schema": LifeMaterialsDraft.model_json_schema()},
                     ensure_ascii=False)},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ]
                messages_path = attempt_dir / "submitted-messages.json"
                if messages_path.exists():
                    if json.loads(messages_path.read_text(encoding="utf-8")) != messages:
                        raise ValueError("saved attempt messages differ from the resumed request")
                else:
                    _private_json(messages_path, messages)
                if completion is None:
                    raw, usage = await _captured_completion(
                        api_key=api_key, base_url=base_url, model_name=model_name,
                        messages=messages, attempt_dir=attempt_dir, usage_observer=observe,
                    )
                else:
                    raw, usage = await completion(messages=messages, attempt_dir=attempt_dir, model_name=model_name)
                saved = {"status": "response_captured", "response_content": raw, "usage": usage,
                         "finish_reason": _captured_finish_reason(attempt_dir),
                         "usage_observer": usage_rows[-1] if usage_rows else None,
                         "usage_audit": provider_usage_audit(model_name, usage, usage_rows[-1] if usage_rows else None)}
                _private_json(attempt_result_path, saved)

            if not saved.get("usage_audit"):
                saved["usage_audit"] = provider_usage_audit(model_name, usage)
            _record_usage_audit(manifest, saved, attempt_no)
            _private_json(attempt_result_path, saved, exclusive=False)
            if saved.get("finish_reason") == "length":
                saved.update(status="output_truncated")
                _private_json(attempt_result_path, saved, exclusive=False)
                manifest.update(status="output_truncated", attempts=attempt_no,
                                failure_type="ProviderOutputTruncated",
                                failure_code="prehistory.provider_output_limit_reached",
                                billing_state=_billing_state(manifest, run_dir, attempt_no))
                _private_json(manifest_path, manifest, exclusive=False)
                raise ProviderOutputTruncated(
                    "provider response reached max_tokens; captured output is incomplete and will not be retried"
                )

            try:
                parsed, time_normalizations = normalize_ingested_material_metadata(raw, brief)
                materials = validate_ingested_materials(brief, narrative_text, parsed)
                if materials.source_artifact_ref != f"narrative#sha256={source_hash}":
                    raise ValueError("ingestion output must use the canonical narrative#sha256 source reference")
                if not any(actor.actor_ref == actor_ref for actor in materials.actors):
                    raise ValueError("ingestion output omitted the requested archive actor")
                if revision_review is not None:
                    archive = validate_faithful_revision(
                        brief, narrative_text, actor_ref=actor_ref, archive_id=archive_id,
                        previous_materials=previous_materials, previous_archive=previous_archive,
                        review=revision_review, revised_materials=materials,
                    )
                else:
                    archive = export_actor_archive(materials, actor_ref, archive_id)
                from .prehistory_authoring import validate_draft
                review_brief = brief.model_copy(update={"draft_source_ref": archive.source_artifact_ref})
                validate_draft(review_brief, archive)
                materials_path = run_dir / "materials.json"
                if not materials_path.exists():
                    _private_json(materials_path, json.loads(materials.model_dump_json()))
                saved["time_normalizations"] = list(time_normalizations)
                _private_json(attempt_result_path, saved, exclusive=False)
                manifest.update(status="completed", materials_sha256=canonical_digest(materials),
                                archive_sha256=canonical_digest(archive), attempts=attempt_no,
                                time_normalizations=list(time_normalizations),
                                billing_state=_billing_state(manifest, run_dir, attempt_no))
                manifest.pop("failure_type", None)
                manifest.pop("failure_code", None)
                manifest.pop("validation_errors", None)
                _private_json(manifest_path, manifest, exclusive=False)
                return materials, archive, manifest
            except (ValidationError, ValueError) as exc:
                details = (
                    exc.errors(include_input=False, include_context=False)
                    if isinstance(exc, ValidationError) else [str(exc)]
                )
                prior.append({"attempt": attempt_no, "validation_errors": details, "response_content": raw,
                              "usage": usage})
                if attempt_no > max_repairs:
                    raise
                _private_json(attempt_result_path, {**saved, "status": "validation_failed",
                             "validation_errors": details}, exclusive=False)
                _record_usage_audit(manifest, saved, attempt_no)
                manifest.update(status="repair_pending", attempts=attempt_no,
                                failure_type=type(exc).__name__, validation_errors=details,
                                billing_state=_billing_state(manifest, run_dir, attempt_no))
                _private_json(manifest_path, manifest, exclusive=False)
        except Exception as exc:
            if isinstance(exc, ProviderOutputTruncated):
                failed_status = "output_truncated"
            elif isinstance(exc, (ValidationError, ValueError)):
                failed_status = "validation_rejected"
            else:
                failed_status = "provider_or_runner_failed"
            if attempt_result_path.exists():
                try:
                    last_saved = json.loads(attempt_result_path.read_text(encoding="utf-8"))
                    if isinstance(last_saved, dict) and last_saved.get("usage"):
                        _record_usage_audit(manifest, last_saved, attempt_no)
                except (OSError, json.JSONDecodeError):
                    pass
            already_terminal = manifest.get("status") in {
                "provider_or_runner_failed", "output_truncated",
            }
            if not already_terminal:
                manifest.update(status=failed_status, attempts=attempt_no,
                                failure_type=type(exc).__name__,
                                billing_state=_billing_state(manifest, run_dir, attempt_no))
                _private_json(manifest_path, manifest, exclusive=False)
            failure_path = attempt_dir / "failure.json"
            if not failure_path.exists():
                safe_message = str(exc).replace(api_key, "[redacted]")[:2000]
                observer = usage_rows[-1] if usage_rows else None
                _private_json(failure_path, {"type": type(exc).__name__, "message": safe_message,
                                             "usage_audit": provider_usage_audit(model_name, {}, observer or {})})
            raise
    raise AssertionError("unreachable ingestion state")


async def run_story_linking(
    *,
    materials: LifeMaterialsDraft,
    actor_ref: str,
    narrative_text: str,
    run_dir: Path,
    api_key: str,
    reviewed_link_findings: tuple[str, ...] = (),
    base_url: str = "https://api.deepseek.com",
    model_name: str = "deepseek-flash",
    completion=None,
) -> tuple[LifeMaterialsDraft, dict[str, Any]]:
    """Author actor-scoped story links as a separate bounded metadata call.

    The response can only connect existing active-view record IDs and source
    blocks. It cannot write a new event, statement or privacy permission. A
    missing response after an emitted request is unknown-billed and is never
    retried. One complete but structurally invalid response may receive a
    captured, same-model correction with exact validation errors.
    """
    from .character_prehistory import digest as canonical_digest

    materials = LifeMaterialsDraft.model_validate_json(materials.model_dump_json())
    request = story_link_request(
        materials, actor_ref, narrative_text=narrative_text,
        reviewed_link_findings=reviewed_link_findings,
    )
    source_materials_hash = canonical_digest(materials)
    request_hash = hashlib.sha256(json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    link_dir = run_dir / "story-linking"
    link_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(link_dir, 0o700)
    manifest_path = link_dir / "manifest.json"
    expected = {
        "contract": "prehistory-story-link-run.1",
        "actor_ref": actor_ref,
        "source_materials_hash": source_materials_hash,
        "request_hash": request_hash,
        "model": model_name,
        "status": "started",
    }
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("contract", "actor_ref", "source_materials_hash", "model"):
            if manifest.get(key) != expected[key]:
                raise ValueError("story-link run directory is bound to different actor, source or model")
        if manifest.get("status") == "completed":
            if manifest.get("request_hash") != expected["request_hash"]:
                raise ValueError("completed story-link run is bound to different review findings or prompt")
            saved = json.loads((link_dir / "story-links.json").read_text(encoding="utf-8"))
            draft = PrehistoryStoryLinkDraft.model_validate_json(
                json.dumps(saved["draft"], ensure_ascii=False),
            )
            return apply_story_links(materials, actor_ref=actor_ref, draft=draft,
                                     narrative_text=narrative_text), manifest
        if manifest.get("status") == "output_truncated":
            raise RuntimeError("previous story-link result was truncated; inspect its captured evidence")
    else:
        if any(link_dir.iterdir()):
            raise ValueError("story-link run directory is non-empty but has no manifest")
        manifest = expected
        _private_json(manifest_path, manifest)

    request_path = link_dir / "story-link-request.json"
    if not request_path.exists():
        _private_json(request_path, request)
    saved_request = json.loads(request_path.read_text(encoding="utf-8"))
    saved_request_hash = hashlib.sha256(json.dumps(
        saved_request, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if saved_request_hash != manifest.get("request_hash", expected["request_hash"]):
        raise ValueError("saved story-link request does not match its manifest hash")
    prompt_matches_current_code = saved_request_hash == expected["request_hash"]
    captured_responses = any(
        (link_dir / f"attempt-{number:02d}" / "attempt-result.json").is_file()
        or (link_dir / f"attempt-{number:02d}" / "provider-response.json").is_file()
        for number in (1, 2)
    )
    if not prompt_matches_current_code and not captured_responses:
        raise ValueError("story-link prompt changed without a captured response; use a new run directory")
    request_for_call = saved_request
    manifest.setdefault("attempt_usage_audit", [])
    attempt_dir = None
    try:
        previous_response = None
        previous_errors = manifest.get("validation_errors", [])
        for attempt_no in (1, 2):
            attempt_dir = link_dir / f"attempt-{attempt_no:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(attempt_dir, 0o700)
            request_wire = attempt_dir / "provider-request.json"
            response_wire = attempt_dir / "provider-response.json"
            result_path = attempt_dir / "attempt-result.json"
            observer_rows: list[dict[str, Any]] = []

            def observe(usage):
                observer_rows.append({
                    "provider": getattr(usage, "provider", "deepseek"),
                    "model": getattr(usage, "model", model_name),
                    "status": getattr(usage, "status", "unknown"),
                    "latency_ms": getattr(usage, "latency_ms", None),
                    "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage, "completion_tokens", 0),
                    "cache_hit_tokens": getattr(usage, "cache_hit_tokens", 0),
                    "cache_miss_tokens": getattr(usage, "cache_miss_tokens", 0),
                    "billing_state": getattr(usage, "billing_state", "unknown"),
                })

            if result_path.exists():
                saved = json.loads(result_path.read_text(encoding="utf-8"))
                raw = saved["response_content"]
                usage = saved.get("usage") or {}
                finish_reason = saved.get("finish_reason") or _captured_finish_reason(attempt_dir)
            elif response_wire.exists():
                response_obj = json.loads(response_wire.read_text(encoding="utf-8"))
                raw, usage = _response_content(response_obj)
                finish_reason = _response_finish_reason(json.loads(response_obj["body_utf8"]))
                saved = {"status": "response_captured", "response_content": raw,
                         "usage": usage, "finish_reason": finish_reason}
                _private_json(result_path, saved)
            else:
                if request_wire.exists():
                    raise RuntimeError(
                        f"story-link attempt {attempt_no} request was emitted without response capture; retry is disabled"
                    )
                if attempt_no == 1:
                    messages = [
                        {"role": "system", "content": json.dumps({
                            "instruction": request_for_call["instruction"],
                            "output_schema": request_for_call["output_schema"],
                        }, ensure_ascii=False)},
                        {"role": "user", "content": json.dumps(request_for_call, ensure_ascii=False)},
                    ]
                else:
                    if not prompt_matches_current_code:
                        raise ValueError("cannot issue a correction under a changed story-link prompt; use a new run directory")
                    if previous_response is None or not previous_errors:
                        raise RuntimeError("story-link repair lacks a complete prior response and exact validation errors")
                    repair_instruction = (
                        request_for_call["instruction"]
                        + "上一份完整JSON因以下确定性校验错误未被接受："
                        + json.dumps(previous_errors, ensure_ascii=False)
                        + "。请只修正这些错误，继续使用同一原文、actor、事件ID和证据范围；"
                        + "删除确实无法满足约束的关系，不要改写经历或扩大权限；返回完整且符合schema的JSON。"
                    )
                    messages = [
                        {"role": "system", "content": json.dumps({
                            "instruction": repair_instruction,
                            "output_schema": request_for_call["output_schema"],
                        }, ensure_ascii=False)},
                        {"role": "user", "content": json.dumps({
                            "request": request_for_call,
                            "validation_errors": previous_errors,
                            "previous_complete_response": previous_response,
                        }, ensure_ascii=False)},
                    ]
                messages_path = attempt_dir / "submitted-messages.json"
                if messages_path.exists():
                    if json.loads(messages_path.read_text(encoding="utf-8")) != messages:
                        raise ValueError("saved story-link messages differ from the current attempt")
                else:
                    _private_json(messages_path, messages)
                if completion is None:
                    raw, usage = await _captured_completion(
                        api_key=api_key, base_url=base_url, model_name=model_name,
                        messages=messages, attempt_dir=attempt_dir, usage_observer=observe,
                        max_completion_tokens=MAX_STORY_LINK_OUTPUT_TOKENS,
                    )
                    finish_reason = _captured_finish_reason(attempt_dir)
                else:
                    raw, usage = await completion(messages=messages, attempt_dir=attempt_dir,
                                                  model_name=model_name)
                    finish_reason = _captured_finish_reason(attempt_dir)
                saved = {
                    "status": "response_captured", "response_content": raw,
                    "usage": usage, "finish_reason": finish_reason,
                    "usage_observer": observer_rows[-1] if observer_rows else None,
                    "usage_audit": provider_usage_audit(
                        model_name, usage, observer_rows[-1] if observer_rows else None,
                    ),
                }
                _private_json(result_path, saved)

            if finish_reason == "length":
                raise ProviderOutputTruncated("story-link response reached max_tokens; captured output is incomplete")
            if not saved.get("usage_audit"):
                saved["usage_audit"] = provider_usage_audit(
                    model_name, usage,
                    saved.get("usage_observer") if isinstance(saved.get("usage_observer"), dict) else None,
                )
            audit = saved["usage_audit"]
            usage_rows = [row for row in manifest["attempt_usage_audit"]
                          if not isinstance(row, dict) or row.get("attempt") != attempt_no]
            usage_rows.append({"attempt": attempt_no, **audit})
            manifest["attempt_usage_audit"] = usage_rows
            _private_json(result_path, saved, exclusive=False)

            try:
                response = PrehistoryStoryLinkResponse.model_validate_json(raw)
                response, dropped_duplicate_relations = normalize_story_link_response(response)
                draft = PrehistoryStoryLinkDraft(
                    actor_ref=actor_ref,
                    source_materials_hash=source_materials_hash,
                    relations=response.relations,
                )
                draft = validate_story_links(
                    materials, actor_ref=actor_ref, draft=draft, narrative_text=narrative_text,
                )
            except (ValidationError, ValueError) as exc:
                errors = (
                    exc.errors(include_input=False, include_context=False)
                    if isinstance(exc, ValidationError) else [str(exc)]
                )
                saved.update(status="validation_failed", validation_errors=errors)
                _private_json(result_path, saved, exclusive=False)
                manifest.update(
                    status="repair_pending" if attempt_no == 1 else "validation_rejected",
                    attempts=attempt_no, failure_type=type(exc).__name__, validation_errors=errors,
                    usage_audit=audit,
                    billing_state=audit.get("cost", {}).get("billing_state", "unknown"),
                )
                _private_json(manifest_path, manifest, exclusive=False)
                if attempt_no == 2:
                    raise
                previous_response, previous_errors = raw, errors
                continue

            linked_materials = apply_story_links(
                materials, actor_ref=actor_ref, draft=draft, narrative_text=narrative_text,
            )
            link_artifact = PrehistoryStoryLinkArtifact(
                request_hash=manifest.get("request_hash", expected["request_hash"]),
                source_artifact_ref=materials.source_artifact_ref,
                source_materials_hash=source_materials_hash,
                draft=draft,
                linked_materials_hash=canonical_digest(linked_materials),
                dropped_duplicate_relation_count=dropped_duplicate_relations,
            )
            link_artifact_value = json.loads(link_artifact.model_dump_json())
            link_artifact_path = link_dir / "story-links.json"
            if link_artifact_path.exists():
                if json.loads(link_artifact_path.read_text(encoding="utf-8")) != link_artifact_value:
                    raise ValueError("existing story-link artifact differs from the captured response")
            else:
                _private_json(link_artifact_path, link_artifact_value)
            manifest.update(
                status="completed", attempts=attempt_no, links=len(draft.relations),
                dropped_duplicate_relations=dropped_duplicate_relations,
                linked_materials_hash=canonical_digest(linked_materials),
                usage_audit=audit,
                billing_state=audit.get("cost", {}).get("billing_state", "unknown"),
            )
            manifest.pop("failure_type", None)
            manifest.pop("failure_message", None)
            manifest.pop("validation_errors", None)
            _private_json(manifest_path, manifest, exclusive=False)
            return linked_materials, manifest

        raise AssertionError("unreachable story-link attempt state")
    except Exception as exc:
        if attempt_dir is not None:
            failure = attempt_dir / "failure.json"
            if not failure.exists():
                safe_message = str(exc).replace(api_key, "[redacted]")[:2000]
                _private_json(failure, {
                    "type": type(exc).__name__, "message": safe_message,
                    "billing_state": (manifest.get("billing_state") or "unknown_if_request_was_emitted"),
                })
        if isinstance(exc, ProviderOutputTruncated):
            manifest.update(status="output_truncated", failure_type=type(exc).__name__,
                            failure_message=str(exc)[:512])
        elif isinstance(exc, (ValidationError, ValueError)):
            manifest.update(status="validation_rejected", failure_type=type(exc).__name__,
                            failure_message=str(exc)[:512])
        else:
            manifest.update(status="provider_or_runner_failed", failure_type=type(exc).__name__,
                            failure_message=str(exc)[:512])
        _private_json(manifest_path, manifest, exclusive=False)
        raise
