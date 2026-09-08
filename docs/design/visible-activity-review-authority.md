# Selected activity material in visible review

This is review-input qualification, not deployment of a chat reviewer or proof of
semantic entailment. CharacterInterior still authors intention and lifecycle
choices. The public activity reader already verifies the original role decision,
accepted Plan, current lifecycle event, owner, pinned cursor and privacy before
the Context compiler selects a typed activity item.

The review projection recognizes only `ActiveActivityContextItem` and
`CompletedActivityContextItem` from an already selected world-life item. It retains
the complete original value and checks its value hash, the outer source hash,
the exact two inner/outer event bindings, original privacy and untruncated
intention hash. The current lifecycle reference must name an ActivityStarted or
ActivityResumed for active state, or ActivityCompleted for completed state. The
other binding must remain ActivityPlanned. Owner is read only from this validated
typed object and must match the same evidence packet's explicit participants.

Only that exact lifecycle event row gains `visible-activity-source.1` support.
Plan references and hash aliases retain their former qualification; ref selection,
order and ordinary deterministic source guards do not change. Materials remain
deduplicated per original selected entry. Withheld/unavailable, truncated, hash
mismatched or unrecognized material cannot gain this support. This module does
not retrieve event bodies or independently replay the producer's accepted audit;
it consumes the same trusted, selected Context and checks its internal binding.

The review-local scope makes state limits explicit: active means an activity is
in progress; completed means its lifecycle ended. Neither proves an embedded
backstory, achieved goal, location presence, objective outcome or permission to
disclose private material. The text retains its original accepted-intention-only
scope. Detecting a candidate's stronger semantic claim still requires a reviewer
and independent qualification.

Public regression uses current Chat LifeIntent through the actual DeepSeek
adapter with MockTransport, temporary SQLite, real lifecycle choices and the
ordinary Context/source-table producer. It covers started/completed state,
future Plan exclusion, exact original values, wrong participant and tampered
body/status/bindings. It does not claim live-provider, day-open-specific or
whole-Beat semantic qualification. Legacy manual packets and parser calls without
material tables retain their old bytes and behavior; activity qualification is
opt-in through the new row contract.
