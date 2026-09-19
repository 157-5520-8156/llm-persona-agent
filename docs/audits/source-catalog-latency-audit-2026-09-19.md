# Source catalog and latency audit — 2026-09-19

Status: `manual_only / qualification_incomplete`. Read-only inspection of the
closed `release-permission-chat-20260919-01` trial and its v18 source pipeline.
This audit made no provider calls, changed no runtime code, and granted no
release or delivery authority. It identifies a source-permission defect and
rules out a configuration explanation for the observed timeouts; it does not
establish that fixing the defect will resolve latency.

## Evidence and exact source timings

The local evidence directory is
`output/private-audits/release-permission-chat-20260919-01/run/`. Requests were
joined to usage records by their exact `usage_reservation_id`, rather than by
assuming every `source_review` usage record belonged to the source adjudicator.
That purpose also includes the independent meaning readers.

| Source capture ID suffix | Request body bytes | Recorded latency (ms) | Terminal result |
| --- | ---: | ---: | --- |
| `0c1d04a223c644eea4aa0e1a8ff6a427` | 72,307 | 22,002 | caller cancelled |
| `f99cbd10a19f42cc9745f34b279a0156` | 77,855 | 22,003 | caller cancelled |
| `df49ce64171c4f5d92286ff488419325` | 211,028 | 22,003 | caller cancelled |
| `e169dedcea534c4aa3fbd5766a077498` | 73,857 | 22,004 | caller cancelled |

All four transport captures received HTTP 200 response headers, observed zero
response-body bytes, and ended with `CancelledError`. No complete source response
was available to parse or inspect. Zero token counts on those failed usage rows
are missing usage, not evidence of zero consumption; their existing unknown
billing reservations remain authoritative.

All four actual request bodies contained:

```json
{
  "model": "deepseek-v4-flash",
  "max_tokens": 8192,
  "thinking": {"type": "enabled"},
  "reasoning_effort": "low",
  "response_format": {"type": "json_object"}
}
```

They had no tools or tool selection. The configured source reasoning effort and
JSON carrier therefore reached transport correctly. The source-stage timeout is
22 seconds in `visible_independent_review_runtime.py::invoke`; the complete
independent review has a 46-second bound. The two meaning readers run in
parallel, followed by source review. The recorded role-author calls took
3,574 / 5,595 / 5,139 / 5,338 ms; successful reader calls took 1,498–6,125 ms.
These observations locate the four failures before source response completion,
not in JSON parsing or receipt persistence. They do not distinguish provider
queueing, input processing, or reasoning time. Reducing `max_tokens` is not a
verified fix and could instead produce truncated results.

The 211,028-byte request is materially larger than the other three. Its fixed
facts included subjective-history readings with 84 eligible source IDs. The
permission selector retained 66 material cards, including 36 appraisals, and
omitted only three cards. The other calls retained 25–28 cards and omitted
40–43. Preserving those eligible sources follows
`visible_source_scope_selection.py::select_permission_context`; this is not
evidence of a selector implementation error or permission to discard history.

## Accepted Fact metadata is selectable as direct evidence

`visible_source_reading_experiment.py::_direct_paths` explicitly narrows known
dialogue, report, biography, subjective-history, settled-life and activity
sources. An `accepted_fact_with_observation_source` card has no corresponding
branch and falls through to all scalar paths, even with
`content_fields_only=True`.

The actual `relevant_facts` card consequently had 22 selectable readings.
They included accepted-Fact and Observation event IDs, payload hashes,
`accepted_value_binding` contract/hash/ref, revisions, timestamps, confidence,
privacy and status, in addition to `source_excerpt` and predicate/subject
context. `visible_source_subject_authority.py` gives this unhandled family the
generic owner restriction, allowing those metadata readings for
`utterance_record`, `accepted_intention`, `activity_lifecycle`, `environment`,
and `external_fact` for the counterpart. Metadata identity cannot establish the
propositional content of an accepted Fact.

For the first request, the decoded card's original `item` serialized to 2,228
characters, while its material-plus-reading row serialized to 9,879 characters
(`json.dumps(..., ensure_ascii=False)`, before shared-string packing). These
figures describe the local representation, not provider tokens or the final
wire-size saving of a proposed change. The defect expands both the evidence
choice surface and the request; its authority problem matters independently of
the possible size reduction. No observed false acceptance is attributed to this
specific defect by this audit.

## Why keeping only the excerpt is not a valid repair

`FactRecallItem.source_excerpt` contains the enclosing Observation. The retained
Fact value can be only a substring of it; its exact bytes are bound by
`accepted_value_binding`. In the inspected live card, the SHA-256 of the whole
`source_excerpt` **does not equal** `accepted_value_binding.value_hash`.

Changing the catalog to permit just `/item/value/source_excerpt` with the old
broad Fact permissions would still elevate the entire observed report into an
accepted Fact. Inferring the intended value by meaning in code would introduce
another unsupported semantic decision. Silently removing the whole
Fact family would also remove an expected memory capability rather than close
its contract.

The existing Life implementation provides a reusable boundary:

- `character_interior/life_fact_readings.py::fact_value_reading` validates the
  typed Fact, its displayed fields, subject and source identity. It keeps the
  Observation as context and grants no ordinary scalar permission. The
  descriptor carries the value binding, predicate, status and temporal context.
- `character_interior/life_source_readings.py::PreparedLifeSourceReadings.require_fact_value`
  requires a consumer-selected exact quotation, the right subject and the
  current/historical accepted-Fact permission.
- `fact_observation_value.py::FactObservationValueBinding.select` checks that
  quote against the original substring and accepted hash. This establishes the
  selected value's identity; semantic entailment against its predicate remains
  for review.

The visible review needs an explicit, versioned equivalent of this value
selection consumer and claim scope before narrowing the catalog. It must keep
the complete contextual card and original proof, deny metadata as direct
content, distinguish current from historical Facts and ordinary report uptake,
and verify the new selections during receipt reconstruction. Existing protocol
bytes and old receipt reconstruction must remain unchanged. There is no such
visible-consumer repair in this audit, and the release blocker remains open.

## Evidence identity

No request or response bodies, credentials, or native model reasoning are copied
into this document. The inspected artifacts have these SHA-256 digests:

| Artifact | SHA-256 |
| --- | --- |
| `model-inputs.jsonl` | `5418c3bf343fb6f2295eafd53894aa11d12349e01f689c5761a466fb12a3c994` |
| `provider-usage.json` | `bf1426d19215ade9c2bbdf8394519381e1a26b303ebb26f8d0c4729b637e1f0d` |
| `manifest.json` | `08959bcaa26f5a173c6fb793362fe89a727af9213687bfc6f830074e5973e3ff` |

## Subsequent bounded repair and qualification

The opt-in v19 path now uses `visible_fact_value_readings.py`: ordinary Fact
metadata/excerpt readings have no direct scalar permissions; exact values use
separate selections checked against original source, subject, predicate context
and current/historical status. Source cards and immutable bindings stay pinned.

The first real positive failed because the provider quoted the whole Observation.
The installed Fact producer stores only a substring hash after normalization,
so prompting the reviewer to guess the exact accepted substring was insufficient.
`fact_observation_value_lookup.py` resolves this content address by enumerating
only the producer's bounded substring space and comparing exact UTF-8 SHA-256.
This is identity resolution, not semantic selection or inferred reconstruction.
The resolved value is presented explicitly and rechecked on use. Unknown hashes
remain unavailable. The cache stores content identity only, never model verdicts.

After the change, one accepted-value positive and three negative controls all
matched with real Pro nonthinking source calls in 2.7–6.0 seconds. Interpretations
were scripted and the sources were isolated committed Fact fixtures; this is
not full chat qualification. Related checks passed (234, 7, and 101 checks with
scope overlap). See `fact-value-chat-validation-2026-09-19.json` for exact scope,
failed first attempt, fees and continuation points. No default route was changed.
