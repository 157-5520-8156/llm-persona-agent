import asyncio
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.authorized_reply_handoff import (
    authorized_reply_handoff, handoff_authorized_reply,
)
from companion_daemon.world_v2.qq_c2c_host import QQC2CHost
from companion_daemon.world_v2.qq_ingress_policy import MemoryQQIngressStore
from test_qq_c2c_action_handoff import NOW, _PacingClock, _DurableDispatchHost


@pytest.mark.asyncio
async def test_first_authorized_text_is_drained_before_remaining_state_settlement():
    class Host(_DurableDispatchHost):
        state_settled = False

        async def action_due_projection(self):
            return SimpleNamespace(actions=(
                SimpleNamespace(action_id='action:reply:one', kind='reply', state='authorized',
                                not_before=None, claim_lease=None),))

        async def inbound(self, inbound):
            await handoff_authorized_reply(('action:reply:one',))
            assert self.delivered_action_ids == ['action:reply:one']
            self.state_settled = True
            return await super().inbound(inbound)

    platform = Host()
    platform.provider_release.set()
    clock = _PacingClock()
    host = QQC2CHost(host=platform, recipient_id='10001', canonical_user_id='geoff',
        ingress_store=MemoryQQIngressStore(), ingress_now=clock.now, ingress_sleep=clock.sleep,
        idle_heartbeat_seconds=0)
    try:
        outcome = await host.inbound_text(message_id='early',recipient_id='10001',text='你好',observed_at=NOW)
        assert outcome.status == 'action_authorized'
        assert platform.state_settled
        assert platform.delivered_action_ids == ['action:reply:one']
    finally:
        await host.aclose()


@pytest.mark.asyncio
async def test_handoff_is_retired_even_in_an_inherited_task_context():
    called = []
    ready = asyncio.Event()

    async def handler(ids):
        called.append(ids)

    async def delayed():
        await ready.wait()
        await handoff_authorized_reply(('late',))

    with authorized_reply_handoff(handler):
        task = asyncio.create_task(delayed())
        await handoff_authorized_reply(('current',))
    ready.set()
    await task
    assert called == [('current',)]


@pytest.mark.asyncio
@pytest.mark.parametrize('reader_failure,provider_failure', [(True, False), (False, True)])
async def test_failed_hint_or_provider_does_not_erase_paid_state(reader_failure, provider_failure):
    class Host(_DurableDispatchHost):
        state_settled = False
        sends = 0

        async def action_due_projection(self):
            if reader_failure:
                raise ValueError('unavailable hint projection')
            return SimpleNamespace(actions=(SimpleNamespace(
                action_id='action:reply:one', kind='reply', state='authorized',
                not_before=None, claim_lease=None),))

        async def inbound(self, inbound):
            await handoff_authorized_reply(('action:reply:one',))
            self.state_settled = True
            return await super().inbound(inbound)

        async def drain_action(self, action_id):
            self.sends += 1
            if provider_failure:
                return SimpleNamespace(action_id=action_id, action_kind='reply',
                    status='settled', provider_status='failed')
            assert self.state_settled  # Reader failure falls back to the ordinary path.
            return await super().drain_action(action_id)

    platform = Host()
    platform.provider_release.set()
    clock = _PacingClock()
    host = QQC2CHost(host=platform, recipient_id='10001', canonical_user_id='geoff',
        ingress_store=MemoryQQIngressStore(), ingress_now=clock.now, ingress_sleep=clock.sleep)
    try:
        await host.inbound_text(message_id='failure',recipient_id='10001',text='你好',observed_at=NOW)
        assert platform.state_settled and platform.sends == 1
        assert len(platform.delivered_action_ids) == int(not provider_failure)
    finally:
        await host.aclose()
