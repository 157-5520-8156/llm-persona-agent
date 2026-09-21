"""Independent reviewer configuration without execution or World authority.

Platform composition may validate the supplied reviewer before opening stores.
Provider calls and immutable evidence recording remain in the review runtime.
"""

from dataclasses import dataclass

from .visible_review_protocols import GROUNDED_REVIEW_VERSION, REVIEW_PROTOCOLS


@dataclass(frozen=True)
class GroundedVisibleReviewer:
    """One independently invoked reviewer sees both language context and facts."""

    source_model: object

    def __post_init__(self):
        if not callable(getattr(self.source_model, "complete_json_with_usage", None)):
            raise ValueError("grounded review requires an explicit metered provider")

    async def complete_json_with_usage(self, **_kwargs):
        raise ValueError("grounded reviewer requires its whole-candidate runtime")


@dataclass(frozen=True)
class IndependentVisibleReviewer:
    meaning_models: tuple[object, object]
    source_model: object
    scope_subjective_history: bool = False
    scope_permission_context: bool = False
    source_response_mode: str = "tool"

    def __post_init__(self):
        if self.source_response_mode not in ("tool", "json_object"):
            raise ValueError("unsupported source response mode")
        if type(self.scope_permission_context) is not bool or (self.scope_permission_context and self.scope_subjective_history):
            raise ValueError("permission context selection cannot combine selectors")
        if type(self.scope_subjective_history) is not bool:
            raise TypeError("subjective source scope flag must be boolean")
        if len(self.meaning_models) != 2:
            raise ValueError("independent review requires exactly two meaning models")
        clients = (*self.meaning_models, self.source_model)
        if any(not callable(getattr(m, "complete_json_with_usage", None)) for m in clients):
            raise ValueError("independent review requires explicit metered providers")
        identities = [getattr(m, "model", None) for m in self.meaning_models]
        if any(not isinstance(m, str) or not m for m in identities) or identities[0] == identities[1]:
            raise ValueError("independent review requires distinct reader model identities")

    async def complete_json_with_usage(self, **_kwargs):
        # Compatibility with the existing deployment capability check only.
        # A legacy single-call review may not consume this composite port.
        raise ValueError("independent reviewer requires the version 9 runtime")


def validate_independent_reviewer_configuration(reviewer, version):
    if version == GROUNDED_REVIEW_VERSION:
        if not isinstance(reviewer, GroundedVisibleReviewer):
            raise ValueError("grounded review requires its explicit single reviewer")
        return
    if isinstance(reviewer, GroundedVisibleReviewer):
        raise ValueError("legacy review cannot consume a grounded reviewer")
    if (version in REVIEW_PROTOCOLS) != isinstance(reviewer, IndependentVisibleReviewer):
        raise ValueError("independent review versions require an explicit independent reviewer; legacy versions cannot consume one")
