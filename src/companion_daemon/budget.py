from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from companion_daemon.db import CompanionStore
from companion_daemon.usage_metrics import (
    GPT_IMAGE_2_OUTPUT_USD,
    estimate_gpt_image_2_cost_usd,
)


# GPT Image 2 standard pricing as checked on 2026-08-18 against the OpenAI
# official pricing page and image-generation calculator.  This is a local
# planning estimate, not an invoice: the API may return a different token
# count for a particular edit request.
# Sources: https://developers.openai.com/api/docs/pricing
#          https://developers.openai.com/api/docs/guides/image-generation
_USD_TO_CNY = 7.2
_IMAGE_OUTPUT_USD = dict(GPT_IMAGE_2_OUTPUT_USD)

# Generation-side caps.  Delivery already has max 2/day and a 2h gap; those
# do not stop OpenAI spend.  These bounds keep paid renders aligned with a
# slot that can actually be sent, without choosing whether she shares.
DEFAULT_DAILY_IMAGE_LIMIT = 2
DEFAULT_IMAGE_MIN_GAP = timedelta(hours=2)
DEFAULT_MAX_IN_FLIGHT_RENDERS = 1
_TERMINAL_ACTION_STATES = frozenset(
    {"delivered", "failed", "unknown", "cancelled", "expired"}
)
_PAID_RENDER_KINDS = frozenset({"media_render", "media_repair"})
_DELIVERY_POLICY_ACTOR = "system:world-v2:media-delivery-policy"


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ModelBudgetReservation:
    allowed: bool
    reason: str
    reservation_id: str


@dataclass(frozen=True)
class UsageEstimate:
    kind: str
    cny: float


@dataclass(frozen=True)
class ImageGenerationOccupancy:
    """Ledger-visible paid-image pressure for one world at one clock."""

    paid_renders_today: int = 0
    in_flight_renders: int = 0
    undelivered_previews: int = 0
    deliveries_today: int = 0
    last_delivery_at: datetime | None = None


@dataclass(frozen=True)
class ImageGenerationAdmission:
    allowed: bool
    reason: str


def occupancy_from_media_projection(
    projection: object,
    *,
    logical_time: datetime,
    policy_actor: str = _DELIVERY_POLICY_ACTOR,
) -> ImageGenerationOccupancy:
    """Count paid renders, in-flight Actions, and delivery slots from a projection.

    Missing attributes are empty: unit tests and non-media projections stay
    admitted.  The 24h window matches media auto-delivery, not calendar UTC.
    """

    window_start = logical_time - timedelta(days=1)
    actions = tuple(getattr(projection, "actions", ()) or ())
    artifacts = tuple(getattr(projection, "media_artifacts", ()) or ())
    previews = tuple(getattr(projection, "media_previews", ()) or ())
    deliveries = tuple(getattr(projection, "media_deliveries", ()) or ())
    approvals = tuple(getattr(projection, "media_delivery_approvals", ()) or ())
    render_actions = {
        getattr(action, "action_id", ""): action
        for action in actions
        if getattr(action, "kind", None) in _PAID_RENDER_KINDS
    }
    paid_today = 0
    for artifact in artifacts:
        action = render_actions.get(getattr(artifact, "render_action_id", ""), None)
        when = getattr(action, "logical_time", None) if action is not None else None
        if isinstance(when, datetime) and window_start <= when <= logical_time:
            paid_today += 1
    in_flight = sum(
        1
        for action in render_actions.values()
        if getattr(action, "state", None) not in _TERMINAL_ACTION_STATES
    )
    delivered_plan_ids = {
        getattr(item, "plan_id", None) for item in deliveries if getattr(item, "plan_id", None)
    }
    undelivered = sum(
        1
        for preview in previews
        if getattr(preview, "plan_id", None) not in delivered_plan_ids
    )
    recent_approvals = tuple(
        item
        for item in approvals
        if getattr(item, "operator_ref", None) == policy_actor
        and isinstance(getattr(item, "approved_at", None), datetime)
        and window_start <= item.approved_at <= logical_time
    )
    last_delivery_at = max(
        (item.approved_at for item in recent_approvals),
        default=None,
    )
    return ImageGenerationOccupancy(
        paid_renders_today=paid_today,
        in_flight_renders=in_flight,
        undelivered_previews=undelivered,
        deliveries_today=len(
            {getattr(item, "approval_id", None) for item in recent_approvals}
        ),
        last_delivery_at=last_delivery_at,
    )


def admit_image_generation(
    *,
    occupancy: ImageGenerationOccupancy,
    logical_time: datetime,
    estimated_cny: float = 0.0,
    daily_cny_used: float = 0.0,
    daily_cny_limit: float | None = None,
    max_generations_per_day: int = DEFAULT_DAILY_IMAGE_LIMIT,
    max_in_flight_renders: int = DEFAULT_MAX_IN_FLIGHT_RENDERS,
    max_deliveries_per_day: int = DEFAULT_DAILY_IMAGE_LIMIT,
    min_gap: timedelta = DEFAULT_IMAGE_MIN_GAP,
) -> ImageGenerationAdmission:
    """Hard spend boundary: do not pay for a photo that cannot be delivered.

    This does not choose whether she shares.  It only withholds the paid
    provider call until a delivery slot is actually open.
    """

    if occupancy.in_flight_renders >= max_in_flight_renders:
        return ImageGenerationAdmission(False, "in_flight_generation")
    if occupancy.undelivered_previews >= 1:
        return ImageGenerationAdmission(False, "undelivered_preview")
    if occupancy.paid_renders_today >= max_generations_per_day:
        return ImageGenerationAdmission(False, "daily_generation_limit")
    if occupancy.deliveries_today >= max_deliveries_per_day:
        return ImageGenerationAdmission(False, "delivery_daily_limit")
    if (
        occupancy.last_delivery_at is not None
        and logical_time - occupancy.last_delivery_at < min_gap
    ):
        return ImageGenerationAdmission(False, "delivery_min_gap")
    if (
        daily_cny_limit is not None
        and daily_cny_used + max(0.0, estimated_cny) > daily_cny_limit
    ):
        return ImageGenerationAdmission(False, "daily_budget_exceeded")
    return ImageGenerationAdmission(True, "ok")


def image_render_estimate(
    *,
    reference_count: int,
    size: str = "1024x1536",
    quality: str = "medium",
    attempts: int = 1,
) -> UsageEstimate:
    """Return a worst-case local estimate for one planned identity render.

    The planner owns this estimate so callers cannot accidentally budget a
    three-reference portrait as if it were a bare low-quality square render.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least one")
    if (size, quality) not in _IMAGE_OUTPUT_USD:
        raise ValueError(f"unsupported image render plan: {size} / {quality}")
    usd, _version = estimate_gpt_image_2_cost_usd(
        size=size,
        quality=quality,
        reference_count=reference_count,
    )
    return UsageEstimate(
        "image_generation",
        round(usd * _USD_TO_CNY * attempts, 4),
    )


class BudgetGate:
    def __init__(
        self,
        store: CompanionStore,
        *,
        monthly_budget_cny: float,
        daily_budget_cny: float,
        soft_daily_budget_cny: float,
        monthly_image_limit: int,
        monthly_vision_limit: int,
        monthly_audio_limit: int,
        daily_image_limit: int = DEFAULT_DAILY_IMAGE_LIMIT,
        image_min_gap: timedelta = DEFAULT_IMAGE_MIN_GAP,
    ):
        self.store = store
        self.monthly_budget_cny = monthly_budget_cny
        self.daily_budget_cny = daily_budget_cny
        self.soft_daily_budget_cny = soft_daily_budget_cny
        self.monthly_image_limit = monthly_image_limit
        self.monthly_vision_limit = monthly_vision_limit
        self.monthly_audio_limit = monthly_audio_limit
        self.daily_image_limit = daily_image_limit
        self.image_min_gap = image_min_gap

    def check(self, estimate: UsageEstimate, *, automatic: bool) -> BudgetDecision:
        now = datetime.now(UTC)
        daily = self.store.usage_total("day", now)
        monthly = self.store.usage_total("month", now)
        kind_monthly = self.store.usage_count(estimate.kind, "month", now)

        if monthly + estimate.cny > self.monthly_budget_cny:
            return BudgetDecision(False, "monthly_budget_exceeded")
        if daily + estimate.cny > self.daily_budget_cny:
            return BudgetDecision(False, "daily_budget_exceeded")
        if automatic and daily + estimate.cny > self.soft_daily_budget_cny:
            return BudgetDecision(False, "soft_daily_budget_requires_manual")
        if estimate.kind == "image_generation" and kind_monthly >= self.monthly_image_limit:
            return BudgetDecision(False, "monthly_image_limit_exceeded")
        if estimate.kind == "image_generation":
            kind_daily = self.store.usage_count(estimate.kind, "day", now)
            if kind_daily >= self.daily_image_limit:
                return BudgetDecision(False, "daily_image_limit_exceeded")
            last_at = _latest_usage_at(self.store, estimate.kind)
            if last_at is not None and now - last_at < self.image_min_gap:
                return BudgetDecision(False, "image_generation_min_gap")
        if estimate.kind == "vision" and kind_monthly >= self.monthly_vision_limit:
            return BudgetDecision(False, "monthly_vision_limit_exceeded")
        if estimate.kind == "transcription" and kind_monthly >= self.monthly_audio_limit:
            return BudgetDecision(False, "monthly_audio_limit_exceeded")
        return BudgetDecision(True, "ok")

    def record(self, estimate: UsageEstimate, *, note: str = "") -> None:
        self.store.record_usage(estimate.kind, estimate.cny, note=note)

    def remaining_model_budget_cny(
        self, *, automatic: bool, now: datetime | None = None
    ) -> float:
        """Return remaining spend after actual token-priced model usage.

        Legacy fixed-cost usage and provider token usage live in separate
        ledgers, so both are included without recording a model call twice.
        """
        observed_at = now or datetime.now(UTC)
        daily_fixed = self.store.usage_total("day", observed_at)
        monthly_fixed = self.store.usage_total("month", observed_at)
        daily_model = float(
            self.store.model_usage_report("day", observed_at)["total"][
                "estimated_cost_cny"
            ]
        )
        monthly_model = float(
            self.store.model_usage_report("month", observed_at)["total"][
                "estimated_cost_cny"
            ]
        )
        limits = [
            self.daily_budget_cny
            - daily_fixed
            - daily_model
            - self.store.pending_model_budget_reservation_total("day", observed_at),
            self.monthly_budget_cny
            - monthly_fixed
            - monthly_model
            - self.store.pending_model_budget_reservation_total("month", observed_at),
        ]
        if automatic:
            limits.append(
                self.soft_daily_budget_cny
                - daily_fixed
                - daily_model
                - self.store.pending_model_budget_reservation_total("day", observed_at)
            )
        return max(0.0, min(limits))

    def reserve_model_call(
        self,
        *,
        reservation_id: str,
        estimated_cny: float,
        automatic: bool,
        now: datetime | None = None,
        lease_seconds: int = 300,
    ) -> ModelBudgetReservation:
        """Reserve a priced provider call before it reaches the network."""
        allowed, reason = self.store.reserve_model_budget(
            reservation_id=reservation_id,
            estimated_cny=estimated_cny,
            automatic=automatic,
            monthly_budget_cny=self.monthly_budget_cny,
            daily_budget_cny=self.daily_budget_cny,
            soft_daily_budget_cny=self.soft_daily_budget_cny,
            now=now or datetime.now(UTC),
            lease_seconds=lease_seconds,
        )
        return ModelBudgetReservation(allowed, reason, reservation_id)

    def start_model_call(
        self,
        reservation_id: str,
        *,
        now: datetime | None = None,
        lease_seconds: int = 300,
    ) -> bool:
        """Durably mark a preflight reservation immediately before provider I/O."""
        return self.store.start_model_budget_reservation(
            reservation_id,
            now=now or datetime.now(UTC),
            lease_seconds=lease_seconds,
        )

    def finalize_model_call(
        self,
        reservation_id: str,
        *,
        request_emitted: bool,
        usage_persisted: bool,
    ) -> None:
        """Release only calls proven not to have reached the provider."""
        self.store.finalize_model_budget_reservation(
            reservation_id,
            request_emitted=request_emitted,
            usage_persisted=usage_persisted,
        )

    def release_model_call(self, reservation_id: str) -> None:
        self.store.release_model_budget_reservation(reservation_id)


def _latest_usage_at(store: CompanionStore, kind: str) -> datetime | None:
    """Read the newest usage_events timestamp without adding a db.py API."""

    with store.connect() as conn:
        row = conn.execute(
            "select created_at from usage_events where kind = ? order by id desc limit 1",
            (kind,),
        ).fetchone()
    if row is None:
        return None
    raw = row["created_at"] if not isinstance(row, tuple) else row[0]
    if not raw:
        return None
    text = str(raw)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


ESTIMATES = {
    "vision": UsageEstimate("vision", 0.03),
    "transcription": UsageEstimate("transcription", 0.05),
    # Compatibility default for callers that have not yet built a render plan.
    "image_generation": image_render_estimate(reference_count=2),
    "memory_maintenance": UsageEstimate("memory_maintenance", 0.02),
    "proactive_decision": UsageEstimate("proactive_decision", 0.01),
    "life_event": UsageEstimate("life_event", 0.02),
}
