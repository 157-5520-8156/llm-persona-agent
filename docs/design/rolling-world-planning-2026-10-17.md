# Rolling future preparation

User requirement: Fate maintains a changeable month/year outline; published objective planning
describes external obligations and exceptions, subjective Plans remain role-authored, and the
clock executes bounded batches of authored external events. A calendar repeating the same week
without regeneration does not satisfy this requirement.

Reuse WorldOutlook's author/store/retry and LifeEcology, WorldPlanning publications, the existing
Outlook executor and prepared-detail lifecycle. No new worker, per-NPC model or role-policy engine.

- Private, bounded long-range stages coexist with the existing 30-day execution horizon.
- Published planning separates phases (e.g. teaching/training/activity week) from detailed slots.
  Phase rules replace ordinary rules for their dates. A phase may have no classes.
- Clock preparation deadlines re-open authoring before outline/detail coverage runs out and before
  published phases change. A keep answer cannot acknowledge missing preparation as complete.
- Each newly authored slot carries its external event script and requires a complete reviewed
  detail program when the event starts. Near instances are expanded on demand,
  effect-once through the existing executor, with exact publication and original-instance identity.
  The opening phase is released on that clock; later phases are released only when due.
  Revision/cancellation invalidates pending instances. Late work never backfills attendance.
- Role context includes bounded published long-range phases, not private stages or future details.
  Existing role decisions can create, abandon or alter subjective Plans; new sources feed Fate.
- Dashboard projects phases/instances/plans/experiences from the same publications, without model calls.
- Stored legacy requests retain their schema/bytes; failures, receipts and historical publications remain.

Acceptance through existing public application seams: teaching → no-class activity week → training;
next-month question sees the published phase but no private storyline; role abandons an intention
without the system reinstating it; missing coverage automatically schedules authoring; timeouts retain
failed attempts and later recover; revision + restart cannot duplicate or resurrect cancelled events.
Long-range inputs and expansion remain bounded independently of elapsed simulation years.

Design references: [Generative Agents §4.3](https://arxiv.org/html/2304.03442v2#S4.SS3)
motivates coarse-to-fine planning and replanning. Its planning is not our fact/agency contract and
its reported results are not qualification here. [RFC5545](https://www.rfc-editor.org/rfc/rfc5545)
informs recurring-instance identity and exceptions. Existing ADR0010/0025/0026 govern authority.

Implemented in the existing Outlook/Planning/clock composition, without a new worker or role policy.
The current author maintains continuous 365–730 day private coverage; publication reviews are within seven
days, detail coverage renews two days before exhaustion, and continuing periods renew before expiry.
Unchanged arrangements may remain unchanged while at least 330 days remain: a review is not a
requirement to manufacture disruption or rewrite a full year every day. New revisions still cover
at least 365 days.
Stored `.6` outlook requests and `.2` planning publications retain their original 90-day/optional-detail
contracts; only fresh `.7` requests require the year and `.3` publications require course details.

Validation on 2026-10-17:

- Final frozen full suite: 10,807 passed, 19 skipped, 2 expected failures. Both Standards and Spec reviews passed.
- Production-composition tests cover automatic renewal after retained timeouts, multiple domains,
  changed/abandoned role Plans, same-clock revision/restart, and hidden details released exactly once.
- A full-history clone (1,259 prehistory records) accepted real GLM-5.3-Flash authoring after one
  same-model correction. Earlier rejected attempts remain. Its actual publication produced three
  phases and six near-term instances through the existing executor/settlement path; the same
  published planning reader supplies role context and the weekly dashboard. This targeted execution
  is separate from unattended live-scheduler qualification.
- Advancing that isolated clock to the October 22 renewal boundary produced a first-attempt valid
  GLM revision: the same planning key supersedes the exact publication, detail coverage extends
  from October 24 to October 29, and the private outline extends through March 31, 2027.
- A real DeepSeek next-month chat received all three published phases and answered from the courses;
  it left its own personal intentions undecided. CaptureDelivery took 10.615 seconds with simulated
  sender delays. No private outline stage IDs were present in that foreground request.
- GLM's native tool carrier now offers only the legal response shape when renewal is required;
  exact source/prior-object selectors avoid re-copying authority hashes. Correction uses native
  assistant tool calls and matching tool feedback, as described in the
  [GLM function-calling documentation](https://docs.z.ai/guides/capabilities/function-calling).
  Stored older messages, tool definitions and interpretation remain pinned.

The live candidate exposed a separate scheduling gap: ordinary aftermath settlement kept pushing
the generic life cadence forward. Coverage preparation now has an independent deadline in that
same scheduler; it does not accelerate the role's discretionary life cadence. The existing prepaid
clock lane executes published-document work, and the existing aftermath settlement commits its
single external outcome even when new model spending is paused. Failures retain durable receipts
and the existing hourly input-identity retry; waiting or invalidated replacement publications do
not create clock loops or suppress genuinely missing coverage.

The original live candidate database was restarted on this implementation with CaptureDelivery.
Its scheduler automatically called GLM, accepted the first response, activated the planning notice
and settled it one logical minute later. The new publication has two public phases and six near
course instances; the private outline has four stages. The October 19, November 2 and January 4
calendar views were read from that same live owner, and a course's time/detail was verified in the
browser. Both restart audits preserved every earlier World event and all 27 prior author attempts.

Cold recovery caused one 40-second dashboard timeout; the final warm calendar reads took
0.37–0.40 seconds. The live GLM request took about 90 seconds. These observations are not continuous
month/year qualification, a latency guarantee, or a production QQ release.

The follow-up on 2026-10-17 made complete course details mandatory for new `.3` publications. The
same World Author and two existing source reviews validate the whole timed program at activation;
the existing EventDetailRuntime releases its phases and the active-world reader exposes only an
already released, source-verified phase to the character. That reading explicitly says an external
class event does not prove the character attended or was present. An event recovered with under two minutes
remaining is rejected as a typed failure rather than starting without a possible closing phase.
Unrelated aftermath/cooldown cannot postpone mandatory detail preparation. A model failure is
recorded as a technical failure plus a source-specific retry receipt in the existing Outlook store;
the next attempt is bounded by the original event window and an unrelated clock cannot spend it
early. No role decision is inferred, and no new worker or table is installed.

A fresh real GLM-5.3-Flash run on a full-history isolated backup accepted its first `.7` attempt.
The private outline continuously covers 2026-10-18 through 2027-10-18, and the published school
planning candidate has four slots, each requiring full event details. The preparation call took
314.661 seconds end to end, so this is semantic acceptance evidence, not acceptable background
latency. The backup and failed historical attempts were preserved, and QQ was not used. The first
real course detail attempt was rejected for undeclared visual facts; two scheduled retries were
also rejected, first for malformed provisional-person tags, then for incomplete source closure.
These failures remain in the audit. The source-specific retry and narrower model instructions were
added from those observations. A second ordinary class then failed repeatedly under the broad
World Author draft: different attempts violated privacy, visual-field and source-review contracts.
High reasoning effort and the GLM auto-function carrier did not make that broad draft valid.

The new `event-detail-capability.2` uses the same World Author, source reviews, LifeEcology and
EventDetailRuntime, but asks ordinary courses for two external outcomes plus complete timed text;
typed new people/places, visual evidence, biography and life-direction effects remain available to
separate Fate events. A frozen low-effort GLM control produced a structurally valid six-step draft
but repeated one offset; the full normal runtime then accepted the next ordinary class on its
opening clock. It prepared five steps at offsets 0, 1500, 3900, 5400 and 5940 seconds. The pinned
role context at 10:00 contained only step 0 and explicitly marked the external event as no proof
of the character's attendance. This isolated call took 477.121 seconds end to end, including cold
history verification; it is one successful real course, not a long-run reliability rate or QQ test.
On a copy of that accepted course, subsequent real ledger clocks at 10:25, 11:05, 11:30 and
11:39 released exactly one additional accepted phase each time; earlier receipts remained and
future phases stayed pending until their clock. The programme finished with all five phases
released and no new GLM call. Settlement of a detail child keeps its external-event scope in
role context; settling the parent before the original window closes no longer revokes the
remaining accepted detail phases. Historical `.6` Outlook keep validation is unchanged.

On the same full-history backup, a cold read-only `Outlook.next_due()` took 381.125 seconds;
directly replaying its pinned historical projection took 317.612 seconds, whereas the repeated
due read in the same process took 0.048 seconds. Outlook now remembers successful verification of
the same immutable revision in memory and invalidates that proof when the ledger reports an
external history generation change. First cold verification remains expensive; this cache is not
a replacement for the execution and authoring checks.
The scheduler's mandatory-detail peek now reads one projection and scans pending course sources
once per peek instead of up to eight complete scans. Its one remaining scan still grows with
the projected event history; a long-running soak is needed before making a latency claim.
