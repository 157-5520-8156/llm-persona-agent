"""Context-truth auditor: what she sees vs what the ledger records.

Path A is the production InnerLifeSnapshot compile (`model_view()`).
Path B is a ledger projection of the same facts. Slots compare the two.

Add a slot in ``slots.py``; see ``SlotSpec`` and ``HOW_TO_ADD_A_SLOT``.
"""

from .types import Finding, LedgerTruth, SeenView, SlotSpec

__all__ = ["Finding", "LedgerTruth", "SeenView", "SlotSpec"]
