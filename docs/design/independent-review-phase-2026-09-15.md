# Independent review must enter its owned timing phase

The real `visible-independent-review.3` trials repeatedly cancelled source
review after 0.6–2 seconds. Local compilation and presentation were improved,
but the larger wiring error remained: `visible_independent_review_runtime`
called providers directly without entering Deliberation's validation phase.
The outer author timer therefore cancelled a completed author while its
independent reviewers were still running.

`InteractiveTurnBudgetPolicy` already defines a candidate-local validation
phase, and ADR0014 describes its ownership: the ordinary author does not renew
its own deadline, while a completed draft receives separately bounded source
validation and, only after semantic rejection, one same-author correction and
final review. Earlier release reports incorrectly treated the 12-second author
setting as a complete interaction limit for this topology. This repair makes
that distinction explicit; it does not establish acceptable user latency.

## Ownership

- Deliberation's `run_validation_review_once` owns entry, cancellation, exit and
  the existing immutable phase deadline. It never reruns the operation and
  never interprets a verdict. Reentry cannot renew the phase.
- The independent reviewer owns two independent readings, their existing
  single structural reselections, and the subsequent source call. The group
  has a 46-second ceiling (fitted to an already active phase); each physical
  call has a 22-second ceiling. No successful reading is rerun as a group retry.
- The inbound character owner opens the existing correction/final-review phase
  only for an actual whole-candidate semantic rejection. Core retains control
  of the single same-author correction. Technical failure does not open it.
- Source provenance, protocol versions, request bytes, verdicts, receipts,
  action authorization and cumulative billing retain their existing contracts.

Ordinary author configuration remains 12 seconds with hedging disabled in
controlled tests. Validation can extend the full interaction beyond 12 seconds;
the existing correction phase is capped at 100 seconds. These are cancellation
ceilings, not latency targets or release evidence. A disconnected-stage control
must reproduce cancellation; connected full-application tests must preserve
bounded failure, same-author correction and receipt verification.
