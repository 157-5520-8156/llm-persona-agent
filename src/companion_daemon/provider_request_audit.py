"""Local HTTPX correlation only; never a prompt, header, or billing verdict."""

from collections.abc import Mapping

_RESERVATION_EXTENSION = "companion.usage_reservation_id"


def _native_reservation(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("reservation:"):
        return None
    suffix = value.removeprefix("reservation:")
    if len(suffix) != 32 or any(char not in "0123456789abcdef" for char in suffix):
        return None
    return value


def request_usage_extensions(reservation_id: object) -> dict:
    """Attach only a native generated identifier to local transport metadata."""
    value = _native_reservation(reservation_id)
    return {"extensions": {_RESERVATION_EXTENSION: value}} if value else {}


def captured_usage_correlation(extensions: Mapping[str, object]) -> dict:
    """Whitelist one opaque identifier; arbitrary extension data stays private."""
    value = _native_reservation(extensions.get(_RESERVATION_EXTENSION))
    return {"usage_reservation_id": value, "usage_association": "client_declared"} if value else {}
