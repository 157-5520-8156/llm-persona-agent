"""Every Settings-backed local host must preserve the Recall rollback switch."""

from datetime import UTC, datetime

import pytest

from companion_daemon import cli
from companion_daemon.config import Settings
from companion_daemon.llm import FakeCompanionModel
from companion_daemon.world_v2 import http_capture_host


@pytest.mark.asyncio
@pytest.mark.parametrize("host_name", ["simulator", "http_capture"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_local_host_honors_parent_reading_environment(
    tmp_path, monkeypatch, host_name, enabled
) -> None:
    monkeypatch.setenv("WORLD_V2_RECALL_PARENT_CONTEXT", str(enabled).lower())
    settings = Settings(_env_file=None, database_path=tmp_path / "world.sqlite")
    configs = []

    class CompositionObserved(Exception):
        pass

    def capture_application_config(*, config, **_kwargs):
        configs.append(config)
        raise CompositionObserved

    module = cli if host_name == "simulator" else http_capture_host
    monkeypatch.setattr(
        module, "build_sqlite_world_v2_turn_application", capture_application_config
    )
    with pytest.raises(CompositionObserved):
        if host_name == "simulator":
            monkeypatch.setattr(cli, "get_settings", lambda: settings)
            await cli.run_simulation("检查读取配置。", fake=True)
        else:
            http_capture_host.build_http_v2_capture_host(
                settings=settings,
                bootstrap_at=datetime(2026, 10, 6, tzinfo=UTC),
                model=FakeCompanionModel(),
            )

    assert len(configs) == 1
    assert configs[0].recall_parent_context is enabled
