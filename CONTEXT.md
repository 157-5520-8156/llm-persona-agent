Prehistory coverage and conversational evaluation (2026-09-29):
`docs/audits/prehistory-grounding-repair-2026-09-29.md`. User explicitly accepts ordinary
Chinese concise negation (no pet record -> "never had a cat"), approximation and ellipsis;
evaluate concrete invented episodes/people/results, not missing verbatim proof.
Production now prehistory-grounding-20260929-f9448e6aba77, PID21178. Added optional
WORLD_V2_PREHISTORY_PACKAGE_PATH; strict reviewed-hash/World/actor import, no auto retention.
Creation brief now includes committed BiographicalTimelineConfigured: enrollment2024,
not the older test archive's2023. New12 fictional pre-start records independently reviewed
by real V4.1, then ALL12 voluntarily retained via12 real production setup calls. One archive,
12 imports/decisions verified in live DB. Setup used actual NapCat composition without
starting ASGI/scheduler or sending messages. Startup import is idempotent; recovery idle.
191 scoped tests pass in isolated test env; clone cold replay hash matches,0 findings.
Health/dashboard/OpenAPI200 after activation. Backups in output/private-audits/grounding-repair-20260929/activation-backup;
permanent reviewed package under ~/Library/Application Support/Girl-Agent/prehistory/.
Policy file now contains a QUOTED path; Python tools should use dotenv_values, not naive split.
Current v25 prompt.4-slim and shadow grading ordinary-chinese-material-factuality.2 reflect
user semantics; no blocking text review restored, no Fact/Experience permission relaxation.
Known-source graduation/roommate answers can use new records, but uncovered birthday still
fabricates and old fabricated birthday is repeated from dialogue. NOT solved, no5% claim.
Contrastive examples and an extra visible-history index did not prove useful; NOT deployed.
Do not call this lower hallucination rate: it improves coverage and fixes deployment wiring.

Background recovery repair (2026-09-29): `docs/audits/background-recovery-2026-09-29.md`.
Memory review v2 pins fixed source refs in the host; role owns every retention/time
choice and attention (new protocol capacity32, original v1 unchanged). Fix same-owner
lease reclaim at exact expiry; previous <= caused retry without advancing backoff.
Expired/superseded/contradicted accepted Appraisal closes only its unapplied old Affect
in both recovery selection and compiler; missing sources remain errors, accepted Affect
is unchanged.250 scoped tests pass. Production clone baseline0/8 after2 calls; initial
candidate22 reviews/3 calls; final candidate8 reviews/1 call/3.78s. Cold full replay
matches,0 findings. Six experimental calls total estimatedCNY0.1334,0 unknown bills.
Production switched to background-repair-20260929-c82c19a83a58 (PID57754 at launch),
old package/launcher and SQLite backup retained under output/private-audits/background-repair-20260929/.
Initial activation exposed recovery scanning that replayed each historical relationship/
experience prefix on the main thread. Fixed selection to use verified current projection
and accepted descendants, retaining full pin/compiler/acceptance for actual processing;
SQLite scan now runs off-loop. Final280 checks and final-package cold replay pass (hash matches,0 findings); production-copy scan4.17s under
profiler, no historical replay, no pending jobs. Final package is
background-recovery-scan-20260929-278eb4f8f242, PID68071. Dashboard/health/OpenAPI200;
three short health snapshots running, scheduler passes2->3,0 fresh technical failures.
These idle live passes do not replace the real-model clone proof. One legacy retry-identity
warning remains diagnostic; historical errors were not erased.
Research only for chat fabrication: `docs/audits/role-grounding-industry-2026-09-29.md`.
Rechecked Sufficient Context, Contextual Retrieval, LongMemEval, RAFT and earlier51-call
project experiments. Prefer prehistory coverage and old-self-report contamination diagnosis;
no new synchronous reviewer, no new hallucination-rate or overall-release claim.

Local context optimization (2026-09-29): `docs/audits/shared-context-resolution-2026-09-29.md`.
Chat and CharacterInterior now share one exact-query LedgerProjectionContextResolver
through ContextCapsuleCompiler.with_policy; independent budgets/handle issuers retained.
Same-head validation remains before cache hits; serialized resolution coalesces concurrent
refresh/prefetch. No prompt/model/budget/authority/event changes.127 scoped tests pass.
Three production-snapshot local pairs retain identical compiler hashes, ~1.02-1.43s
becomes0.53-0.58s. Real V4.1+BGE-M3 clone A/B: baseline first capture5.695/4.994s,
candidate4.648/4.375s; total5.958/5.434 ->4.927/4.829s. One author call each; four
captures succeed. Small sample, no real QQ transport timing or p95 qualification.
Candidate cold replay hash matches,0 findings. Production launcher now selects
context-local-20260929-ea46f245ca79, prior package retained; activation backup under
output/private-audits/context-local-20260929/activation-backup. PID78567 verified: health/dashboard/OpenAPI and authenticated owner dashboard/snapshot
return200; health running/ok at check.450ms recall join misses still occur; earlier
background appraisal failures have not been addressed by this change.

Production text mode activated (2026-09-29): `docs/audits/epoch-replay-and-text-cutover-2026-09-29.md`.
Fixed true replay cause: Aug20 changed epoch.1 Fact assertion binding to genesis
without a format bump; live preserved archive observed_message bindings.19 Facts
differed only in binding/fingerprint. Exact committed Fact before-images recover
historical representation from original genesis; new snapshots use epoch.2.
No event rewriting or hash bypass.24374-event production copy replay matches ALL
projection fields;151 scoped tests pass. Frozen-package2-turn production-copy
capture succeeds (7.05/6.41s full turns); subsequent cold replay passes,0 findings.
Production8787 now loads sampled-text-20260929-55677eea857f, PID97271 at check.
Launcher in ~/Library/Application Support/Girl-Agent/run-production-napcat.sh loads
production-text-policy.env: v25 fallback, sampled ordinary text, episode off,
V4.1 deepseek-flash, sample every10. Existing .env untouched. Stop-time consistent
DB backup at seq24385 in output/private-audits/replay-compat-20260929/cutover-backup.
First package619f lacked pixel-home static assets; superseded by complete55677.
Dashboard/OpenAPI200, health responds; scheduler running. Still DEGRADED from
background world_stimulus_appraisal refs>8 and legacy retry identity issues.
No real QQ test messages sent. Do not claim all mechanisms healthy, <5s or <5%.
Normal rollback is sampled->blocking in the new mode file, keeping new code/data;
never overwrite newer messages with the old DB backup automatically.

Ordinary text observation mode (2026-09-29): `docs/audits/sampled-chat-review-2026-09-29.md`, ADR0020.
User explicitly accepts ordinary paraphrase/approximation and authorizes testing then
switching ordinary chat to one author plus nonblocking sampled observation.
Implemented opt-in WORLD_V2_ORDINARY_TEXT_REVIEW_MODE=sampled with v25 fallback,
explicit not_semantically_reviewed provenance, typed text/Appraisal/Affect eligibility,
unchanged Fact/Experience/Memory/media gates and old receipt replay. SQLite sidecar
observes only after ACK/delivery, ~1/10, max12 calls/UTCday; clean ContextVar task,
no rewrite/World effects, observable failures/severities. Default remains blocking.
109 scoped tests pass.12 real V4.1 captures10 delivered,2 invalid source/structure.
Paired6: direct6/6 p50=4.10s/CNY0.1172/6calls; blocking4/6 p50=6.76s/CNY0.3863/17calls.
Both modes fabricated graduation scenes. Actual shadow detects major; not5% proof.
Standalone observer2calls~CNY0.14838; installed observer1call0.0019 (cache-dependent).
New isolated direct receipt cold replay passes. PRODUCTION NOT SWITCHED:8787 PID6452
loads frozen c9429779 release, not workspace. Read-only production backup (qq-c2c:geoff,
seq24374) loads snapshot but full replay fails at seq22158 InteractionFactDecisionRecorded,
Sep25 event229564... with exact Fact source context hash mismatch. Do not bypass or edit
history; root cause still open. Production-copy direct capture1call=8.43s (model2.33s).
Candidate activation env is in output/private-audits/sampled-chat-20260929/, not applied.
Next required deployment work: repair/qualify historical replay, then real-production-copy
startup/latency and activation of a new package. Current process/env left unchanged.

Contextual life recall (2026-09-28): `docs/audits/contextual-recall-2026-09-28.md`.
Removed life-corpus newest256 truncation; exact overlapping480-char windows retain
late result text. Existing SQLite incremental vector reuse retained. New hybrid.11
can prioritize a newer result linked to an already selected owned Plan among
eligible matched rows, under unchanged6000B reading/12000B result limits.
Activity-name enrichment regressed and is NOT in production; experiment only.
Local BGE-M3 targeted30-case development set: baseline0, windows20, name-context15,
final exact-chain29/30. NOT a production/hallucination rate or held-out score.
219 scoped tests pass; old .10 and new .11 runtime cold replay/receipts verify.
One real full-chain turn:7.82s/CNY0.0489,2calls; still says half for some, review
leaks it.2 author-only pairs improve useful answers;8 earlier diagnostic replies
lost to accounting script error excluded from quality, included in spend.14 total
V4.1 calls~CNY0.0611. No5%/5s/daily-cost or release claim. No production restart/QQ.
Historical Fact transition reader still independently capped256; current search
is linear, tested420docs. Do not describe this as unlimited historical retrieval.

Action-result chain repair (2026-09-28): `docs/audits/action-result-chain-2026-09-28.md`.
Correction: absent from author/table did NOT mean absent from ledger. Same smoke
snapshot has3 settled photo results; display1440-char budget excluded14/18 life
bodies and was incorrectly reused for Recall candidates. Separate local corpus
reads from display; preserve privacy/pin/hash checks and foreground limits.
Recall also lacked the settled-life -> visible review bridge. New hybrid.10
reading carries bounded exact World material, expands authenticated aliases,
and uses actual LifeContentRecorded authority. Old .9 reading remains unchanged.
Final 215+23 distinct scoped tests pass; final2 real V4.1+BGE-M3 one-turn capture delivers via
one author+one review,6.38s/CNY0.0371.14 total diagnostic calls/CNY0.3301.
Final2 cold replay matches, zero findings, new review receipt verifies. Earlier
in-turn experimental .10 carrier shapes are NOT release/replay qualification.
Main progress grounded, but 'not finished writing' still exceeds 'a few notes'.
No5%/5s/daily-cost claim, no production restart, no QQ. Global256-doc recall cap
and excerpt bounds remain separate long-history limitations.

Sufficient-context diagnosis (2026-09-28): `docs/audits/sufficient-context-experiments-2026-09-28.md`.
51 isolated V4.1 calls, estimated CNY0.47736192. Evidence-only input, speech-only,
evidence-first and two-stage expression still fabricate; no production adoption.
Three low-thinking probes: two exhausted4096 output tokens in reasoning (22.10/19.61s),
one returned in7.59s. Not a general model impossibility result. Synthetic four-topic
paired controls improve with sufficient facts; all four insufficient conditions
make unsupported claims. Sufficient answers still show coverage/expansion/ID defects.
Fixtures are synthetic diagnostic data, not real biography or release qualification.
Next: qualify actual current-code action-result -> memory -> chat on a fresh isolated
World; don't infer new-chain performance from result-empty legacy snapshots.
No 5% claim, no gate removal, no production restart, no real QQ messages.

V4.1-only input experiment (2026-09-28): `docs/audits/v41-input-experiments-2026-09-28.md`.
User explicitly stopped Flash/Pro model comparisons; use official V4.1 API name
`deepseek-flash` for further paid tests. Legacy v4-flash is an official alias,
not evidence of a different current checkpoint. Aborted matrix is excluded.
8 frozen first-author pairs +6 fresh runtime questions per input profile:
bounded narrative saves input but both profiles delivered2/6; bounded is NOT a
production setting. Experiment hook remains only in compare_chat_review_gate.
Applied: canonical V4.1 model pricing admission; atomic-slim set ordering for
Recall filters; grounded ordered wire.4 expands identifier slots only and removes
only redundant plain IDs also carried by exact Fact-value selections. Old
wire1/2/3 unchanged. Quotes/owner/scope/unknown refs remain validated. Two recorded
Recall ordering errors recover offline; original schedule review now closes both
Beats under wire4 with identical content/sources/instructions. Fresh V4.1 schedule
probe delivers in7.29s with2 calls/0.0514CNY, no re-authoring.399+81 scoped tests
pass; no overall hallucination/5s/daily-cost qualification, no production restart.

Author input/Recall repair (2026-09-28): `docs/audits/author-input-repair-2026-09-28.md`.
Per-record annotation experiment did not improve delivery and was removed.
Current v25 slim prompt.3 replaces redundant/obsolete field explanations;
actual initial system text15875->9957 chars including complete Recall shape.
Fixed a separate bridge bug: authored CharacterRecallRequest filters were lost
between inbound author/Faculty/Core and coordinator. Optional recall_parameters
now carries validated kind/history/time/link/lexical/limit, with query matching;
absent legacy field keeps old query-only defaults.213 scoped regressions pass.
12-case concise precursor still7/12 delivered; not reliability qualification.
Recall-shape2-case probe did complete a real recall, cold replay matches; its
trace exposed dropped filters. Latest filter-fixed single case did NOT choose
recall and still fabricated graduation details accepted by reviewer. No claim
of solved hallucination or5s target; no production restart or gate removal.

Chat review gate comparison (2026-09-28): `docs/audits/chat-review-gate-comparison-2026-09-28.md`.
12 real-provider independent cases, SAME candidate observed before and after
blocking v25 review. Shadow is not unreviewed Action/delivery or persistence.
Candidate-ready p50 3.49s; reviewed capture9/12, delivered p50 7.49s. First
review pass5/12. Independent supplied-source inspection found6 cases with
unsupported experience/subject confusion,1 minor time error,5 without clear
unsupported experience. Review itself also overrejects and leaks paraphrases.
CNY0.8458 total/37 calls;0.2594 before first candidates; no daily-cost estimate.
No production toggle change. Current evidence does NOT qualify full gate removal.

Integrated repair (2026-09-28): `docs/audits/integrated-repair-2026-09-28.md`.
Exact accepted Thread descriptions now reach dashboard; current WorldStimulus v6
permits model-owned brief reaction without three mandatory prose explanations.
Life .20/.21 bind transport receipts and reply-gap readings; brief negative Affect
can settle, but natural long-term behavior remains unqualified. Exact-input caches
and audit reducer avoid repeated local work; v25 uses bound short references and
versioned subjective proof elision. Local profiled replay 10.418s -> 3.090s is NOT
real chat latency. Latest real 4-turn v25 capture: 4/4 eventual, 3/4 immediate,
first visible5.985/11.482/6.030s, CNY0.872/25calls; no daily-cost qualification.
398 focused regressions pass; broad isolated scan stopped at3159 passes, not a
full-suite pass. One compact run was a configuration mistake and is excluded. Source
fabrication and false rejection persist. No production restart or real QQ sends.

Silence/negative-affect audit (2026-09-28): `docs/audits/silence-negative-affect-2026-09-28.md`.
Production has20 silence appraisals but no anger/hurt/resentment components;
old calls emphasize delivery and~61m latest wait despite38.7h since inbound and
5 expression groups. New pinned silence chronology preserves that cumulative
history without counting Beats/receipt updates as contacts; trigger framing
now names the reply gap rather than a new receipt. Real candidate probes show
loneliness instead of no_change, not anger or qualified behavioral improvement.
No production deployment; source review, persistence and later behavior remain
to qualify together. No deterministic emotion or contact policy added.

Dashboard coverage audit (2026-09-28): `docs/audits/dashboard-runtime-coverage-2026-09-28.md`.
Production-copy pin23979 has54 plans,70 World events,69 Experiences,110 memories;
several independent state domains really have0 records. Delivered expression
text and exact Experience environment reads are now connected; expectation
cards reflect assessment/expiry. Add scoped process counts, zero explanations,
and bounded browser fetch.91 focused checks pass. Authenticated8792 preview
is explicitly a frozen production-copy snapshot, NOT live production.8787
still runs September26 code and repeatedly times out; native sampling shows a
busy Python/Pydantic worker but no confirmed function-level root cause.

Release repair update (2026-09-28): see `docs/audits/release-repair-2026-09-28.md`.
Ordered v25 review wire, bounded format repair, lossless large-evidence storage,
slim auto author, pinned Appraisal retention and Life .19 narrow state readers
are installed in code. Periodic model-owned memory retention review now has a
real due/lease/CAS/recovery path; it is not semantic memory compression. World
v5 can declare objective results for an exactly authorized started attempt.
375 focused checks pass; cold replay matches. Latest 30-minute isolated probe
still delivered only1/2, first visible16.98s, known CNY1.0268. Cost, latency,
first-draft fabrication and natural diversity remain unqualified. Additional
active-attempt fixture mismatches were traced to incomplete reply transport and
changed retry-slot expectations. No production restart or deployment.

# Girl-Agent Domain Glossary

For current release qualification read `docs/audits/release-status-2026-09-13-current.md`.
Current status: manual_only / qualification_incomplete (2026-09-21).

Accelerated release check (2026-09-28): `docs/audits/release-accelerated-2026-09-28.md`.
Current repaired code with reference wire, configured BGE-M3 and grounded_review_v25
ran one complete virtual24h in16.6 wall minutes, with97 clock advances,10 World
settlements,11 accepted memories and8 reflection openings. No skipped new Plan
windows; cold replay and40 focused regressions pass. This candidate FAILED:
4/4 user turns had no capture delivery; v25 overlapping/duplicate review segments
and subsequent retries persist. Known estimate CNY5.0653 plus3 unknown bills;
inbound review is49.7% of known cost. Life still repeats maintenance/obstacles;
one private response asserts a visit/photo handling absent from its bound result.
Periodic memory consolidation remains explicitly dormant. Planned days2/3 stopped
after this failed gate; no long-memory/personality qualification. This is NOT the
running compact profile, and production was not changed or restarted.

Continuity/read-path repair (2026-09-27): `docs/audits/continuity-repair-2026-09-27.md`.
Memory retrieval items now carry original update/review times, and capsule ranking
reads their top-level strength. Eight historical items previously all scored0;
now the existing age/importance/relevance formula receives its intended inputs.
Private choice views retain owned paused/abandoned states and explicit recall
results, including wrapper source refs. Continuation shows the prior accepted
intention as history, never an obligation or completed act. No memory is erased
or automatically devalued based on a lifecycle event. Current World.2 general
review uses native strict schema with bounded reason; two recorded bad-shape
requests now return valid unsupported verdicts. A controlled mundane candidate
passed real reviews and World settlement.227 regressions and cold replays pass;
long-term behavioral qualification remains open. No production restart.

Product correction and living-frame repair (2026-09-27): see
`docs/audits/lived-texture-2026-09-27.md`. Diversity means avoiding a life entirely
dominated by persona hobbies and productivity-style plans; venue/topic counts
are not acceptance. Private role composition now receives the same configured
personality/values as chat, without repeatedly injecting biography/habit/examples.
The immutable frame hash participates in new turn identity; it grants no facts.
Living choices explain permission/privacy fields without requiring meaningful
outputs or finishing old work. No topic bans, forced leisure, memory deletion,
extra model step, or temperature change. Same-history final samples:2 casual
choices and1 photo continuation vs3 photo continuations before. Real controlled
chain accepted ordinary intentions across restart; not proof of completed acts.
Long-term richness remains unqualified, and old-history chat still repeated an
old concern. Production was not restarted.

Cost qualification update (2026-09-27): see `docs/audits/cost-150-2026-09-27.md`.
Isolated fixed24h +6 chats: known CNY1.328, weekday equivalent1.555419,
1 unknown bill; immediate capture delivery5/6, eventual6/6. Not qualified at
CNY1.5/day with unchanged quality. Short-reference wire remains opt-in; no
production restart. Core regression438 passed and latest cold replay matched.

Life diversity repair (2026-09-27): `docs/audits/life-diversity-fix-2026-09-27.md`.
Current continuation also recognizes exactly bound abandoned activities, without
relabeling them completed. Activity catalog9 prevents expired paused attempts from
resuming under an old window; legacy8 semantics remain frozen. Ordinary World
requests sample an authorized available environment, without choosing character
actions. Failed old consequence retries now yield one due slot to other life work.
192 related checks pass. Candidate-level real A/B escaped the maintenance theme,
and final4h produced3 actual settlements and role-authored new plans; abandoned
activity continuation reached the real model and next plan. All4 checkpoints cold
replay consistently. Overall richness is NOT passed: photo/book intentions still
recur and source-review failures still block many results. Validation cost~CNY3.43
for181 calls, not daily-cost qualification. No production restart/deployment.

Richness verification (2026-09-27): `docs/audits/life-richness-validation-2026-09-27.md`.
Frozen candidate + configured BGE-M3 ran a complete24h with6 ordinary chats.
Not qualified:17/18 new settlements concern the same facilities-notice theme;
2 new plans remain closeout/old-concern work;2 NPC decisions are no_op;4 proactive
expressions repeat asking whether the user rested. All6 chats eventually captured.
Known estimate CNY3.5654 plus1 unknown bill. Five of6 retained inbound audits use
local recall fallback; do not claim all turns used BGE. No skipped new plan windows;
cold replay matches. This is behavioral evidence, beyond earlier mechanism tests.

Life reconsideration (2026-09-27): `docs/audits/life-reconsideration-2026-09-27.md`.
Current day-open capability5 / choice3 supports role-selected later consideration,
with durable original-choice binding, cross-day restart and fresh-context choice.
V2 advisory routines are separate from legacy hobby schedules. NPC requests now
expose source IDs, owner/time state, now/later fields and privacy floors before
commit. 324 related tests passed; bounded real-provider probes used CNY0.10839764
(29 calls, no unknown bills). Recorded-failure replay plus real correction reached
NPC settlement. Fresh NPC no_op remains valid; week-long richness is unqualified.

Life richness audit (2026-09-27): see `docs/audits/life-richness-2026-09-27.md`.
Current World Consequence context now excludes protagonist private affect,
relationship, threads and subjective experience slices; character cognition retains
them. Causally ready NPC work/stimuli precede discretionary ambient generation;
exact protagonist attempt outcomes keep priority. Accelerated probes now honor
production due boundaries and owner kinds. Earlier fixed-clock samples skipped a
whole planned activity window; their low cost is not full-life qualification.
Real diagnostic runs still repeated photos/maintenance. Richness remains unqualified.

Grounding continuation (2026-09-26): BGE-M3 remains the configured semantic
retriever; no E5 experiment or live provider call was made. The completed-attempt
end-to-end fixture now also verifies that the exact `authorized_attempt_result`
reaches grounded visible review with its source binding; its `no_op` control
creates no occurrence result or Experience to cite. Recall gained an
ephemeral stage diagnostic (corpus, eligibility, matched candidate, initial
rank, final selection); offline controls distinguish absent facts, ineligible
status, retrieval misses, top-k exclusion, and context transport without changing
thresholds or durable receipt contracts. Grounded review v25 is opt-in and
preserves the v24 compiler: it requires a lossless ordered partition of every
Beat and does not permit an activity lifecycle field to support a companion
action. A paired regression reproduces the known v24 lifecycle false acceptance
and verifies v25 rejects that same source use. Current local settings remain
`compact` + `stream`, so v25 is not active in the running profile. The reviewer's
semantic classification still needs controlled provider qualification; no live
accuracy claim is made. The focused suites passed 364 tests across four runs,
with Ruff and `git diff --check` clean; the suites overlap with existing coverage.

Chat is now actually usable; invitation release is not qualified. At8cce5843 a real
reviewer answer that repeated a JSON member inside one fact object was proved to be the
cause of the previous turn that produced no delivery and no correction at all; an answer
that is not the contract is now separated from a verdict, re-asks the same reviewer once
inside the already-open validation phase, never re-asks the character, and never retries a
definite verdict or a provider timeout.288 related checks and Ruff pass.
Same two inputs as the previous trial: first-draft delivery0/2 to1/2, actual delivery1/2
to2/2; input1 step48.85s to17.67s; input2 went from no delivery to4 bubbles at29.08s.
Real adaptive chat:14 inputs,10 delivered, delivered steps13.1-29.1s, with in-the-moment
follow-ups and her own callbacks to earlier turns. Still failing: she invents her own lived
experience (turns3/7/9 correctly rejected, turns1/2 wave-through), corrections are often
invalid, and one turn lost to an author timeout. Cost is not controlled: single turn about
0.98 CNY at DeepSeek peak pricing, well above the100 CNY/month target.
Continuation and billing point:output/private-audits/source-model-cost-20260921-01/
chat-first-draft-20260921-03/world.sqlite;1930 usage/1927 reservations/77 unknown,
66 physical calls all with native usage rows, known estimate13.772318 CNY,2 new unknown
holds (capacity, not a charge); ledger2587. Verified by grounded-chat-20260921-03/
reconciliation.json. No real QQ send, no production database write, no deployment.
Slim author exit shipped at64d277a5 (+afed85a9 host guard): the atomic author tool is the
existing compact carrier and its payload goes through compile_slim_consider_payload into the
same canonical drafts; carrier version is "slim", not4, because the review namespace owns4.
Same pinned input: provider schema26,974 to1,007 chars, her output1,562 to275 tokens (-82%),
schema+system -54%. Cost did NOT improve: about1.16 CNY/turn (3 turns3.489328 CNY), and that
prompt rose34,578 to45,050 tokens because the control context grew14 rounds and because the
system itself grew8,334 to15,265 chars. The bottleneck is now the system text and the user
material, not the envelope. The same real run shows8cce5843 working in production: the review
exception now reads GroundedReviewWireFailure:unknown or duplicate grounded source reading and
the ledger shows the identical request bytes re-asked once4s later (second call cache_hit26,368).
Continuation:output/private-audits/source-model-cost-20260921-01/slim-chat-20260921-04/world.sqlite,
1946/1943/77, ledger2827,16 calls all with native usage rows, new estimate3.489328 CNY,
0 new unknown; grounded-chat-20260921-04/reconciliation.json.3 inputs,1 delivered; the other2
are the known review-rejection-plus-invalid-correction and author primary_invalid.
Context measured and shrunk one layer at20ee1c11: user material is86% inner_life_snapshot and
its materials block is75% of the whole (67,190 chars in one real request; affect alone19,501).
The provider-facing copy of materials now goes through the existing lossless
pack_shared_strings; role_result_correction, source_refs and the ids she must echo stay plain.
Same continuation point, two real turns: materials67,190 to53,326 chars, user material -15.9%,
prompt tokens45,050 to38,403 (-14.8%), one author call0.304930 to0.275744 CNY (-9.6%), both
turns delivered. Per-turn cost still did NOT fall (11 calls/2.374112 CNY for2 inputs, about1.19
CNY/turn) because a turn's cost is dominated by how many times it retries - the first input took
8 calls. Continuation:output/private-audits/source-model-cost-20260921-01/
packed-chat-20260921-05/world.sqlite,1957/1954/77, ledger2944,11 calls all with native usage
rows, new estimate2.374112 CNY,0 new unknown; grounded-chat-20260921-05/reconciliation.json.
Retry structure located atcc583f3a (run 06): one ordinary turn is author, review rejects,
character correction, review passes. The extra calls are bought by invented life detail in the
first draft, not by protocol or schema failures. One real input took5 calls and1.076252 CNY;
the review's three reasons were an invented environment scene, an invented activity duration and
an unsourced negative proposition - the section7 root cause, no real execution results in the
ledger. The same turn also shows the reverse: the correction稿 regressed scope from past_world
back to current_world and the review let it through, so the reviewer is not self-consistent on
scope. The slim scope clarification did work (first draft moved from current_world in runs04/05
to past_world here), so the prompt layer is exhausted. Continuation:
output/private-audits/source-model-cost-20260921-01/diagnosed-chat-20260921-06/world.sqlite,
1962/1959/77, ledger3023,5 calls all with native usage rows, new estimate1.076252 CNY,
0 new unknown; grounded-chat-20260921-06/reconciliation.json.
Next single item: the life result chain - make what she actually did leave a result in the
ledger so she has something to cite instead of inventing it. No more protocol or prompt tuning
first, and no new review layer.
See docs/audits/chat-first-draft-2026-09-21.md.
This paragraph supersedes the v24 trial paragraph below for delivery status.

Latest full-host chat at 072a25e7 uses opt-in v24 single contextual review.
Two ordinary inputs: first-draft deliveries0/2; one corrected reply delivered
four Beats after27.43s from ingress, but falsely supported actual walking with
activity lifecycle evidence. Other turn failed at receipt.ValueError with no
delivery. Do not equate a valid receipt or corrected delivery with sound prose.
User priority: first-draft quality and delivery, corrections counted separately
as recovery. Next inspect author inputs and missing actual execution evidence;
do not add another semantic review stage. See
docs/audits/contextual-review-chat-2026-09-21.md and ADR0020.
New modules29+10 and historical compatibility104 checks passed; no new full suite.
The two trials made3+9 physical calls, known estimate0.42613688 CNY and one new
unknown hold0.20785 CNY (not a known charge). Processes and clients closed.
Latest unified runtime/billing continuation:
output/private-audits/source-model-cost-20260921-01/grounded-chat-after-timing/world.sqlite;
ledger1369/revision544 at logical05:11Z;1864 usage/1861 reservations/75 unknown.
Cold replay verified5 unchanged v22 receipts and1 new v24 receipt; its semantic
false support remains a defect. This supersedes all continuation paths below.

Earlier full-host hands-on chat at d5b5a58f: three adaptive ordinary inputs,
zero character deliveries, about40-56 seconds per complete step. Review misread
metaphor, omitted subjects and current reported state; author also introduced
unsupported life claims, including during correction. Do not call current chat
qualified based on author drafts. No production logic changes in this audit.
See docs/audits/hands-on-chat-2026-09-21.md.29 physical calls, all known,
estimated0.67498448 CNY; new unknown0. Runtime and full billing now share:
output/private-audits/source-model-cost-20260921-01/hands-on-chat/world.sqlite;
ledger1237/revision483 at logical05:11Z;1852 usage/1849 reservations/74 inherited
unknown. Cold replay and5 inherited receipts verified; new receipts0.
That continuation/billing path is now historical; use the latest point above.

Current atomic-v3 chat author now uses one compact semantic prompt; v1/v2,
stream/nonforced and stored carriers retain their old bytes. Original identity
is intact: final system29,919->8,334 characters. Two author-only pairs reduced
input tokens about12%, but did not improve measured latency or establish factual
quality. Six known calls total estimated0.28462660 CNY, no new unknown bill.
Final cross-field guidance has31 related passing checks; earlier compact commit
has245 passing checks and1 independently reproduced pre-existing prefetch failure.
No World writes, source qualification, new delivery or deployment. Details:
docs/audits/author-prompt-compaction-2026-09-21.md.
Previous full billing was output/private-audits/author-prompt-20260921-01/run-v2/world.sqlite:
1823 usage/1820 reservations/74 inherited unknown;5 old receipts cold-verified.
At that stage runtime remained after-json-transport1163/471, before hands-on above.

User priority update (2026-09-21): prioritize life continuity plus speed/cost;
current-life prose remains deferred. Retain source authority and character agency.
See docs/audits/life-speed-cost-2026-09-21.md for the bounded repair and measured limits.
Subsequent output-contract repair: docs/audits/life-output-contract-2026-09-21.md.
New World tool3 disallows all-null visual environments without forcing environment facts;
old tools1/2 remain byte-exact. Appraisal strict3 now preserves and flattens complete
unions; old standard1/strict2 remain exact. Its single real provider-format probe returned
HTTP200 in4.812s and passed strict JSON/schema/typed shape only: no source authority,
Appraisal materialization, Experience or delivery was tested by that probe.
Related202 checks and9 focused schema checks passed separately. No deployment.
New World source material reader3 preserves both cited event payloads and exact author-visible
accepted content. OldNone/2 byte compilation stays unchanged;29+87 related checks pass.
This repairs a proven evidence omission; the subsequent real continuation still failed as below.
Independent visible review now uses the existing reason-preserving model timeout helper:
its own 22s deadline counts as provider_timeout; external cancellation stays caller_cancelled.
Two new real-adapter offline cases and 171 related regressions pass. No new paid calls,
provider latency improvement, deployment, or release qualification is claimed.

Previous resumable runtime (superseded by hands-on above):
output/private-audits/source-model-cost-20260921-01/after-json-transport/world.sqlite;
ledger1163/revision471 at logical/operator05:11Z, runtime billing prefix1815/1812/74.
Previous full billing (superseded by the author-prompt run-v2 above):
output/private-audits/source-model-cost-20260921-01/appraisal-v3-probe2/world.sqlite;
1817 usage/1814 reservations/74 inherited unknown. Merge this complete billing prefix
before any continuation; the runtime source alone omits the two format probes.
Probe1 was400/not_billed. Probe2 was200/known,0.09089224 CNY repository estimate,
43,097 input/650 output tokens,4.812s; native/schema/typed shape only passed.
Both preserved every nonbilling row; no World result or delivery was added.
Cold hash8e0ac08c1d3acf5029d41ff60b9dd1fa4f0ec4effe164c044a3fdfe876f76490;
5 inherited v22 receipts remain byte-exact and cold-verified,0 new receipts.
Frozenc89efca9 made10 calls:9 known +1 provider400 not_billed,
adding0.19990700 CNY estimate,0 new unknown. No new delivery or life consequence.
World JSON opt-in was not reached. A future invitation in the new color chat was
semantically misread/rejected; no first-delivery latency exists for this failed round.
Appraisal2 DID reach beta but failed400: result declares type object with no properties;
its union has the real object branches. Projection also lost affect operation variants.
New Appraisal3 preserves the complete affect unions and removes the invalid union
object envelope; old1/2 remain exact.202 related checks pass. The first isolated v3 probe still hit400 on nested unions;
3349f716 flattened only pure unions. The second single format probe passed as above.
Original rejected requests remain immutable; source/authority/life acceptance was not tested.

Earlier after-contract-fix1141/468 and1805/1802/74 is now historical.
Frozenfff6a8d3 made12 known calls, adding0.29235522 CNY estimate,0 new unknown.
Blue-leaf chat delivered3 authored beats and1 new receipt; chat plus fact0.05953650 CNY,
complete step17.7522s was only a first-delivery upper bound. ActivityStarted1125,
no new WorldOccurrence/Experience/Memory. World native3 still failed after correction.
Do not claim life continuity, latency, monthly cost, or invitation release qualified.

Prior after-source-fix1027/413 and1793/1790/74 is historical, not a resume point.
It made6 known calls adding0.18973288 CNY, no unknown; no new character/life receipt.
It repaired missing author-visible accepted source material, but the new same-event
material overlap branch was proved by offline reproduction, not isolated by that run.

The preceding8-call continuation at1016/revision412 added0.16875778 CNY known,
no unknown, and demonstrated that invalid World rewrites are now durably audited.
Three transport receipts only settled older04:29 captures. Old inbound recovery
uses persistent30s/30min/2h backoff; it is not a repeated-per-clock scheduling bug.

The preceding4 source-only calls added0.0645963 CNY known estimate. Flash was
faster but both models falsely rejected a previously accepted response; this
trial did not qualify a default model change or cheaper service. All its bills
are included in the1817 prefix above. Do not resume older cheaper snapshots.
Earlier recall-fact-chat run ended973/396 with1775 usage/1772 reservations/74 unknown.
It restored runtime10 and merged all32 subsequent probe bills before calling.
The b8243599 trial made11 physical calls:10 known (0.4497742 CNY repository estimate),
1 source-review timeout with unknown bill retained as a1.22976 CNY capacity hold, not a known charge.
First chat delivered3 beats and a newv22 receipt; second produced no delivery after source-review timeout.
All4 visible receipts cold-verified; inherited3 unchanged. First answer used original dialogue;
the .9 recalled Fact was also shown/reviewed but was already in the original Capsule and was not selected
by the reviewer, so this is no evidence of retrieval quality gain. The second draft again claimed
continuous nonmovement without a supporting interval. Do not retry this as an already qualified chat.
The later historical attention-inventory reference alignment is offline-tested only.
Details: docs/audits/recall-fact-authority-2026-09-21.md.

10 ran frozen2978be51, initialization0, physical ceiling40, actual30 known calls;
new estimated cost1.0408613 CNY, zero new unknown holds. Normal stop, clients,
independent reconciliation and cold replay passed. Three inherited visible v22 receipts
verified; no new character receipt or Experience. The sole new visible text is an explicit
system technical-failure notice, not character speech or chosen silence.

The character chose ActivityCompleted(seq865). This opened a new World request with
actual tool2/8192 wire. Its draft and correction still failed; no new activity aftermath
was accepted. An older environment occurrence separately settled. Do not confuse these.
World draft parsed; source review hit a33-vs32 path limit, then rejected support;
author rewrite had one extra native trailing brace, not output truncation.
Life .13 also accepted a personal-seeing claim using environment-only support;
this unresolved semantic risk is recorded in final-independent-inspection.json.
One appraisal/affect and one existing-Experience memory retention advanced. The consumed
old Started/no_op was preserved. Inspect exact new failures before any repeat paid trial.

Four offline historical recall cursors were reconstructed with exact original hit equality.
Tea(T13)/name(T29) were eligible corpus items but never admitted as candidates: no lexical
match and below-threshold feature-hash vectors, not byte-budget rejection. Broad shared
conversation links independently admitted irrelevant appraisals. Removing that noise does
not solve missing semantic recall. 75976daa repairs the partial-cache SQL placeholder mismatch (74 focused tests).
ec58520d excludes verified broad conversation membership from new automatic relevance
(97 focused tests including recovery); exact saved-query replay remains unchanged.
These repairs do not solve the demonstrated tea/name semantic candidate omissions. Details and subsequent repair validation are in
`docs/audits/release-continuation-rag-2026-09-21.md`.

Subsequent offline M3 comparison (24 variants, zero cloud calls) found current-message
queries rank tea/name first/second, but fixed5500 admission still excludes them.
Long attention also adds false candidates; no default query or threshold was changed.
Automatic prefetch CAN use configured semantic embeddings; older contrary prose was
incorrect. The09/10 audit host explicitly disabled them. Life .14 in4423ca04 adds
opt-in claim-scope/subject-to-permission checks (71 focused tests), default still .13;
semantic labeling can still fail. Real paired review and chat improvement are untested.
See `docs/audits/release-local-semantic-recall-2026-09-21.md`. That fixed5500 experiment
did NOT use the production semantic adapter's existing4200 threshold.
The subsequent bounded recall phase tried two proposals at4200: current-message
queries retrieved2/3 old targets; fixed RRF plus query-cue coverage retrieved3/3 but
increased forbidden old-negative material3->5. Neither passed the frozen criteria;
neither entered production. Independent query positives stayed8/8 per split, already
achieved by the semantic baseline; this is not a new gain or a pronoun-resolution proof.
Both retrieval trials closed without a third proposal or paid call in that phase.
See `docs/audits/recall-phase-2026-09-21.md`; subsequent author diagnostic billing is above.

The subsequent eight-pair author diagnostic made32 physical calls: the first16 used
incorrect old policy wiring and are excluded; the second16 match journey10 author policy/tools,
but added recall still lacks runtime authority traces. Twelve decision drafts and four unexecuted
Recall choices are not complete chat, review or delivery evidence. Source text was present yet
sometimes ignored; missing biography also became unsupported negative family facts.
The Core recall reading drops existing subject/speaker metadata; the narrow repair restores
these for new hybrid.8 readings and retains historical index-bound shapes. Do not claim model
behavior improved from this interface fix or use permission-incomplete overlays as acceptance.
Repair26e775d3 passed94 focused checks plus a3-test native/legacy rerun; Ruff/diff passed.
A separate native coordinator -> Core -> InboundTurnFaculty offline fixture also confirms:
prefetch-only Fact text and trusted trace reach the author, but its absent original semantic lane
leaves counterpart_history permission missing. Existing augmentation removes the mechanical rejection.
This confirmed a real local Faculty seam defect, not merely the overlay limitation.
The subsequent hybrid.9 repair carries source-bound accepted Fact values through author permissions,
existing Fact source review and cold receipt replay, for both automatic prefetch and chosen pull.
It uses the exact accepted value rather than elevating the enclosing Observation, verifies the native
presentation inverse, and cites historical images by their unique accepted event. Old readings and
prehistory receipt reconstruction stay unchanged. 148 related checks plus3 full offline integration
cases pass, including wrong-subject rejection and historical scope. These use fixture model judgments;
real-provider quality and QQ delivery are not implied. See docs/audits/recall-fact-authority-2026-09-21.md.
Total added estimated cost0.56389952 CNY; all32 known, no new unknowns, all nonbilling tables unchanged.
See `docs/audits/recall-author-comparison-2026-09-21.md` for evidence and limitations.

The .13/.14 request compiler now builds its final wire directly; .1-.12 retain
their historical builders. 42 frozen public-fixture envelopes/provider hashes and
cold recompile are unchanged; 51 compatibility/boundary plus141 related checks pass.
The removed .6 intermediate envelope limit can no longer reject a smaller valid
final request; original input bounds and the final256000-byte limit remain.
This is a maintenance refactor, not evidence that retrieval or chat improved.

Full suite at older frozenb560a3d2:9485 passed/19 skipped/2 xfailed/0 failures;
that run does not cover later code. Later focused tests do not establish complete chat,
QQ receipts,24-hour operation,recordable journey or100 CNY/month qualification.
No deployment, real QQ sends or production database writes have been performed.

## World

A continuous fictional life epoch centred on the companion. A World has one authoritative history and one Logical Time.

## World Event

An accepted, immutable record that something changed in the World. Correction is expressed by a later compensating World Event, never by rewriting history.

## Projection

A deterministic, rebuildable view derived from World Events. A Projection is not an independent source of truth.

## Logical Time

The World's event-recorded time. It may pause or advance at different rates and is distinct from wall-clock time.

## Character Fact

A maintained fact about the companion's stable identity, values, preferences, or boundaries. It does not prove that a particular life event occurred.

## User Fact

A sourced, confirmed fact about the user. A current User Fact may supersede an older one without deleting the older historical record.

## Plan

An intention or scheduled future activity that has not happened. A Plan is never an Experience.

An accepted self-directed Plan from chat, a World Life Response, day opening, or an Activity Continuation
retains its original role-authored intention in `planned_activities` before it
starts. This source-bound view proves the intention and scheduled window only;
it grants no present location, started activity, embedded history or completed
outcome. Other Plan producers require their own verified readers.

A started or resumed self-directed Plan may provide the exact original
Character-authorized intention to World Consequence authoring through its
source-specific reader. That reading authorizes an attempt's scope, not the
intention's embedded history or successful fulfillment. A recorded reader
identity preserves the original source set when older requests are replayed.

## Activity Continuation

A source-bound opportunity to consider a future self-directed Plan after the
companion's latest owned ActivityCompleted, once no live owned Plan remains.
It reuses empty-catalog planning and the existing background budget, with one
journal identity per completion event across days and restarts. The character
may choose an intention or no_op; refusal consumes this opportunity, and a
technical failure retains bounded retry rather than becoming refusal.
The source proves that an activity ended, not that its intention succeeded.
_Avoid_: Automatic next activity, daily behavior script, invented Experience

## Biographical Context

A source-bound reading of age, academic/calendar phase, current residence
context, and active Life Arcs at one Logical Time. It is derived from reviewed
timeline facts plus accepted World Events; it is not copied from a static
persona prompt and does not decide what the companion should do.
_Avoid_: Static age label, behavior script

## Character Prehistory

An explicit `life_source_reviewer` now reviews complete Life candidates before
acceptance; rejection reuses one same-author correction with the original draft,
while unavailable/incomplete review remains technical failure. Accepted receipts
bind exact author output, source view, candidate and review, including durable
recovery. Recall control transfers wait for retrieval before terminal proposal
validation. Review v10 separates current authored states from record-bound claims,
checks whole-candidate coverage and distinguishes unsupported claims from unreadable
sources. Five controlled real reviews matched expectations; a real Life chain
corrected and accepted one response, then cold-restored its receipt. A future
photo-organizing intention was still falsely rejected. The capture journey can
explicitly install the same gate with evidence in its durable database. Three
new real chat turns delivered twice and failed once; this chat run did not invoke
the Life gate. Pro meaning-reader JSON and habitual-inner-history correction remain
unqualified. The default remains unconfigured; see current release status and
`docs/audits/life-chat-integration-validation-2026-09-16.json`, as well as
`docs/design/life-source-review-gate-2026-09-16.md`.

Explicit visible review v17 restores complete utterances and both independent readings
at the source stage and requires whole-Beat omission review, including empty extracted
fact inventories. v18 separates non-record expressions, missing record-bound assertions
and material ambiguity. Nonreasoning trials still miss embedded history. Explicit
capture-only source reasoning matched six of seven controlled cases, with one timeout;
the whole chat trial had three source timeouts and one accepted retry, cold-verified.
Source requests remain about 169–181 KB; monthly cost and response latency are unqualified.
Auto source-tool selection is pinned and joined to immutable invocation hashes. Defaults
remain unchanged. Runtime and billing now share
`output/private-audits/release-contextual-chat-20260917-01/run` (1200 usage, 1197 reservations,
41 unknown holds). See `docs/audits/contextual-chat-validation-2026-09-17.json`.

Explicit visible review v16 pins string-condition guidance and includes reader
claim modes in same-character rejection feedback, retaining v15 source permissions.
Two further real turns still failed delivery; present feelings were recognized,
but habitual/past claims and a false recent-answer claim survived correction.
The previous raw-JSON diagnosis was too broad: frozen closing-tail handling already
accepts redundant closers; actual structural retries concerned object-valued string
conditions. Beat-level hypothetical scope is absent from the source-stage fixed
facts, a concrete interface risk still requiring a scoped repair and real proof.
See `docs/audits/typed-feedback-chat-validation-2026-09-16.json`.

Explicit review15 restricts retained prehistory direct readings to narrative text
and permits declared historical people/groups alongside the companion. The latest
opt-in author schema ordering places evidence before Beats in actual provider
requests and all four author responses, but two real turns still failed delivery.
The author embellished autobiographical detail; source review also rejected an
exact supported father action. A conversational denial was read as an external
historical fact. Ordering alone does not qualify natural expression or correction.

Life field reading preparation now restricts known source families to explicit
fields and their actual presented semantic coordinates. It separates environment,
actions, intention, lifecycle, speech, subjective history and retained prehistory;
unknown types have no scalar fallback. Version 2 additionally re-derives typed
biographical coordinates from the exact displayed parent, field/ID and logical
time; this grants coordinate use only, never an unlisted event or activity.
Version 3 retains the accepted observation-value hash and requires an exact
consumer-selected quotation before granting predicate-bound Fact use; the whole
Observation has no direct Fact-value permission. Current and historical scopes
remain distinct. Historical Fact RecallDocument adaptation and semantic admission
remain incomplete. No real
provider calls or continuation changes. See
`docs/design/life-source-field-readings-2026-09-16.md`.

Life now retains its original Capsule in private snapshot identity and preserves
it through Recall/prefetch/capability joins. The structured author binds the exact
sent messages/tools and source catalogue before calling; configured durable turn
stores retain and revalidate that preparation. No archive enters model material.
This is not a semantic review: unpresented catalogue material, added sources and
field permissions remain unqualified. 265 checks pass,2 factual blockers xfail;
no paid calls or continuation changes. See
`docs/design/life-source-view-binding-2026-09-16.md`.

Selected source compilation is now independent of chat ModelInput, reusing the
same activity, settlement, prehistory and subjective permissions. Chat adapters
retain exact input/current participant binding; four old/new outputs are byte
equal and all20 cold receipts survive. Generic tables have no author-view or
write authority. Life must still bind its actual snapshot/provider selection
before semantic review and durable admission. No paid calls; runtime and billing
continuation remain unchanged. See
`docs/design/shared-selected-source-compilation-2026-09-15.md`.

Whole-Life candidate reading now inventories all text fields, including summary,
appraisal, thread reasons and newly nested prose. Its versioned inspector proves
field coverage only, never semantic completeness or source/write authority.
Real v2 fixes some current-feeling/future-intention classifications but still misses
an appraisal's walk premise and misreads mental closure as an external event.
It is not wired to acceptance. 22 mechanical checks pass; three calls are closed
and reconciled (known CNY0.0449115 plus unknown hold0.328752). Runtime remains
evidence-order trial; billing now comes from life-candidate-reading trial02,
1040 usage/1037 reservations. See
`docs/audits/life-candidate-reading-validation-2026-09-15.json`.

Life correction now carries the exact rejected provider result (summary and all
proposals), bound to the original pinned request, invocation hashes and author.
Core retains one correction; overbound originals remain technical failures, not
truncated data or null choices. The source catalog is unchanged. Coverage errors
now precede derived status errors. This supplies the correction boundary only;
Life semantic factual acceptance remains missing. See
`docs/audits/life-role-correction-validation-2026-09-15.json` (no new paid calls).

The actual earlier Life author received a walk-under-way result and an explicit
activity-ended-not-intention-fulfilled boundary, but wrote a finished walk across
its response, summary, appraisal meanings and thread reason. Response, Experience
and Appraisal were accepted; current acceptance checks provenance, not embedded
factual semantics. A public SQLite regression reproduces the gap (14 passed,
2 strict xfailed); the latter are unresolved blockers, not qualification passes.
See `docs/design/life-response-factual-acceptance-gap-2026-09-15.md`.
The historical unified runtime/billing checkpoint was
release-chat-evidence-order-20260915-01/run, 1037 usage/1034 reservations; it is now
only the runtime source, with the newer complete billing ledger listed above. Its
20 known calls cost approximately CNY0.466526. All20 old
receipts cold-verify, without erasing 7 known historical semantic false accepts.
See `docs/audits/expression-evidence-order-validation-2026-09-15.json`.

The new memory_representation_draft module stages exact-reader-bound compression
inputs and content-addressed unaccepted drafts in the existing life sidecar.
Drafts have no World visibility descriptor, verified author association, or
ordinary retrieval authority. 82 checks and 10 old cold receipts pass; a real
log clone retained identical projection/readings after draft persistence/reopen.
CharacterInterior authoring, semantic acceptance and the authorized compressed
reader remain to be connected. See
`docs/audits/release-memory-representation-draft-validation-2026-09-14.json`.

All memory lanes now require the exact candidate image at the pinned cursor
before original-content reads. Compression/opaque replacement cannot restore
source detail; native correction aliases require exact source event/hash and
cannot erase prior compression. 140 checks and 10 old cold receipts pass;
the previous real-cursor recall trace is unchanged. Compressed representation
storage/authoring/reading itself remains unimplemented. See
`docs/audits/release-memory-read-lifecycle-validation-2026-09-14.json`.

Recall hybrid.7 now counts independent query overlap groups, excludes unqualified
dense similarity from fusion, and packs complementary query cues before source
diversity ties. The original natural question now retrieves the admission-envelope
memory in an offline recompile of the same 39 documents and pinned query; exact
model reading and both archive/record proofs survive the original byte ceilings.
174 related tests and 10 old cold receipts pass. No new model turn occurred, so
natural character use remains unqualified. See
`docs/audits/release-independent-recall-cues-validation-2026-09-14.json`.

Historical dating windows now average the existing recency curve (recall hybrid.6);
an unknown birth-to-start interval no longer inherits its newest possible date.
This does not change source dates or close the natural-query recall failure.
The missing compressed representation chain is recorded in
`docs/design/memory-representation-gap-2026-09-14.md` and remains unimplemented.

A subsequent read-only unknown-bill audit found no uniquely associated uncounted
usage across 39 capture files / 347 requests; all 17 cumulative unknown holds
remain. Future native reservation IDs now travel in local HTTPX extensions to
private capture records, labeled client_declared, with audit association still
unverified. This changes neither provider input nor settlement authority.
119 provider/capture checks pass; no paid calls or budget release in this step.
See `docs/audits/release-reservation-capture-validation-2026-09-14.json`.


Latest witness experiment made one actual paid call on the full frozen evidence.
The model correctly called the unrecorded seating an external_fact/unclosed,
explicitly distinguishing self-speech, intention and environment. The actual
trial still failed consumer validation: explanations were 609/501/798/462 chars
against the original hidden512 limit; pointers used the full source_materials
packet path. Inspection.2 accepts only the exact selected material's canonical
packet index (or the original relative pointer), and bounds explanations at1024.
The same preparation/provider request and raw returned bytes replay unchanged.
31 checks pass; model verdicts are closed/closed/unclosed/closed (the last Beat
was originally expected source_free, now grounded in counterpart source30).
No accepted receipt/Action authority is conferred and fresh model qualification
remains open. One known call cost estimated CNY0.103401, latency5469ms, no new
unknown hold. Latest budget: `release-witness-real-20260914-01/run/world.sqlite`,
173 usage/170 reservation rows, native committed14.3817294, remaining0.2182706.
Runtime remains trial08; always inherit the newest cost ledger alongside it.
See `docs/audits/release-witness-real-and-consumer-validation-2026-09-14.json`.


A non-authorizing, single-call witness experiment now prepares complete evidence
and inspects per-clause model readings: exact text coverage, original/displayed
quotes, source indexes, explicit participant roles, and declared source scopes.
The offline CLI is `scripts/inspect_visible_source_witness.py`. It is not v9 and
is not wired to production review, receipts or Actions. 19 checks and 5 frozen
source permission probes pass. Crucially, a model that misclassifies an actual
occurrence as recorded speech still passes structural checks; the result always
says semantic qualification unproven and receipt_authority false. Do not count
this as closing the original semantic blocker. The complete frozen request's
reservation projects to CNY0.317904 at the unchanged 4096 output ceiling, versus
remaining0.3216716; no paid call or ledger change was made. Next measure actual
model classification and evidence readings before considering deployment.
See `docs/audits/release-source-witness-experiment-validation-2026-09-14.json`.


Latest paid utterance-scope qualification is FAILED. Two full-source semantic
comparisons matched all expected judgments, including prior speech and historical
intention positives. Offline formal-preparation comparison found JSON key ordering
different despite identical parsed content. A third fresh call using the exact
formal request again closed the unsupported morning seating with refs 55/57
(prior self-expression), 20 (lifecycle/intention), and 11/12/13 (environment).
Isolated receipt admission incorrectly accepted it. No causal claim about key
order is supported; the annotation alone is insufficient. No World receipt or
new character dialogue was created; 10 old receipts still cold-verify.
Three known calls cost an estimated CNY0.169973, no new unknowns. Latest budget
source is `release-utterance-review-20260914-02/run/world.sqlite` (172 usage rows,
169 reservations), native committed CNY14.2783284 against 14.60; headroom0.3216716.
Runtime remains `release-prehistory-real-20260914-08/run`. A continuation must
combine that closed runtime with the newest complete cost ledger, never resume
an older cheaper ledger. Next investigate inspectable proposition/source support
in reviewer output instead of further prompt-only scope assertions. All original
release gates remain open. See
`docs/audits/release-utterance-scope-real-validation-2026-09-14.json`.


Latest source-review inspection identifies a concrete self-citation path: the
frozen v8 false acceptance first cites source 55, an earlier companion utterance
of the same unsupported seating claim, before lifecycle/environment references.
The selected-source producer now attaches `recorded_companion_utterance_only`
scope plus explicit content/delivery non-authority boundaries. This preserves
speech-recollection evidence, original text and proofs; it does not make a local
semantic decision. All eight reviewer views carry the annotation. 64 related
checks and 10 old cold receipts pass. The frozen incorrect verdict still passes
the structural parser: real entailment improvement remains unqualified, with no
new paid call. See `docs/audits/release-companion-utterance-scope-validation-2026-09-14.json`.


Strict atomic v3 now has an explicit, default-off local schema reference option.
It factors exact repeated schemas using DeepSeek's documented `$ref` / `$def`;
expansion must equal the original, and the final wire remains digest-bound.
Initial, after-recall and final contracts share the option. Host composition and
isolated CLI record it; no production default is changed. The frozen trial08
request saves 29766 bytes in tools (53550 to 23784), projecting the single author
reservation from CNY0.573108 to 0.483810. This is not provider acceptance, actual
billing, or admission for the whole author/correction/review chain. 361 distinct
checks pass, with no paid calls, hold releases or live-state changes. See
`docs/audits/release-local-schema-reference-validation-2026-09-14.json`.


Latest offline recall inspection reproduced trial08's exact prefetch at cursor
1386. The target memory was eligible but not selected. Corpus.5 now indexes only
the participant/place labels already carried by each retained historical reading;
record text, privacy and dual proof stay unchanged. Specific place cues improve,
but the original natural question still misses the target. Scoring was not changed.
Provider Affect views now omit accepted change/transition IDs from otherwise intact
appraisal references, after redaction. Canonical snapshot bytes/id/hash and full
authority remain unchanged. Applied alone to the frozen request, this saves 8087
wire bytes and projects its reserve from CNY0.573108 to 0.548847; that still exceeds
the remaining CNY0.4916446. This is offline projection, not measured provider savings.
171 distinct tests and 10 cold receipts pass, with no paid call or new allocation.
Trial08 remains the budget authority. Natural recall, larger prompt/tool costs,
and all original live release gates remain open.

Latest real trial08 imported the independently reviewed 24-record archive into
the isolated World. The role retained admission preparation, a borrowed camera,
and a borrowed notebook in three explicit choices; 21 remain uninitialized.
One natural question produced a delivered objection to interview-style questioning,
but none of the three new memories was in the actual author input. This proves
retention and a valid reply, not natural autobiographical recall. Six known calls
cost about CNY0.346122 (initialization 0.1418256, online chain 0.2042964), no new
unknown holds. Trial08/run is now the continuation source: 169 usage rows, 166
reservations, native committed CNY14.1083554 / 14.60. The remaining CNY0.4916446
cannot reserve the prior author's CNY0.573108 request; continue offline first.
Offline inspection also removed invalid Affect ID coercion: update/supersede must
not become open, and resolve must not become no_change. The role must reselect
through existing Core correction. Cross-episode component failure now identifies
the exact parent mismatch; 138 checks and 10 cold receipts pass. No live test of
this stricter path yet; original recall, factuality, life and cost gates stay open.

Offline creation tooling now prepares bounded author/reviewer requests from a
read-only original WorldStarted, accepted archive manifests and pinned profile.
A review package must cover the exact brief, document and every record before it
can become a ReviewedPrehistoryArchive; importing and remembering remain separate.
Reviewer identity is operator-provided provenance, not independent-execution proof.
A 24-record Celia life candidate (2012-2026) has an independently captured
DeepSeek semantic approval; trial08 above is the later import evidence. Two earlier outputs
failed on positive findings in a blocking-only field, then a copied hash typo;
neither was repaired locally or retrospectively approved. A new semantic protocol
returns indexed judgments while code binds hashes to the exact submitted packet.
The offline binder trusts operator provenance; the real runner separately checked
the actual request, full response and usage. 41 workflow/binding tests pass.
At that review-only stage, three calls cost an estimated CNY0.082023 with no new
unknown holds; review03 carried 163 usage rows / 160 reservations and native
committed CNY13.7622334. Trial08 above supersedes this continuation source.
Natural recall of the richer archive and historical summaries remain outstanding,
alongside the original release gates.

Paired expression now compares selected historical readings using the same chat
semantic normalization and exact source_ref at both materializations. Full record
and archive proof remains independently validated in the original requirement.
This fixes the observed full-vs-compacted claim capability mismatch; it does not
implement long-term memory compression. 75 checks and 9 old cold receipts pass.
Real trial07 still authored unsupported details and also exposed an Appraisal
component outside the active head. The new paired fix is offline-qualified only;
real retention and recall of richer reviewed archives remain part of the active goal.

Latest boundary evidence: the next real trial produced two unsupported historical
episodes with empty claims; whole-body v8 review correctly rejected both, followed
by correction timeouts and zero deliveries. Shipped profile/shared prompt wording
that allowed unwritten color or fuzzy private memory has been clarified: private
feelings and imagination remain character-owned; uncertain recall does not supply
missing autobiographical events, including pre-start ones. This is a prompt fix,
not a semantic filter or automatic memory creation. 68 checks and 9 old cold
receipts pass; no real-provider acceptance after this prompt change. Future trials
must pin the changed profile hash while retaining old archive provenance and all
usage/unknown holds. Original review/life/longitudinal release gates remain open.

Current 2026-09-14 qualification: hybrid.5 real calls now received the retained
school excerpt, but explicit `past_world` bookkeeping rejected its Recall ref.
The request-aware claim matrix now accepts only original selected or sealed,
same-cursor, actually presented historical readings. Citing the item or record
binds both record and reviewed archive events into Proposal evidence. This adds
no current-world, counterpart/shared-history or stable-identity capability.
101 offline checks and 9 old cold receipts pass; the claim fix has no subsequent
real-provider acceptance. Unsupported embellishments and correction failures
still block release. The real trial added 6 calls, estimated CNY0.1073741 known
and CNY0.932127 unresolved holds; no new delivery or fresh budget allocation.

The companion's accepted fictional history before this World's original
runtime boundary, with its own creation provenance. It may become remembered
personal history but cannot establish a runtime activity or an unconfirmed
shared event with the real user.
Reviewed archives bind the original WorldStarted, explicit historical identities
and individual record hashes. Import records acceptance at current Logical Time
and creates no MemoryCandidate; remembering remains a separate character choice.
The operator review artifact binds content but does not itself prove semantic
consistency. Ordinary chat must not bypass memory access by reading the archive.
MemoryCandidate now recognizes a separate `prehistory` source; source reads require
both the imported record and reviewed archive, exact actor, current candidate and
privacy. Recall retains occurrence precision and the `character_prehistory` scope.
Explicit initialization now calls the same CharacterInterior memory purpose at
the original import commit cursor. Each call considers at most one record;
retain/no-change, bounded technical retries and paid-result recovery have durable
records. Ordinary chat now selects only the owner's active retained excerpts;
Capsule and visible review bind both the record and reviewed archive. Historical
dates/identities remain visible, without exposing the whole archive or granting
runtime occurrence/current-user shared-history authority. This is offline chain
coverage, not real-model semantic qualification. A compressed/replaced summary has no installed historical
summary reader and must not fall back to the original archive's details.
Core-owned selective Recall now carries historical identities into InnerLifeSnapshot.
Visible review can append same-cursor history actually presented to the winning
author, using the sealed recall result rather than new archive reads. Original
source indexes remain fixed; replay requires matching independent author recall
audits. This path has offline application/receipt coverage for v1 and v3/v8,
not real-provider recall or factual-entailment qualification.
Multi-record initialization uses the bounded historical Context reader only
for an opportunity that exactly matches its durable original import commit.
This prevents the first retention write from invalidating later initialization;
ordinary stale role/chat requests keep the live-head guard. A real trial retained
one record, then exposed this bug before a second provider call. A subsequent
real continuation retained the second and delivered two chat responses, but did
not qualify substantive history recall: lexical-ngram.1 rejects a lone two-CJK-
character cue even when the active historical memory is readable. Fixing bounded
recall requires preserving old context/receipt reproducibility; it must not force
the character to answer or authorize persona prose as a runtime occurrence.
Automatic recall hybrid.4 supplements exact lexical coverage with inverse
frequency over eligible documents only. This gives short distinctive cues access
under the existing byte cap without altering the legacy Context rank policy or
requiring another provider call. Direct prefetch readings and wrapped selected
recall have separate presentation shapes; source review verifies each exact
shape. The frozen failed corpus now retrieves the school memory offline; real
conversation qualification after this change failed on longer ordinary wording:
old appraisal/private-impression hits still displaced history; reviewer inputs
lacked historical evidence, and no reply was delivered. Source-exact overlap
with already-present working context and allocation under the existing byte cap
remain the next diagnosis. Do not infer role silence from these technical failures.
Hybrid.5 separates source-proof storage from the character reading budget:
readings retain a 6 KB UTF-8 cap, complete result hits have a 12 KB cap, and the
complete replay audit retains its existing 32 KB limit. No proof is stripped to
make an excerpt fit. The failed longer query now retrieves history offline;
seven anchored wording probes pass on the frozen corpus. Real-model behavior,
unanchored semantic recall and the other release gates remain unqualified.
_Avoid_: Retroactive runtime Experience, improvised chat evidence

## Autobiographical Memory

The companion's accessible recollection and interpretation of sourced personal
history, whether Character Prehistory or runtime Experience. Its detail,
accessibility and interpretation may change without rewriting the source history.
_Avoid_: Complete historical archive, unsourced factual authority

## Routine Background

A captured configuration of habitual windows, supplied as advisory context.
It is neither today's Plan nor evidence of a lived episode. Canonical snapshot
compilers .23/.24 bind it into snapshot identity separately from the dated
calendar view, retain all configured habits, and preserve it through Recall,
prefetch and capability binding. Reading the same new snapshot cannot reload
a changed global routine configuration. Earlier snapshot identities retain
their legacy decoding and rendering. Habit data grants no World fact refs.

## Life Arc

An accepted, long-lived chapter such as an internship, job, residence, trip,
or sustained personal undertaking. A Life Arc contributes a reviewed Context
Pack while active and may introduce or retire places, NPCs, and activity
possibilities. It starts only from an authoritative settled consequence and
ends through an explicit event; daily activities remain separate Plans and
Experiences.
_Avoid_: Daily activity, unsourced backstory, permanent persona rewrite

## Major Biographical Transition

An accepted, durable change to a foundational life coordinate such as
education status, work status, or primary residence, which changes the future
possibilities available in the World. A Life Arc may express the chapter that
follows, but cannot replace a foundational coordinate. An accepted occurrence
settlement may carry either an objective coordinate consequence already made
true by its exact selected branch, or a separate Character-Model-authored
subjective direction. Objective and subjective namespaces are disjoint. Prose,
unselected World-Author options, or ordinary Life Arc tags cannot substitute
for the transition or silently override it. Active aspirations are sourced
inner context, not catalog-to-plot mappings: only a Character-accepted open
Plan may explicitly bind the aspiration's planting event and atomically
crystallize it.
_Avoid_: Prose-only Life Arc, daily Plan, retroactive backstory, predetermined life path

## Context Pack

A source-bound bundle of capabilities and environmental coordinates attached
to a Life Arc or biographical phase. It says which resources, places, people,
and effects are currently available; it neither supplies a finite activity
menu nor instructs the character to choose a development.
_Avoid_: Plot script, mandatory routine

## Life Influence

A sourced interaction, memory, relationship change, Affect episode, or
unfinished matter made visible to life deliberation. It may change what the
character notices or chooses, but the system never maps an influence directly
to an activity. A user's place mention may become inspiration for a future
Plan; it never proves that the companion visited.
_Avoid_: Keyword-to-event rule, retroactive Experience

## World Author

A model authority that may propose fictional environmental opportunities,
contingencies, provisional people, and objective outcomes inside the
companion's World. It cannot decide the companion's motives or responses, and
its output remains a Proposal until admitted and settled.
_Avoid_: Plot director, character model, deterministic event table

## World Consequence

A sourced environmental change or objective consequence of an authorized
execution. Its occurrence does not decide the companion's response, and a
consequence cannot supply missing authority for a new character action.
_Avoid_: Character response, retroactive action permission

## Character Life Response

The companion's own reading of an encountered World change and any future
activity she chooses in response. Her reading is not a new external fact, and
her intention is not proof of completed execution.
_Avoid_: World-Author-written behavior, automatic emotional reaction

## World Life Intent

A future self-directed activity authored by the companion while experiencing
an exact settled World event. Its source contract binds that settlement and
the original Character Interior decision to a Plan; starting and ending still
require the activity lifecycle. It grants no location, other-actor, external
delivery, or completed-outcome authority.
_Avoid_: Inbound-chat authority, World-Author-authored action, completed experience

## Capability Manifest

A revision-pinned declaration of the World effects currently available to a
model, including their scope, consequence budget, and required evidence. It
describes what may be proposed without suggesting what should happen. A
location capability binds an opaque stable ref to the exact place, privacy
floor, local schedule or accepted-Plan interval, admission kind, and authority
source; a current-presence snapshot carries a finite wake-bound horizon rather
than promising future presence. A bare location ID is never executable
permission.
_Avoid_: Plot menu, behavior recommendation

## Life Review Evidence Packet

A minimal, capsule-bound input to the independent Life source reviewers.
General closure receives only its frozen, lane-owned World Author fields, exact
immutable events cited by existing-world claims, the exact selected location
capability, existing-entity refs, and the full manifest/cursor identity. Outcome
text enters this lane only when a typed location needs an exact consistency
coordinate. An entity ref gains descriptor evidence only through an exact
structural ref join to a source-bound item in the already selected Context; the
join never guesses a name or alias, and a missing match is non-evidence.
Focused novel-origin review additionally receives every item already selected
and bounded by the relevant character, current-life, dialogue, relationship,
thread, appraisal, Affect, accepted-fact, recent-experience, world-life,
Private-Impression, and perception slices. Source-bound item hashes are checked
against the transported value. A projected baseline preserves the original
Capsule item hash separately from the hash of the review-visible value and can
only signal uncertainty; it cannot prove a claim or rejection. Unconsolidated
Memory Candidates are non-authoritative and excluded. The compiler removes
transport-only resolver, rank, and budget metadata, but never keyword-filters,
re-ranks, or applies a second item cap. Focused manifest descriptor entries are
hash-bound pointers into those same transported items, not duplicated semantic
copies. Packet contract plus exact request bytes bind current replay identity;
historical proposal versions retain only their historical identity formula, so
changed evidence or compiler bytes cannot reuse an old result. Its compiler has
no verdict or character behavior authority. Life and
visible conversation may reuse provider route configuration and an HTTP pool,
but their reviewer circuits, suppression state, active tasks, and shutdown
leases are separate. Locally installed strict JSON schemas remain distinct from
release-qualified route evidence.
_Avoid_: Full creative Context copied into a classifier, local fact verdict,
background failure suppressing visible review, unqualified route reported ready

## Provisional NPC

A model-authored person that exists only inside a Proposal or unsettled
occurrence. It becomes a referencable World NPC only after the introducing
occurrence settles with sufficient evidence. A proposal-scoped novel place is
likewise not reusable before settlement. After the introducing outcome settles,
its exact source-reviewed descriptor may materialize as a typed `attempt_only`
place capability; missing or hash-invalid descriptor bytes fail closed. This
proves only stable place identity and permission to attempt a later visit, never
opening hours, access, or a completed visit.
_Avoid_: Pre-registered future acquaintance, invented historical fact

## Outcome Resolution Envelope

A frozen authorization for resolving an unsettled occurrence from later
evidence within a bounded set of World capabilities. The current implementation
stores a small set of possibilities authored afresh by the World Author for
that exact context, including their relative plausibility and effect bounds.
This is not an operator-authored plot catalog, but it is intentionally frozen
before settlement so replay cannot silently change the past. A subjective
long-term direction may only be authored freely by the Character Model while
resolving an objective result; World Author does not offer direction choices or
motives. The character may author one structurally closed subjective-direction
coordinate replacement or none. A candidate may separately declare an open,
objective biographical consequence only when the independent source review
finds it entailed by that exact branch; any resolution authority can install it
only by selecting and settling that branch. The validated model audit, exact choice, optional direction,
Outcome Proposal, Acceptance, Settlement, and appraisal trigger share one
pinned CAS transaction; old Context is never carried across a later Clock.
_Avoid_: Operator-authored ending list, predetermined selected result

## Proposal

A candidate produced by a model, rule, or recorded draw that has not yet been accepted as a World Event.

## Committed Experience

A referencable experience derived from a settled activity or confirmed shared event. Character background, model prose, failed delivery, and uncompleted Plans are not Committed Experiences.

## Action

A traceable attempt to produce an observable online or external effect. An Action has one terminal outcome: delivered, failed, cancelled, expired, or unknown.

## Dedicated Companion

A character and World exclusively assigned to one user, with no memories, relationships, budgets, or private evidence shared with another user's companion. The current deployment has one Dedicated Companion; future multi-user service creates one isolated companion scope per user.
_Avoid_: Shared public character, multi-user persona

## World Administration

An authenticated management operation over a Dedicated Companion, such as export, archival, correction, privacy revision, or a future reset/fork. It is initiated through the management panel and becomes an auditable command or event; ordinary QQ dialogue is not an administration interface.
_Avoid_: Chat command, direct database edit

## System Notice

A platform-authored statement about technical availability, maintenance, delivery, or billing that is visibly distinct from character Expression and cannot create World or relationship facts.
_Avoid_: Character excuse, fallback role message

## Adult Eligibility

A current, independently attested qualification that both the user and fictional character are adults for an adult-content capability. Relationship stage, conversational wording, appearance, or model inference cannot establish it.
_Avoid_: Age guess, relationship proxy

## Intimate Consent

A recipient-, channel-, content-class-, and capability-scoped permission that is explicit, current, and revocable. It permits consideration or delivery inside its scope but never proves desire or commands the character to participate.
_Avoid_: Relationship stage, blanket permanent consent

## Intimate Expression

A Character-authored romantic, sensual, or sexual communication whose content class remains explicit through planning, provider execution, delivery, and memory. Its existence and form belong to the character; Adult Eligibility, Intimate Consent, privacy, and provider capability remain hard prerequisites.
_Avoid_: Intimacy script, consent grant

## Capability Allowlist

The currently deployed set of external effects the companion may request. An unspecified future capability is unavailable until explicitly designed, authorized, qualified, and added; extensibility does not imply latent permission.
_Avoid_: Open-ended tool access

## Fast Reply Interface

The production interactive expression path. One Character Model request owns the
complete choice and is incrementally exposed as validated expression units; transport
packaging cannot change whether, what, or how many messages the character chose.

## Delayed Attention Reply Interface

A retained complete-response capability reserved for a future, explicit character-owned
choice that the companion was unavailable or did not attend in time. It is disabled and
not selected by current production routing; technical failure must never activate it or
pretend the character was busy.

## External Result

A recorded outcome from a model, random draw, media generator, tool, network, clock, or platform receipt that replay must not invoke again.

## External Signal

A source-bound, versioned claim that something outside the World may be
happening or becoming salient. It records what a source reported, with time,
place, confidence, evidence, expiry, and correction lineage; it is neither a
World Event nor proof that the companion perceived or believed it.
_Avoid_: News fact, World Event, character knowledge

## World Perception Hub

The bounded context that acquires, normalizes, clusters, and retrieves
External Signals from replaceable sources. It may offer a sourced perception
opportunity, but it cannot decide the companion's attention, interpretation,
life response, or communication.
_Avoid_: News engine, event generator, behavior trigger

## Perception Candidate

An ephemeral, source-bound External Signal that is plausibly accessible or
relevant to the companion at one Pinned Turn. It is advisory input to
attention and may expire without entering the World ledger.
_Avoid_: Perceived fact, mandatory stimulus

## Perception Channel

A source-bound capability explaining how the companion could plausibly
encounter an External Signal at one Pinned Turn, such as a public alert,
available online feed, current-place medium, accepted NPC report, or authorized
search result. It proves access, not attention, belief, interest, or action.
_Avoid_: Behavior trigger, invented browsing history, source authority

## Perception Window

A frozen, expiring packet of exact External Signal revisions, correction and
conflict evidence, and available Perception Channels offered to one
source-bound character attention attempt. It is not a Context Capsule, a World
fact, or proof that any candidate was noticed.
_Avoid_: News digest, character knowledge, behavior menu

## External Perception

An accepted World Event recording that the companion encountered a particular
revision of an External Signal through a plausible channel. It proves the
perceptual experience and its evidence lineage, not that every source claim
was objectively correct or that the companion must act on it.
_Avoid_: Internet truth, automatic life event, mandatory response

## Photo Candidate

A rebuildable Projection entry indicating that a Committed Experience may have enough visual and sharing value to become media. It is not permission to generate or send anything.

## Media Opportunity

The World's frozen selection of one Photo Candidate for possible rendering. It identifies one Committed Experience, chooses life-share or character-media, and sets a privacy ceiling.

## Media Plan

One evidence-bound, replayable photographic interpretation of a Media Opportunity. It selects exactly one primary visual subject, capture authorship, visual form, sharing intent, polish, tone, and privacy level without changing World facts or deciding whether to send.

## Media Render Profile

A versioned, frozen declaration of how one Media Plan is rendered: its model ecosystem, rendering route, identity-binding method, supported controls, cost policy, and capability status. A Media Render Profile may only claim a capability proved for that exact route; it does not select an event, reinterpret a plan, or decide delivery.

## Identity Binding

The precise mechanism by which a Media Render Profile preserves character identity: `reference_edit`, `style_reference_unverified`, or `compatible_lora`. Identity Binding is a renderer capability, not a property of an image file; a LoRA cannot be presumed compatible across model ecosystems or provider routes.

## External Generation

One persisted submission of a frozen Media Plan to an external rendering provider. It records the render profile, provider receipt, request hash, terminal result, cost and artifact hash so replay never resubmits or changes the plan.

## Media Lane

The frozen semantic kind of one new character-media plan: `ordinary_life` is pure event sharing; `alluring_life` is event-grounded life sharing with visible feminine or hormonal expression; `exclusive_private` is the legacy evidence-bound recipient display; `suggestive_private` and `explicit_private` are separately routed high-private render lanes. `explicit_reserved` is a historical non-renderable proposal value. A Media Lane is suggested by the planner model and accepted only by deterministic candidate, capture and privacy checks; upstream authorization, relationship and delivery policy are not reimplemented here.

## Suggestive Private Media

`suggestive_private` is the adult-fictional, recipient-exclusive high-private Media Lane for a strongly recipient-directed private expression. It reuses the ordinary Media Plan and photographic contracts and freezes a dedicated rendering route. It never silently degrades to a normal renderer or lower Lane.

## Private Render Contract

The replayable high-Lane portion of a new Media Plan: lane, attraction mechanism, framing mode, coverage mode, visibility tier and dedicated renderer route. It is not an authorization decision; the upstream environment owns authorization, relationship and sending policy. Legacy Suggestive Media Authorization payloads remain readable only for replay of historical plans.

## Suggestive Private Contract

The historical v1 high-Lane contract containing a frozen authorization. It is retained solely to replay old Media Plans; new plans use `PrivateRenderContract`.

## Recipient Access

The frozen intended scope of one Media Lane: ambient, recipient-directed, or recipient-exclusive. It describes how the image addresses its audience, not who is allowed to possess the artifact after delivery.

## Attraction Expression

The frozen communicative intensity of a Media Lane: `none`, `feminine`, `charged`, `sexual_suggestive`, `explicit_adult`, or the historical reject value `explicit_reserved`. It is distinct from visible skin, outfit category and relationship stage; those remain separate evidence and upstream policy concerns.

## Media Inspection

A recorded visual assessment of media. It states whether the artifact is deliverable and describes what is actually visible, including deviations from the Media Plan.

## Appearance State

A time-bound World Projection of visible character facts such as current hair arrangement, outfit role, grooming, and accessories. It must be sourced from committed history and is optional in a Media Opportunity snapshot; generated media never writes it back automatically.

## Subject Presentation

The frozen, shot-local way a character appears and performs in one Media Plan: appearance source, head and shoulder orientation, gaze, expression, posture, gesture, and photo awareness. It is not a World fact or a media category, and identity references must not silently override it.

## Media Interaction Bid

The response a character hopes one delivered Media Plan may invite, including its communicative goal, hoped response, and response pressure. It is an invitation rather than a claim or obligation. Planning freezes it, but only a confirmed delivery may open a pending World interaction state.

## Media Address Strategy

The frozen, whole-image way a Media Plan addresses its intended recipient: observational or direct stance, engagement tactic, disclosure, staging, temporal beat, visual priority, and expression charge. It translates a Media Interaction Bid into photographic communication without deciding whether to send.

## Camera Geometry

The frozen physical camera contract for one Media Plan: distance, height, view axis, pitch, roll, orientation, subject occupancy and placement, environment share, focus, imperfection, and device visibility. It is independent of Capture Mode, which identifies who operates the camera.

Version 2 also freezes camera-to-face distance and the face's radial position in the frame so a front-camera image is not reduced to one arm-length ratio and wide-angle edge distortion can be reasoned about explicitly. Version 1 payloads remain immutable.

## Photo Display Strategy

The shot-local social performance used to make a Media Interaction Bid visually legible, such as playing innocent, sharing restrained pride, or presenting a mishap deadpan. It belongs to Subject Presentation, not Affect or World truth, and freezes a coherent expression recipe rather than independently composed facial axes.

## Facial Display Strategy

The semantic, recipient-facing family of one visible facial performance, such as amusement leaking, deliberate cuteness, mock defiance, tender privacy, or direct/withheld desire. It describes communicative display rather than inferred inner emotion.

## Facial Micro-Performance

The frozen visible actions of one still-frame facial beat: brow, eye aperture, current gaze, nose/cheek action, mouth, asymmetry, intensity, authorship, temporal phase, and energy. The name refers to fine-grained visible performance, not a scientific claim that a static image proves a temporal microexpression.

## Photographic Authenticity Profile

The frozen whole-image phone-photography behavior of one Media Plan: device rendering, exposure and color compromise, processing, scene orderliness, one credible capture imperfection, environmental entropy, regional grounding, and aesthetic intent. It never adds unsupported location facts and does not equate authenticity with blanket noise, blur, clutter, or poor quality.

## Relationship Stage

A slow projection of settled interaction history. It influences likely choices and their cost but does not grant the user control over the companion or act as a context-free vocabulary licence.

## Affect

A sourced, time-varying feeling and residual tendency. Affect influences deliberation and action but cannot authorize life facts.

## Character Core

The companion's stable identity, values, preferences, boundaries, and experience-supported long-term continuity.

## Self Core Projection

A deterministic summary of Character Core, current goals, relationship, and committed continuity. It is a read model, not a free-form write authority.

## Character Interior

The actor-scoped deep Module that owns the single production boundary for
subjective integration and Character Decisions. It deterministically projects
one Inner Life Snapshot and lets the actor model either experience a committed
stimulus or consider one opportunity. It coordinates instantaneous private
self, attention, selective Recall, Appraisal/Affect, emotional continuity,
subjective relationships, aspirations/conflicts, impulses and pre-expression
stance without owning their durable domain authorities. World facts, typed
acceptance, privacy, consent, safety, Actions, CAS, receipts and replay remain
outside it. Production callers do not assemble an alternative self or invoke a
role model beside it.
_Avoid_: Mind database, behavior controller, role-model facade over old paths

## NPC Actor-Isolated Semantic Domain

The explicitly approved low-cost autonomy boundary for NPCs. Each decision is
bound to one NPC actor ref and that NPC's private capsule; it must not read or
reuse the protagonist's Inner Life Snapshot. Its durable result becomes a
source-bound World stimulus that the protagonist may experience through her
single Character Interior. This is not a second protagonist author and it is
not permission to add arbitrary actor-model lanes: production composition,
health and the architecture guard recognize only the registered NPC Ecology
domain. A future full NPC Interior requires a separate actor-scoped instance
and private authority.
_Avoid_: Shared protagonist/NPC mind, unregistered actor model, NPC plot script

## Inner Life Snapshot

The canonical, deterministic, source-bound read model compiled by Character
Interior for one actor, complete ledger cursor, Logical Time, privacy scope and
compiler version. It keeps Character Core, Situation, concurrent Appraisals
and Affect episodes, directional relationships, Private Impressions, open
Threads and Commitments, aspirations, goals, recent self Experiences, memory
candidates, perception and bounded Inner Advisories legible together without
flattening them into one mood or behavior verdict. Each item retains its
source, authority, availability and expiry; deterministic purpose views may
only redact or remove items. The snapshot has a stable semantic hash and is
not a second truth store or a free-form model write authority.
_Avoid_: Current personality rewrite, mutable mind blob, consumer-specific self

## Inner Turn

One actor-owned, effect-once instance of private integration or choice. It
binds purpose, stimulus or opportunity identity, Pinned Turn cursor, Inner
Life Snapshot hash, capabilities, privacy scope, role-provider request, Recall
and correction lineage, final Private Turn State and terminal result. A new
Observation or cursor invalidates an older unaccepted result. Apart from
recorded actor-chosen Recall and one constrained reselection by the same role
model, one opportunity cannot have a second character author.
_Avoid_: Chat session, hidden chain-of-thought, parallel role winner

## Inner Transition

A Character-Model-authored proposal that a committed stimulus may have changed
one or more subjective domains, or changed none. Character Interior routes its
source-bound items to the existing Appraisal, Affect, Relationship, Private
Impression, Memory, Aspiration, Goal, Thread or Commitment authorities. It
cannot replace those projections and technical failure is not `no_change`.
_Avoid_: Inner-state replacement event, deterministic emotional reaction

## Inner Decision

The auditable result of Character Interior considering one opportunity. It
contains the same Inner Turn's final Private Turn State, optional typed Inner
Transitions and one purpose-specific Character Decision. It grants no World or
Action authority by itself; the corresponding Acceptance and Action layers
still validate every effect.
_Avoid_: Behavior policy verdict, unpinned model reply

## Historical Current Self State Contract

`current-self-state.1` is the former compact provider view that preceded Inner
Life Snapshot. Its recorded payloads remain readable by historical replay
codecs so old Model Results and events retain their exact meaning. New
production compilation, provider requests and business callers must not emit,
accept or depend on this contract; it is not a compatibility route or a
synonym for Inner Life Snapshot.
_Avoid_: Legacy production fallback, second canonical self

## Private Turn State

A concise, free-text, role-model-owned account of what is salient to the
companion as she forms one Expression choice. It is a required member of the
same model result that selects silence, timing, questions and Expression
Beats, and its attended source refs must come from that Inner Turn's Inner Life
Snapshot. JSON
object member order is transport serialization, not evidence of causal order.
It is
audit material bound into Proposal identity, not hidden chain-of-thought,
World truth, a behavior category, a reply-mode switch, or a durable memory.
Attended refs record only what material was available to the character's
attention; they are not fact evidence and the private summary does not enter a
semantic truth review. Any external material later selected into visible text,
a World claim, an Action payload, Memory, Relationship, or another durable
effect must establish source closure again at that effect-bearing boundary and
cannot inherit authority from this private state.
`stuck_with_me` is the optional wording she explicitly chooses to retain;
it is distinct from her momentary `inner_state_summary`. New paid retention
requires both that authored text and an accepted, causally bound Appraisal;
missing authored prerequisites enter same-role correction, while acceptance
failures remain technical failures. The PrivateImpression boundary establishes
the durable source and subject; it must not borrow the latest unrelated
Appraisal. `noticed` remains subjective attention audit and cannot authorize
a new WorldOccurrence. Historical absent optional fields remain readable.
The persisted paid DecisionProposal is also the durable source of unfinished
retention work. Its exact authored text and accepted Appraisal may be
materialized without another model call, before independent reflection budget
gates. New materialization commits its derived role audit, private proposal
and acceptance atomically; it never invents a new choice on storage failure.
Historical partial writes remain subject to their original World/clock pin.
When the character chooses bounded Recall, she forms one Private Turn State
before requesting it and a new one from the augmented Inner Life Snapshot
before the final Expression. New ExpressionDrafts must carry the same final
Inner Turn identity; no production expression path may bypass it.
_Avoid_: Post-hoc rationale, response policy, question quota, motive enum

## Expression Reliability Lifecycle

The durable, effect-once processing state for one inbound Expression choice.
It is opened and claimed atomically with the Observation, binds each provider
attempt to its Model Result, and distinguishes a character-authored silent
Proposal from technical failure. A crash before a model result resumes the
same failure ordinal: only that live Runtime instance may continue generation
before its 120-second provider in-flight lease expires, while another Runtime
waits to reclaim it. Inner Life Snapshot or Context preparation failure is
itself a source-bound technical Model Result, so it cannot busy-spin an
unaudited claim. The short ownership lease is independent of terminal
technical retry deadlines, which remain 10, 30, then 120 minutes; and a
durable now/later/silent Proposal may be continued exactly by any Runtime
under CAS and effect-once without regenerating prose. It owns recovery and
liveness only—it cannot choose whether the character speaks. A candidate
from an older Observation is atomically superseded when a newer
user Observation commits if it has not crossed the Action authorization
boundary; this closes the old lifecycle without manufacturing another attempt.
An already authorized Action remains under its original dispatch and
settlement authority. A combined cognition candidate may be reused only under
the exact originating ModelInput identity;
if its call id, cursor, Capsule, route, or model-facing Context changes, the
role model must be invoked again and the new Model Result must retain that
actual request lineage rather than relabelling cached bytes or usage. Each
provider invocation—including Recall, correction and reselection—has an
identity derived from the messages and temperature the provider actually saw;
follow-up calls retain their parent-call relation. A later cursor also requires
a newly compiled, source-bound advisory. Technical quick recovery is still a
full Character Decision over now/later/silent and Expression Beats; the
Minimal Proposal format is only a lossless representation of the exact
single-immediate-text subset, never a host-imposed reply policy. The normal
successful path retains its 12-second budget and 1.2-second
acceptance/dispatch reserve. Only an observed invalid/exception/timeout or an
actually elapsed candidate deadline may open one bounded recovery attempt by
the same configured role provider; production never switches to a second
character-author provider. The proactive-contact route is stricter: the same
pinned role author may receive its one precise source/shape correction, but an
invalid corrected candidate is retried later through its durable 10/30/120-
minute lifecycle rather than immediately reauthored as a new intention. For
proactive contact, the role includes factual permission metadata in that same
structured Character Decision. The installed one-shot composition does not
insert a second synchronous claim-binding or general truth-review model.
Local source-lane closure remains mandatory and cannot author or replace the
expression. It verifies declared references and scopes, not whether free prose
omitted a factual assertion; that semantic gap must remain visible in testing
and qualification.
_Avoid_: Forced reply, failure fallback text, silence inference

The provider-visible request may include a compact hard-boundary manifest that
maps only visible source refs to valid factual claim scopes and states numeric
or cross-field schema constraints. It is an executable interface description,
not behavioral advice; proof-only Capsule refs stay hidden and the full
Capsule remains the Acceptance authority.

## User Request

The user's expressed preference for how the companion should speak or act. It is an input to deliberation, not an invariant the companion must obey.

## Character Decision

A model-owned choice about motive, stance, timing, expression, silence, or use of an available capability after considering the current World. It may be stochastic and surprising, but it cannot create authority or override a Hard Invariant.
_Avoid_: Behavior verdict, scripted reaction

## Controlled High Variance

The project principle that permits broad, context-sensitive variation in Character Decisions while keeping World truth, permissions, external effects, privacy, consent, safety, and replay deterministic.
_Avoid_: Rule-driven personality, unconstrained randomness

## Capability Boundary

A deterministic declaration of what the character can attempt and what evidence or authorization that attempt requires. It constrains executable effects without choosing whether the character wants to use them.
_Avoid_: Behavior policy, suggested action

## Appraisal

A structured interpretation of what an event means to the companion, such as care, pressure, offence, repair, or uncertainty.

## Drive

A current action motive such as care, autonomy, curiosity, irritation, repair, withdrawal, or desire to help.

## Stance

The companion's selected position after weighing requests, drives, relationship, Affect, values, and available Actions. Examples include comply, compromise, disagree, refuse, defer, or seek repair.

## Display Strategy

How the companion chooses to express or withhold a felt state: directly, cautiously, playfully, ironically, partially, or not yet.

## Conversation Thread

A sourced and expiring conversational commitment, question, concern, or unresolved matter. It must eventually resolve, cancel, or expire.

## Hard Invariant

A truth, Action, delivery, safety, privacy, legal, or consent rule that personality and user preference cannot override.

## Producer-First Authority

Any new authority — an event type with its reducers and acceptance chain — must land in the same delivery as its first real producer and its first consumer. An authority that nothing produces must not merge: it is inventory that every schema migration and grammar-coverage assertion then has to carry. An already-merged authority whose producer never arrived must be explicitly marked dormant in `configs/mechanism_closure.yaml`, and activating it later requires a recorded producer verdict first.
_Avoid_: Speculative authority, dormant-by-default inventory

## Inner Advisory

A sourced, bounded, and non-authoritative signal about what may be influencing the companion, such as an Appraisal, Drive, Affect tendency, repair need, or candidate Stance. It may shape a Proposal but cannot write World truth or veto expression.
_Avoid_: Rule verdict, mandatory stance

## Context Capsule

A bounded, revision-pinned packet compiled from authoritative World Projections plus explicitly non-authoritative advisories for one Deliberation. It has a token budget and truncation log, and is not a second store of truth.
Character Interior is the only production role boundary that may join its
actor-scoped material into an Inner Life Snapshot; a business consumer cannot
turn a separately compacted Capsule into another current self.
_Avoid_: Full-history prompt, free-form context dump

## Pinned Turn

One effect-once deliberation attempt whose Context Capsule, Inner Life
Snapshot, Inner Advisories, Model Result and Proposal Audit all refer to the
same complete ledger cursor. Character Interior gives the actor-owned private
portion of the attempt an Inner Turn identity. If that cursor becomes stale
before an authoritative write, the Pinned Turn is discarded and rebuilt; it
never grants a stale Proposal acceptance.
_Avoid_: Chat turn, mutable prompt session

## Text Turn Endpoint

A provider-local, advisory estimate of whether the same user is likely to add
another text bubble soon. It may combine the uncommitted batch, personal
bubble-gap history, typing presence, burst evidence, and recent message
lengths to size one bounded listening opportunity. It has no authority over a
character reply, interruption, silence, wording, or World mutation; new input
invalidates the older estimate.
_Avoid_: Turn-taking policy, punctuation rule, reply classifier

## Expression Unit Stream

One role-author provider response whose append-only `expression-events.1`
transport exposes a singular, complete, independently valid first Expression
Beat before any additional model-chosen Beats finish arriving. The head frame
carries the role-owned decision coordinates and exactly one visible Beat
(optionally preceded by one role-chosen typing Beat); later Beat frames and the
terminal frame remain part of the same provider result. The historical
`expression-units.1` envelope remains readable for replay but is not requested
from new production calls. Every frame still passes the normal source,
permission, Action, receipt and latest-cursor gates. A tail cannot win
independently or be regenerated after restart merely because its already
delivered head survived. Process health reports provider TTFT, first complete
frame, completed source closure, first fully validated candidate and
platform-visible ACK separately.
_Avoid_: Two-author provisional reply, token-by-token QQ output

## Internal World Snapshot

A revision-pinned, read-only deep Projection containing the authoritative material required by WorldRuntime internals. It is produced by deterministic reducers and is never exposed as a viewer-facing projection or edited as a second source of truth.

## World Revision

The compare-and-swap revision advanced only by events that change authoritative World, Action, budget, or grant state. Draws and model audit records advance a separate deliberation revision so that a turn cannot invalidate its own Acceptance.

## Trigger Process

The effect-once processing lifecycle for one Observation, clock trigger, recovery item, or settlement input. Concurrent callers join the same process instead of independently deliberating and authorizing duplicate Actions.

An in-flight Claim Lease and a technical retry deadline are separate coordinates.
Once the current expression attempt has a recorded terminal technical failure,
clock wake readers use its authoritative retry schedule. The ingress-claimed
inline appraisal may wait for that same missing role result only when its
Observation and failure binding are proven and no role/appraisal Proposal is
already durable. This changes no claim state or retry policy; durable results,
unproven dependencies and external Action leases retain their recovery work.

## Action Intent

A stable-identity value object inside a Proposal describing a candidate external effect. It is not an Action and gains no execution authority until Proposal Acceptance creates an authorized Action.

## Action Reconciliation

A compensating record that resolves evidence about an Action already settled as unknown. It may establish an external outcome or budget correction, but it never reopens or re-executes the original Action.

## Behavior Tendency

A model-facing coordinate describing a plausible direction of action, such as maintain, explore, avoid, repair, or set boundary. It changes proposal likelihood but never mandates a visible response.
_Avoid_: Behavior rule, mandatory reaction

## Change Phase

A sourced, time-bound stage describing how the companion is departing from or returning toward baseline: baseline, preference deviation, stress response, relationship tension, or recovery. A single phase cannot rewrite Character Core.
_Avoid_: Mood label, personality rewrite

## Affect Episode

A sourced set of time-varying Affect components with versioned decay, residue, and lifecycle semantics. Surface expression does not implicitly resolve it, and replay uses Logical Time plus the recorded policy version.

## Relationship Adjustment

A sourced, accepted delta to one or more slow relationship variables under a versioned integrity policy. It records both proposed and accepted deltas and never dictates a particular visible response.

## Action Layer

The authority layer at which a proposed change belongs: internal state transition, World event, external Action, media Action, or read-only tool. Each layer has distinct commit and settlement semantics.

## Model Result

A versioned, hashed record of a bounded model call, including its purpose, input capsule identity, parsed payload, latency, usage, and failure metadata. Replay reuses it and never silently calls a live model.
_Avoid_: Unlogged model answer, replay-time inference

## Source Review Qualification

Visible Beat reviewer version 5 explicitly distinguishes an immediate sensation
or conversational self-assessment from a recently completed personal episode.
This remains a model entailment judgment against the pinned source table, not a
local word/tense filter. A routine or a newly authored appraisal cannot establish
that a bodily transition occurred. Eligible same-actor activity evidence retains
its exact time/status authority; pure feelings and intentions remain available.
Version 5 is opt-in, with separate tool, schema, preparation and receipt identity.
Versions 1–4 retain their historical compiler bytes for cold receipt verification.
Offline wiring and replay checks do not establish reviewer semantic quality or
authorize a release; real-provider qualification is recorded separately.

Evidence that one installed review lane, exact request/schema contract and
provider route performed its bounded responsibility. Configuration, successful
transport, valid JSON and local reference checks are distinct from semantic
coverage. None proves another lane qualified or gives the reviewer authority
to choose character behavior.

The current one-shot composition has removed the general chat truth-review,
external-proposition inventory and general Life model-review lanes. The older
2026-08-01 Inventory V5 / full V7 topology and its route measurements are
historical evidence, not installed routing or a startup requirement. See the
2026-08-13 H1d record in
`docs/design/harness-restructure-execution-plan.md`. Its deterministic
replacement checks declared source coordinates; an empty declaration is not
proof that the message contains no external facts.

Life Development may separately install the optional focused novel-origin
critic. It reviews World Author truth-origin and authorship boundaries, not
visible dialogue or all semantic source coverage. Its exact request, enabled
state, self-review setting and real semantic counterexamples must be reported
independently; it does not restore the retired general topology. The
2026-09-08 real-dialogue audit still records both undeclared chat facts and a
focused-critic false acceptance. Those are open limitations, not qualification
success: `docs/audits/adaptive-companionship-2026-09-08.md`.
_Avoid_: Configuration as evidence, timeout as schema success, local checks as
complete semantic coverage, retired reviewer topology reported as active

## Background Context Profile

A purpose-specific, auditable declaration of which Inner Life Snapshot
materials and Context Capsule slices may enter one background model call's
provider view. Undeclared content is withheld from the provider payload only;
the canonical snapshot and capsule remain unchanged for replay and acceptance.
Eight profiles cover protagonist life choices, World Author, stimulus appraisal,
private impression, proactive contact, memory retention, interaction background,
and novel-origin review. Protagonist life, contact, appraisal and reflection
choices share bounded, source-bound continuity material: her life, aspirations,
memories, prior interpretations and latest dialogue remain available when the
purpose changes. Chronological dialogue budgets preserve the newest exchange;
compact appraisal tables retain the same budget and source inventory as their
canonical form. World Author does not inherit protagonist-private material.
Startup tests enforce complete purpose coverage via
`assert_background_context_profile_coverage`.
_Avoid_: Second truth store, lane-specific ledger rewrite

## Dual Self-State Fields

The slim expression contract's required pair `meaning_of_this` and `my_state`.
`meaning_of_this` records how she provisionally reads his message or the
immediate situation; `my_state` records her own felt state, desire, resistance,
or pull at this moment. They are separate from visible `messages` and from
durable Affect proposals. The host no longer accepts a `mood` shorthand or
fills default 5000 basis-point weights on her behalf.
_Avoid_: Single fused `felt`, host-authored intensity

## Fact Predicate Stability

A deterministic classification of fact predicates as stable or episodic for
context ranking. Stable predicates use a fixed recency multiplier
(`STABLE_FACT_RECENCY_BP`) instead of time-decaying recency, so identity and
coordinate facts are not evicted ahead of transient circumstances during capsule
compaction.
_Avoid_: Keyword-based fact pinning, host-chosen fact subsets

## Life Development Disturbance Occasion

A sparse, replayable draw (600 basis points mass in a 10 000 total table beside
ordinary opportunity and nothing) that opens a life-development beat in
`disturbance` mode. The World Author must propose at least one outcome carrying
a durable world consequence validated by
`validate_disturbance_consequence_closure`. Cloned multi-day thematic diversity
from this mechanism remains unverified production evidence.
_Avoid_: Deterministic plot injection, disturbance without closure

## Consecutive Unanswered Expired Chase Count

A projection-derived count of how many consecutive proactive or expectation
follow-ups ended with expired hope while the counterpart still had not replied.
It surfaces only as advisory input to proactive deliberation; it does not
hard-cap outreach or choose wording.
_Avoid_: Keyword chase limit, automatic follow-up script

## Private Impression

The companion's fallible, source-bound interpretation of a user, relationship, or event. The character authors its tentative `reflection_summary`; deterministic authority binds that reading to accepted appraisal sources, confidence, possible counter-evidence, and an expiry or settlement condition. It is never a User Fact, and legacy impressions without authored prose continue to resolve through their exact appraisal references.
_Avoid_: Hidden fact, inferred user fact

## Private Commitment

An internal decision to keep caring about, remember, revisit, or later act on something. It may open a Conversation Thread or produce an Action Proposal, but it is neither a completed Plan nor a Committed Experience.
_Avoid_: Completed intention, hidden experience

## Expression Beat

One independently dispatchable and settleable fragment in an ordered, interruptible expression. A Beat may depend on an earlier receipt and may be cancelled or reconsidered when the user interjects.
_Avoid_: Text chunk, random split
