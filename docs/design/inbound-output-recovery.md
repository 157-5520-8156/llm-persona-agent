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

## Public RED: another recovery seam is required

`test_public_sqlite_reopens_completed_output_before_proposal` uses the real
application, production CharacterInterior, DeepSeek with MockTransport, and a
temporary SQLite turn store. It stops at ProposalAuditRecorder.record after the
role's Core terminal is committed. No accepted event is fabricated. This
fixture's installed atomic path makes two physical author calls before the
stop; the test measures additional calls rather than claiming this is one call.

At the stop there is one durable inbound terminal and no Proposal audit.
Two public restart windows were tested:

| Window | Actual baseline result |
| --- | --- |
| Foreign runtime, original lease still live | Pure join: no new HTTP or Action |
| Public Clock tick to +121 seconds, then duplicate ingress | Cursor 8 becomes 10; HTTP count 2 becomes 4; another terminal and fresh candidate are authored |

The targeted run is **1 passed, 1 failed**. The failing assertion is that a
completed author must not silently be asked again during terminal recovery.
The Clock changes World revision, so the test does not expect the original
candidate to authorize an Action at the new revision. It retains the distinction
between recovering evidence and accepting a still-current decision.

The reason is `WorldRuntime._existing_observation_outcome`: with no Proposal it
joins a live foreign claim, or obtains a fresh expression attempt after expiry.
`CharacterInteriorInboundDeliberationAdapter._consider` derives the turn from
attempt, capability, and full cursor. Consequently adding a body to the original
terminal alone does not make this public path read that terminal.

`derive_expression_plan_material` independently requires the audit's evaluated
World revision to equal the acceptance cursor. Rewriting an old output's cursor
or creating a new trusted handle from its JSON would bypass this boundary.

## Proposed additional seam, awaiting ownership approval

1. Add an inbound-specific Core reader over terminal_records_for_source. It
   verifies both stored hashes, original request/snapshot/capability, role
   decision and lineage, and then reads only a `.2` complete output carrier.
   It must not route through a new author call or broaden the existing generic
   purpose-decision reader, which currently requires a different payload shape.
2. Before choosing a new author attempt, the pinned/runtime recovery path can
   look for this exact completed source. A still-current candidate must use the
   existing validation/acceptance and CAS. A changed World revision makes the
   original candidate stale; its evidence must remain original, not be rebased.
   Exactly how to journal stale terminal recovery and prevent implicit reauthoring
   requires explicit runtime ownership. Live foreign provider leases remain
   protected; finding a terminal is not a generic lease override.
3. Recovery also needs the original Deliberation request coordinates and frozen
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

Current ownership is the new test and this design only. No runtime, Core,
provider, health, budget, production database, or frozen audit has been changed.
