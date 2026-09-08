# Selected source proof for Life focused review

The trusted Context Capsule already contains the exact Fact and Dialogue items
selected at the author pin. Its chat presentation removes their source bindings
and some payload fields to save space. Feeding that presentation to focused
review turns these items into baseline-only material: the original value hash
does not describe the shortened value, so adding bindings back alone is invalid.

Add one purpose-specific view of the same trusted Capsule. Start with its normal
model view and replace only the selected `relevant_facts` and `recent_dialogue`
items with their complete Capsule payloads, source bindings and matching hashes.
Validate the original Capsule and preserve item order, membership, privacy,
unavailable slices and the original cursor. No ledger read, new selection,
semantic classification or second model call belongs in this projection.
Other slices and the chat/World Author presentation remain unchanged.

Production capability manifest `.4` explicitly qualifies this view for the
focused reviewer. Its evidence packet `.8` and review subject `.7` identify the
changed request. The output review contract remains unchanged. Historical
production `.2` and `.3` manifests retain their exact compact view and request
hashes, including cold recovery of successful model audits. Old `.1` outcome
contracts are not upgraded. An unavailable or invalid qualified proof must fail
closed, not silently use baseline-only data under the new identity.

Implementation is confined to `life_context`, the capability version, focused
packet identity/compiler, and the runtime's focused-review call using its
already pinned Capsule. The public test uses real SQLite Observation → Fact
acceptance → Resolver → Capsule → LifeDevelopment → DeepSeek MockTransport.
It checks exact Fact/Observation event/hash/actor material, unchanged chat
presentation, selected-item boundaries, tamper rejection and historical
recovery without additional HTTP. Added request bytes are reported separately.

This closes an input authority loss. It does not prove that a semantic critic
will notice the actor mistake in the real trial, or entail every statement from
a correctly cited source. No real provider or production database is used.
