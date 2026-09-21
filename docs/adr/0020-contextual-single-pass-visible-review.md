# ADR 0020: One contextual review for visible chat

- Status: Implemented opt-in; qualification pending real conversation
- Date: 2026-09-21

The latest full-host trial delivered none of three ordinary inputs. Two readers
interpreted isolated replies without the current user message; the subsequent
source reviewer inherited wrong subjects and literal readings of conversational
language. Corrections added cost and sometimes introduced unsupported life facts.
See [the actual chat record](../audits/hands-on-chat-2026-09-21.md).

## Decision

Version 24 has one contextual reviewer, invoked separately from the character.
It receives the original complete Beats, the bound user report, dialogue evidence
and all authorized source readings. It identifies actual factual commitments and
their sources in that same call. It neither consumes blind-reader propositions
nor treats the author's claim inventory or rationale as an exhaustive authority.
Every Beat needs an explicit complete review, including source-free expressions.

The host checks exact candidate/source/request/response bindings, source scope,
subject, selected fields and accepted Fact values. Unresolved or unsupported
content cannot receive a passing receipt. A definite rejection sends the full
original candidate and diagnostic data to the same character for its existing
single correction. No local prose rewriting or extra reviewer retry is added.

The current request uses JSON output with one schema and actual source-permission
names. There is no intermediate blind-reader mode taxonomy. No source is omitted
because the character declared no claims or a prior reader failed to notice one.
Lifecycle fields retain their narrow authority; a plan or active status is not
proof of an executed action, arrival or completed result.

`visible_grounded_review.py` owns preparation, source checks and immutable
receipts. `visible_grounded_review_runtime.py` owns its one metered call and error
reporting. The existing review entry and receipt verifier dispatch to this module.
Historical compilers remain for exact historical receipt verification, without
running their two-reader path for version 24. No default deployment is changed
before real qualification.

## Evidence and limits

This returns to the single-guard direction previously recorded in ADR 0017; it
does not inherit that historical implementation's qualification claims.
Microsoft's [GroundednessEvaluator implementation](https://source.dot.net/Microsoft.Extensions.AI.Evaluation.Quality/GroundednessEvaluator.cs.html)
shows a single call over response, context and user request. We use that call
shape, not its relevance/completeness grading, which would constrain the character.
[Ragas faithfulness](https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/)
motivates checking individual claims against context; combining interpretation
and checking remains our design choice and must be evaluated here.

The reviewer remains a fallible model. With the same checkpoint as the character,
this is a separate invocation with correlated model errors, not independent
semantic certainty. Schema and receipt validity do not prove correct entailment.
Tests must cover missing sources and false support as well as false rejection;
real acceptance, delivery latency, cost and continued dialogue must be reported
separately. Fewer provider calls alone do not qualify the release.
