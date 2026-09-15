# Speech acts and their background facts

The closed content-evidence trial delivered a reminder implying prior user
phone use. Both v10 readers classified the whole suggestion as a current
private expression. The available counterpart fact/report inventory did not
establish that history; a follow-up elicited an invented prior user statement.
Two agreeing readers therefore did not establish semantic completeness.

## Design boundary

Meaning reader v11 adds a required `presuppositions` inventory on every Beat.
It uses the existing factual and historical-subjective modes, never a current
expression mode. Questions keep their own premises; directly asserted facts
and current speech acts keep `meanings`. The compiler routes returned
background facts into the same source review as other factual readings.
It never infers a fact or social decision from a word in the character text.
The model can return an empty inventory, but its completeness remains a
fallible semantic judgment, not proof supplied by the schema.

This first change is a versioned reader experiment, not a deployment default
or release qualification. Existing v7–v10 request bytes remain frozen. The
two readers receive only original Beats; neither the expected test readings
nor the other reader's output is available to them. No world evidence or
character behavior is authored by this module.

## Evaluation rationale

The [PROPRES study](https://aclanthology.org/2023.conll-1.9/) evaluates
presupposition across multiple linguistic environments and notes variability
in human judgments. The [NOPE corpus](https://aclanthology.org/2021.conll-1.28/)
focuses on context-sensitive presuppositions in naturally occurring language.
These support testing contrasting contexts rather than treating a lexical
trigger as a deterministic fact extractor. They do not qualify our Chinese
reader or companion runtime.

The fixed diagnostic cases cover the actual reminder, plain advice, future
repetition, explicit uncertainty, past gratitude, prospective companionship,
past comparisons, completed results, present and past willingness, a prior
utterance question and a quoted reminder. Human criteria remain outside the
model request. Structural tests verify mandatory inventory, scope, source
routing and frozen compilation; fresh provider outputs need separate semantic
inspection before runtime adoption. Historical faulty receipts stay intact.

## First probe and predicate scope

The first fixed probe made 24 metered calls (two models per case). Both readers
extracted the actual reminder's prior phone-use premise; the ordinary advice,
future/conditional, uncertainty and quoted-reminder controls did not invent
history. One Pro reading classified an explicitly reported prior desire as a
past subjective state instead of a prior utterance. That is a semantic mismatch,
even though the response was structurally valid. The first probe is preserved
as 23 matching readings and one mismatch, not a passing runtime gate.

Reader v12 retains v11's schema and adds a predicate-scope instruction: a
claim about expressing subjective content is a speech event; a direct claim
about feeling something is a subjective state. Neither may be silently
converted to the other or promote quoted content to truth. v11's exact request
remains frozen for replay. Fresh probes must check the scope correction before
the new reader is installed in an independent review protocol.

The second 24-call probe corrected the reported-desire case, but both readers
again missed the actual reminder's background history; Flash also made current
gratitude historical. It has 21 matching readings and three mismatches. Merely
appending a scope rule was not a stable improvement and is not adopted.

Reader v13 uses the same explicit inventories with one cohesive specification,
instead of extending the historical instruction ladder. It separately defines
speech function, background facts, predicate scope, emotional time versus the
time of an emotion's object, quotation and conditional cancellation. It has no
production lexical classifier and does not change the character's behavior.
The previous preparations and failed probe outputs remain verifiable.
