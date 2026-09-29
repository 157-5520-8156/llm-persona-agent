"""Source-exact overlapping life windows with search-only causal context.

No summarizer or role decision runs here. Each text window is copied from an
already verified settled field; identifiers and time improve
retrieval, while only the original World reading supplies factual authority.
"""
from __future__ import annotations

WINDOW_CHARACTERS = 480
WINDOW_OVERLAP = 64


def life_recall_windows(item):
    world = item.content.world_consequence
    fields = {'environment': world.environment}
    if world.authorized_attempt_result is not None:
        fields['authorized_attempt_result'] = world.authorized_attempt_result
    for field, excerpt in fields.items():
        for start in range(0, len(excerpt.text), WINDOW_CHARACTERS - WINDOW_OVERLAP):
            text = excerpt.text[start:start + WINDOW_CHARACTERS]
            selected = excerpt.model_copy(update={
                'text': text, 'truncated': excerpt.truncated or start != 0 or len(text) != len(excerpt.text),
            })
            updates = {}
            for sibling, value in fields.items():
                updates[sibling] = selected if sibling == field else value.model_copy(update={
                    'text': value.text[:256], 'truncated': value.truncated or len(value.text) > 256,
                })
            reading = world.model_copy(update=updates)
            content = item.content.model_copy(update={
                'world_consequence': reading,
                'truncated': any(value.truncated for value in updates.values()),
            })
            carrier = item.model_copy(update={'content': content})
            context = f'{field}; settled_at={item.settled_at.isoformat()}'
            yield field, start, text, carrier, context + '\n' + text
            if start + WINDOW_CHARACTERS >= len(excerpt.text):
                break


def attempt_family(document):
    """An exact execution-linked Plan, never a topic guessed from words."""
    life = document.settled_life
    if life is None:
        return None
    result = life.content.world_consequence.authorized_attempt_result
    if result is None or result.execution_binding.source_kind != 'activity_execution':
        return None
    return result.execution_binding.actor_ref, result.execution_binding.plan_id
