const assert = require('node:assert/strict');
const test = require('node:test');

const client = require(process.env.DASHBOARD_APP_JS_PATH);

const snapshot = (room) => ({
  schema_version: 'world-v2-dashboard-home.1',
  snapshot_hash: 'a'.repeat(64),
  logical_time: '2026-08-12T20:30:00+08:00',
  cursor: { world_revision: 4, deliberation_revision: 7, ledger_sequence: 11 },
  sections: { room },
});

test('dashboard capture uses the authenticated same-origin home snapshot with ETag', async () => {
  const requests = [];
  const fetchSnapshot = async (url, options) => {
    requests.push([url, options]);
    return {
      ok: true,
      status: 200,
      headers: { get: name => name.toLowerCase() === 'etag' ? '"snapshot-2"' : null },
      json: async () => ({
        schema_version: 'world-v2-dashboard-home.1',
        snapshot_hash: 'a'.repeat(64),
        cursor: { world_revision: 4, deliberation_revision: 7, ledger_sequence: 11 },
        sections: {
          room: {
            state: 'ready',
            render_state: {
              route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
            },
          },
        },
      }),
    };
  };

  const result = await client.capture(fetchSnapshot, '"snapshot-1"');

  assert.deepEqual(requests, [[
    '/world-v2/dashboard/home',
    {
      credentials: 'same-origin',
      headers: { Accept: 'application/json', 'If-None-Match': '"snapshot-1"' },
    },
  ]]);
  assert.equal(result.kind, 'snapshot');
  assert.equal(result.etag, '"snapshot-2"');
  assert.equal(result.snapshot.cursor.ledger_sequence, 11);
});

test('room message forwards only the owner-provided renderer route', () => {
  const message = client.roomMessageFrom(snapshot({
    state: 'ready',
    render_state: {
      route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
    },
  }));

  assert.deepEqual(message, {
    type: 'pixel-home-state',
    v: 2,
    state: 'ready',
    route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
    logical_time: '2026-08-12T20:30:00+08:00',
  });
});

test('stale room state is explicit and never invents a renderer route', () => {
  const message = client.roomMessageFrom(snapshot({
    state: 'stale',
    render_state: { route: null },
  }));

  assert.deepEqual(message, {
    type: 'pixel-home-state',
    v: 2,
    state: 'stale',
    route: null,
    logical_time: '2026-08-12T20:30:00+08:00',
  });
});

test('not-modified result revives the last verified snapshot after a stale poll', () => {
  const verified = snapshot({
    state: 'ready',
    render_state: {
      route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
    },
  });

  const restored = client.snapshotFromCapture(
    { kind: 'not_modified', etag: '"snapshot-2"', snapshot: null },
    verified,
  );

  assert.equal(restored, verified);
  assert.equal(client.roomMessageFrom(restored).state, 'ready');
});

test('section headings prefer the server display label without inventing one', () => {
  assert.equal(client.sectionLabel('overview_life', { label: '生活概览' }), '生活概览');
  assert.equal(client.sectionLabel('future_section', {}), 'future_section');
});

test('zero metrics remain available but are separated from active information', () => {
  assert.deepEqual(client.metricGroups([
    { key: 'facts', label: '事实', count: 0 },
    { key: 'npcs', label: '人物', count: 2 },
  ]), {
    active: [{ key: 'npcs', label: '人物', count: 2 }],
    zero: [{ key: 'facts', label: '事实', count: 0 }],
  });
});

test('owner-facing labels hide snapshot jargon', () => {
  assert.equal(client.stateLabel('ready'), '正常');
  assert.equal(client.stateLabel('empty'), '暂无');
  assert.equal(client.stateLabel('unavailable'), '暂时看不到');
  assert.equal(client.stateLabel('stale'), '数据有点旧');
  assert.equal(client.captureStateLabel('ready'), '已同步');
  assert.equal(client.roomOverlayText('unavailable'), '房间暂时看不到');
  assert.equal(client.roomOverlayText('stale'), '房间画面有点旧');
  assert.equal(client.sectionHeading('facts_memory_inner', { label: '事实、记忆与内在' }), '心里');
  assert.equal(client.metaLabel('logical_time'), '世界时间');
  assert.equal(client.dataFieldLabel('typed_change_terminals'), null);
});

test('clock formatting uses Shanghai wall time', () => {
  assert.equal(client.formatClock('2026-08-15T23:38:00+08:00'), '8月15日 23:38');
  assert.equal(client.formatClock('2026-08-15T15:38:00Z'), '8月15日 23:38');
});

test('highlight view prefers labels and percent chips over raw keys', () => {
  const view = client.highlightView({
    kind: 'plan',
    kind_label: '计划与活动',
    title: '窝着刷手机',
    status_code: 'active',
    status_label: '进行中',
    occurred_at: '2026-08-15T23:38:00+08:00',
    values: [{ key: 'importance_bp', label: '重要度', value: 8000 }],
  });

  assert.equal(view.kindLabel, '在做什么');
  assert.equal(view.title, '窝着刷手机');
  assert.equal(view.status, '进行中');
  assert.deepEqual(view.values, [{ label: '重要度', text: '80%' }]);
});

test('generic highlight titles fall back to the status label', () => {
  const view = client.highlightView({
    kind: 'location',
    kind_label: '位置',
    title: '位置状态已更新',
    status_label: '可见',
  });

  assert.equal(view.kindLabel, '人在哪');
  assert.equal(view.title, '可见');
  assert.equal(view.status, '');
});

test('now story keeps core rows and marks missing states instead of hiding them', () => {
  const lines = client.nowStory({
    sections: {
      overview_life: {
        data: {
          highlights: [
            { kind: 'plan', kind_label: '计划与活动', title: '窝着刷手机', status_label: '进行中' },
            { kind: 'location', kind_label: '位置', title: '位置状态已更新', status_label: '在宿舍' },
          ],
        },
      },
      facts_memory_inner: { data: { highlights: [] } },
      relationship_lifecycle: { data: { highlights: [] } },
      operations: { data: { highlights: [] } },
    },
  });

  assert.deepEqual(
    lines.filter((line) => !line.missing),
    [
      { label: '在做什么', text: '窝着刷手机 · 进行中', detail: '', missing: false },
      { label: '人在哪', text: '在宿舍', detail: '', missing: false },
    ],
  );
  assert.equal(lines.find((line) => line.label === '心情').text, '这会儿没有记下的心情');
  assert.equal(lines.find((line) => line.label === '她在等').text, '这轮没有在等你回');
});
