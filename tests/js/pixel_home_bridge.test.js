const assert = require('node:assert/strict');
const test = require('node:test');

global.window = globalThis;
require('../../prototypes/pixel-home/js/bridge.js');
const bridge = window.PixelHomeBridge;

function fakeEngine(interactions = []) {
  const calls = { dispatch: [], ended: 0 };
  return {
    calls,
    mode: 'live',
    autoLife: true,
    timeScale: 60,
    clock: 0,
    actor: { activity: null, pendingAct: null },
    availableInteractions: () => interactions,
    dist: tile => tile[0] + tile[1],
    dispatch: (name, options) => { calls.dispatch.push([name, options]); },
    endActivity: () => { calls.ended += 1; },
  };
}

test('pixel home accepts one explicit renderer route without domain inference', () => {
  const directive = bridge.directiveFor({
    type: 'pixel-home-state',
    v: 2,
    state: 'ready',
    route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
    logical_time: '2026-08-12T20:30:00+08:00',
  });

  assert.deepEqual(directive, {
    goal: 'interaction',
    action_id: 'study',
    logical_time: '2026-08-12T20:30:00+08:00',
  });
});

test('foreign and future scene messages are ignored entirely', () => {
  assert.equal(bridge.directiveFor(null), null);
  assert.equal(bridge.directiveFor({ type: 'other', v: 2 }), null);
  assert.equal(bridge.directiveFor({ type: 'pixel-home-state', v: 3 }), null);
});

test('query modes keep factual embeds separate from the standalone editor', () => {
  assert.deepEqual(bridge.queryModes('?embed=1'), { embed: true, edit: false });
  assert.deepEqual(bridge.queryModes('?edit=1'), { embed: false, edit: true });
  assert.deepEqual(bridge.queryModes('?embed=0&edit=yes'), { embed: false, edit: false });
});

test('embed mode applies only the dedicated body class', () => {
  const toggles = [];
  const documentRef = {
    body: { classList: { toggle: (...args) => toggles.push(args) } },
  };

  assert.deepEqual(
    bridge.applyQueryMode('?embed=1', documentRef),
    { embed: true, edit: false },
  );
  assert.deepEqual(toggles, [['embed', true]]);
});

test('edit startup reuses the existing mode control', () => {
  const engine = { mode: 'live' };
  let clicks = 0;
  const editControl = {
    click() {
      clicks += 1;
      engine.mode = 'edit';
    },
  };

  assert.equal(bridge.enterEditMode(engine, editControl), true);
  assert.equal(bridge.enterEditMode(engine, editControl), true);
  assert.equal(clicks, 1);
});

test('stale scene freezes external control and never resumes a fallback schedule', () => {
  const engine = fakeEngine();
  const directive = bridge.directiveFor({
    type: 'pixel-home-state',
    v: 2,
    state: 'stale',
    route: null,
    logical_time: null,
  });

  bridge.apply(engine, directive);

  assert.equal(engine.autoLife, false);
  assert.equal(engine.timeScale, 0);
  assert.deepEqual(engine.calls.dispatch, []);
  assert.equal(engine.calls.ended, 0);
});

test('embedded renderer disables its autonomous schedule before the first snapshot', () => {
  const engine = fakeEngine();

  assert.equal(bridge.enterEmbedMode(engine), true);
  assert.equal(engine.autoLife, false);
  assert.equal(engine.timeScale, 0);
});

test('embed hook freezes an asynchronously installed engine before its first frame', () => {
  const hostWindow = {};
  const engine = fakeEngine();

  assert.equal(bridge.armEmbedMode(hostWindow), true);
  hostWindow.engine = engine;

  assert.equal(engine.autoLife, false);
  assert.equal(engine.timeScale, 0);
});

test('explicit action dispatches the nearest matching renderer interaction at observed time', () => {
  const engine = fakeEngine([
    { name: 'desk-far', key: 'study', approach: [6, 6] },
    { name: 'desk-near', key: 'study', approach: [1, 2] },
    { name: 'sofa', key: 'relax', approach: [0, 1] },
  ]);
  const directive = bridge.directiveFor({
    type: 'pixel-home-state',
    v: 2,
    state: 'ready',
    route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
    logical_time: '2026-08-12T20:30:00',
  });

  bridge.apply(engine, directive);

  assert.deepEqual(engine.calls.dispatch, [['desk-near', { manual: true }]]);
  assert.equal(engine.clock, 20.5 * 60 * 60);
});

test('an unavailable renderer action holds the prior frame without guessing or ending it', () => {
  const engine = fakeEngine([]);
  engine.actor.activity = { name: 'verified-prior-action' };
  const directive = bridge.directiveFor({
    type: 'pixel-home-state',
    v: 2,
    state: 'ready',
    route: { scene_id: 'zhizhi-home', action_id: 'not-installed', availability: 'busy' },
    logical_time: null,
  });

  bridge.apply(engine, directive);

  assert.deepEqual(engine.calls.dispatch, []);
  assert.equal(engine.calls.ended, 0);
  assert.equal(engine.actor.activity.name, 'verified-prior-action');
});

test('same renderer action is not re-dispatched while already active', () => {
  const study = { name: 'desk', key: 'study', approach: [1, 2] };
  const engine = fakeEngine([study]);
  engine.actor.activity = study;
  const directive = bridge.directiveFor({
    type: 'pixel-home-state',
    v: 2,
    state: 'ready',
    route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
    logical_time: null,
  });

  bridge.apply(engine, directive);

  assert.deepEqual(engine.calls.dispatch, []);
});

test('edit mode is never overridden by dashboard state', () => {
  const engine = fakeEngine([
    { name: 'desk', key: 'study', approach: [1, 2] },
  ]);
  engine.mode = 'edit';

  bridge.apply(engine, {
    goal: 'interaction',
    action_id: 'study',
    logical_time: null,
  });

  assert.equal(engine.autoLife, true);
  assert.equal(engine.timeScale, 60);
  assert.deepEqual(engine.calls.dispatch, []);
});

test('same-origin messages from outside the dashboard parent are ignored', () => {
  const engine = fakeEngine([
    { name: 'desk', key: 'study', approach: [1, 2] },
  ]);
  window.location = { origin: 'http://127.0.0.1:8767' };
  window.parent = { name: 'dashboard-parent' };
  window.engine = engine;

  bridge.onMessage({
    origin: window.location.origin,
    source: { name: 'other-window' },
    data: {
      type: 'pixel-home-state',
      v: 2,
      state: 'ready',
      route: { scene_id: 'zhizhi-home', action_id: 'study', availability: 'busy' },
      logical_time: null,
    },
  });

  assert.deepEqual(engine.calls.dispatch, []);
});
