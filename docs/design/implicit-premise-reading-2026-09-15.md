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
