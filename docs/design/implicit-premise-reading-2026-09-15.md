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

## Third probe and provider reasoning experiment

The unified v13 prompt also fails: both readers retained the phone-use premise,
but promoted the conversational evaluation to factual background. Pro also
asserted tiredness from the conditional clause; Flash classified a quoted
expression's existence as a past utterance. Both duplicated the cat-related
prior statement across two inventories. This is not a stable improvement and
no v11–v13 reader has been adopted in a runtime review protocol.

The [DeepSeek thinking documentation](https://api-docs.deepseek.com/guides/thinking_mode/)
describes explicit thinking and effort controls. We tested the frozen v11
reader, rather than adding another semantic instruction. The initial forced
tool requests were rejected by the provider with no billing. Experimental
readers now support an explicitly pinned `auto` carrier: the compiler records
the choice before hashing, while the shared provider decoder still requires
exactly one response using the declared function. Historical preparations keep
their bytes. This does not execute a character action or authorize plain text.

With that carrier, high-effort Pro and Flash both exceeded the existing 22s
deadline on the first case. Low-effort Flash returned a matching first-case
reading in 11.17s; Pro still timed out. Remaining low-effort cases were not run.
These are latency failures and incomplete observations, not semantic passes.
Three unknown-billing reservations remain alongside all inherited reservations.

The next qualification must balance missing background facts, invented facts,
scope preservation and end-to-end latency together. Further prompt-only
iterations must not be adopted based on repairing just one counterexample.
The source-review and character-correction stages remain separate responsibilities;
no local expression heuristic or new production behavior rule was added.
See [metered probe evidence](../audits/implicit-premise-validation-2026-09-15.json).

## Consolidated inventory experiment

`visible_fact_inventory.py` now owns two experimental schemas and compilers;
provider invocation, billing, same-reader structural correction and source
authority remain in their existing owners. The original preparation facade
dispatches explicitly versioned requests, and historical wire bytes stay fixed.
No inventory can issue a receipt or choose the character's behavior.

Reader v14 consolidates assertions and background facts into one factual list,
with a separate free-text account of other meanings. The actual reminder's
illustrative Pro request shrank from 8,473 to 3,625 UTF-8 bytes. That did not
solve semantics: readers promoted current expressions to historical/external
facts, reversed actors, and missed background facts. Low-effort thinking
corrected some errors but retained others, including current willingness being
treated as an external factual state.

Reader v15 retains one typed meaning list, restoring explicit current expression
and intention modes beside historical modes. Pure hypothetical/unknown scope has
its own bounded list; facts from the typed list flow through the same source
review, while separate readers cannot erase each other's propositions. This
fixed several current-expression errors in the nonthinking trial, but both
readers still missed the actual reminder's phone history. In the low-thinking
trial, Flash found that history, Pro missed it and added tiredness; Pro then
timed out on plain advice. Only two cases were attempted in that trial.

Neither reader is adopted. All four trials are closed and reconciled, including
one additional unknown billing reservation. 95 structural/source/preparation
checks pass, including byte freezes for forced and auto carriers in both versions.
See [unified inventory trial evidence](../audits/unified-inventory-validation-2026-09-15.json).

The next diagnostic should examine the original candidate in its recorded
conversation, then follow interpretation through source judgment and correction.
Original speaker-labelled utterances can disambiguate conversational meaning;
they must not silently become authority for the truth of their embedded content.
This is a direction to investigate, not a qualified new context API or proof
that absent context explains every observed error. Avoid another prompt-only
revision based solely on a fixed isolated sentence passing once.
