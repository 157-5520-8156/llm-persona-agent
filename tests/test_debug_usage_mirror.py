"""Provider admission and the independent debug book use different ledgers."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import httpx
import pytest

from companion_daemon import spend_account
from companion_daemon.llm import DeepSeekChatModel, ModelCallUsage, model_call_scope
from companion_daemon.world_v2 import model_usage_budget
from companion_daemon.world_v2.model_usage_budget import ModelUsageAdmissionError, WorldV2UsageStore


class CapturingUsageStore(WorldV2UsageStore):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.received = []

    def record(self, usage):
        self.received.append(usage)
        super().record(usage)


@pytest.fixture
def debug_book(tmp_path, monkeypatch):
    # Exercise the production hook which pytest normally disables. Every write,
    # including the process-global debug mirror, stays in this temporary dir.
    store = WorldV2UsageStore(path=str(tmp_path / "debug.sqlite"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    monkeypatch.delenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", raising=False)
    monkeypatch.setattr(spend_account, "_DEBUG_STORE", store)
    return store


async def complete_offline(store):
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"content": "offline fixture"}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "prompt_cache_hit_tokens": 400,
                    "prompt_cache_miss_tokens": 600,
                    "total_tokens": 1100,
                },
            },
        )

    model = DeepSeekChatModel(
        api_key="fixture-never-transmitted",
        base_url="https://fixture.invalid",
        model="deepseek-v4-flash",
        thinking_enabled=False,
        transport=httpx.MockTransport(respond),
        usage_observer=store.record if store is not None else None,
    )
    try:
        with model_call_scope("activity_lifecycle_choice", actor="actor:companion"):
            result = await model.complete_with_usage([{"role": "user", "content": "fixture"}])
        assert result[0] == "offline fixture"
        assert len(requests) == 1
    finally:
        await model.aclose()


@pytest.mark.asyncio
async def test_reserved_provider_bill_is_mirrored_once_between_ledgers(
    tmp_path, debug_book, caplog, monkeypatch
):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    source = CapturingUsageStore(path=str(tmp_path / "source.sqlite"))
    await complete_offline(source)
    # A retried observer notification must not become another bill.
    spend_account.maybe_record_debug_usage(source.received[0], observer=source.record)
    primary = source.budget_state(monthly_budget_cny=None, daily_budget_cny=None)
    mirror = debug_book.budget_state(monthly_budget_cny=None, daily_budget_cny=None)
    assert primary["purpose_counts"] == {"activity_lifecycle_choice": 1}
    assert mirror["purpose_counts"] == primary["purpose_counts"]
    assert mirror["monthly_cost_cny"] == primary["monthly_cost_cny"]
    assert "usage has no matching reservation" not in caplog.text


def provider_bill(reservation_id, *, billing_state="known", status="succeeded"):
    return ModelCallUsage(
        purpose="activity_lifecycle_choice",
        model="deepseek-v4-flash",
        provider="deepseek",
        status=status,
        latency_ms=10,
        prompt_tokens=1000,
        completion_tokens=100,
        cache_hit_tokens=400,
        cache_miss_tokens=600,
        total_tokens=1100,
        budget_reservation_id=reservation_id,
        billing_state=billing_state,
    )


def admit(store, reservation_id=""):
    return store.admit_provider_call(
        purpose="activity_lifecycle_choice",
        actor="actor:companion",
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_characters=100,
        reservation_id=reservation_id,
    )


def read_rows(path, table):
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]


def test_delayed_import_keeps_original_call_month_and_offpeak_price(
    tmp_path, debug_book, monkeypatch
):
    class Clock(datetime):
        current = datetime(2026, 9, 30, 0, 59, 59, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz)

    monkeypatch.setattr(model_usage_budget, "datetime", Clock)
    source_path = tmp_path / "source.sqlite"
    source = WorldV2UsageStore(path=str(source_path))
    reservation_id = admit(source)
    # The provider bill and mirror callback arrive in a later month, at peak.
    Clock.current = datetime(2026, 10, 1, 1, 0, 1, tzinfo=timezone.utc)
    source.record(provider_bill(reservation_id))
    assert debug_book.import_settled_provider_usage(source=source, reservation_id=reservation_id)
    assert debug_book.import_settled_provider_usage(source=source, reservation_id=reservation_id)
    assert debug_book.monthly_cost_cny() == 0
    assert debug_book.cost_since(since=datetime(2026, 9, 1, tzinfo=timezone.utc)) == pytest.approx(
        0.00137
    )
    mirrored = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert len(mirrored) == 1
    assert mirrored[0]["recorded_at"] == "2026-09-30T00:59:59+00:00"
    assert mirrored[0]["pricing_version"] == "deepseek-2026-08-17-offpeak"
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_reservations")
    receipt = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage_imports")[0]
    assert receipt["source_ledger_path"] == str(source_path.resolve())
    assert receipt["source_reservation_id"] == reservation_id
    assert receipt["source_usage_id"] == 1
    assert receipt["target_usage_id"] == mirrored[0]["id"]
    assert receipt["imported_at"] == "2026-10-01T01:00:01+00:00"


@pytest.mark.asyncio
async def test_same_ledger_hook_does_not_import_its_own_bill(tmp_path, monkeypatch, caplog):
    source = CapturingUsageStore(path=str(tmp_path / "same.sqlite"))
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    monkeypatch.delenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", raising=False)
    monkeypatch.setattr(spend_account, "_DEBUG_STORE", source)
    await complete_offline(source)
    spend_account.maybe_record_debug_usage(source.received[0], observer=source.record)
    assert len(read_rows(tmp_path / "same.sqlite", "world_v2_model_usage")) == 1
    assert not read_rows(tmp_path / "same.sqlite", "world_v2_model_usage_imports")
    assert "usage has no matching reservation" not in caplog.text


@pytest.mark.asyncio
async def test_unreserved_standalone_provider_call_keeps_direct_debug_usage(
    tmp_path, debug_book, monkeypatch, caplog
):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    await complete_offline(None)
    rows = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert len(rows) == 1 and rows[0]["billing_state"] == "known"
    assert rows[0]["total_tokens"] == 1100
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_reservations")
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage_imports")
    assert "usage has no matching reservation" not in caplog.text


@pytest.mark.parametrize("state", ["absent", "pending", "unknown", "legacy", "not_billed"])
def test_no_final_primary_receipt_cannot_be_mirrored_as_settled(
    tmp_path, debug_book, monkeypatch, state
):
    source = WorldV2UsageStore(path=str(tmp_path / "source.sqlite"))
    reservation_id = "reservation:absent" if state == "absent" else admit(source)
    bill = provider_bill(reservation_id)
    if state in {"unknown", "legacy"}:
        source.record(replace(bill, billing_state=state))
    elif state == "not_billed":
        source.record(
            replace(
                bill,
                billing_state="not_billed",
                prompt_tokens=0,
                completion_tokens=0,
                cache_hit_tokens=0,
                cache_miss_tokens=0,
                total_tokens=0,
            )
        )
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    spend_account.maybe_record_debug_usage(bill, observer=source.record)
    assert not debug_book.import_settled_provider_usage(
        source=source, reservation_id=reservation_id
    )
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage_imports")
    if state == "unknown":
        # An unsuccessful model turn may still have an actual final provider bill.
        source.record(replace(bill, status="failed"))
        assert debug_book.import_settled_provider_usage(
            source=source, reservation_id=reservation_id
        )
        rows = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
        assert len(rows) == 1 and rows[0]["status"] == "failed"


def test_foreign_reservation_without_primary_observer_is_not_directly_recorded(
    tmp_path, debug_book, monkeypatch
):
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    spend_account.maybe_record_debug_usage(provider_bill("reservation:foreign"))
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")


def test_source_tokens_are_namespaced_from_other_sources_and_local_admissions(tmp_path, debug_book):
    token = "reservation:shared-token"
    admit(debug_book, token)
    for index in range(2):
        source = WorldV2UsageStore(path=str(tmp_path / f"source-{index}.sqlite"))
        admit(source, token)
        source.record(provider_bill(token))
        assert debug_book.import_settled_provider_usage(source=source, reservation_id=token)
    # Existing target-local reservation is still pending, unrelated to either import.
    reservations = read_rows(tmp_path / "debug.sqlite", "world_v2_model_reservations")
    assert [(row["reservation_id"], row["status"]) for row in reservations] == [(token, "pending")]
    debug_book.record(provider_bill(token))
    rows = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert len(rows) == 3
    assert len({row["reservation_id"] for row in rows}) == 3
    imports = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage_imports")
    assert len(imports) == 2
    assert {row["source_reservation_id"] for row in imports} == {token}
    # Imported bookkeeping references cannot later become spend admissions.
    with pytest.raises(ModelUsageAdmissionError, match="import"):
        admit(debug_book, imports[0]["import_id"])


def test_import_write_failure_rolls_back_bill_and_cold_retry_is_idempotent(tmp_path, debug_book):
    source = WorldV2UsageStore(path=str(tmp_path / "source.sqlite"))
    token = admit(source)
    source.record(provider_bill(token))
    target_path = tmp_path / "debug.sqlite"
    with sqlite3.connect(target_path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_import BEFORE INSERT ON world_v2_model_usage_imports "
            "BEGIN SELECT RAISE(ABORT, 'offline write interruption'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="offline write interruption"):
        debug_book.import_settled_provider_usage(source=source, reservation_id=token)
    assert not read_rows(target_path, "world_v2_model_usage")
    assert not read_rows(target_path, "world_v2_model_usage_imports")
    with sqlite3.connect(target_path) as connection:
        connection.execute("DROP TRIGGER reject_import")
    for _ in range(2):
        reopened = WorldV2UsageStore(path=str(target_path))
        assert reopened.import_settled_provider_usage(source=source, reservation_id=token)
    assert len(read_rows(target_path, "world_v2_model_usage")) == 1
    assert len(read_rows(target_path, "world_v2_model_usage_imports")) == 1


def test_changed_source_bill_cannot_replace_imported_receipt(tmp_path, debug_book):
    source_path = tmp_path / "source.sqlite"
    source = WorldV2UsageStore(path=str(source_path))
    token = admit(source)
    source.record(provider_bill(token))
    assert debug_book.import_settled_provider_usage(source=source, reservation_id=token)
    # Simulate corruption/replacement of a stopped source file, not a new provider bill.
    with sqlite3.connect(source_path) as connection:
        connection.execute("UPDATE world_v2_model_usage SET completion_tokens = 101")
    with pytest.raises(ModelUsageAdmissionError, match="conflicting imported provider bill"):
        debug_book.import_settled_provider_usage(source=source, reservation_id=token)
    rows = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert len(rows) == 1 and rows[0]["completion_tokens"] == 100


def test_bill_may_complete_attribution_left_empty_at_admission(tmp_path, debug_book):
    source = WorldV2UsageStore(path=str(tmp_path / "source.sqlite"))
    token = admit(source)
    source.record(replace(provider_bill(token), world_id="world:test", turn_id="turn:test"))
    assert debug_book.import_settled_provider_usage(source=source, reservation_id=token)
    row = read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")[0]
    assert (row["world_id"], row["turn_id"]) == ("world:test", "turn:test")


@pytest.mark.asyncio
async def test_failed_primary_record_cannot_be_hidden_by_a_settled_debug_mirror(
    tmp_path, debug_book, monkeypatch, caplog
):
    class RejectedUsageStore(WorldV2UsageStore):
        def record(self, usage):
            # The budget callback swallows its own attribution validation error.
            super().record(replace(usage, purpose="wrong-purpose"))

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "")
    source = RejectedUsageStore(path=str(tmp_path / "source.sqlite"))
    await complete_offline(source)
    rows = read_rows(tmp_path / "source.sqlite", "world_v2_model_reservations")
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert "no settled source provider bill" in caplog.text


def test_import_refuses_different_exchange_rate_instead_of_repricing_bill(tmp_path, debug_book):
    source = WorldV2UsageStore(path=str(tmp_path / "source.sqlite"), usd_to_cny=3.0)
    token = source.admit_provider_call(
        purpose="activity_lifecycle_choice",
        actor="actor:companion",
        provider="openai",
        model="gpt-4o",
        prompt_characters=100,
    )
    source.record(replace(provider_bill(token), provider="openai", model="gpt-4o"))
    with pytest.raises(ModelUsageAdmissionError, match="exchange rate"):
        debug_book.import_settled_provider_usage(source=source, reservation_id=token)
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage")
    assert not read_rows(tmp_path / "debug.sqlite", "world_v2_model_usage_imports")
