# Completed inbound output recovery

Status: exact-pin recovery implemented; public ingress policy unchanged.
Baseline: `2256967d8518220a6bd633814ce07a62602ad6f9`.

## The accepted slice

Persist the complete, bounded ModelOutput alongside the original Core terminal
under `character-interior-inbound-turn-decision.2`. The existing terminal row is
the storage boundary; there is no new table or cross-table atomic transaction.
Keep `.1` serialization unchanged. A missing historical carrier is unavailable,
not permission to fabricate a carrier or repeat its completed author call.

The stable record must retain physical-provider, subcall, and authored-candidate
audits that ModelOutput.model_dump currently excludes. It must bind the original
world, actor, complete cursor, capability, InnerTurn, proposal, and author/private
lineages. Recorded Recall/prefetch audits belong in stable identity; the
process-local HMAC seal does not. Only a verified original terminal can authorize
restoring a live trace. No arbitrary-dictionary signing API is acceptable.
Existing output byte/node limits remain hard failures, never truncation. This
does not recover an unfinished HTTP stream, install a visible-source receipt,
alter provider usage, or fill gaps in an author's original audit collection.

## Public diagnosis and exact-pin port RED

`test_public_sqlite_reopens_completed_output_before_proposal` uses the real
application, production CharacterInterior, DeepSeek with MockTransport, and a
temporary SQLite turn store. It stops at ProposalAuditRecorder.record after the
role's Core terminal is committed. No accepted event is fabricated. This
fixture's installed atomic path makes two physical author calls before the
stop; the test measures additional calls rather than claiming this is one call.

At the stop there is one durable inbound terminal and no Proposal or ModelResult
audit. A separate temporary WorldV2UsageStore has both known, provider-reported
100-input/100-output-token records. That primary ledger survives restart. The
loss is the output and World audit, not evidence that provider usage was unbilled.
Three restart windows were tested:

| Window | Actual baseline result |
| --- | --- |
| Foreign runtime, original lease still live | Pure join: no new HTTP or Action |
| Public Clock tick to +121 seconds, then duplicate ingress | Cursor 8 becomes 10; HTTP count 2 becomes 4; new attempt and terminal; fresh candidate authorizes Action |
| Cold-composed public Deliberation adapter given the actual unchanged original ModelInput | Core joins the exact original terminal; Faculty.consume_output raises `inbound CharacterInterior output cache is unavailable` |

The corrected targeted run is **2 passed, 1 failed**. The first two are
diagnostics of current public ingress behavior. The expired case correctly
allows a fresh judgment at a different World revision. The earlier draft's
zero-additional-author expectation for that case was invalid and is removed;
it is not evidence of a needless duplicate call. The original terminal remains
unchanged, but its output's winning call still has no World ModelResult audit.

The remaining RED is exactly the unchanged-pin consumption failure. It captures
the original ModelInput at the real application-to-adapter boundary and passes
it unchanged to the adapter from a new application, Core, Faculty, and SQLite
turn-store connection. It supplies neither the saved output nor a fabricated
Capsule. This public port is compose_character_interior_inbound_deliberation's
adapter.propose, not app.respond recovery. The ordinary case uses separate application instances in one process. Separate
Recall cases also execute a real Python subprocess; those results are recorded
below and are not inferred from same-process cache tests.

The reason is `WorldRuntime._existing_observation_outcome`: with no Proposal it
joins a live foreign claim, or obtains a fresh expression attempt after expiry.
`CharacterInteriorInboundDeliberationAdapter._consider` derives the turn from
attempt, capability, and full cursor. Consequently adding a body to the original
terminal alone does not make public cold ingress read that terminal.

`derive_expression_plan_material` independently requires the audit's evaluated
World revision to equal the acceptance cursor. Rewriting an old output's cursor
or creating a new trusted handle from its JSON would bypass this boundary.

## Implemented seam and remaining integration question

1. For the proven exact-pin port, Core already joins the original terminal.
   Its `.2` decision now provides a complete stable carrier to the adapter after
   stored terminal/prepared hashes, original request/snapshot/capability,
   role decision and lineage have been verified. This is narrower than changing
   ingress leases or blocking fresh decisions. The existing generic
   completed_considerations_for_source expects a different purpose payload;
   it must not be weakened to accept arbitrary decisions.
2. There is currently no public cold-ingress route that supplies that old
   ModelInput unchanged. Recovering the original World audit after a crash is a
   separate integration question, not solved by a passing codec/port test. It may
   need an inbound-specific exact terminal reader and PinnedTurn audit recovery.
   Preserve the old evaluated cursor and output/call hashes. A later World head
   may legitimately require a fresh role judgment; retaining the old paid
   evidence must not suppress it, rebase the old candidate, or make it accepted.
   No new runtime recovery policy is approved or implemented by this document.
3. That later audit recovery also needs original Deliberation coordinates and frozen
   source validation, not only ModelOutput. Its current capability carries the
   ModelInput hash but not the body. Either reconstruct and exactly compare the
   original request from an audited-prefix reader, or persist its bounded body
   with the carrier. Neither route may import later facts or issue a new
   deliberation handle for an old snapshot. This is not yet implemented.

Capsule compiler_result_tag is a deterministic content hash, not a process
secret. Its complete model validator checks identity, but
TrustedContextCapsuleHandle is deliberately process-issued and unserializable.
The existing compile_for_audit_recovery returns a Capsule, explicitly no new
handle. Recall is different: `_TRACE_AUTHORITY_KEY` is process-random. The real subprocess test confirms that the old live seal fails under the new
key. It then restores the exact recorded audit through the original terminal,
with no new Recall/prefetch or HTTP call. A changed original snapshot trace is
rejected as `inbound_output_record.invalid_original_checkpoint`; its outer
checkpoint hash is deliberately recomputed in the temporary fixture, so the
failure checks the original snapshot proof rather than only that outer hash.

## Stable carrier and restoration authority

`inbound_output_record.py` keeps the original ModelOutput grammar and explicitly
serializes all three otherwise-excluded audit arrays. Recall/prefetch identities
contain recorded audit bodies and presentation call identities, not a live HMAC.
The record binds the original world, actor, full cursor, InnerTurn, capability
ref/hash, original ModelInput hash, snapshot, proposal hash, and author lineage.
The same original prepared checkpoint and terminal also retain the final private
lineage. The canonical record is limited to 512,000 UTF-8 bytes and the existing
ModelOutput node bound. Bounds reject rather than truncate output or evidence.

Core retains a bounded handle to the exact coordination request it actually
acquired. Only a completed same-pin `consider` can use that handle to read its
installed terminal store. Restoration compares stored hashes, terminal and
prepared decision, original snapshot/capability, private/author lineages, and
original Recall trace/presentation material. The private Recall helper receives
only an opaque, nonserializable verified-terminal proof containing a fixed audit
tuple. It does not accept arbitrary audit dictionaries for signing. No new
trusted Context handle or source membership is minted.

The output record is local technical coordination evidence with the same privacy
as the existing prepared snapshot and role terminal. It is not a public world
fact, receipt, or additional model-visible input. No new table, migration, or
provider request is added. Fresh `.2` decisions have a new output identity; old
`.1` records retain their original hash formula and are never supplemented with
a body. A cold `.1` without the old process cache remains explicitly unavailable,
without a replacement author call at that same pin.

## Executed validation and limits

The new file has **19 passing cases**. These cover the original live-lease and
changed-cursor diagnostics; same-pin and Recall recovery with repeat consumption;
real subprocess Recall success and source-proof failure; old `.1` same-pin
unavailability with unchanged terminal bytes; eight rehashed identity mutations
(world, actor, cursor, capability, source, turn, proposal, author); retention of
all three excluded audit arrays, including a rejected candidate; UTF-8 byte/node
bounds; and rejection of arbitrary-dictionary Recall signing.

The legacy test is an explicitly constructed technical-terminal fixture using
the exact baseline `.1` serialization formula and the real original role output.
It does not claim an inventory or migration test of production historical data.
The audit-array codec fixture verifies preservation of recorded fields, not an
extra successful HTTP or an author's completeness in collecting every rejected
call. The initial public fixture still proves primary usage survives the crash;
the recovery path does not meter those calls again.

**193 passed in 25.84s** across the new file and eight adjacent files:
`test_character_interior`, `test_character_interior_private_self_lineage`,
`test_character_interior_turn_store`, `test_character_interior_durable_lineage`,
`test_character_interior_single_author_composition`,
`test_character_interior_inbound_author`,
`test_character_interior_stream_tail_audit`, and `test_recall_attention`.
Ruff and diff checks passed. All provider interactions were temporary
MockTransport fixtures; no real provider, QQ, production DB, or frozen artifact
was accessed or changed.

This slice does not restore automatic cold app ingress, change leases or CAS,
suppress a fresh judgment at a changed cursor, recover unfinished HTTP tails,
install source-review receipts, or repair omitted original author audits.
Only the narrow Core consumption hook, inbound Faculty/adapter, private Recall
restoration helper, new carrier module, tests, and this design changed.

## Automatic prefetch integration correction

The root `11909fb5` 91-file gate exposed two actual regressions in the public
active/completed activity journeys. Their second message had automatic prefetch
in the original snapshot. The Faculty deliberately retains that audit as
`presented_prefetch_traces` for the actual invocation while leaving the legacy
top-level `prefetch_trace` empty. The first recovery implementation incorrectly
required the empty top-level field to equal the snapshot trace, resulting in
`inbound_output_record.recall_source_mismatch` and downstream retry/budget noise.

`56f6e371` validates that original snapshot trace through its presentation and
the final author's actual call ID. It retains the original empty top-level field,
does not add source material, and still rejects missing/replaced presentations.
The stored presentation list must match the original prepared and terminal
records; each presented trace must match the original snapshot prefetch audit.
Ordinary Recall retains its exact top-level binding. New subprocess cases cover
the actual automatic-prefetch producer format as well as deletion and replacement.
No timeout, budget limit, source scope or character choice was changed.
