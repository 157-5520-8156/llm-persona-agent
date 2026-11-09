"""One CharacterInterior author with explicitly assigned background compute."""

from .structured_role import StructuredCharacterRoleFaculty


# These are scheduler/retention purposes, not decisions inferred from user text.
# New and potentially reply-blocking purposes keep the interactive route by default.
BACKGROUND_CHARACTER_PURPOSES = frozenset({
    'private_impression_reflection', 'world_stimulus_appraisal', 'life_development_choice',
    'activity_lifecycle_choice', 'outcome_selection', 'fact_memory_retention',
    'experience_memory_retention', 'memory_consolidation', 'memory_withdrawal_review',
    'prehistory_memory_batch', 'external_perception_attention', 'media_selection',
})


class BackgroundStructuredRoleFaculty(StructuredCharacterRoleFaculty):
    name = 'character_background_compute'

    def __init__(self, *, interactive, background_model, character_disposition,
                 life_source_reviewer, reference_wire, temperature: float | None = None):
        if temperature is None:
            provider = str(getattr(background_model, 'provider', '')).lower()
            model_name = str(getattr(background_model, 'model', '')).lower()
            if 'deepseek' in provider or 'deepseek' in model_name:
                temperature = 1.3
            elif 'glm' in provider or 'glm' in model_name:
                temperature = 1.0
            else:
                temperature = 0.8
        super().__init__(
            model=background_model, model_id=background_model.model,
            temperature=temperature,
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
