"""Version identities shared by composition, transport and immutable receipts.

No provider or domain imports: entry points can agree on support without
importing the reviewer implementation or maintaining divergent allowlists.
"""
PROTOCOL = "visible-independent-review.1"
RESELECTING_PROTOCOL = "visible-independent-review.2"
SHARED_STRING_PROTOCOL = "visible-independent-review.3"
SUBJECTIVE_HISTORY_PROTOCOL = "visible-independent-review.4"
CONTENT_FIELD_PROTOCOL = "visible-independent-review.5"
SUBJECTIVE_HISTORY_PROTOCOLS = frozenset((SUBJECTIVE_HISTORY_PROTOCOL, CONTENT_FIELD_PROTOCOL))
SHARED_STRING_PROTOCOLS = frozenset((SHARED_STRING_PROTOCOL, *SUBJECTIVE_HISTORY_PROTOCOLS))
RESELECTING_PROTOCOLS = frozenset((RESELECTING_PROTOCOL, *SHARED_STRING_PROTOCOLS))
REVIEW_PROTOCOLS = {"9": PROTOCOL, "10": RESELECTING_PROTOCOL, "11": SHARED_STRING_PROTOCOL, "12": SUBJECTIVE_HISTORY_PROTOCOL, "13": CONTENT_FIELD_PROTOCOL}
RECEIPT_PROTOCOLS = {p: f"visible-source-review-receipt.{v}" for v, p in REVIEW_PROTOCOLS.items()}
SUPPORTED_REVIEW_VERSIONS = ("1", "2", "3", "4", "5", "6", "7", "8", *REVIEW_PROTOCOLS)
