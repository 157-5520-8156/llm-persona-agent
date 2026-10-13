"""Bounded real-provider hybrid check on a copied World; never send to QQ."""

import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit

import httpx

from companion_daemon.config import Settings
from companion_daemon.usage_metrics import estimate_provider_request_reserve_cny
from companion_daemon.world_v2.longitudinal_journey import JourneyClock
from companion_daemon.world_v2.qq_c2c_host import build_qq_c2c_host
from drive_production_lanes import CaptureDelivery, DriveSession
from trial_anthropic_proxy import WORLD, digest, save


class BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(self, model, evidence, allowance, entered):
        self.model, self.evidence, self.allowance = model, evidence, allowance
        self.entered = entered
        self.inner = httpx.AsyncHTTPTransport(trust_env=False)

    async def handle_async_request(self, request):
        body = json.loads(request.content)
        family = 'proxy' if self.model.provider == 'anthropic' else 'deepseek'
        endpoint = urlsplit(self.model.base_url)
        if (request.url.host != endpoint.hostname or request.url.port != endpoint.port
                or body['model'] != self.model.model):
            raise ValueError('hybrid trial endpoint/model mismatch')
        if self.allowance[family] <= 0:
            raise ValueError('hybrid trial request allowance exhausted')
        if family == 'deepseek':
            reserve = estimate_provider_request_reserve_cny(request_payload=body)
            if reserve > self.allowance['cny']:
                raise ValueError('hybrid trial CNY allowance exhausted')
            self.allowance['cny'] -= reserve
        self.allowance[family] -= 1
        row = {'provider':self.model.provider, 'model':body['model'],
               'request_bytes':len(request.content), 'path':request.url.path,
               'started_monotonic':time.monotonic(), 'status':'sent'}
        self.evidence.append(row)
        if family == 'proxy':
            self.entered.set()
        try:
            response = await self.inner.handle_async_request(request)
            row.update(http_status=response.status_code,
                       response_headers_seconds=time.monotonic()-row['started_monotonic'])
            return response
        except BaseException as exc:
            row.update(status='transport_failure', error_type=type(exc).__name__)
            raise

    async def aclose(self):
        await self.inner.aclose()


async def run(args):
    root, source = args.output.resolve(), args.source.resolve()
    root.mkdir(parents=True, exist_ok=False, mode=0o700)
    database = root / 'world.sqlite'
    source_hash = digest(source)
    with sqlite3.connect(source.as_uri()+'?mode=ro', uri=True) as src:
        with sqlite3.connect(database) as dst:
            src.backup(dst)
    with sqlite3.connect(database) as connection:
        raw = connection.execute("SELECT event_json FROM world_v2_events WHERE world_id=? "
            "AND json_extract(event_json,'$.event_type')='ClockAdvanced' "
            "ORDER BY ledger_sequence DESC LIMIT 1", (WORLD,)).fetchone()[0]
        origin = datetime.fromisoformat(json.loads(json.loads(raw)['payload_json'])[
            'logical_time_to'].replace('Z','+00:00'))
    settings = Settings(_env_file=tuple(args.env_file), database_path=database,
        PRIMARY_USER_ID='geoff', DEEPSEEK_API_KEY=None,
        WORLD_V2_BACKGROUND_PROVIDER='anthropic_subscription',
        WORLD_V2_BACKGROUND_API_KEY='local-proxy',
        WORLD_V2_REFERENCE_WIRE_ENABLED=True,
        WORLD_V2_MODEL_USAGE_BUDGET_DISABLED=False,
        WORLD_V2_LIFE_OUTLOOK_ENABLED=True,
        WORLD_V2_EXTERNAL_PERCEPTION_MODE='off',
        WORLD_V2_EXTERNAL_PERCEPTION_SIDECAR_PATH=root/'perception.sqlite',
        ATTACHMENT_CACHE_PATH=root/'attachments')
    assert settings.deepseek_debug_api_key and not settings.deepseek_api_key
    assert settings.world_v2_ordinary_text_review_mode == 'sampled'
    assert settings.world_v2_background_provider == 'anthropic_subscription'
    assert settings.world_v2_reference_wire_enabled
    for endpoint in (settings.world_v2_background_base_url,
                     settings.world_v2_recall_embedding_base_url,
                     settings.world_v2_text_endpoint_base_url):
        assert urlsplit(endpoint).hostname in {'localhost','127.0.0.1','::1'}
    os.environ['COMPANION_DISABLE_DEBUG_USAGE_LEDGER'] = '1'
    result = {'completed':False, 'requests':[], 'source_sha256':source_hash,
              'mode':'isolated_capture', 'quality_qualified':False,
              'initial_allowance':{'deepseek_requests':2,'deepseek_reserved_cny':0.30,
                                   'proxy_requests':4}}
    allowance = {'proxy':4, 'deepseek':2, 'cny':0.30}
    clock, delivery, entered = JourneyClock(origin), CaptureDelivery(), asyncio.Event()
    host = None
    memory = None
    try:
        host = build_qq_c2c_host(settings=settings, recipient_id='world-v2-console-user',
            bootstrap_at=origin, delivery=delivery, ingress_now=clock.presentation_now,
            ingress_sleep=clock.presentation_sleep, action_due_now=clock.now,
            action_due_sleep=clock.timer_sleep)
        composition = host._semantic_chat
        for model in composition._owned_models:
            if model.provider not in {'deepseek','anthropic'}:
                continue
            await model.client.aclose()
            model.client = httpx.AsyncClient(timeout=getattr(model,'timeout_seconds',45),
                trust_env=False, transport=BoundedTransport(model,result['requests'],allowance,entered))
        app = host._host._application
        result['initial_prehistory'] = len(app._ledger.project().prehistory_records)
        session = DriveSession(database=database, recipient_id='world-v2-console-user',
            delivery=delivery, host=host, clock=origin, journey_clock=clock)
        memory = asyncio.create_task(app._turns._runtime._memory_consolidation.drain_one())
        await asyncio.wait_for(entered.wait(), 60)
        result['background_pending_at_chat_start'] = not memory.done()
        result['chat'] = await session.inbound('刚忙完，想跟你聊会儿。你今天怎么样？', background_units=0)
        result['background_pending_at_first_chat_completion'] = not memory.done()
        save(root/'progress.json',result)
        result['memory'] = (await memory).model_dump(mode='json')
        projection = app._ledger.project()
        wake = next(e for e in reversed(projection.committed_world_event_refs)
                    if e.event_type == 'ClockAdvanced')
        result['outlook'] = await app._life_ecology._life_development_followup.prepare_outlook_once(
            wake_event_ref=wake.event_id)
        result['subscription'] = composition.world_support_model.token_ledger.snapshot()
        result['final_prehistory'] = len(app._ledger.project().prehistory_records)
        result['completed'] = True
    except BaseException as exc:
        result['error_type'] = type(exc).__name__
        raise
    finally:
        if memory is not None and not memory.done():
            memory.cancel()
            await asyncio.gather(memory, return_exceptions=True)
        if host is not None:
            await host.aclose()
        result['source_unchanged'] = digest(source) == source_hash
        result['remaining_allowance'] = allowance
        save(root/'result.json',result)
        print(json.dumps({k:result.get(k) for k in ('completed','error_type','memory','outlook',
            'subscription','background_pending_at_chat_start','background_pending_at_first_chat_completion',
            'source_unchanged')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--env-file',type=Path,action='append',default=[])
    parser.add_argument('--live',action='store_true')
    args = parser.parse_args()
    if not args.live:
        parser.error('real requests require --live; use offline transport tests otherwise')
    asyncio.run(run(args))
