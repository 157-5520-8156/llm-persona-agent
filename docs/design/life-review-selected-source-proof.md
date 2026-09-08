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

The actual one-Fact/one-Dialogue fixture at `1a1731af` emitted a 45,024-byte
World Author request and a 24,288-byte focused request. With this projection,
the author request remains 45,024 bytes and focused becomes 25,973 bytes
(+1,685, about 6.94%). These are the captured HTTP body bytes, not tokenizer
counts or a monthly cost estimate. Complete proof increases review tokens;
the original Capsule selection caps, item count, order and privacy are
unchanged. Existing provider admission still estimates the assembled payload.
Chat compaction is unchanged, and there is no additional model lane or call.

The implementation's 13 dedicated cases cover the actual HTTP/acceptance chain,
selection/privacy preservation, later-prefix isolation, corrupt Capsule proof
returning a typed technical failure before focused HTTP, empty material,
unproved-view rejection, wrong manifest/packet pairs, and `.2/.3/.4` cold audit
recovery without new HTTP. The 13-file related gate passes 231 tests. These
fixtures use temporary SQLite and MockTransport; no real semantic verdict,
production migration or frozen longitudinal baseline is claimed here.
