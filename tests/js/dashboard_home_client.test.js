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
