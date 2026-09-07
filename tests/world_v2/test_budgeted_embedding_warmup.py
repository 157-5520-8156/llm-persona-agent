import sqlite3

import httpx
import pytest

from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
from companion_daemon.world_v2.recall_embedding import OpenAICompatibleRecallEmbedding
from test_production_turn_application import (
    NOW,
    _config,
    _DeliveredTransport,
    _Identities,
    _Router,
    _DraftChatModel,
)
from world_v2_application import (
    build_sqlite_world_v2_test_application,
    compose_fixture_character_interior,
)


@pytest.mark.parametrize(
    "warm,denied,expected_calls", [(True, False, 1), (True, True, 0), (False, False, 0)]
)
def test_installed_warmup_respects_durable_embedding_budget(tmp_path, warm, denied, expected_calls):
    path = tmp_path / "world.sqlite"
    calls = []

    def handler(request):
        # The reservation exists before any provider work, including warmup.
        with sqlite3.connect(path) as connection:
            assert connection.execute(
                "SELECT last_status FROM world_recall_embedding_usage_daily"
            ).fetchone() == ("reserved",)
        calls.append(request)
        return httpx.Response(
            200, json={"data": [{"embedding": [1.0, 0.0]}], "usage": {"total_tokens": 6}}
        )

    embedding = OpenAICompatibleRecallEmbedding(
        api_key="fixture",
        base_url="https://embedding.test/v1",
        model="test-embedding",
        dimensions=2,
        daily_budget_cny=1e-12 if denied else 0.1,
        monthly_budget_cny=1,
        transport=httpx.MockTransport(handler),
    )
    app = build_sqlite_world_v2_test_application(
        path=path,
        config=_config(),
        identities=_Identities(),
        router=_Router(),
        character_interior=compose_fixture_character_interior(
            inbound_author=_InboundCharacterAuthor(flash_model=_DraftChatModel()),
        ),
        transport=_DeliveredTransport(),
        now=NOW,
        semantic_recall_embedding=embedding,
        warm_semantic_recall=warm,
    )
    app.close()
    assert len(calls) == expected_calls
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT request_count, rejected_count FROM world_recall_embedding_usage_daily"
        ).fetchone()
    assert row == ((1, 0) if expected_calls else ((0, 1) if denied else None))


def test_invalid_provider_warmup_closes_the_owned_embedding(tmp_path):
    class InvalidEmbedding:
        version = "fixture:invalid-warmup"
        dimensions = 2
        closed = False

        def embed(self, texts):
            raise ValueError("invalid embedding response")

        def close(self):
            self.closed = True

    embedding = InvalidEmbedding()
    with pytest.raises(ValueError, match="invalid embedding response"):
        build_sqlite_world_v2_test_application(
            path=tmp_path / "invalid.sqlite", config=_config(), identities=_Identities(), router=_Router(),
            character_interior=compose_fixture_character_interior(
                inbound_author=_InboundCharacterAuthor(flash_model=_DraftChatModel()),
            ), transport=_DeliveredTransport(), now=NOW,
            semantic_recall_embedding=embedding, warm_semantic_recall=True,
        )
    assert embedding.closed
