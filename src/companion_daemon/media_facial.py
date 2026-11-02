"""Recipient-aware facial display and visible still-frame performance contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path

import yaml


FACIAL_DISPLAY_VERSION = "facial-display-strategy-v1"
FACIAL_MICRO_VERSION_V1 = "facial-micro-performance-v1"
FACIAL_MICRO_VERSION = "facial-micro-performance-v2"
FACIAL_CATALOG_VERSION = "media-facial-catalog-v1"
DEFAULT_FACIAL_CATALOG = Path("configs/media_facial_performance_templates.yaml")

DISPLAY_FAMILIES = {
    "present_and_available",
    "warm_connection",
    "amusement_leaking",
    "deliberate_cuteness",
    "mock_defiance",
    "comic_self_exposure",
    "proud_display",
    "consultative_check",
    "frustrated_complaint",
    "embarrassed_repair",
    "tired_access",
    "vulnerable_disclosure",
    "tender_private",
    "desire_direct",
    "desire_withheld",
    "neutral_evidence",
}
BROW_ACTIONS = {
    "neutral",
    "settled",
    "soft_lift",
    "bilateral_lift",
    "one_brow_lift",
    "brief_question",
    "slight_inward_draw",
    "slight_knit",
    "relaxed_lowered",
    "not_visible",
}
EYE_APERTURES = {
    "natural",
    "slightly_widened",
    "wide_playful",
    "smile_narrowed",
    "heavy_lidded",
    "relaxed_heavy",
    "brief_squint",
    "brief_squeeze",
    "single_wink",
    "not_visible",
}
GAZE_TARGETS = {
    "lens",
    "screen",
    "screen_preview",
    "primary_evidence",
    "off_frame",
    "environment",
    "companion",
    "reflection",
    "not_visible",
}
GAZE_SEQUENCES = {
    "held_lens",
    "just_returned_to_lens",
    "about_to_look_away",
    "caught_off_frame",
    "evidence_then_recipient",
    "screen_then_lens",
    "companion_then_camera",
    "lens_then_away",
    "no_face",
}
NOSE_CHEEK_ACTIONS = {
    "relaxed",
    "cheek_lift",
    "small_nose_scrunch",
    "cheek_puff",
    "one_cheek_tension",
    "asymmetric_cheek",
    "flushed_relaxation",
    "not_visible",
}
MOUTH_ACTIONS = {
    "relaxed_closed",
    "soft_parted",
    "small_smile",
    "suppressed_smile",
    "asymmetric_half_smile",
    "subtle_pout",
    "lightly_pressed",
    "mid_speech",
    "open_laugh",
    "breath_recovery",
    "not_visible",
}
FACIAL_ASYMMETRIES = {
    "balanced",
    "subtle_left",
    "subtle_right",
    "dynamic_natural",
    "momentary_irregular",
    "not_visible",
}
DISPLAY_INTENSITIES = {"trace", "low", "subtle", "medium", "clear", "high", "heightened"}
PERFORMANCE_AUTHORSHIPS = {
    "spontaneous",
    "recipient_aware",
    "deliberately_performed",
    "photographer_prompted",
    "unperformed_capture",
    "responsive_candid",
    "selfie_micro_pose",
    "playfully_performed",
    "polished_portrait_performance",
    "private_recipient_performance",
    "not_visible",
}
TEMPORAL_PHASES = {
    "preparation",
    "onset",
    "held_beat",
    "leaking",
    "apex",
    "release",
    "after_reaction",
    "not_visible",
}
FACIAL_ENERGIES = {"low", "contained", "lively", "breathless", "held", "recovering", "not_visible"}


@dataclass(frozen=True)
class FacialDisplayStrategy:
    strategy_family: str
    recipient_effect: str
    performance_intent: str
    catalog_version: str
    contract_signature: str
    version: str = FACIAL_DISPLAY_VERSION

    @classmethod
    def create(
        cls,
        *,
        strategy_family: str,
        recipient_effect: str,
        performance_intent: str,
        catalog_version: str = FACIAL_CATALOG_VERSION,
    ) -> "FacialDisplayStrategy":
        payload = (strategy_family, recipient_effect, performance_intent, catalog_version)
        result = cls(*payload, contract_signature=_signature(payload))
        result._validate()
        return result

    def _validate(self) -> None:
        if (
            self.version != FACIAL_DISPLAY_VERSION
            or self.catalog_version != FACIAL_CATALOG_VERSION
            or self.strategy_family not in DISPLAY_FAMILIES
        ):
            raise ValueError("invalid facial display strategy")
        if not self.recipient_effect or not self.performance_intent:
            raise ValueError("incomplete facial display strategy")
        if self.contract_signature != _signature(
            (
                self.strategy_family,
                self.recipient_effect,
                self.performance_intent,
                self.catalog_version,
            )
        ):
            raise ValueError("invalid facial display contract")

    def to_payload(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_payload(cls, value: object) -> "FacialDisplayStrategy":
        if not isinstance(value, dict):
            raise ValueError("facial display strategy must be an object")
        result = cls(
            **{
                name: str(value.get(name) or "")
                for name in (
                    "strategy_family",
                    "recipient_effect",
                    "performance_intent",
                    "catalog_version",
                    "contract_signature",
                    "version",
                )
            }
        )
        result._validate()
        return result


@dataclass(frozen=True)
class FacialMicroPerformance:
    brow_action: str
    eye_aperture: str
    gaze_target: str
    gaze_sequence: str
    nose_cheek_action: str
    mouth_action: str
    facial_asymmetry: str
    display_intensity: str
    performance_authorship: str
    temporal_phase: str
    facial_energy: str
    recipe_id: str
    catalog_version: str
    contract_signature: str
    version: str = FACIAL_MICRO_VERSION
    expression_beat_id: str = "legacy"
    visible_evidence: tuple[str, ...] = ()

    @classmethod
    def create(cls, **values: str) -> "FacialMicroPerformance":
        names = (
            "brow_action",
            "eye_aperture",
            "gaze_target",
            "gaze_sequence",
            "nose_cheek_action",
            "mouth_action",
            "facial_asymmetry",
            "display_intensity",
            "performance_authorship",
            "temporal_phase",
            "facial_energy",
        )
        recipe_id = str(values.get("recipe_id") or "custom")
        catalog_version = str(values.get("catalog_version") or FACIAL_CATALOG_VERSION)
        version = str(values.get("version") or FACIAL_MICRO_VERSION)
        expression_beat_id = str(values.get("expression_beat_id") or "")
        visible_evidence = tuple(
            str(item).strip() for item in values.get("visible_evidence", ()) if str(item).strip()
        )
        payload = (*tuple(values[name] for name in names), recipe_id, catalog_version)
        if version == FACIAL_MICRO_VERSION:
            if not expression_beat_id or not visible_evidence:
                raise ValueError("facial micro-performance v2 requires a visible expression beat")
            signature_payload = (*payload, expression_beat_id, visible_evidence)
        elif version == FACIAL_MICRO_VERSION_V1:
            signature_payload = payload
        else:
            raise ValueError("unsupported facial micro-performance version")
        result = cls(
            *payload,
            contract_signature=_signature(signature_payload),
            version=version,
            expression_beat_id=expression_beat_id or "legacy",
            visible_evidence=visible_evidence,
        )
        result._validate()
        return result

    def _validate(self) -> None:
        enums = (
            (self.brow_action, BROW_ACTIONS),
            (self.eye_aperture, EYE_APERTURES),
            (self.gaze_target, GAZE_TARGETS),
            (self.gaze_sequence, GAZE_SEQUENCES),
            (self.nose_cheek_action, NOSE_CHEEK_ACTIONS),
            (self.mouth_action, MOUTH_ACTIONS),
            (self.facial_asymmetry, FACIAL_ASYMMETRIES),
            (self.display_intensity, DISPLAY_INTENSITIES),
            (self.performance_authorship, PERFORMANCE_AUTHORSHIPS),
            (self.temporal_phase, TEMPORAL_PHASES),
            (self.facial_energy, FACIAL_ENERGIES),
        )
        if (
            self.version not in {FACIAL_MICRO_VERSION_V1, FACIAL_MICRO_VERSION}
            or self.catalog_version != FACIAL_CATALOG_VERSION
            or not self.recipe_id
            or any(value not in allowed for value, allowed in enums)
        ):
            raise ValueError("invalid facial micro-performance")
        payload = (
            self.brow_action,
            self.eye_aperture,
            self.gaze_target,
            self.gaze_sequence,
            self.nose_cheek_action,
            self.mouth_action,
            self.facial_asymmetry,
            self.display_intensity,
            self.performance_authorship,
            self.temporal_phase,
            self.facial_energy,
            self.recipe_id,
            self.catalog_version,
        )
        signature_payload = (
            (*payload, self.expression_beat_id, self.visible_evidence)
            if self.version == FACIAL_MICRO_VERSION
            else payload
        )
        if (
            self.version == FACIAL_MICRO_VERSION
            and (not self.expression_beat_id or not self.visible_evidence)
        ) or self.contract_signature != _signature(signature_payload):
            raise ValueError("invalid facial micro-performance contract")

    def to_payload(self) -> dict[str, object]:
        value: dict[str, object] = asdict(self)
        value["visible_evidence"] = list(self.visible_evidence)
        if self.version == FACIAL_MICRO_VERSION_V1:
            value.pop("expression_beat_id", None)
            value.pop("visible_evidence", None)
        return value

    @classmethod
    def from_payload(cls, value: object) -> "FacialMicroPerformance":
        if not isinstance(value, dict):
            raise ValueError("facial micro-performance must be an object")
        names = (
            "brow_action",
            "eye_aperture",
            "gaze_target",
            "gaze_sequence",
            "nose_cheek_action",
            "mouth_action",
            "facial_asymmetry",
            "display_intensity",
            "performance_authorship",
            "temporal_phase",
            "facial_energy",
            "recipe_id",
            "catalog_version",
            "contract_signature",
            "version",
        )
        version = str(value.get("version") or FACIAL_MICRO_VERSION_V1)
        result = cls(
            **{name: str(value.get(name) or "") for name in names},
            expression_beat_id=(
                str(value.get("expression_beat_id") or "")
                if version == FACIAL_MICRO_VERSION
                else "legacy"
            ),
            visible_evidence=(
                tuple(
                    str(item).strip()
                    for item in value.get("visible_evidence", [])
                    if str(item).strip()
                )
                if version == FACIAL_MICRO_VERSION
                else ()
            ),
        )
        result._validate()
        return result


def _micro(
    brow_action: str,
    eye_aperture: str,
    gaze_target: str,
    gaze_sequence: str,
    nose_cheek_action: str,
    mouth_action: str,
    facial_asymmetry: str,
    display_intensity: str,
    performance_authorship: str,
    temporal_phase: str,
    facial_energy: str,
) -> dict[str, str]:
    return locals()


_MICRO_ACTION_FIELDS = (
    "brow_action",
    "eye_aperture",
    "gaze_target",
    "gaze_sequence",
    "nose_cheek_action",
    "mouth_action",
    "facial_asymmetry",
    "display_intensity",
    "performance_authorship",
    "temporal_phase",
    "facial_energy",
)


def choose_facial_contract(
    *,
    stable_seed: str,
    engagement_tactic: str,
    attraction_mechanism: str | None,
    capture_mode: str = "character_front_camera",
    face_visible: bool = True,
    catalog_path: Path = DEFAULT_FACIAL_CATALOG,
) -> tuple[FacialDisplayStrategy, FacialMicroPerformance]:
    tactic_affinities, attraction_affinities, expression_beats, micro_recipes = (
        _load_affinity_catalog(catalog_path)
    )
    if not face_visible:
        family = "neutral_evidence"
        micro = FacialMicroPerformance.create(
            **_micro(
                "not_visible",
                "not_visible",
                "not_visible",
                "no_face",
                "not_visible",
                "not_visible",
                "not_visible",
                "trace",
                "not_visible",
                "not_visible",
                "not_visible",
            ),
            recipe_id="neutral_evidence:no_face",
            expression_beat_id="no_face",
            visible_evidence=("the face is out of frame for this evidence-led photo",),
        )
        return FacialDisplayStrategy.create(
            strategy_family=family,
            recipient_effect="keep attention on grounded evidence",
            performance_intent="face is not visible",
        ), micro
    families = list(tactic_affinities.get(engagement_tactic, ("present_and_available",)))
    if attraction_mechanism in attraction_affinities:
        families = list(attraction_affinities[attraction_mechanism])
    family = _pick(stable_seed + ":family", tuple(families))
    compatible = tuple(
        item
        for item in micro_recipes[family]
        if _capture_compatible(item, capture_mode=capture_mode)
    )
    if not compatible:
        compatible = micro_recipes[family]
    if capture_mode == "character_front_camera" and engagement_tactic == "presence":
        unheld = tuple(item for item in compatible if item.get("temporal_phase") != "held_beat")
        if unheld:
            compatible = unheld
    beat_id, visible_evidence, beat_recipes = _choose_expression_beat(
        stable_seed=stable_seed,
        family=family,
        compatible_recipes=compatible,
        configured_beats=expression_beats,
    )
    recipe = _pick_object(stable_seed + ":micro", beat_recipes)
    recipe_id = str(recipe["id"])
    effect = {
        "desire_direct": "make attraction unmistakably recipient-directed",
        "desire_withheld": "leave the recipient aware that attention is being held back",
        "deliberate_cuteness": "invite a playful affectionate response",
        "mock_defiance": "invite teasing with a wry 'are you seeing this?' challenge",
        "amusement_leaking": "let a real reaction escape before composure returns",
        "comic_self_exposure": "share a vivid, self-deprecating '囧 / look at the awkward mess I am in' reaction to make the recipient laugh or commiserate",
        "frustrated_complaint": "vent everyday exasperation ('好囧 / 无语 / 救命怎么这样') directly to the recipient so they commiserate",
        "tired_access": "share an unguarded 'exhausted / drained right now' check-in seeking quiet comfort or validation",
        "embarrassed_repair": "admit an awkward little moment with a shy, sheepish reaction",
        "vulnerable_disclosure": "offer honest, unguarded emotional access seeking care from the recipient",
        "proud_display": "show off a small everyday win waiting for the recipient to notice and praise it",
        "consultative_check": "ask the recipient for their real opinion with a questioning look",
        "warm_connection": "share warm everyday closeness with the recipient",
        "present_and_available": "check in casually so the recipient feels included in her current moment",
        "neutral_evidence": "let the surrounding scene or object carry the message without theatrical posing",
    }.get(family, "make the intended social bid legible without overacting")
    return (
        FacialDisplayStrategy.create(
            strategy_family=family,
            recipient_effect=effect,
            performance_intent="a coherent still-frame social performance, not an inferred inner emotion",
        ),
        FacialMicroPerformance.create(
            **{name: recipe[name] for name in _MICRO_ACTION_FIELDS},
            recipe_id=recipe_id,
            expression_beat_id=beat_id,
            visible_evidence=visible_evidence,
        ),
    )


def _capture_compatible(recipe: dict[str, str], *, capture_mode: str) -> bool:
    authorship = recipe["performance_authorship"]
    gaze = recipe["gaze_target"]
    sequence = recipe["gaze_sequence"]
    if capture_mode == "character_front_camera":
        return (
            authorship not in {"responsive_candid", "photographer_prompted", "unperformed_capture"}
            and gaze != "companion"
            and sequence != "companion_then_camera"
        )
    if capture_mode == "mirror":
        return gaze not in {"companion", "screen_preview"} and sequence not in {
            "companion_then_camera",
            "screen_then_lens",
        }
    if capture_mode == "timer_fixed":
        return gaze not in {"screen", "screen_preview", "companion"} and sequence not in {
            "screen_then_lens",
            "companion_then_camera",
        }
    if capture_mode in {"known_companion", "external_sender", "requested_helper"}:
        return (
            authorship not in {"selfie_micro_pose"}
            and gaze not in {"screen", "screen_preview"}
            and sequence != "screen_then_lens"
        )
    return True


def _choose_expression_beat(
    *,
    stable_seed: str,
    family: str,
    compatible_recipes: tuple[dict[str, str], ...],
    configured_beats: dict[str, tuple[dict[str, object], ...]],
) -> tuple[str, tuple[str, ...], tuple[dict[str, str], ...]]:
    """Choose a whole visible beat, then a compatible face recipe.

    This deliberately keeps the renderer out of the business of assembling a
    nose, mouth, gaze and temporal claim independently.  A catalogue beat is
    only eligible if an already-valid still-frame recipe can visibly support it.
    """

    eligible: list[tuple[str, tuple[str, ...], tuple[dict[str, str], ...]]] = []
    for beat in configured_beats.get(family, ()):
        required = beat["requires"]
        matching = tuple(
            recipe
            for recipe in compatible_recipes
            if all(recipe.get(axis) == expected for axis, expected in required.items())
        )
        if matching:
            eligible.append((str(beat["id"]), tuple(beat["visible_evidence"]), matching))
    if not eligible:
        return (
            f"{family}:coherent_visible_beat",
            (
                "the visible face reads as one coherent captured reaction, not a default polite smile",
            ),
            compatible_recipes,
        )
    return _pick_object(stable_seed + ":expression-beat", tuple(eligible))


@lru_cache(maxsize=8)
def _load_affinity_catalog(
    path: Path,
) -> tuple[
    dict[str, tuple[str, ...]],
    dict[str, tuple[str, ...]],
    dict[str, tuple[dict[str, object], ...]],
    dict[str, tuple[dict[str, str], ...]],
]:
    """Load the extensible semantic matrix and complete visible performances."""

    if not path.is_file():
        if path != DEFAULT_FACIAL_CATALOG and DEFAULT_FACIAL_CATALOG.is_file():
            return _load_affinity_catalog(DEFAULT_FACIAL_CATALOG)
        raise FileNotFoundError(f"facial performance catalog not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("schema_version") != FACIAL_CATALOG_VERSION:
        raise ValueError("unsupported facial performance catalog")

    def matrix(name: str) -> dict[str, tuple[str, ...]]:
        values = raw.get(name)
        if not isinstance(values, dict):
            raise ValueError(f"missing facial catalog matrix: {name}")
        result: dict[str, tuple[str, ...]] = {}
        for key, families in values.items():
            if not isinstance(key, str) or not isinstance(families, list) or not families:
                raise ValueError(f"invalid facial catalog matrix row: {name}")
            frozen = tuple(str(item) for item in families)
            if any(item not in DISPLAY_FAMILIES for item in frozen):
                raise ValueError(f"unknown facial family in catalog: {name}")
            result[key] = frozen
        return result

    axes = raw.get("visible_action_axes")
    expected_axes = {
        "brow_action": BROW_ACTIONS,
        "eye_aperture": EYE_APERTURES,
        "gaze_target": GAZE_TARGETS,
        "gaze_sequence": GAZE_SEQUENCES,
        "nose_cheek_action": NOSE_CHEEK_ACTIONS,
        "mouth_action": MOUTH_ACTIONS,
        "facial_asymmetry": FACIAL_ASYMMETRIES,
        "display_intensity": DISPLAY_INTENSITIES,
        "performance_authorship": PERFORMANCE_AUTHORSHIPS,
        "temporal_phase": TEMPORAL_PHASES,
        "facial_energy": FACIAL_ENERGIES,
    }
    if not isinstance(axes, dict) or any(
        set(str(item) for item in axes.get(name, [])) != allowed
        for name, allowed in expected_axes.items()
    ):
        raise ValueError("facial catalog visible-action axes do not match runtime contract")
    raw_beats = raw.get("expression_beats")
    if raw_beats is None:
        _, _, default_beats, default_recipes = _load_affinity_catalog(DEFAULT_FACIAL_CATALOG)
        return (
            matrix("tactic_affinities"),
            matrix("attraction_affinities"),
            default_beats,
            default_recipes,
        )
    if not isinstance(raw_beats, dict):
        raise ValueError("invalid facial expression beats catalog")
    beats: dict[str, tuple[dict[str, object], ...]] = {}
    for family, values in raw_beats.items():
        if family not in DISPLAY_FAMILIES or not isinstance(values, list) or not values:
            raise ValueError("invalid facial expression beat family")
        frozen: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, dict) or not str(value.get("id") or "").strip():
                raise ValueError("invalid facial expression beat")
            evidence = tuple(
                str(item).strip() for item in value.get("visible_evidence", []) if str(item).strip()
            )
            required = value.get("requires")
            if not evidence or not isinstance(required, dict) or not required:
                raise ValueError("incomplete facial expression beat")
            if any(
                axis not in expected_axes or str(expected) not in expected_axes[axis]
                for axis, expected in required.items()
            ):
                raise ValueError("facial expression beat has unknown visible action")
            frozen.append(
                {
                    "id": str(value["id"]),
                    "requires": {str(axis): str(expected) for axis, expected in required.items()},
                    "visible_evidence": evidence,
                }
            )
        beats[family] = tuple(frozen)
    if set(beats) != DISPLAY_FAMILIES:
        raise ValueError("facial expression beat catalog does not cover display families")
    raw_recipes = raw.get("micro_recipes")
    if raw_recipes is None:
        _, _, _, default_recipes = _load_affinity_catalog(DEFAULT_FACIAL_CATALOG)
        return (
            matrix("tactic_affinities"),
            matrix("attraction_affinities"),
            beats,
            default_recipes,
        )
    if not isinstance(raw_recipes, dict):
        raise ValueError("invalid facial micro recipe catalog")
    recipes: dict[str, tuple[dict[str, str], ...]] = {}
    for family, values in raw_recipes.items():
        if family not in DISPLAY_FAMILIES or not isinstance(values, list) or not values:
            raise ValueError("invalid facial micro recipe family")
        frozen: list[dict[str, str]] = []
        ids: set[str] = set()
        for value in values:
            if not isinstance(value, dict) or not str(value.get("id") or "").strip():
                raise ValueError("invalid facial micro recipe")
            recipe_id = str(value["id"]).strip()
            axes_value = value.get("axes")
            if recipe_id in ids or not isinstance(axes_value, dict):
                raise ValueError("invalid facial micro recipe")
            if set(axes_value) != set(_MICRO_ACTION_FIELDS):
                raise ValueError("facial micro recipe has incomplete visible actions")
            if any(
                str(axes_value[name]) not in expected_axes[name] for name in _MICRO_ACTION_FIELDS
            ):
                raise ValueError("facial micro recipe has unknown visible action")
            ids.add(recipe_id)
            frozen.append(
                {"id": recipe_id, **{name: str(axes_value[name]) for name in _MICRO_ACTION_FIELDS}}
            )
        recipes[family] = tuple(frozen)
    if set(recipes) != DISPLAY_FAMILIES:
        raise ValueError("facial micro recipe catalog does not cover display families")
    return matrix("tactic_affinities"), matrix("attraction_affinities"), beats, recipes


def facial_prompt_block(strategy: FacialDisplayStrategy, micro: FacialMicroPerformance) -> str:
    return (
        "Facial Display Strategy (social meaning for this recipient):\n"
        f"- family: {strategy.strategy_family}; intended recipient effect: {strategy.recipient_effect}; "
        f"performance intent: {strategy.performance_intent}\n"
        "Facial Micro-Performance (visible actions in this single captured beat, not a diagnosis or a multi-frame claim):\n"
        f"- brow: {micro.brow_action}; eye aperture: {micro.eye_aperture}; current gaze target: {micro.gaze_target}; "
        f"captured gaze beat: {micro.gaze_sequence}\n"
        f"- nose/cheek: {micro.nose_cheek_action}; mouth: {micro.mouth_action}; asymmetry: {micro.facial_asymmetry}\n"
        f"- intensity: {micro.display_intensity}; authorship: {micro.performance_authorship}; "
        f"temporal phase: {micro.temporal_phase}; energy: {micro.facial_energy}. Render these as one coherent, "
        "immediately readable conversational face talking to a close friend (e.g., a real '囧' face, helpless sigh, "
        "or candid reaction)—not as independent sliders, NEVER defaulting to a polite smile, and NEVER flattening "
        "the expression into a calm, aloof, or melancholic art-photography portrait ('文艺写真/纯艺术照'). "
        "Preserve her 3D bone structure, straight nose bridge, and small narrow mouth proportions without pinching "
        "the nose bridge or carving deep nasolabial folds.\n"
        f"- frozen expression beat {micro.expression_beat_id}: {'; '.join(micro.visible_evidence)}"
    )


def _pick(seed: str, values: tuple[str, ...]) -> str:
    return values[int(sha256(seed.encode()).hexdigest()[:16], 16) % len(values)]


def _pick_object(seed: str, values: tuple[dict[str, str], ...]) -> dict[str, str]:
    return values[int(sha256(seed.encode()).hexdigest()[:16], 16) % len(values)]


def _signature(value: object) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()[:24]
