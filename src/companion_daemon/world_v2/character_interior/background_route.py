"""One CharacterInterior author with explicitly assigned background compute."""

from .structured_role import StructuredCharacterRoleFaculty


# These are scheduler/retention purposes, not decisions inferred from user text.
# New and potentially reply-blocking purposes keep the interactive route by default.
BACKGROUND_CHARACTER_PURPOSES = frozenset({
    'private_impression_reflection', 'world_stimulus_appraisal', 'life_development_choice',
    'activity_lifecycle_choice', 'outcome_selection', 'fact_memory_retention',
    'experience_memory_retention', 'memory_consolidation', 'memory_withdrawal_review',
})


class BackgroundStructuredRoleFaculty(StructuredCharacterRoleFaculty):
    name = 'character_background_compute'

    def __init__(self, *, interactive, background_model, character_disposition,
                 life_source_reviewer, reference_wire):
        super().__init__(
            model=background_model, model_id=background_model.model,
            character_disposition=character_disposition,
            life_source_reviewer=life_source_reviewer, reference_wire=reference_wire,
        )
        self._logical_author_identity = dict(interactive.author_identity)
        self._contracts = {key: value for key, value in self._contracts.items()
                           if key in BACKGROUND_CHARACTER_PURPOSES}
        self.purposes = tuple(sorted(self._contracts))

    @property
    def author_identity(self):
        # Preserve one logical protagonist. Actual provider/model lineage stays
        # in the inherited request/result evidence and this purpose's turn hash.
        return {**self._logical_author_identity, 'name': self.name,
                'compute_routing_contract': 'background-compute-routing.1',
                'background_purposes': sorted(BACKGROUND_CHARACTER_PURPOSES),
                'background_author_route': super().author_identity}
