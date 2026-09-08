# Completed inbound output recovery

Status: public RED and recovery boundary investigation. No production change yet.
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
adapter.propose, not app.respond recovery. Both application instances currently
run in the same test process; fresh-process Recall authority is a later required
test, not something this fixture proves.

The reason is `WorldRuntime._existing_observation_outcome`: with no Proposal it
joins a live foreign claim, or obtains a fresh expression attempt after expiry.
`CharacterInteriorInboundDeliberationAdapter._consider` derives the turn from
attempt, capability, and full cursor. Consequently adding a body to the original
terminal alone does not make public cold ingress read that terminal.

`derive_expression_plan_material` independently requires the audit's evaluated
World revision to equal the acceptance cursor. Rewriting an old output's cursor
or creating a new trusted handle from its JSON would bypass this boundary.

## Bounded next seam and remaining integration question

1. For the proven exact-pin port, Core already joins the original terminal.
   Its `.2` decision can provide a complete stable carrier to the adapter after
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
handle. Recall is different: `_TRACE_AUTHORITY_KEY` is process-random. A real
subprocess test with a fresh key and zero retrieval calls remains required for
the eventual green implementation. Neither boundary has been waived by this RED.

Current changes are the new test and this design only. No runtime, Core,
provider, health, budget, production database, or frozen audit has been changed.
