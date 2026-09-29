# Shared pinned context resolution — 2026-09-29

## Problem and change

Ordinary chat had already stopped blocking on semantic text review. The real
production-copy trace nevertheless resolved the same cursor twice: PinnedTurn's
chat compiler and CharacterInterior's projection compiler owned separate,
otherwise identically configured ledger resolvers. Each resolver maintained its
own cache. The second pass also rebuilt the recall corpus and replaced the
first prefetch job.

`ContextCapsuleCompiler.with_policy()` now creates a sibling compiler using the
same resolver. Production uses it for the chat presentation budget. Only the
untrimmed source resolution is shared; policy trimming, compiled bytes, prepared
handle issuers and advisory overlays remain independent. Resolver computation is
serialized with an instance-local reentrant lock, avoiding duplicate concurrent
refresh/prefetch for the same query. This is source preparation, not a character
behavior decision.

Cache keys retain world/snapshot hash, all cursor coordinates, actor, trigger,
consumer scope and logical time. Every hit still checks the current ledger head.
Historical recovery constructs its own resolver. No schema, model, prompt,
context budget, recall threshold, review policy or durable event was changed.

## Evidence

Private reproducible scripts/results: `output/private-audits/context-local-20260929/`.
The source is the same read-only production backup used in the preceding rollout;
all writes/deliveries are confined to clones and CaptureDelivery. No QQ test sent.

Local paired compilation, three existing production triggers, same snapshot:

| Pair | Separate resolvers | Shared resolver |
|---|---:|---:|
| 1 | 1.427s | 0.538s |
| 2 | 1.041s | 0.582s |
| 3 | 1.018s | 0.528s |

Both chat and interior compiler-result hashes match for every pair. This local
experiment uses the harness's deterministic recall embedding, not a provider
latency measurement. The first baseline includes colder caches; the later pairs
still save about 0.46–0.49s.

Real V4.1 (`deepseek-flash`) + configured BGE-M3, two turns per fresh clone:

| Metric | Baseline 1 / 2 | Candidate 1 / 2 |
|---|---|---|
| First captured text | 5.695 / 4.994s | 4.648 / 4.375s |
| Whole harness turn | 5.958 / 5.434s | 4.927 / 4.829s |
| Ingress to first role provider | 2.595 / 2.419s | 1.810 / 1.696s |
| Provider usage latency | 1.714 / 1.166s | 1.488 / 1.242s |
| Context resolutions per turn | 2 / 2 | 1 / 1 |

All four turns were authorized and captured with one author call and no blocking
review call. Model replies/latency are stochastic; this is a small smoke test,
not a p95 or a guarantee of sub-5s QQ delivery. Capture time excludes real QQ
network transport. Baseline has one pending sampled observation; it does not
block the turn. Candidate has two not-sampled entries.

127 scoped tests pass (37 resolver/policy/isolation checks + 90 capsule,
retention/growth, production composition and ordinary-text observation checks).
New tests cover independent budget output, unchanged bytes, separate prepared
handle authority, stale warm-cache rejection, actor/trigger/time partitioning,
and concurrent reuse producing exactly one recall refresh/prefetch.

## Remaining limits

The candidate still sometimes waits the 450ms prefetch join limit. Sharing fixes
the duplicate restart but does not establish the independent embedding/search
bottleneck. A local profile also finds repeated Experience source validation
inside life and memory readers; it has not been bypassed or broadly cached.
Production background appraisal failures are outside this change.

## Rollout

The new two-turn production clone cold-replays successfully under the frozen
candidate package: semantic hash matches, zero evaluator findings (89.68s).
Production launcher now selects `context-local-20260929-ea46f245ca79`; previous
package `sampled-text-20260929-55677eea857f` is retained. Only the three runtime
files above and the new regression test differ in this package. A source-hash
comparison confirms tested workspace runtime code equals the frozen package.
Launcher and consistent SQLite backup are in the private activation-backup
folder. Rollback is to restore the old launcher code root and restart, keeping
the current database; no schema or history migration is involved.

Live PID78567 starts successfully. `/health`, `/dashboard`, `/openapi.json`,
authenticated `/world-v2/dashboard` and the operator snapshot all return200.
The unauthenticated owner route returns403 as designed; the initial endpoint
probe omitted its token, then the authenticated check passed. Health returned
running/ok at the final check. This does not establish that earlier background
character-appraisal failures are fixed; they are outside this rollout.
