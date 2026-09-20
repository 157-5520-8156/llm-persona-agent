"""Shared present-authorship authority, never a source of World facts.

Keep the .1 wording frozen: historical Life review requests embed these exact
values. Invocation evidence hashes belong to the reviewer, not this authority.
"""
from __future__ import annotations


AUTHOR_PRESENTATION_CONTRACT = 'life-author-current-authorship.1'


def current_life_authorship_authority(*, actor_ref: str, logical_time: str) -> dict[str, object]:
    return {
        'contract': 'current-life-authorship.1',
        'actor_ref': actor_ref,
        'logical_time': logical_time,
        'authority': 'Create this actor\'s present appraisal, attitude, feeling, interpretation and choice in this invocation.',
        'exclusions': 'Not evidence of past feelings, past decisions, performed actions, external conditions, other people or embedded historical premises.',
        'world_fact_source': False,
    }
