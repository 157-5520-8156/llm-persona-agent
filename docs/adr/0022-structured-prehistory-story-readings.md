---
status: accepted
---

# Preserve actor-scoped story structure through accepted prehistory and Recall

Implement the first P0/P1 loop from `docs/design/long-horizon-story-system-2026-10-07.md`.
The user authorized proceeding on 2026-10-07. This decision introduces no character
behaviour rules, new World scheduler, live plot authority or production adoption.

`character-prehistory-archive.2` retains each recollection's original source identity,
source blocks/locators/quotes, event identities, occurrence bounds, knowledge acquisition
bounds, and directional typed story links. Each record hash binds this structure and
therefore its existing independent review, import event and replay. The accepted graph
is part of the existing ledger; there is no second factual store. `.1` records omit the
new optional field entirely, retaining their original serialized content and hashes.

The old outer `occurred_*` interval remains the compatibility coordinate used by the
legacy import/source proof. In `.2` it denotes knowledge acquisition; explicit per-block
occurrence bounds are authoritative for new temporal filtering. Unknown occurrence
stays unknown. Source intervals need not be precise; `knowledge_as_of` exposes a whole
record only once its `known_until` bound has passed. It is not an expiry time.

The existing MemoryRetrieval / RecallCoordinator is the StoryMemory interface for this
increment. Active retention, exact current source proof, actor and privacy checks precede
reading. Model-facing story metadata contains source coordinates, event identities,
kinds and dates, never raw narrator quotations, bridge quotes or reviewer rationale.
Typed edges and compatibility adjacency are restricted to currently readable endpoints;
a query further restricts edges to its eligible endpoints. A budget-omitted but
still eligible endpoint remains a legal `story_window` continuation coordinate;
it does not grant evidence for text that was not presented. Forgetting cannot be bypassed
by traversing the accepted graph or requesting a source window.

`CharacterRecallRequest.knowledge_as_of` selects only structured prehistory with known
acquisition bounds, excluding unqualified legacy/live/interpretation sources. This is a
perspective of the returned material, NOT historical simulation of an entire current
character context. Current privacy, retention and source withdrawal continue to apply.

`story_window` permits the role to request the next source-exact 480-character window
(with the existing 64-character overlap) using coordinates from the reading. The same
current source checks and byte budgets apply. Legacy `.1` windows remain readable
without `knowledge_as_of`; they never gain a missing historical knowledge proof. Reading distinguishes full-record coverage
from a bounded record excerpt; neither claims complete story coverage. The existing
one-recall-per-turn constraint is unchanged; no new autonomous multi-step LLM loop.

## Consequences

- The CLI enables `.2` explicitly with `--preserve-story-structure`. Existing captured
  runs, approved `.1` packages and production archives are not silently upgraded.
- New `.2` output requires a fresh content-bound review. The faithful extraction runner
  still captures its `.1` intermediate; `.2` is compiled after the independent link stage.
- If using reviewer-guided correction, the existing correction lane still takes a bound
  `.1` extraction/review pair. Rejected `.2` reviews cannot be relabelled as `.1` approval;
  automatic `.2` revision/resume is not implemented in this increment.
- Original untyped links remain untyped. Only an explicit matching link artifact supplies
  typed edges. No semantic similarity, shared actor or chronological order fabricates
  causation. Event IDs are retained, not yet canonicalized across independently authored
  stories (P2).
- Frozen three-line source/oracle fixtures validate representation and epistemic boundaries.
  They do not establish independent extraction repeatability, real role understanding,
  NPC continuity, week/month narrative quality or production qualification.


Legacy full-envelope tool schemas retain their original fields and frozen identities.
The current slim/native JSON carriers expose the added reading controls through versioned
prompts (`.7-slim` and `.8-slim-core`). The previous `.6-slim` to `.7-slim-core`
conversion remains supported; no frozen hash expectation is rewritten.
