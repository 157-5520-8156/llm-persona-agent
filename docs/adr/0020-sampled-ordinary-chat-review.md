# Ordinary inbound text: one author with sampled observational review

Status: active for ordinary inbound text after compatibility repair and staged verification (2026-09-29). See docs/audits/epoch-replay-and-text-cutover-2026-09-29.md; overall release qualification remains incomplete.

The user explicitly accepts natural approximate language and authorizes testing,
then replacing blocking ordinary-chat semantic review when results justify it.
This supersedes the earlier requirement for a semantic verdict on every ordinary
inbound text reply, not source permissions, privacy, durable fact/experience
writes, external actions, effect-once, or replay authority.

A separately pinned deployment mode may accept text-only reply/followup actions
with only expression, Appraisal and Affect changes. Everything else retains the
existing blocking review. The host decides eligibility from typed action/change
kinds, never keywords, motives or world_claims emptiness. Unreviewed text gets an
explicit provenance receipt saying no semantic review occurred, never a fabricated
LLM verdict. It remains a dialogue record, not evidence that its narrative happened.

Background sampling starts after provider ACK or delivery (neither means read). It is observational: it cannot rewrite, cancel, retry, send a
message or create a World fact. Evaluation distinguishes acceptable colloquial
approximation from unsupported events, identities/shared history and material
contradictions. Sampling/failed observation is visible in local diagnostics and
must not block generation or delivery. Existing reviewed receipts retain their
original meaning and replay path. The global default stays blocking until local
qualification; activation is a separate deployment setting, not a model choice.
