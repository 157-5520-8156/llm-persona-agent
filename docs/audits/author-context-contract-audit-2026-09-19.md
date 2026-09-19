# Author context and contract audit — 2026-09-19

Status: **read-only diagnosis; no qualification change**. The release remains
`manual_only / qualification_incomplete`. This audit made no provider calls,
changed no runtime configuration or database, and did not implement a replacement
review path. Inspected source revision: `9194ac6db92e7fc252e3daa7d53054862708d06d`.

## Evidence and measurement

Inspected the nine `character_inbound_initial_v3` requests and corresponding
completed response bodies in these existing private captures:

- `output/private-audits/release-scoped-json-chat-20260917-01/run/model-inputs.jsonl`
- `output/private-audits/release-permission-chat-20260919-01/run/model-inputs.jsonl`

Request line numbers below are one-based. Request bytes come from each captured
`body_size_bytes`; prompt tokens come from that request's saved provider usage.
They measure the author only, not the complete chat or its review calls.

| Trial | Request line | Capture suffix | Request bytes | Prompt tokens |
| --- | ---: | --- | ---: | ---: |
| scoped-json | 1 | `291fd5b8989c437398da56c22d1e50ce` | 162,291 | 48,503 |
| scoped-json | 17 | `255f42947af641d49a63e8aa774d0f22` | 162,655 | 48,752 |
| scoped-json | 41 | `c26206be25bd4aa4896332bb75d0c218` | 164,185 | 49,331 |
| scoped-json | 57 | `09d37fb4b0f6491f94652ba922f2821c` | 169,200 | 50,844 |
| scoped-json | 73 | `3eb3cdf0c4704424bde2fad9a30f5a29` | 166,164 | 50,017 |
| permission-chat | 1 | `2391706cc4dd49a29320e06a4ca3ba3c` | 166,021 | 49,917 |
| permission-chat | 17 | `2c2e9e374cd14211ba2b0ea4768ba8c1` | 166,365 | 50,083 |
| permission-chat | 37 | `e2411715ede24f2a976aaecf85c7763f` | 166,999 | 50,253 |
| permission-chat | 57 | `dd7d0f65cf944a7890cfaf56e2802ece` | 166,806 | 50,231 |

The first permission-chat request has recorded content hash
`ed0fa63047c945684bebbaea679330c8774d33428128bd3b8f44e4a7fb9b3a48`.
Its saved response body SHA-256 is
`c0d042d2cd42d1a475ea45a4972981c66d75152781e428189517d84c75bc3c8f`.
No private prompt text, response body or credential is reproduced here.

For that request, the system string is 38,968 UTF-8 bytes and the user string is
96,244 bytes. Selected decoded user values have the following sizes when encoded
using `json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()`;
these are comparable compact JSON sizes, not token estimates or disjoint totals:

| Decoded value | Compact JSON bytes |
| --- | ---: |
| `inner_life_snapshot` | 81,406 |
| `inner_life_snapshot.materials` | 71,549 |
| `materials.affect` | 23,778 |
| `materials.appraisals` | 15,135 |
| Sum of individual objects in affect components' `appraisal_refs` | 18,965 |

The affect material contains four episodes, five components and 55
`AppraisalMeaningRef` objects, of which 46 are distinct. The appraisal material
contains 36 rows. Earlier diagnostic messages used ordinary JSON spacing and
therefore reported 24,449 bytes for affect, 15,700 for appraisals and 19,350 for the
reference objects; the differing byte counts describe the same decoded values.

## Finding 1: lifetime provenance is copied into the working context

[`snapshot_compiler.py`, `_affect_entry`, lines 435–502](../../src/companion_daemon/world_v2/character_interior/snapshot_compiler.py)
copies each component's complete `appraisal_refs`, including accepted revision,
appraisal identifier, hypothesis identifier and source-cluster identifier.
[`affect_reducers.py`, lines 290–292](../../src/companion_daemon/world_v2/affect_reducers.py)
correctly requires new state to retain the old appraisal lineage. This durable
provenance requirement is intentional and must remain intact.

The presentation layer has no corresponding compact reference representation:
[`present_prompt.py`, `cache_stable_affect`, lines 611–619](../../src/companion_daemon/world_v2/present_prompt.py)
only separates stable entries from the latest entry. As stimulus history grows,
every author call can carry more identifier tuples even with few active episodes.
The measured reference objects alone occupy roughly 80% of the affect JSON.
This contradicts the intended bounded working-context role of the snapshot; it
does **not** show that emotional state should be truncated or scripted.

A safe candidate improvement is reversible table encoding of the repeated typed
reference structure, retaining every value and its relationship. Any such change
must cover actual author presentation, inverse reconstruction, downstream source
material compilation and replay verification. It must not silently delete
lineage, semantic appraisal text, emotional components, chronology or evidence.
No such change is implemented by this audit. The measurements establish input
overhead, not a demonstrated cause of a particular source-review timeout or a
measured latency/cost improvement.

## Finding 2: private continuity has inconsistent scope across contracts

The active author instruction in
[`inbound_wire.py`, lines 10322–10330](../../src/companion_daemon/world_v2/character_interior/inbound_wire.py)
permits present private states and their immediate retrospective continuity
without World proof; it separately requires proof for embedded external events,
biography and settled history. The permission was verified in the saved author
request, not inferred only from source code.

There are two downstream contract mismatches with that permission:

1. **Author versus meaning reader.**
   [`visible_candidate_meaning.py`, lines 499–505](../../src/companion_daemon/world_v2/visible_candidate_meaning.py)
   instructs the v11 reader to treat retrospective feelings, attitudes and
   conversational intentions as `past_subjective_state` requiring an accepted
   past subjective record. It does not state the author's immediate-continuity
   distinction.
2. **Author versus source adjudicator.**
   [`visible_contextual_source_review.py`, lines 31 and 45](../../src/companion_daemon/world_v2/visible_contextual_source_review.py)
   likewise requires records for old inner states and restricts
   `past_subjective_state` to that actor's historical subjective source. Its
   current-state exception does not explicitly preserve the same immediate
   continuity that the author was allowed to express.

These are two appearances of one semantic boundary drift, not two independently
proved runtime failures. A shared definition should distinguish private
continuity within the current cognition from claims about a prior episode or
sustained history. Actual external actions, completed communication, biography,
habits and established past intentions must retain their source requirements.
The distinction belongs to model interpretation of the complete utterance and
context; it must not become a keyword, tense or phrase exemption in host code.

No controlled case in this audit isolates that mismatch as the cause of a saved
timeout or a particular false rejection. Fixing wording alone would therefore
still need targeted semantic tests and real-provider evidence.

## Observed author behavior and limits

All nine inspected responses parsed into the expected tool envelope. The saved
hobby drafts nevertheless include unsupported sustained intentions and concrete
habitual conduct while declaring `world_claims=[]`. The earlier bath conversation
also has a rejected draft with an embedded repeated late-night-chat premise.
Those are genuine record-dependent claims, not automatically permitted private
continuity. This audit found no deterministic host code granting them authority;
the author was already instructed to declare and ground such claims.

Accordingly, neither input compaction nor aligning the private-continuity
contracts is evidence that those drafts should pass. Complete visible-prose
review, source permissions, same-character correction, technical-failure handling
and receipt/replay requirements remain necessary. This audit makes no claim of
release readiness, long-term human likeness, monthly-cost qualification or a
successful end-to-end chat repair.
