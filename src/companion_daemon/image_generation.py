import base64
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import asyncio
import json
import logging
import math
import mimetypes
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol

import httpx

from companion_daemon.image_requests import detect_style_tags
from companion_daemon.visual_identity import load_visual_identity


_LOG = logging.getLogger(__name__)
_PAID_GENERATE_LOCK = asyncio.Lock()
_IMAGE_SPEND_STORE: ContextVar[object | None] = ContextVar(
    "image_generation_spend_store", default=None
)


@dataclass(frozen=True)
class GeneratedImage:
    path: Path
    prompt: str
    attempts: int = 1
    content_type: str = "application/octet-stream"


# Provider filenames and Content-Type headers are not authoritative.  Civitai
# currently returns JPEG bytes while callers request ``.png``.
_IMAGE_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)


def sniff_generated_image_media_type(data: bytes) -> str | None:
    """Identify image bytes by signature. Never trust a provider filename."""

    for magic, media_type, _suffix in _IMAGE_MAGIC:
        if data.startswith(magic):
            return media_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def sniff_generated_image_suffix(data: bytes) -> str | None:
    media_type = sniff_generated_image_media_type(data)
    if media_type == "image/webp":
        return ".webp"
    for _magic, candidate, suffix in _IMAGE_MAGIC:
        if candidate == media_type:
            return suffix
    return None


def persist_generated_image_bytes(output_path: Path, content: bytes) -> tuple[Path, str]:
    """Write image bytes using a suffix that matches the payload, not the request."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = sniff_generated_image_suffix(content)
    media_type = sniff_generated_image_media_type(content) or "application/octet-stream"
    actual_path = output_path.with_suffix(suffix) if suffix else output_path
    actual_path.write_bytes(content)
    return actual_path, media_type


def _existing_generated_image(output_path: Path) -> Path | None:
    candidates = [output_path]
    for suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        candidate = output_path.with_suffix(suffix)
        if candidate not in candidates:
            candidates.append(candidate)
    for path in candidates:
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def _generated_from_bytes(
    output_path: Path, content: bytes, prompt: str, *, attempts: int = 1
) -> GeneratedImage:
    path, content_type = persist_generated_image_bytes(output_path, content)
    return GeneratedImage(
        path=path, prompt=prompt, attempts=attempts, content_type=content_type
    )


@dataclass(frozen=True)
class ImageQualityResult:
    passed: bool
    reason: str


class ImageGenerator(Protocol):
    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage: ...


class ImageQualityGate(Protocol):
    async def assess(
        self,
        image_path: Path,
        *,
        prompt: str,
        reference_images: Iterable[Path] = (),
    ) -> ImageQualityResult: ...


class ImageQualityRejected(ValueError):
    """A generated image failed every allowed visual acceptance attempt."""


class ImageGenerationProviderError(RuntimeError):
    """A sanitized, machine-actionable failure returned by an image provider.

    The renderer uses ``retryable`` to distinguish a temporary provider outage
    from a prompt/policy rejection.  Do not include request headers, prompts,
    or response bodies here: this value is persisted with media actions.
    """

    def __init__(
        self,
        *,
        provider: str,
        kind: str,
        status_code: int | None = None,
        detail: str = "",
    ) -> None:
        self.provider = provider
        self.kind = kind
        self.status_code = status_code
        self.detail = detail[:240]
        super().__init__(
            ":".join(
                str(part)
                for part in (provider, kind, status_code, self.detail)
                if part not in (None, "")
            )
        )

    @property
    def retryable(self) -> bool:
        return self.kind in {"transient", "transport"}


class OpenAIImageGenerator:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-image-2",
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        spend_store: object | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.proxy_url = proxy_url
        self.transport = transport
        self.spend_store = spend_store

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        references = tuple(reference_images)
        async with _PAID_GENERATE_LOCK:
            _refuse_unbounded_image_spend(
                model=self.model,
                size=size,
                quality=quality,
                reference_count=len(references),
                spend_store=self.spend_store,
            )
            started = time.perf_counter()
            billed = False
            usage_payload: dict[str, object] | None = None
            try:
                async with _openai_client(
                    timeout=180,
                    proxy_url=self.proxy_url,
                    transport=self.transport,
                ) as client:
                    if references:
                        files = [
                            (
                                "image[]",
                                (
                                    path.name,
                                    path.read_bytes(),
                                    mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                                ),
                            )
                            for path in references
                        ]
                        response = await client.post(
                            f"{self.base_url}/images/edits",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            data={
                                "model": self.model,
                                "prompt": prompt,
                                "size": size,
                                "quality": quality,
                                "output_format": "png",
                            },
                            files=files,
                        )
                    else:
                        response = await client.post(
                            f"{self.base_url}/images/generations",
                            headers={
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": self.model,
                                "prompt": prompt,
                                "size": size,
                                "quality": quality,
                                "output_format": "png",
                            },
                        )
                    if response.is_error:
                        raise openai_provider_error(response, provider="openai_image")
                    billed = True
                    try:
                        payload = response.json()
                    except (ValueError, json.JSONDecodeError):
                        payload = None
                    if isinstance(payload, dict):
                        usage_payload = payload
            except ImageGenerationProviderError:
                raise
            except httpx.TransportError as exc:
                raise ImageGenerationProviderError(
                    provider="openai", kind="transport", detail=type(exc).__name__
                ) from exc
            finally:
                if billed:
                    _record_paid_image_generation(
                        model=self.model,
                        size=size,
                        quality=quality,
                        reference_count=len(references),
                        prompt=prompt,
                        usage_payload=usage_payload,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        spend_store=self.spend_store,
                    )
            try:
                if not isinstance(usage_payload, dict):
                    raise ValueError("missing image payload")
                data = usage_payload["data"][0]
                image_b64 = data.get("b64_json") or data.get("image_base64")
                if not isinstance(image_b64, str) or not image_b64:
                    raise ValueError("missing image payload")
                return _generated_from_bytes(output_path, base64.b64decode(image_b64), prompt)
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ImageGenerationProviderError(
                    provider="openai", kind="invalid_response", detail=type(exc).__name__
                ) from exc


def openai_provider_error(
    response: httpx.Response, *, provider: str = "openai_image"
) -> ImageGenerationProviderError:
    """Classify an OpenAI response without persisting raw provider payloads."""

    detail = ""
    try:
        payload = response.json()
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(error, dict):
            detail = str(error.get("code") or error.get("type") or error.get("message") or "")
    except (ValueError, json.JSONDecodeError):
        pass
    normalized = detail.lower()
    if response.status_code in {408, 409, 429} or response.status_code >= 500:
        kind = "transient"
    elif any(token in normalized for token in ("policy", "safety", "content", "moderation")):
        kind = "policy"
    else:
        kind = "invalid_request"
    return ImageGenerationProviderError(
        provider=provider,
        kind=kind,
        status_code=response.status_code,
        detail=detail or f"http_{response.status_code}",
    )


def bind_image_spend_store(store: object | None):
    """Tests inject a CompanionStore; production uses DATABASE_PATH when set."""

    return _IMAGE_SPEND_STORE.set(store)


def reset_image_spend_store(token) -> None:
    _IMAGE_SPEND_STORE.reset(token)


def _image_spend_store(explicit: object | None) -> object | None:
    if explicit is not None:
        return explicit
    bound = _IMAGE_SPEND_STORE.get()
    if bound is not None:
        return bound
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    raw = os.environ.get("DATABASE_PATH", "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_file():
        return None
    from companion_daemon.db import CompanionStore

    return CompanionStore(path)


def _refuse_unbounded_image_spend(
    *,
    model: str,
    size: str,
    quality: str,
    reference_count: int,
    spend_store: object | None,
) -> None:
    store = _image_spend_store(spend_store)
    if store is None or not hasattr(store, "usage_count"):
        return
    from companion_daemon.budget import BudgetGate, image_render_estimate

    monthly, daily, soft, monthly_images = 80.0, 3.0, 2.0, 20
    vision_limit, audio_limit = 120, 60
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        try:
            from companion_daemon.config import Settings

            settings = Settings()
            monthly = float(settings.monthly_budget_cny)
            daily = float(settings.daily_budget_cny)
            soft = float(settings.soft_daily_budget_cny)
            monthly_images = int(settings.monthly_image_limit)
            vision_limit = int(settings.monthly_vision_limit)
            audio_limit = int(settings.monthly_audio_limit)
        except Exception:
            pass
    gate = BudgetGate(
        store,  # type: ignore[arg-type]
        monthly_budget_cny=monthly,
        daily_budget_cny=daily,
        soft_daily_budget_cny=soft,
        monthly_image_limit=monthly_images,
        monthly_vision_limit=vision_limit,
        monthly_audio_limit=audio_limit,
    )
    estimate = image_render_estimate(
        reference_count=reference_count, size=size, quality=quality, attempts=1
    )
    decision = gate.check(estimate, automatic=True)
    if decision.allowed:
        return
    raise ImageGenerationProviderError(
        provider="openai_image",
        kind="invalid_request",
        detail="image_generation_spend_cap:" + decision.reason,
    )


def _record_paid_image_generation(
    *,
    model: str,
    size: str,
    quality: str,
    reference_count: int,
    prompt: str,
    usage_payload: dict[str, object] | None,
    latency_ms: int,
    spend_store: object | None,
) -> None:
    """Record spend after HTTP 200, even when the image bytes cannot be parsed."""

    from companion_daemon.usage_metrics import (
        estimate_gpt_image_2_cost_usd,
        parse_openai_image_usage,
    )

    parsed = parse_openai_image_usage(usage_payload)
    usd, pricing_version = estimate_gpt_image_2_cost_usd(
        size=size,
        quality=quality,
        reference_count=reference_count,
        text_input_tokens=parsed["text_input_tokens"],
        image_input_tokens=parsed["image_input_tokens"] or None,
        output_tokens=parsed["output_tokens"] or None,
    )
    cny = round(usd * 7.2, 4)
    note = (
        f"gpt-image:{model}:{size}:{quality}:refs={reference_count}"
        f":pricing={pricing_version}"
    )
    store = _image_spend_store(spend_store)
    if store is not None and hasattr(store, "record_usage"):
        try:
            store.record_usage("image_generation", cny, note=note)
        except Exception:
            _LOG.exception("image generation usage_events record failed")
    db_path = getattr(store, "path", None) if store is not None else None
    if db_path is None and not os.environ.get("PYTEST_CURRENT_TEST"):
        raw = os.environ.get("DATABASE_PATH", "").strip()
        db_path = raw or None
    if db_path:
        try:
            from companion_daemon.llm import ModelCallUsage
            from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

            WorldV2UsageStore(path=str(db_path)).record(
                ModelCallUsage(
                    purpose="image_generation",
                    model=model or "gpt-image-2",
                    status="succeeded",
                    latency_ms=max(0, latency_ms),
                    prompt_tokens=parsed["text_input_tokens"] + parsed["image_input_tokens"],
                    completion_tokens=parsed["output_tokens"],
                    cache_hit_tokens=0,
                    cache_miss_tokens=parsed["image_input_tokens"]
                    + parsed["text_input_tokens"],
                    total_tokens=parsed["total_tokens"],
                    provider="openai",
                )
            )
        except Exception:
            _LOG.exception("image generation world-v2 usage record failed")
    del prompt  # prompt is not persisted; length is not billed as a secret.


class VolcArkImageGenerator:
    """Seedream adapter for the same reference-image seam as OpenAI edits.

    Ark accepts base64 reference images in its OpenAI-shaped image generation
    endpoint.  Keeping that conversion here lets MediaRenderer continue to
    own identity-reference selection and keeps providers out of MediaPlan.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        model: str = "doubao-seedream-4-0-250828",
        image_size: str = "2K",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.image_size = image_size
        self.transport = transport

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        del size, quality  # Ark uses its own configured quality/size vocabulary.
        output_path.parent.mkdir(parents=True, exist_ok=True)
        references = tuple(path for path in reference_images if path.is_file())
        payload: dict[str, object] = {
            "model": self.model,
            "prompt": prompt,
            "size": self.image_size,
            "stream": False,
            "response_format": "b64_json",
            "watermark": False,
        }
        # Seedream 4.0 needs this switch to prevent an unplanned image set.
        # Seedream 5.0 Pro rejects the parameter altogether, and already
        # produces a single image for this endpoint.
        if not self.model.startswith("doubao-seedream-5-0-pro-"):
            payload["sequential_image_generation"] = "disabled"
        if references:
            # Ark accepts URL or base64 image input.  A MIME-qualified data URL
            # makes the encoding unambiguous: bare base64 was interpreted as a
            # URL by the production endpoint.  Keep identity assets local;
            # they must never need a public object-store URL.
            payload["image"] = [
                "data:"
                f"{mimetypes.guess_type(path.name)[0] or 'application/octet-stream'}"
                ";base64,"
                f"{base64.b64encode(path.read_bytes()).decode('ascii')}"
                for path in references
            ]
        async with httpx.AsyncClient(
            timeout=180,
            trust_env=False,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/images/generations",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            if response.is_error:
                raise _ark_request_error(response)
            data = response.json().get("data", [])
            if not isinstance(data, list) or not data or not isinstance(data[0], dict):
                raise ValueError("Ark image response did not include image data")
            first = data[0]
            image_b64 = first.get("b64_json") or first.get("image_base64")
            image_url = first.get("url")
            if isinstance(image_b64, str) and image_b64:
                return _generated_from_bytes(output_path, base64.b64decode(image_b64), prompt)
            elif isinstance(image_url, str) and image_url:
                download = await client.get(image_url)
                download.raise_for_status()
                return _generated_from_bytes(output_path, download.content, prompt)
            else:
                raise ValueError("Ark image response did not include b64_json or url")


def _ark_request_error(response: httpx.Response) -> ValueError:
    """Expose a safe actionable Ark failure without surfacing request headers."""

    try:
        error = response.json().get("error", {})
    except (ValueError, json.JSONDecodeError):
        error = {}
    if isinstance(error, dict):
        code = str(error.get("code") or "")
        message = str(error.get("message") or "")
        if code == "ModelNotOpen":
            return ValueError(
                "Ark image model is not enabled for this account; activate the configured "
                "ARK_IMAGE_MODEL in Ark Console or set ARK_IMAGE_MODEL to an enabled endpoint/model."
            )
        if code or message:
            return ValueError(f"Ark image request failed ({code or response.status_code}): {message[:360]}")
    return ValueError(f"Ark image request failed with HTTP {response.status_code}")


class CivitaiWorkflowImageGenerator:
    """Pinned Civitai Orchestration workflow with bounded identity variation."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        base_url: str = "https://orchestration.civitai.com/v2/consumer",
        ecosystem: str = "sdxl",
        engine: str = "comfy",
        steps: int = 24,
        cfg_scale: float = 6.0,
        denoise_strength: float = 0.58,
        allow_mature_content: bool = True,
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.ecosystem = ecosystem
        self.engine = engine
        self.steps = steps
        self.cfg_scale = cfg_scale
        self.denoise_strength = denoise_strength
        self.allow_mature_content = allow_mature_content
        self.proxy_url = proxy_url
        self.transport = transport

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        del quality
        output_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = _civitai_dimensions(size)
        references = tuple(path for path in reference_images if path.is_file())
        input_payload: dict[str, object] = {
            "engine": self.engine,
            "ecosystem": self.ecosystem,
            "model": self.model,
            "prompt": prompt,
            "negativePrompt": _civitai_negative_prompt(),
            "width": width,
            "height": height,
            "steps": self.steps,
            "cfgScale": self.cfg_scale,
            "quantity": 1,
            "outputFormat": "png",
        }
        if references:
            # The provider's SDXL variant API takes one source.  The renderer
            # places its canonical identity anchor first, avoiding conflicts
            # from loading a whole reference catalog into one request.
            input_payload.update(
                {
                    "operation": "createVariant",
                    "image": _civitai_data_url(references[0]),
                    "denoiseStrength": self.denoise_strength,
                }
            )
        else:
            input_payload["operation"] = "createImage"
        workflow = {
            "ephemeral": True,
            "allowMatureContent": self.allow_mature_content,
            "externalId": _civitai_external_id(
                prompt,
                references,
                model=self.model,
                width=width,
                height=height,
            ),
            "steps": [{"$type": "imageGen", "input": input_payload}],
        }
        try:
            async with _openai_client(
                timeout=195,
                proxy_url=self.proxy_url,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/workflows",
                    params={"wait": 180},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=workflow,
                )
                if response.is_error:
                    raise _civitai_provider_error(response)
                payload = response.json()
                workflow_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
                while _civitai_workflow_pending(payload):
                    if not workflow_id:
                        raise ValueError("missing workflow id")
                    await _sleep_briefly()
                    response = await client.get(
                        f"{self.base_url}/workflows/{workflow_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    if response.is_error:
                        raise _civitai_provider_error(response)
                    payload = response.json()
                image_url = _civitai_image_url(payload)
                if not image_url:
                    raise _civitai_terminal_error(payload)
                # Civitai returns a signed blob URL which currently redirects
                # once to its content endpoint. A 301 is not an empty image.
                artifact = await client.get(image_url, follow_redirects=True)
                if artifact.is_error:
                    raise _civitai_provider_error(artifact)
                return _generated_from_bytes(output_path, artifact.content, prompt)
        except ImageGenerationProviderError:
            raise
        except httpx.TransportError as exc:
            raise ImageGenerationProviderError(
                provider="civitai_image", kind="transport", detail=type(exc).__name__
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageGenerationProviderError(
                provider="civitai_image", kind="invalid_response", detail=type(exc).__name__
            ) from exc


class CivitaiKrea2ImageGenerator(CivitaiWorkflowImageGenerator):
    """Civitai's generic ``imageGen`` Krea2 path with pinned AIR LoRAs.

    This intentionally differs from the SDXL variant adapter above: Krea2's
    documented generic workflow accepts a LoRA stack but does not expose that
    adapter's source-image variant contract.  Identity is therefore supplied
    by the account's Krea2 AIR LoRA, while the media planner still freezes the
    same identity-reference selection for audit and inspection.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        loras: Iterable[tuple[str, float]],
        base_url: str = "https://orchestration.civitai.com/v2/consumer",
        steps: int = 8,
        cfg_scale: float = 1.0,
        sampler: str = "euler",
        scheduler: str = "simple",
        allow_mature_content: bool = True,
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            api_key,
            model=model,
            base_url=base_url,
            ecosystem="krea2",
            engine="comfy",
            steps=steps,
            cfg_scale=cfg_scale,
            allow_mature_content=allow_mature_content,
            proxy_url=proxy_url,
            transport=transport,
        )
        normalized = tuple((str(air).strip(), float(weight)) for air, weight in loras)
        if not normalized or any(not air for air, _ in normalized):
            raise ValueError("Civitai Krea2 requires at least one AIR LoRA")
        self.loras = normalized
        self.sampler = sampler
        self.scheduler = scheduler

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        del quality
        # Krea2 generic imageGen currently has no verified source-image field.
        # Refuse neither plan nor generation when references exist: the frozen
        # identity selection remains audit data and the identity LoRA is the
        # cloud identity mechanism, not an accidental SDXL img2img fallback.
        del reference_images
        output_path.parent.mkdir(parents=True, exist_ok=True)
        width, height = _civitai_dimensions(size)
        input_payload: dict[str, object] = {
            "engine": "comfy",
            "ecosystem": "krea2",
            "model": self.model,
            "operation": "createImage",
            "prompt": prompt,
            "negativePrompt": _civitai_negative_prompt(),
            "width": width,
            "height": height,
            "steps": self.steps,
            "cfgScale": self.cfg_scale,
            "sampler": self.sampler,
            "scheduler": self.scheduler,
            "quantity": 1,
            "outputFormat": "png",
            "loras": [
                {"model": air, "strength": weight} for air, weight in self.loras
            ],
        }
        workflow = {
            "ephemeral": True,
            "allowMatureContent": self.allow_mature_content,
            "externalId": _civitai_external_id(
                prompt,
                (),
                model=f"krea2:{self.model}",
                width=width,
                height=height,
            ),
            "steps": [{"$type": "imageGen", "input": input_payload}],
        }
        try:
            async with _openai_client(
                timeout=195,
                proxy_url=self.proxy_url,
                transport=self.transport,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/workflows",
                    params={"wait": 180},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=workflow,
                )
                if response.is_error:
                    raise _civitai_provider_error(response)
                payload = response.json()
                workflow_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
                while _civitai_workflow_pending(payload):
                    if not workflow_id:
                        raise ValueError("missing workflow id")
                    await _sleep_briefly()
                    response = await client.get(
                        f"{self.base_url}/workflows/{workflow_id}",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                    )
                    if response.is_error:
                        raise _civitai_provider_error(response)
                    payload = response.json()
                image_url = _civitai_image_url(payload)
                if not image_url:
                    raise _civitai_terminal_error(payload)
                artifact = await client.get(image_url, follow_redirects=True)
                if artifact.is_error:
                    raise _civitai_provider_error(artifact)
                return _generated_from_bytes(output_path, artifact.content, prompt)
        except ImageGenerationProviderError:
            raise
        except httpx.TransportError as exc:
            raise ImageGenerationProviderError(
                provider="civitai_krea2", kind="transport", detail=type(exc).__name__
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageGenerationProviderError(
                provider="civitai_krea2", kind="invalid_response", detail=type(exc).__name__
            ) from exc


class CivitaiTemplateWorkflowImageGenerator(CivitaiWorkflowImageGenerator):
    """Render through one reviewed Civitai workflow template.

    The template owns the provider-specific recipe: model ecosystem, LoRA
    stack, sampler, scheduler, negative prompt and mature-content setting.
    Callers can only provide a frozen MediaPlan prompt and output geometry.
    A reviewed workflow may run reference-free, retain a fixed face-only
    input, or explicitly opt into the dynamic identity placeholder used by
    older reference-edit recipes.  A caller can require the reference-free
    form for a profile such as the Celia high-private Krea2 route.  This keeps
    Hermes at the semantic planning seam; it cannot mutate Civitai nodes,
    LoRA weights, or provider controls.
    """

    _PROMPT_SLOT = "{{render_prompt}}"
    _IDENTITY_SLOT = "{{identity_reference_data_url}}"
    _SEED_SLOT = "{{seed}}"
    # Submit with a short wait, persist the workflow id, then reconcile on
    # later drain/tick.  Never block this call for minutes: a preparing LoRA
    # can outlive any synchronous poll, and a timeout-then-retry POSTs twice.
    _SUBMISSION_WAIT_SECONDS = 8
    _REQUEST_TIMEOUT_SECONDS = 20
    _RECONCILE_MIN_INTERVAL_SECONDS = 15
    _ABANDON_AFTER = timedelta(hours=4)

    def __init__(
        self,
        api_key: str,
        *,
        template_path: Path,
        base_url: str = "https://orchestration.civitai.com/v2/consumer",
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        require_reference_free: bool = False,
        spend_store: object | None = None,
    ) -> None:
        self.template_path = template_path
        self.template = _load_civitai_imagegen_template(
            template_path,
            require_reference_free=require_reference_free,
        )
        self.require_reference_free = require_reference_free
        self.spend_store = spend_store
        self._submit_lock = asyncio.Lock()
        self._uses_dynamic_identity_anchor = (
            _template_imagegen_input(self.template).get("images") == [self._IDENTITY_SLOT]
        )
        super().__init__(
            api_key,
            model="template",
            base_url=base_url,
            ecosystem="krea2",
            engine="comfy",
            allow_mature_content=bool(self.template.get("allowMatureContent", True)),
            proxy_url=proxy_url,
            transport=transport,
        )

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        quality: str = "medium",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        del quality
        output_path.parent.mkdir(parents=True, exist_ok=True)
        prior = _read_civitai_workflow_receipt(output_path)
        existing = _existing_generated_image(output_path)
        if (
            existing is not None
            and prior.get("status") == "succeeded"
            and prior.get("has_output") is True
        ):
            media_type = sniff_generated_image_media_type(existing.read_bytes()) or (
                "application/octet-stream"
            )
            return GeneratedImage(path=existing, prompt=prompt, content_type=media_type)
        references = tuple(path for path in reference_images if path.is_file())
        workflow = deepcopy(self.template)
        image_input = _template_imagegen_input(workflow)
        width, height = _civitai_dimensions(size)
        image_input["prompt"] = prompt
        image_input["width"] = width
        image_input["height"] = height
        image_input["seed"] = _civitai_template_seed(prompt, width=width, height=height)
        identity_reference: Path | None = None
        if self._uses_dynamic_identity_anchor:
            if not references:
                raise ImageGenerationProviderError(
                    provider="civitai_template",
                    kind="invalid_request",
                    detail="identity_reference_required",
                )
            identity_reference = references[0]
        workflow["externalId"] = _civitai_external_id(
            prompt,
            references[:1] if self._uses_dynamic_identity_anchor else (),
            model=f"template:{self.template_path.name}",
            width=width,
            height=height,
        )
        # Retention is required for GET reconciliation.  Ephemeral workflows
        # 404 after they finish, which would turn a billed success into a lost
        # image and a second POST.
        workflow["ephemeral"] = False
        try:
            async with _openai_client(
                timeout=195,
                proxy_url=self.proxy_url,
                transport=self.transport,
            ) as client:
                if identity_reference is not None:
                    image_input["images"] = [
                        await _civitai_upload_reference(
                            client,
                            base_url=self.base_url,
                            api_key=self.api_key,
                            reference=identity_reference,
                        )
                    ]
                return await self._submit_or_reconcile(
                    client,
                    workflow=workflow,
                    output_path=output_path,
                    prompt=prompt,
                )
        except ImageGenerationProviderError:
            raise
        except httpx.TimeoutException as exc:
            # A timed-out provider request may have been accepted remotely;
            # do not let the caller automatically duplicate it.
            raise ImageGenerationProviderError(
                provider="civitai_template", kind="unknown", detail="request_timeout"
            ) from exc
        except httpx.TransportError as exc:
            raise ImageGenerationProviderError(
                provider="civitai_template", kind="transport", detail=type(exc).__name__
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ImageGenerationProviderError(
                provider="civitai_template", kind="invalid_response", detail=type(exc).__name__
            ) from exc

    async def _submit_or_reconcile(
        self,
        client: httpx.AsyncClient,
        *,
        workflow: dict[str, object],
        output_path: Path,
        prompt: str,
    ) -> GeneratedImage:
        async with self._submit_lock:
            receipt = _read_civitai_workflow_receipt(output_path)
            workflow_id = str(receipt.get("workflow_id") or "")
            payload: object
            if workflow_id:
                payload = await self._get_workflow(client, workflow_id=workflow_id, receipt=receipt)
            else:
                await _assert_civitai_airs_schedulable(
                    client,
                    base_url=self.base_url,
                    api_key=self.api_key,
                    airs=_template_lora_airs(self.template),
                )
                response = await client.post(
                    f"{self.base_url}/workflows",
                    params={"wait": self._SUBMISSION_WAIT_SECONDS},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=workflow,
                    timeout=self._REQUEST_TIMEOUT_SECONDS,
                )
                if response.is_error:
                    raise _civitai_provider_error(response)
                payload = response.json()
                workflow_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
                if not workflow_id:
                    raise ValueError("missing workflow id")
                yellow_buzz, currency = _civitai_debit_buzz(payload)
                _write_civitai_workflow_receipt(
                    output_path,
                    workflow_id=workflow_id,
                    payload=payload,
                    template_name=self.template_path.name,
                    yellow_buzz=yellow_buzz,
                    buzz_currency=currency,
                    billed=True,
                )
                if yellow_buzz:
                    _record_civitai_buzz_usage(
                        yellow_buzz=yellow_buzz,
                        workflow_id=workflow_id,
                        has_output=False,
                        spend_store=self.spend_store,
                    )
                if _civitai_workflow_pending(payload) and not _civitai_ready_image_url(payload):
                    payload = await self._get_workflow(
                        client,
                        workflow_id=workflow_id,
                        receipt=_read_civitai_workflow_receipt(output_path),
                        force=True,
                    )
        return await self._materialize_or_pending(
            client,
            payload=payload,
            output_path=output_path,
            prompt=prompt,
            workflow_id=workflow_id,
        )

    async def _get_workflow(
        self,
        client: httpx.AsyncClient,
        *,
        workflow_id: str,
        receipt: dict[str, object],
        force: bool = False,
    ) -> object:
        if (
            not force
            and _civitai_receipt_polled_recently(
                receipt, min_interval_seconds=self._RECONCILE_MIN_INTERVAL_SECONDS
            )
            and not _civitai_receipt_abandoned(receipt, abandon_after=self._ABANDON_AFTER)
        ):
            return {
                "id": workflow_id,
                "status": receipt.get("status") or "preparing",
                "cost": {"total": receipt.get("yellow_buzz")},
            }
        response = await client.get(
            f"{self.base_url}/workflows/{workflow_id}",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self._REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 404:
            raise ImageGenerationProviderError(
                provider="civitai_template",
                kind="unknown",
                status_code=404,
                detail=(
                    f"billed_no_output:{workflow_id}"
                    if receipt.get("billed") or receipt.get("yellow_buzz")
                    else f"workflow_missing:{workflow_id}"
                ),
            )
        if response.is_error:
            raise _civitai_provider_error(response)
        return response.json()

    async def _materialize_or_pending(
        self,
        client: httpx.AsyncClient,
        *,
        payload: object,
        output_path: Path,
        prompt: str,
        workflow_id: str,
    ) -> GeneratedImage:
        yellow_buzz, currency = _civitai_debit_buzz(payload)
        _write_civitai_workflow_receipt(
            output_path,
            workflow_id=workflow_id,
            payload=payload,
            template_name=self.template_path.name,
            yellow_buzz=yellow_buzz,
            buzz_currency=currency,
        )
        image_url = _civitai_ready_image_url(payload)
        if image_url:
            artifact = await client.get(image_url, follow_redirects=True)
            if artifact.is_error:
                raise _civitai_provider_error(artifact)
            generated = _generated_from_bytes(output_path, artifact.content, prompt)
            _write_civitai_workflow_receipt(
                output_path,
                workflow_id=workflow_id,
                payload=payload,
                template_name=self.template_path.name,
                yellow_buzz=yellow_buzz,
                buzz_currency=currency,
                has_output=True,
            )
            if yellow_buzz:
                _record_civitai_buzz_usage(
                    yellow_buzz=yellow_buzz,
                    workflow_id=workflow_id,
                    has_output=True,
                    spend_store=self.spend_store,
                )
            return generated
        receipt = _read_civitai_workflow_receipt(output_path)
        status = str(payload.get("status") or "") if isinstance(payload, dict) else ""
        if status in {"failed", "expired", "canceled", "cancelled"}:
            raise _civitai_terminal_error(payload)
        if _civitai_receipt_abandoned(receipt, abandon_after=self._ABANDON_AFTER):
            _write_civitai_workflow_receipt(
                output_path,
                workflow_id=workflow_id,
                payload=payload,
                template_name=self.template_path.name,
                yellow_buzz=yellow_buzz,
                buzz_currency=currency,
                billed_no_output=True,
            )
            billed_buzz = int(yellow_buzz or receipt.get("yellow_buzz") or 0)
            if billed_buzz:
                _record_civitai_buzz_usage(
                    yellow_buzz=billed_buzz,
                    workflow_id=workflow_id,
                    has_output=False,
                    billed_no_output=True,
                    spend_store=self.spend_store,
                )
            raise ImageGenerationProviderError(
                provider="civitai_template",
                kind="unknown",
                detail=f"billed_no_output:{workflow_id}",
            )
        raise ImageGenerationProviderError(
            provider="civitai_template",
            kind="pending",
            detail=f"workflow_pending:{workflow_id}",
        )


def _load_civitai_imagegen_template(
    path: Path,
    *,
    require_reference_free: bool = False,
) -> dict[str, object]:
    """Load a reviewed template and reject any free-form provider recipe."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid Civitai workflow template: {path}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Civitai workflow template must be an object")
    image_input = _template_imagegen_input(raw)
    if image_input.get("prompt") != CivitaiTemplateWorkflowImageGenerator._PROMPT_SLOT:
        raise ValueError("Civitai workflow template must contain the {{render_prompt}} slot")
    images = image_input.get("images")
    reference_free = images is None or images == []
    dynamic_anchor = images == [CivitaiTemplateWorkflowImageGenerator._IDENTITY_SLOT]
    fixed_anchor = (
        isinstance(images, list)
        and len(images) == 1
        and isinstance(images[0], str)
        and bool(images[0].strip())
        and "{{" not in images[0]
    )
    if not reference_free and not dynamic_anchor and not fixed_anchor:
        raise ValueError(
            "Civitai workflow template images must be absent, use one reviewed fixed face anchor, "
            "or use the identity reference slot"
        )
    if require_reference_free and not reference_free:
        raise ValueError("Civitai workflow template must be reference-free for this profile")
    if image_input.get("seed") not in (None, CivitaiTemplateWorkflowImageGenerator._SEED_SLOT):
        raise ValueError("Civitai workflow template seed must use the {{seed}} slot")
    if image_input.get("engine") != "comfy" or image_input.get("ecosystem") != "krea2":
        raise ValueError("Civitai workflow template must be a Comfy Krea2 imageGen recipe")
    if image_input.get("operation") != "createImage" or not image_input.get("model"):
        raise ValueError("Civitai workflow template must declare a Krea2 createImage model")
    if not isinstance(image_input.get("model"), str):
        raise ValueError("Civitai workflow template model must be a string")
    if not isinstance(image_input.get("negativePrompt"), str):
        raise ValueError("Civitai workflow template must pin a negative prompt")
    if not isinstance(image_input.get("loras"), dict) or not image_input["loras"]:
        raise ValueError("Civitai workflow template must pin its LoRA stack")
    if any(
        not isinstance(air, str)
        or not air.startswith("urn:air:krea2:lora:")
        or not isinstance(weight, (int, float))
        or isinstance(weight, bool)
        or not math.isfinite(float(weight))
        or float(weight) <= 0
        for air, weight in image_input["loras"].items()
    ):
        raise ValueError("Civitai workflow template contains an invalid Krea2 LoRA stack")
    if not isinstance(raw.get("allowMatureContent"), bool):
        raise ValueError("Civitai workflow template allowMatureContent must be boolean")
    _template_positive_int(image_input, "width")
    _template_positive_int(image_input, "height")
    _template_positive_int(image_input, "steps")
    _template_positive_int(image_input, "quantity")
    for field in ("cfgScale",):
        value = image_input.get(field)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError(f"Civitai workflow template {field} must be a finite non-negative number")
    if not all(isinstance(image_input.get(field), str) and image_input[field] for field in ("sampler", "scheduler")):
        raise ValueError("Civitai workflow template must pin sampler and scheduler")
    return raw


def _template_imagegen_input(workflow: dict[str, object]) -> dict[str, object]:
    steps = workflow.get("steps")
    if not isinstance(steps, list) or len(steps) != 1 or not isinstance(steps[0], dict):
        raise ValueError("Civitai workflow template must contain exactly one imageGen step")
    step = steps[0]
    if step.get("$type") != "imageGen" or not isinstance(step.get("input"), dict):
        raise ValueError("Civitai workflow template must contain an imageGen input")
    return step["input"]


def _template_positive_int(input_payload: dict[str, object], field: str) -> None:
    value = input_payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"Civitai workflow template {field} must be a positive integer")


def _civitai_template_seed(prompt: str, *, width: int, height: int) -> int:
    digest = sha256(f"{prompt}|{width}x{height}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


async def _civitai_upload_reference(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    reference: Path,
) -> str:
    """Upload one frozen identity anchor and return its Civitai blob ID.

    Generic Krea2 imageGen's ``images`` array expects a Civitai blob, not a
    local filename or a data URL.  Uploading happens inside the renderer so
    the workflow template remains reusable and the planner never learns a
    provider-specific asset protocol.
    """

    presign = await client.get(
        f"{base_url}/blobs/upload",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if presign.is_error:
        raise _civitai_provider_error(presign)
    try:
        upload_url = str(presign.json().get("uploadUrl") or "")
    except (ValueError, AttributeError) as exc:
        raise ValueError("Civitai blob upload URL missing") from exc
    if not upload_url:
        raise ValueError("Civitai blob upload URL missing")
    mime = mimetypes.guess_type(reference.name)[0] or "application/octet-stream"
    uploaded = await client.post(
        upload_url,
        content=reference.read_bytes(),
        headers={"Content-Type": mime},
    )
    if uploaded.is_error:
        raise _civitai_provider_error(uploaded)
    try:
        payload = uploaded.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Civitai blob upload response invalid") from exc
    blob_id = str(payload.get("id") or "") if isinstance(payload, dict) else ""
    if not blob_id or not bool(payload.get("available")):
        raise ValueError("Civitai blob upload unavailable")
    return blob_id


def _civitai_dimensions(size: str) -> tuple[int, int]:
    try:
        width, height = (int(part) for part in size.lower().split("x", maxsplit=1))
    except (AttributeError, TypeError, ValueError):
        return (1024, 1536)
    return (min(2048, max(64, width)), min(2048, max(64, height)))


def _civitai_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def _civitai_external_id(
    prompt: str,
    references: tuple[Path, ...],
    *,
    model: str,
    width: int,
    height: int,
) -> str:
    digest = sha256(prompt.encode("utf-8"))
    digest.update(model.encode("utf-8"))
    digest.update(f"{width}x{height}".encode("ascii"))
    if references:
        digest.update(references[0].read_bytes())
    return "media-" + digest.hexdigest()[:40]


def _civitai_negative_prompt() -> str:
    return (
        "text, watermark, logo, public figure, malformed hands, extra fingers, extra limbs, "
        "impossible mirror geometry, visible genitals, explicit sexual activity, transparent clothing, "
        "coercion, fetishized body-part crop"
    )


def _civitai_workflow_pending(payload: object) -> bool:
    return isinstance(payload, dict) and str(payload.get("status") or "") in {
        "unassigned",
        "preparing",
        "scheduled",
        "processing",
    }


def _template_lora_airs(template: dict[str, object]) -> tuple[str, ...]:
    loras = _template_imagegen_input(template).get("loras")
    if not isinstance(loras, dict):
        return ()
    return tuple(str(air) for air in loras if isinstance(air, str) and air)


def _civitai_resources_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/consumer"):
        return root[: -len("consumer")] + "resources"
    return root.rsplit("/", 1)[0] + "/resources"


async def _assert_civitai_airs_schedulable(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    airs: tuple[str, ...],
) -> None:
    """Fail closed before POST when orchestration cannot resolve a LoRA AIR."""

    missing: list[str] = []
    root = _civitai_resources_root(base_url)
    for air in airs:
        response = await client.get(
            f"{root}/{air}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if response.status_code == 404:
            missing.append(air)
            continue
        if response.is_error:
            raise ImageGenerationProviderError(
                provider="civitai_template",
                kind="transient",
                status_code=response.status_code,
                detail=f"resource_lookup_http_{response.status_code}",
            )
    if missing:
        labels = ",".join(air.rsplit(":", 1)[-1] for air in missing)
        raise ImageGenerationProviderError(
            provider="civitai_template",
            kind="invalid_request",
            status_code=404,
            detail=f"lora_not_schedulable:{labels}",
        )


def _civitai_receipt_path(output_path: Path) -> Path:
    return output_path.with_suffix(output_path.suffix + ".civitai.json")


def _read_civitai_workflow_receipt(output_path: Path) -> dict[str, object]:
    path = _civitai_receipt_path(output_path)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _civitai_parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _civitai_receipt_abandoned(
    receipt: dict[str, object], *, abandon_after: timedelta
) -> bool:
    submitted = _civitai_parse_time(receipt.get("submitted_at"))
    if submitted is None:
        return False
    return datetime.now(timezone.utc) - submitted >= abandon_after


def _civitai_receipt_polled_recently(
    receipt: dict[str, object], *, min_interval_seconds: int
) -> bool:
    polled = _civitai_parse_time(receipt.get("last_polled_at"))
    if polled is None:
        return False
    return (datetime.now(timezone.utc) - polled).total_seconds() < min_interval_seconds


def _civitai_debit_buzz(payload: object) -> tuple[int | None, str | None]:
    if not isinstance(payload, dict):
        return None, None
    transactions = payload.get("transactions")
    if isinstance(transactions, dict):
        items = transactions.get("list")
        if isinstance(items, list):
            total = 0
            currency: str | None = None
            for item in items:
                if not isinstance(item, dict) or item.get("type") != "debit":
                    continue
                amount = item.get("amount")
                if isinstance(amount, int) and not isinstance(amount, bool) and amount > 0:
                    total += amount
                    account = item.get("accountType")
                    if isinstance(account, str) and account:
                        currency = account
            if total:
                return total, currency or "yellow"
    cost = payload.get("cost")
    if isinstance(cost, dict):
        total = cost.get("total")
        if isinstance(total, int) and not isinstance(total, bool) and total > 0:
            return total, "yellow"
    return None, None


def _civitai_preparing_resource(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    steps = payload.get("steps")
    step = steps[0] if isinstance(steps, list) and steps and isinstance(steps[0], dict) else {}
    preparation = step.get("preparation") if isinstance(step, dict) else None
    if not isinstance(preparation, dict):
        preparation = payload.get("preparation") if isinstance(payload.get("preparation"), dict) else None
    if not isinstance(preparation, dict):
        return None
    resource = preparation.get("resource")
    return resource if isinstance(resource, str) and resource else None


def _write_civitai_workflow_receipt(
    output_path: Path,
    *,
    workflow_id: str,
    payload: object,
    template_name: str,
    yellow_buzz: int | None = None,
    buzz_currency: str | None = None,
    billed: bool | None = None,
    has_output: bool | None = None,
    billed_no_output: bool | None = None,
) -> None:
    """Persist durable Civitai task state beside a generated artifact.

    A billed workflow may continue remotely after this process returns.
    The receipt is the effect-once source for later GET reconciliation and
    the audit trail for buzz spent with or without an image.
    """

    previous = _read_civitai_workflow_receipt(output_path)
    now = datetime.now(timezone.utc).isoformat()
    status = str(previous.get("status") or "unknown")
    if isinstance(payload, dict):
        raw_status = payload.get("status")
        if isinstance(raw_status, str) and raw_status:
            status = raw_status
    submitted_at = previous.get("submitted_at")
    if not isinstance(submitted_at, str) or not submitted_at:
        submitted_at = now
    abandon_after = previous.get("abandon_after")
    if not isinstance(abandon_after, str) or not abandon_after:
        abandon_after = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
    stored_buzz = previous.get("yellow_buzz")
    if yellow_buzz is None and isinstance(stored_buzz, int):
        yellow_buzz = stored_buzz
    stored_currency = previous.get("buzz_currency")
    if not buzz_currency and isinstance(stored_currency, str):
        buzz_currency = stored_currency
    receipt = {
        "provider": "civitai_template",
        "workflow_id": workflow_id or previous.get("workflow_id"),
        "status": status,
        "template": template_name,
        "submitted_at": submitted_at,
        "last_polled_at": now,
        "abandon_after": abandon_after,
        "yellow_buzz": yellow_buzz,
        "buzz_currency": buzz_currency,
        "billed": True if billed else bool(previous.get("billed") or yellow_buzz),
        "has_output": True if has_output else bool(previous.get("has_output")),
        "billed_no_output": True if billed_no_output else bool(previous.get("billed_no_output")),
        "preparing_resource": _civitai_preparing_resource(payload),
        "ephemeral": False,
    }
    _civitai_receipt_path(output_path).write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _record_civitai_buzz_usage(
    *,
    yellow_buzz: int,
    workflow_id: str,
    has_output: bool,
    spend_store: object | None,
    billed_no_output: bool = False,
) -> None:
    """Record buzz as buzz. Do not invent a USD/CNY exchange rate."""

    if yellow_buzz <= 0:
        return
    status = "billed_no_output" if billed_no_output else ("succeeded" if has_output else "billed")
    note = (
        f"civitai:yellow_buzz={yellow_buzz}:workflow={workflow_id}"
        f":has_output={int(has_output)}:billed_no_output={int(billed_no_output)}"
    )
    store = _image_spend_store(spend_store)
    if store is not None and hasattr(store, "record_usage"):
        try:
            store.record_usage("civitai_buzz", 0.0, note=note)
        except Exception:
            _LOG.exception("civitai buzz usage_events record failed")
    db_path = getattr(store, "path", None) if store is not None else None
    if db_path is None and not os.environ.get("PYTEST_CURRENT_TEST"):
        raw = os.environ.get("DATABASE_PATH", "").strip()
        db_path = raw or None
    if not db_path:
        return
    try:
        from companion_daemon.llm import ModelCallUsage
        from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

        WorldV2UsageStore(path=str(db_path)).record(
            ModelCallUsage(
                purpose="image_generation",
                model="civitai-krea2-template",
                status=status,
                latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
                cache_hit_tokens=0,
                cache_miss_tokens=0,
                total_tokens=0,
                provider="civitai",
                error=note,
            )
        )
    except Exception:
        _LOG.exception("civitai buzz world-v2 usage record failed")


def _civitai_image_url(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps or not isinstance(steps[0], dict):
        return ""
    output = steps[0].get("output")
    images = output.get("images") if isinstance(output, dict) else None
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return ""
    url = images[0].get("url")
    return str(url) if isinstance(url, str) and url else ""


def _civitai_ready_image_url(payload: object) -> str:
    """Return a fetchable blob URL. Advertised but unavailable blobs stay pending."""

    if not isinstance(payload, dict):
        return ""
    steps = payload.get("steps")
    if not isinstance(steps, list) or not steps or not isinstance(steps[0], dict):
        return ""
    output = steps[0].get("output")
    images = output.get("images") if isinstance(output, dict) else None
    if not isinstance(images, list) or not images or not isinstance(images[0], dict):
        return ""
    image = images[0]
    if image.get("available") is False:
        return ""
    url = image.get("url")
    return str(url) if isinstance(url, str) and url else ""


def _civitai_terminal_error(payload: object) -> ImageGenerationProviderError:
    detail = "workflow_failed"
    if isinstance(payload, dict):
        steps = payload.get("steps")
        if isinstance(steps, list) and steps and isinstance(steps[0], dict):
            output = steps[0].get("output")
            errors = output.get("errors") if isinstance(output, dict) else None
            if isinstance(errors, list) and errors:
                detail = str(errors[0])[:240]
    kind = "policy" if any(word in detail.lower() for word in ("policy", "safety", "mature")) else "invalid_request"
    return ImageGenerationProviderError(provider="civitai_image", kind=kind, detail=detail)


def _civitai_provider_error(response: httpx.Response) -> ImageGenerationProviderError:
    detail = ""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            detail = str(payload.get("title") or payload.get("detail") or payload.get("message") or "")
    except (ValueError, json.JSONDecodeError):
        pass
    raw_text = response.text.lower()
    if "insufficientbuzz" in raw_text or "insufficient buzz" in raw_text:
        # Do not persist arbitrary provider bodies. This stable provider code
        # is safe and lets the world layer distinguish an account quota from a
        # malformed prompt.
        detail = "insufficient_buzz"
    elif not detail:
        detail = f"http_{response.status_code}"
    normalized = detail.lower()
    if detail == "insufficient_buzz":
        kind = "quota"
    elif response.status_code in {408, 409, 429} or response.status_code >= 500:
        kind = "transient"
    elif any(word in normalized for word in ("policy", "safety", "mature", "content")):
        kind = "policy"
    else:
        kind = "invalid_request"
    return ImageGenerationProviderError(
        provider="civitai_image", kind=kind, status_code=response.status_code, detail=detail
    )


class ComfyUIImageGenerator:
    """Submit a user-provided ComfyUI API workflow with small, explicit substitutions.

    The workflow stays outside Python so a local LoRA, IP-Adapter, or FaceID graph
    remains editable in ComfyUI.  Use $PROMPT, $NEGATIVE_PROMPT, $LORA_PATH, and
    $REFERENCE_IMAGE_N placeholders in its string values.
    """

    def __init__(
        self,
        *,
        base_url: str,
        workflow_path: Path,
        lora_path: str | None = None,
        negative_prompt: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.workflow_path = workflow_path
        self.lora_path = lora_path or ""
        self.negative_prompt = negative_prompt
        self.transport = transport

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        if not self.workflow_path.is_file():
            raise FileNotFoundError(f"ComfyUI workflow was not found: {self.workflow_path}")
        try:
            workflow = json.loads(self.workflow_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"ComfyUI workflow is not valid JSON: {self.workflow_path}") from exc
        references = tuple(reference_images)
        substitutions = {
            "$PROMPT": prompt,
            "$NEGATIVE_PROMPT": self.negative_prompt,
            "$LORA_PATH": self.lora_path,
            "$SIZE": size,
            **{f"$REFERENCE_IMAGE_{index}": str(path) for index, path in enumerate(references, start=1)},
        }
        workflow = _replace_workflow_tokens(workflow, substitutions)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=30, trust_env=False, transport=self.transport) as client:
            queued = await client.post(f"{self.base_url}/prompt", json={"prompt": workflow})
            queued.raise_for_status()
            prompt_id = str(queued.json().get("prompt_id") or "")
            if not prompt_id:
                raise ValueError("ComfyUI did not return a prompt_id")
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                history = await client.get(f"{self.base_url}/history/{prompt_id}")
                history.raise_for_status()
                image = _comfy_output_image(history.json(), prompt_id)
                if image:
                    response = await client.get(
                        f"{self.base_url}/view",
                        params={"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")},
                    )
                    response.raise_for_status()
                    return _generated_from_bytes(output_path, response.content, prompt)
                await _sleep_briefly()
        raise TimeoutError(f"ComfyUI generation did not finish within 240 seconds: {prompt_id}")


class FallbackImageGenerator:
    """Prefer one backend but make a failed local render non-fatal when allowed."""

    def __init__(self, primary: ImageGenerator, fallback: ImageGenerator | None = None):
        self.primary = primary
        self.fallback = fallback

    async def generate(
        self,
        prompt: str,
        *,
        output_path: Path,
        size: str = "1024x1536",
        reference_images: Iterable[Path] = (),
    ) -> GeneratedImage:
        references = tuple(reference_images)
        try:
            return await self.primary.generate(
                prompt, output_path=output_path, size=size, reference_images=references
            )
        except Exception:
            if self.fallback is None:
                raise
            return await self.fallback.generate(
                prompt, output_path=output_path, size=size, reference_images=references
            )


class OpenAIImageQualityGate:
    """A compact visual check for face/hand failures before an image is delivered."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.proxy_url = proxy_url
        self.transport = transport

    async def assess(
        self,
        image_path: Path,
        *,
        prompt: str,
        reference_images: Iterable[Path] = (),
    ) -> ImageQualityResult:
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        primary_reference = next(iter(reference_images), None)
        reference_content: list[dict[str, object]] = []
        identity_instruction = ""
        if primary_reference and primary_reference.is_file():
            reference_encoded = base64.b64encode(primary_reference.read_bytes()).decode("ascii")
            reference_mime = mimetypes.guess_type(primary_reference.name)[0] or "image/png"
            reference_content = [
                {"type": "text", "text": "Identity reference image (not the requested output):"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{reference_mime};base64,{reference_encoded}"},
                },
            ]
            identity_instruction = " It must remain recognizably the same fictional character as the identity reference."
        request = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Review this fictional-character image for delivery. Return JSON only: "
                            '{"passed": boolean, "reason": string}. Pass only if the face is coherent, '
                            "hands are not visibly malformed, it contains no text/watermark, and it fits the request."
                            " When the request contains a 'Frozen world media shot plan', every listed "
                            "non-negotiable camera, pose, companion, and scene constraint is a delivery "
                            "requirement; reject an image that contradicts one."
                            " When it contains a 'Motion requirement', reject a visibly static result: a "
                            "front-facing posed stance, both arms hanging still, or no visible evidence of "
                            "the required step, interaction, observation, or candid exchange."
                            f"{identity_instruction} "
                            f"Request: {prompt[:1600]}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                    *reference_content,
                ],
            }],
        }
        async with _openai_client(
            timeout=45,
            proxy_url=self.proxy_url,
            transport=self.transport,
        ) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=request,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        decision = json.loads(content)
        return ImageQualityResult(bool(decision.get("passed")), str(decision.get("reason") or "unspecified"))


def _openai_client(
    *,
    timeout: float,
    proxy_url: str | None,
    transport: httpx.AsyncBaseTransport | None,
) -> httpx.AsyncClient:
    """Keep OpenAI routing explicit instead of inheriting stale global proxies."""
    options: dict[str, Any] = {"timeout": timeout, "trust_env": False}
    if proxy_url:
        options["proxy"] = proxy_url
    if transport is not None:
        options["transport"] = transport
    return httpx.AsyncClient(**options)


async def render_character_image(
    generator: Any,
    prompt: str,
    *,
    output_path: Path,
    reference_images: Iterable[Path] = (),
    size: str = "1024x1536",
    quality: str = "medium",
    quality_gate: ImageQualityGate | None = None,
) -> GeneratedImage:
    """Call new renderers with references while keeping existing test doubles valid."""
    references = tuple(reference_images)
    active_prompt = prompt
    max_attempts = 2 if quality_gate else 1
    for attempt in range(max_attempts):
        try:
            generated = await generator.generate(
                active_prompt,
                output_path=output_path,
                size=size,
                quality=quality,
                reference_images=references,
            )
        except TypeError as exc:
            if not any(name in str(exc) for name in ("reference_images", "quality")):
                raise
            generated = await generator.generate(active_prompt, output_path=output_path, size=size)
        if quality_gate is None:
            return replace(generated, attempts=attempt + 1)
        try:
            assessment = await quality_gate.assess(
                generated.path,
                prompt=active_prompt,
                reference_images=references[:1],
            )
        except TypeError as exc:
            if "reference_images" not in str(exc):
                raise
            assessment = await quality_gate.assess(generated.path, prompt=active_prompt)
        except Exception:
            raise ImageQualityRejected("visual acceptance unavailable")
        if assessment.passed:
            return replace(generated, attempts=attempt + 1)
        if attempt + 1 == max_attempts:
            raise ImageQualityRejected(assessment.reason)
        active_prompt = (
            f"{prompt}\n\nCorrect the prior rejected render: {assessment.reason}. "
            "Keep the same fictional character and requested scene; repair only the rejected visual defects. "
            "Make the requested motion evidence visibly happen; do not substitute a front-facing posed stance "
            "or both arms hanging still."
        )
    raise AssertionError("image render loop exhausted unexpectedly")


def visual_reference_paths(
    visual_identity_path: Path | None,
    *,
    profile: str = "everyday_selfie",
    relationship_tier: str | None = None,
    scene_hint: str = "",
    max_references: int = 2,
) -> tuple[Path, ...]:
    if visual_identity_path is None or not visual_identity_path.is_file():
        return ()
    identity = load_visual_identity(str(visual_identity_path))
    assets = (
        identity.relationship_reference_assets(relationship_tier)
        if relationship_tier
        else identity.reference_assets(profile)
    )
    available = tuple(path for asset in assets if (path := Path(asset)).is_file())
    if not available or max_references <= 0:
        return ()
    # Keep the canonical identity anchor fixed and choose only one contextual
    # variation.  Sending every candidate costs more and lets conflicting
    # hairstyles/camera angles dilute the identity signal.
    anchor = available[0]
    if max_references == 1 or len(available) == 1:
        return (anchor,)
    seed = f"{profile}|{relationship_tier or ''}|{scene_hint}".encode("utf-8")
    variant = available[1:][int.from_bytes(sha256(seed).digest()[:4], "big") % (len(available) - 1)]
    return (anchor, variant)


def life_image_prompt(
    topic: str,
    *,
    kind: str = "life",
    profile: str = "everyday_selfie",
    relationship_tier: str | None = None,
    capture_mode: str = "handheld_selfie",
    visual_identity_path: Path | None = Path("configs/visual_identity.yaml"),
) -> str:
    style_tags = detect_style_tags(topic)
    if kind in {"selfie", "character_media"}:
        identity_block = ""
        capture_block = ""
        if visual_identity_path and visual_identity_path.exists():
            identity = load_visual_identity(str(visual_identity_path))
            capture_style = identity.capture_prompt(capture_mode)
            identity_block = "\n" + identity.prompt_block(
                relationship_tier=relationship_tier if profile == "relationship_private" else None,
                camera_style=capture_style,
            )
            capture_block = "\nCapture mode (must follow):\n" + capture_style
        privacy_line = (
            " It is a private, tender moment between established fictional partners; "
            "still fully clothed, non-explicit, and never pornographic."
            if profile == "relationship_private" and not relationship_tier
            else ""
        )
        camera_line = (
            "Create an original virtual-life selfie-style image of沈知栀 / Celia Shen, "
            if capture_mode == "handheld_selfie"
            else "Create an original virtual-life personal media photo of沈知栀 / Celia Shen, "
        )
        return (
            camera_line + "a gentle Chinese college student with shoulder-length dark hair and a subtle teal hairpin. "
            "It should feel like a tasteful fictional character life photo, not a real person's photo. "
            f"Moment/topic: {topic}. Style: {style_tags}. No text, no watermark.{privacy_line}"
            f"{capture_block}{identity_block}"
        )
    if kind == "food":
        return (
            "Create a cozy phone-photo style image of a small meal or snack a Chinese college student might share. "
            f"Moment/topic: {topic}. Style: {style_tags}. Natural lighting, realistic but clearly AI-generated, no text, no watermark."
        )
    return (
        "Create a cozy phone-photo style fictional life snapshot from a Chinese college student's day. "
        f"Moment/topic: {topic}. Style: {style_tags}. No text, no watermark."
    )


def _replace_workflow_tokens(value: object, substitutions: dict[str, str]) -> object:
    if isinstance(value, dict):
        return {key: _replace_workflow_tokens(item, substitutions) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_workflow_tokens(item, substitutions) for item in value]
    if isinstance(value, str):
        for token, replacement in substitutions.items():
            value = value.replace(token, replacement)
    return value


def _comfy_output_image(history: object, prompt_id: str) -> dict[str, str] | None:
    record = history.get(prompt_id) if isinstance(history, dict) else None
    outputs = record.get("outputs") if isinstance(record, dict) else None
    if not isinstance(outputs, dict):
        return None
    for node in outputs.values():
        images = node.get("images") if isinstance(node, dict) else None
        if isinstance(images, list) and images and isinstance(images[0], dict):
            first = images[0]
            if isinstance(first.get("filename"), str):
                return {key: str(value) for key, value in first.items() if key in {"filename", "subfolder", "type"}}
    return None


async def _sleep_briefly() -> None:
    import asyncio
    await asyncio.sleep(0.75)
