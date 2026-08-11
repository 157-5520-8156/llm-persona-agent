'use strict';

// ---------------------------------------------------------------------------
// Dashboard bridge: applies renderer-ready owner snapshot routes posted by the
// World v2 Dashboard host page to the pixel-home engine.
//
// Standalone opens (file:// or direct /pixel-home/index.html) receive no
// messages, so the prototype may keep its own demo schedule. Embed mode is a
// factual renderer: it disables that schedule before the engine starts and
// never resumes it when owner snapshots become stale or unavailable.
//
// Message contract (host -> iframe, same origin):
//   {type:'pixel-home-state', v:2, state:'ready'|'stale'|'unavailable',
//    route:{scene_id,action_id,availability}|null, logical_time:string|null}
// ---------------------------------------------------------------------------

const PixelHomeBridge = (() => {
  const MESSAGE_TYPE = 'pixel-home-state';
  const MESSAGE_VERSION = 2;

  function queryModes(search) {
    const params = new URLSearchParams(search || '');
    return {
      embed: params.get('embed') === '1',
      edit: params.get('edit') === '1',
    };
  }

  function applyQueryMode(search, documentRef) {
    const modes = queryModes(search);
    if (documentRef && documentRef.body) {
      documentRef.body.classList.toggle('embed', modes.embed);
    }
    return modes;
  }

  // Reuse the editor's public UI control so mode labels, body classes, and
  // palette state stay in sync with main.js.
  function enterEditMode(engine, editControl) {
    if (!engine || !editControl) return false;
    if (engine.mode !== 'edit') editControl.click();
    return engine.mode === 'edit';
  }

  function enterEmbedMode(engine) {
    if (!engine) return false;
    engine.autoLife = false;
    engine.timeScale = 0;
    return true;
  }

  function armEmbedMode(windowRef) {
    if (!windowRef) return false;
    if (enterEmbedMode(windowRef.engine)) return true;
    let engine = null;
    Object.defineProperty(windowRef, 'engine', {
      configurable: true,
      enumerable: true,
      get: () => engine,
      set: value => {
        engine = value;
        enterEmbedMode(value);
      },
    });
    return true;
  }

  // Pure decision step: only one explicit renderer action may become an
  // interaction. Missing, unknown, stale, and unavailable routes hold the
  // last verified frame.
  function directiveFor(message) {
    if (!message || message.type !== MESSAGE_TYPE || message.v !== MESSAGE_VERSION) return null;
    if (message.state !== 'ready') return { goal: 'hold', state: message.state === 'stale' ? 'stale' : 'unavailable' };
    const route = message.route;
    if (!route || route.scene_id !== 'zhizhi-home' || route.availability === 'unavailable') {
      return { goal: 'hold', state: 'unavailable' };
    }
    if (typeof route.action_id !== 'string' || !route.action_id) {
      return { goal: 'hold', state: 'unavailable' };
    }
    return {
      goal: 'interaction',
      action_id: route.action_id,
      logical_time: typeof message.logical_time === 'string' ? message.logical_time : null,
    };
  }

  function pickInteraction(engine, actionId) {
    const candidates = engine.availableInteractions()
      .filter(it => it.key === actionId)
      .sort((a, b) => engine.dist(a.approach) - engine.dist(b.approach));
    return candidates[0] || null;
  }

  function observedClockSeconds(value) {
    if (typeof value !== 'string') return null;
    const observed = new Date(value);
    if (Number.isNaN(observed.getTime())) return null;
    return observed.getHours() * 3600 + observed.getMinutes() * 60 + observed.getSeconds();
  }

  let editModeWaiter = null;

  function apply(engine, directive) {
    if (!directive || engine.mode !== 'live') return;
    engine.autoLife = false;
    engine.timeScale = 0;
    if (directive.goal === 'hold') return;
    const observedClock = observedClockSeconds(directive.logical_time);
    if (observedClock !== null) engine.clock = observedClock;
    const it = pickInteraction(engine, directive.action_id);
    if (!it) return;
    const current = engine.actor.activity || engine.actor.pendingAct;
    if (current && current.name === it.name) return; // already doing/heading there
    engine.dispatch(it.name, { manual: true });
  }

  // main.js builds the engine asynchronously (sprite overrides load first);
  // buffer the newest directive until window.engine exists.
  let pendingDirective = null;
  let engineWaiter = null;

  function onMessage(event) {
    if (event.origin !== window.location.origin) return;
    if (event.source !== window.parent) return;
    const directive = directiveFor(event.data);
    if (!directive) return;
    if (window.engine) { apply(window.engine, directive); return; }
    pendingDirective = directive;
    if (!engineWaiter) {
      engineWaiter = setInterval(() => {
        if (!window.engine) return;
        clearInterval(engineWaiter);
        engineWaiter = null;
        const queued = pendingDirective;
        pendingDirective = null;
        apply(window.engine, queued);
      }, 300);
      if (engineWaiter.unref) engineWaiter.unref();
    }
  }

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('message', onMessage);
  }

  function initializeQueryMode() {
    if (typeof window === 'undefined' || typeof document === 'undefined') return;
    const modes = applyQueryMode(window.location.search, document);
    if (modes.embed) {
      armEmbedMode(window);
      return;
    }
    if (!modes.edit) return;
    const tryEnterEdit = () => enterEditMode(
      window.engine,
      document.getElementById('mode'),
    );
    if (tryEnterEdit()) return;
    editModeWaiter = setInterval(() => {
      if (!tryEnterEdit()) return;
      clearInterval(editModeWaiter);
      editModeWaiter = null;
    }, 50);
    if (editModeWaiter.unref) editModeWaiter.unref();
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', initializeQueryMode);
    } else {
      initializeQueryMode();
    }
  }

  return {
    MESSAGE_TYPE,
    MESSAGE_VERSION,
    directiveFor,
    pickInteraction,
    apply,
    onMessage,
    queryModes,
    applyQueryMode,
    enterEditMode,
    enterEmbedMode,
    armEmbedMode,
  };
})();

if (typeof window !== 'undefined') window.PixelHomeBridge = PixelHomeBridge;
