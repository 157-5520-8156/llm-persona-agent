---
status: accepted
---

# History-scoped incremental story import

This extends ADR0022 and implements the bounded P2 slice in
`docs/design/story-import-p2-2026-10-07.md`. It does not adopt new history into production
or authorize a separate Fate scheduler.

## Decision

An incremental `character-prehistory-archive.3` contains a frozen import context:
exact same-actor prior archive manifests, original World-start coordinates, biography
configuration event hashes, and exact prior records used by a proposed connection.
The independent review binds the entire resulting manifest. First acceptance checks this
basis inside the existing serialized transaction. Exact completed reimport remains inert
after subsequent history changes; a different package using that identity is rejected.

Event identity is a reviewed proposal. The host generates a source-local coordinate from
World, actor, archive and event ID, or follows an explicitly reviewed mapping to a prior
structured occurrence. Similar names, dates and topics do not authorize merging. Later
recollection, disclosure and interpretation can connect to an old event without asserting
that the new act of learning or reflection was the old occurrence.

Cross-story links reference one new and one accepted endpoint, with separate exact block
IDs and quotations. Separate endpoint fields avoid collisions between manuscript-local
block IDs. The new manifest authorizes its edges; old record bytes remain unchanged.
Current Memory readings project an eligible edge onto both endpoints and carry the new
archive's exact committed proof. Recall and claim binding require that proof as well as
the original record/archive proofs. Forgetting, owner/privacy/source withdrawal and
`knowledge_as_of` filtering remove inaccessible endpoints and their relationships.
The current reader derives canonical coordinates for existing `.2` records too, so a
reviewed `.2` to `.3` match has the same coordinate at both ends.

Durable `prehistory-memory-retention.1` opportunities keep their original initialization
reading shape. They are not regenerated using today's richer Recall representation.
An exact opportunity fixture captured with the older implementation guards that boundary.

## Preparation and recovery

`prepare_story_import.py prepare` freezes its brief, complete manuscript hash, actor,
archive identity, models, endpoint and revision limit. It runs extraction, local links,
prior-event/relationship planning and independent review. It writes a reviewed package
only after all required verdict dimensions approve; it does not write a World or choose
what the role retains. Semantic revision is bounded, reuses unchanged source material,
and preserves approved record content while re-reviewing changed relationships.

New plans explicitly classify every new event ID as same-event, distinct or unresolved.
New review contracts include individual event-match and cross-story-edge judgments and
source-backed omitted-connection findings. Empty lists alone do not prove coverage.
The host checks inventory and proof structure; the models still own semantic judgments.

Each paid stage saves intent and wire response. A complete invalid result receives at
most one constrained correction from the same model. A completed wrong tool envelope
is also a correctable result. A missing response or truncation stops the stage; recovering
a captured result emits no request. An operator may make a separately recorded recovery
request after diagnosing new evidence, without erasing unknown billing from the old run.

The new extraction contract uses `known_from/known_until` as acquisition bounds. The
legacy producer instruction treated `known_until` as retention until World start; that
conflicted with the `.2` historical reader. Existing captures retain their exact protocol
and hashes. They are not silently relabelled or repaired as new evidence.

## Limits

The source planner provides lossless UTF-8 partitions, exact character spans and hashes.
It is a coverage artifact, not a global novel compiler. Semantic preparation still needs
a complete bounded evidence packet. Automatically accepting chapters before later
chapters clarify earlier events is not implemented.

Two interwoven sources exercise this path. Independent extraction and review still vary;
structural correctness and one approved package do not establish stable semantic quality.
There is no cross-actor NPC identity adoption, runtime plot authority or long-term story
quality qualification in this increment. See the measured audit rather than treating the
accepted interface as a production qualification.
