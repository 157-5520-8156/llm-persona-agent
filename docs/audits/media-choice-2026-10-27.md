# Inbound media choice, 2026-10-27 China time

## Incident and evidence

In the 01:09 inbound turn the character visibly offered a photo. The accepted
plan contained four text beats and no media request; all four text beats later
received QQ delivered receipts. The pinned author input exposed two existing
photo candidates, but `now.photographable=false` (`no_active_activity`). The
source snapshot and accepted proposal were read without mutating the live world.
The original model output bytes were not recovered, so the evidence cannot
distinguish an omitted model field from a lost field before materialization.

On the actual `slim2` parser a reconstructed decision carrying the incident's
words and no `photo` compiles to `media_request=none`. In the new version,
omission is an explicit contract error; the role can choose true, false, or a
source ref. A second invalid result remains a technical failure. This check
does not infer intention from prose. `slim2` decoding remains unchanged.

`grounded_review_v25` now composes `slim3` for the non-native carrier. Old
accepted `slim2` actions were reopened without another author call in an
isolated SQLite test. This is not proof of recovery for every possible paid
in-flight state; old paid requests and audit records must not be rewritten.

## Bounded provider probe

One frozen incident author context was sent in alternating order twice to the
old and twice to a reconstruction with the new instructions, with a separately configured DeepSeek debug
key, the configured `deepseek-v4-flash` request model, temperature from the
captured request, and no QQ, media renderer, or production database writes.
Frozen user-message SHA-256:
`d359dd2131739b5e1cae95dff453415ae2079b8e55f47479bf2aa46cf56e8ac5`.
Both old outputs and both new outputs included a media choice; one new output
chose the offered source handle, the other chose `true`. This is a four-call
sanity check, not a comparative reliability estimate or an end-to-end media
qualification. After the probe, the new prompt's wording was tightened to say
that a request is an attempt and requires actual receipts. One new response
still suggested a current selfie despite the
snapshot saying new photography was unavailable; semantic source closure
remains a separate failure risk. Provider-reported usage: 122,808 input and
870 output tokens total; pricing was not calculated. No image request was made.

## Boundary and reference

The media request is a request to consider an available candidate, not proof of
render or delivery; the character's later selection, authority checks, provider
outcome, inspection, delivery receipt, and replay retain their own semantics.
No keyword detector or deterministic photo selection is introduced. Explicit
false plus contradictory prose remains semantically possible, as does source
review error. An eventual delivery guarantee cannot be inferred from this patch.

Isolated coverage separately exercised the new inbound choice through the real
accepted plan into the media-request wake, and the existing media pipeline from
candidate selection through a fake renderer and fake QQ image transport. The
latter now exercises both immediately verified delivery and deferred receipt
recovery, including restart and effect-once behavior. These are separate tests;
they do not establish a continuous real-provider-to-QQ delivery in this case.

The provider's [tool-call documentation](https://api-docs.deepseek.com/guides/tool_calls/)
describes strict schema validation of function parameters. The slim carrier's
decision is serialized inside a string, so its inner fields require host-side
validation; a valid outer schema is not evidence of a complete inner decision.
