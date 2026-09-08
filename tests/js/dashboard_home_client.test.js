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
  assert.equal(client.sectionHeading('facts_memory_inner', { label: '事实、记忆与内在' }), '记忆与情绪');
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

  assert.equal(view.kindLabel, '活动与计划');
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
      { label: '活动与计划', text: '窝着刷手机 · 进行中', detail: '', missing: false },
      { label: '人在哪', text: '在宿舍', detail: '', missing: false },
    ],
  );
  assert.equal(lines.find((line) => line.label === '情绪记录').text, '暂无可展示的情绪记录');
  assert.equal(lines.find((line) => line.label === '互动状态').text, '暂无可展示的互动状态');
});

test('a future plan stays planned and cannot turn into current activity or past experience', () => {
  const payload = snapshot({state: 'empty'});
  payload.sections.overview_life = {state: 'ready', data: {highlights: [{
    kind: 'plan', title: '明天下午整理笔记', status_code: 'planned', status_label: '计划中',
    occurred_at: '2026-08-12T20:00:00+08:00',
    values: [{key: 'scheduled_start', label: '计划开始', value: '2026-08-13T14:00:00+08:00'}],
  }]}};
  const original = JSON.stringify(payload);
  const view = client.lifeView(payload);
  assert.equal(view.activities[0].status, '计划中');
  assert.equal(view.activities[0].when, '2026-08-12T20:00:00+08:00');
  assert.equal(view.timeline.length, 0);
  assert.match(client.nowStory(payload)[0].text, /计划中/);
  assert.equal(JSON.stringify(payload), original);
});

test('life timeline uses recorded times, exposes no inferred links and distinguishes unavailable', () => {
  const payload = snapshot({state: 'empty'});
  payload.sections.overview_life = {state: 'ready', data: {highlights: [
    {kind: 'experience', title: '一段经历', occurred_at: '2026-08-12T20:30:00+08:00'},
    {kind: 'world_occurrence', title: '一次世界变化', status_label: '已结算', occurred_at: '2026-08-12T20:00:00+08:00'},
  ]}};
  const view = client.lifeView(payload);
  assert.deepEqual(view.timeline.map(item => item.kind), ['experience', 'world_occurrence']);
  assert.ok(view.timeline.every(item => !('source_ref' in item) && !('caused_by' in item)));
  payload.sections.overview_life.state = 'unavailable';
  assert.deepEqual(client.lifeView(payload), {state: 'unavailable', activities: [], timeline: []});
  assert.equal(client.nowStory(payload)[0].text, '生活状态暂时不可用');
});

test('withheld and private reflection rows never enter the rendered material', () => {
  assert.equal(client.highlightView({kind: 'private_impression', title: 'PRIVATE', detail: 'REFLECTION'}), null);
  assert.equal(client.highlightView({kind: 'plan', privacy_class: 'withhold', title: 'HIDDEN', values: [{key:'x', value:1}]}), null);
  const view = client.highlightView({kind: 'plan', title: '整理笔记', values: [
    {key:'intention', label:'原意图', value:'我想整理完这周的笔记，再决定怎么继续。'},
    {key:'scheduled_end', label:'计划结束', value:'2026-08-13T15:00:00+08:00'},
  ]});
  assert.equal(view.detail, '我想整理完这周的笔记，再决定怎么继续。');
  assert.deepEqual(view.values, [{label:'计划结束', text:'8月13日 15:00'}]);
});

// A small DOM adapter exercises the shipped script's events and poll loop.
// It does not assert CSS layout; that remains a browser visual check.
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag='div') {
    this.tagName=tag;
    this.children=[];
    this.dataset={};
    this.attributes={};
    this.events={};
    this.hidden=false;
    this.value='all';
    this.className='';
    this.ownText='';
    this.classList={toggle:(name,on)=>{
      const values=new Set(this.className.split(' ').filter(Boolean));
      on?values.add(name):values.delete(name);
      this.className=[...values].join(' ');
    }};
  }
  set textContent(value) {this.ownText=String(value);this.children=[];}
  get textContent() {return this.ownText+this.children.map(child=>child.textContent).join('');}
  set innerHTML(_value) {throw new Error('unsafe HTML rendering');}
  append(...children) {this.children.push(...children);}
  appendChild(child) {this.append(child);}
  replaceChildren(...children) {this.ownText='';this.children=[...children];}
  setAttribute(name,value) {this.attributes[name]=value;}
  addEventListener(name,handler) {this.events[name]=handler;}
  fire(name,event={}) {this.events[name]?.({target:this,...event});}
}
function browserWith(responses) {
  const ids=Object.fromEntries(['sectionGrid','captureState','captureNotice','recordingToggle',
    'recordingTools','recordingFocus','headerClock','worldClock','nowStory'].map(id=>[id,new Element()]));
  const now=new Element('section');
  now.dataset.focusArea='now';
  now.append(ids.nowStory);
  const body=new Element('body');
  body.append(now,...Object.entries(ids).filter(([id])=>id!=='nowStory').map(([,value])=>value));
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const document={body,events:{},createElement:tag=>new Element(tag),getElementById:id=>ids[id],
    querySelectorAll:selector=>{
      assert.equal(selector,'[data-focus-area]');
      return descendants(body).filter(node=>node.dataset.focusArea);
    },addEventListener(name,handler){this.events[name]=handler;}};
  const requests=[];
  let poll;
  const window={scrollY:250,scrollTo(_x,y){this.scrollY=y;},fetch:async (url,options)=>{
    requests.push([url,options]);
    const response=responses.shift();
    if(response instanceof Error)throw response;
    assert.ok(response,'unexpected extra poll');
    return response;
  }};
  vm.runInNewContext(fs.readFileSync(process.env.DASHBOARD_APP_JS_PATH,'utf8'),{
    window,document,Intl,Date,console,setInterval(callback,delay){assert.equal(delay,15000);poll=callback;},
  });
  return {ids,body,document,window,requests,poll:()=>poll(),flush:()=>new Promise(resolve=>setImmediate(resolve))};
}
function responseFor(payload) {
  return {ok:true,status:200,headers:{get:()=> '"verified"'},json:async()=>payload};
}

test('recording focus is reversible, read-only and survives poll failures without hiding stale state', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{metrics:[],highlights:[{
    kind:'plan',title:'<img src=x onerror=alert(1)>',status_code:'planned',status_label:'计划中',
  }]}};
  payload.sections.facts_memory_inner={state:'empty',data:{metrics:[{key:'facts',label:'事实',count:0}],highlights:[]}};
  payload.sections.relationship_lifecycle={state:'ready',data:{metrics:[],highlights:[{
    kind:'private_impression',title:'PRIVATE_REFLECTION',detail:'DO_NOT_RENDER',
  }]}};
  payload.world_id='world:DO_NOT_RENDER_ID';
  payload.sections.operations={state:'ready',data:{metrics:[],highlights:[],raw_prompt:'DO_NOT_RENDER_PROMPT'}};
  const original=JSON.stringify(payload);
  const browser=browserWith([responseFor(payload),new Error('PRIVATE_UPSTREAM_ERROR'),
    {ok:false,status:304},responseFor(payload)]);
  await browser.flush();
  assert.equal(browser.ids.captureState.textContent,'已同步');
  assert.match(browser.body.textContent,/<img src=x onerror=alert\(1\)>/);
  assert.doesNotMatch(browser.body.textContent,/PRIVATE_REFLECTION|DO_NOT_RENDER/);
  browser.ids.recordingToggle.fire('click');
  assert.equal(browser.ids.recordingToggle.attributes['aria-pressed'],'true');
  assert.equal(browser.window.scrollY,0);
  browser.ids.recordingFocus.value='life';
  browser.ids.recordingFocus.fire('change');
  assert.ok(browser.document.querySelectorAll('[data-focus-area]').every(
    panel=>panel.hidden===(panel.dataset.focusArea!=='life')));
  await browser.poll();
  assert.equal(browser.ids.captureNotice.hidden,false);
  assert.match(browser.ids.captureNotice.textContent,/上次同步/);
  assert.match(browser.body.textContent,/计划中/);
  assert.doesNotMatch(browser.body.textContent,/PRIVATE_UPSTREAM_ERROR/);
  await browser.poll();
  assert.equal(browser.ids.captureNotice.hidden,true);
  assert.equal(browser.ids.captureState.textContent,'已同步');
  await browser.poll();
  assert.ok(browser.document.querySelectorAll('[data-focus-area]').every(
    panel=>panel.hidden===(panel.dataset.focusArea!=='life')));
  browser.document.events.keydown({key:'Escape'});
  assert.equal(browser.ids.recordingToggle.attributes['aria-pressed'],'false');
  assert.equal(browser.ids.recordingTools.hidden,true);
  assert.equal(browser.window.scrollY,250);
  assert.ok(browser.document.querySelectorAll('[data-focus-area]').every(panel=>!panel.hidden));
  assert.ok(browser.requests.every(([url,options])=>url==='/world-v2/dashboard/home'&&!options.method));
  assert.equal(browser.requests[1][1].headers['If-None-Match'],'"verified"');
  assert.equal(JSON.stringify(payload),original);
});

test('a first failed capture renders unavailable without invented zero metrics or private errors', async () => {
  const browser=browserWith([new Error('http://private.example/SECRET')]);
  await browser.flush();
  assert.equal(browser.ids.captureState.textContent,'暂时连不上');
  assert.equal(browser.ids.captureNotice.hidden,false);
  assert.match(browser.ids.nowStory.textContent,/暂时不可用/);
  assert.match(browser.ids.sectionGrid.textContent,/尚未取得/);
  assert.doesNotMatch(browser.body.textContent,/SECRET|private.example|计数|正常/);
});
