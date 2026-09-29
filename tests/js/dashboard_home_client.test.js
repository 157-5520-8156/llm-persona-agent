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
      signal: requests[0][1].signal,
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

test('unfinished matters preserve typed status and section availability without guessing from time', () => {
  const payload=snapshot({state:'empty'});
  payload.sections.relationship_lifecycle={state:'ready',data:{highlights:[
    {kind:'thread',title:'一次待续的讨论',status_code:'open',status_label:'未结束'},
    {kind:'commitment',title:'承诺',status_code:'completed',status_label:'已完成'},
    {kind:'interaction_bid',title:'一次互动期待',status_code:'expired',status_label:'已过期'},
    {kind:'thread',title:'PRIVATE',privacy_class:'withhold'},
    {kind:'npc',title:'人物'},
  ]}};
  payload.sections.operations={state:'ready',data:{highlights:[
    {kind:'revisit_intention',title:'回头再谈',status_code:'pending',status_label:'待处理'},
    {kind:'response_expectation',title:'一份回应期待',status_code:'open',status_label:'开放',
      occurred_at:'2000-01-01T00:00:00Z'},
    {kind:'action',title:'一般行动'},
  ]}};
  const original=JSON.stringify(payload);
  const groups=client.pendingView(payload);
  assert.deepEqual(groups[0].items.map(item=>[item.kind,item.statusCode,item.status||item.title]),[
    ['thread','open','未结束'],['commitment','completed','已完成'],['interaction_bid','expired','已过期'],
  ]);
  assert.deepEqual(groups[1].items.map(item=>[item.kind,item.statusCode]),[
    ['revisit_intention','pending'],['response_expectation','open'],
  ]);
  assert.equal(groups[1].items[1].kindLabel,'回应期待');
  assert.equal(JSON.stringify(payload),original);
  payload.sections.operations.state='unavailable';
  assert.equal(client.pendingView(payload)[0].items.length,3);
  assert.equal(client.pendingView(payload)[1].state,'unavailable');
  assert.deepEqual(client.pendingView(payload)[1].items,[]);
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
    this.style={};
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
    'recordingTools','recordingFocus','headerClock','worldClock','nowStory',
    'mechanismMap','mechanismDetail','evidenceNode','evidenceKind','evidenceTime','evidenceSearch',
    'evidenceCount','evidenceRecords','evidencePage','previousEvidence','nextEvidence','resetEvidence',
    'evidenceCoverage','runtimeRibbon','domainDetails','pauseDisplay','pauseLabel'].map(id=>[id,new Element()]));
  ids.evidenceSearch.value='';
  const now=new Element('section');
  now.dataset.focusArea='now';
  now.append(ids.nowStory);
  const body=new Element('body');
  body.append(now,...Object.entries(ids).filter(([id])=>id!=='nowStory').map(([,value])=>value));
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const document={body,events:{},createElement:tag=>new Element(tag),getElementById:id=>ids[id],
    createElementNS:(_namespace,tag)=>new Element(tag),
    createTextNode:text=>{const node=new Element('#text');node.textContent=text;return node;},
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
    window,document,Intl,Date,console,AbortController,setTimeout,clearTimeout,setInterval(callback,delay){assert.equal(delay,15000);poll=callback;},
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
  browser.ids.recordingFocus.value='pending';
  browser.ids.recordingFocus.fire('change');
  const pending=browser.document.querySelectorAll('[data-focus-area]').find(
    panel=>panel.dataset.focusArea==='pending');
  assert.ok(pending);
  assert.equal(pending.hidden,false);
  assert.match(pending.textContent,/未完成事项/);
  assert.ok(browser.document.querySelectorAll('[data-focus-area]').every(
    panel=>panel.hidden===(panel.dataset.focusArea!=='pending')));
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
  assert.doesNotMatch(browser.body.textContent,/SECRET|private.example|正常/);
});

test('explorer filters only visible summaries, preserves provenance/status, and respects snapshot time', () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{highlights:[
    {kind:'plan',title:'出门散步',status_code:'planned',status_label:'计划中',occurred_at:'2026-08-12T18:00:00+08:00'},
    {kind:'experience',title:'昨天的散步',occurred_at:'2026-08-10T18:00:00+08:00'},
    {kind:'plan',title:'未来记录',occurred_at:'2026-08-13T18:00:00+08:00'},
    {kind:'plan',title:'没有时间的记录'},
    {kind:'plan',title:'WITHHELD',privacy_class:'withhold'},
  ]}};
  payload.sections.facts_memory_inner={state:'unavailable',data:{highlights:[{kind:'fact',title:'UNAVAILABLE_CONTENT'}]}};
  payload.sections.relationship_lifecycle={state:'ready',data:{highlights:[{kind:'private_impression',title:'PRIVATE_REFLECTION'}]}};
  const original=JSON.stringify(payload),explorer=client.explorer;
  const items=explorer.records(payload,client);
  assert.equal(items.length,4);
  assert.doesNotMatch(JSON.stringify(items),/WITHHELD|UNAVAILABLE_CONTENT|PRIVATE_REFLECTION/);
  const matches=explorer.filterRecords(items,{node:'life',hours:'24',query:'散步'},payload.logical_time);
  assert.equal(matches.length,1);
  assert.equal(matches[0].statusCode,'planned');
  assert.equal(matches[0].sectionId,'overview_life');
  assert.equal(explorer.filterRecords(items,{hours:'undated'},payload.logical_time).length,1);
  assert.equal(explorer.filterRecords(items,{hours:'24'},null).length,0);
  assert.equal(explorer.filterRecords(items,{node:'memory'},payload.logical_time).length,0);
  assert.equal(explorer.nodeState(payload,explorer.nodes.find(n=>n.id==='memory')),'unavailable');
  assert.equal(explorer.nodeState(payload,explorer.nodes.find(n=>n.id==='world')),'degraded');
  assert.equal(JSON.stringify(payload),original);
});

test('pause freezes only the displayed snapshot and resume refetches without world writes', async () => {
  const payload=snapshot({state:'unavailable'});
  const browser=browserWith([responseFor(payload),{ok:false,status:304}]);
  await browser.flush();
  browser.ids.pauseDisplay.fire('click');
  await browser.poll();
  assert.equal(browser.requests.length,1);
  assert.equal(browser.ids.pauseLabel.hidden,false);
  browser.ids.pauseDisplay.fire('click');
  await browser.flush();
  assert.equal(browser.requests.length,2);
  assert.equal(browser.ids.pauseLabel.hidden,true);
  assert.ok(browser.requests.every(([url,options])=>url==='/world-v2/dashboard/home'&&!options.method));
});

test('explorer search and SVG keyboard selection remain read-only across refreshes', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{highlights:[{kind:'plan',title:'散步',status_label:'计划中'}]}};
  const browser=browserWith([responseFor(payload),responseFor(payload)]);
  await browser.flush();
  const svg=browser.ids.mechanismMap.children[0];
  const memory=svg.children.find(n=>n.attributes['data-node']==='memory');
  let prevented=false;
  memory.fire('keydown',{key:'Enter',preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(memory.attributes['aria-pressed'],'true');
  assert.match(browser.ids.mechanismDetail.textContent,/事实与记忆/);
  browser.ids.evidenceSearch.value='不匹配';browser.ids.evidenceSearch.fire('input');
  assert.match(browser.ids.evidenceRecords.textContent,/没有匹配/);
  await browser.poll();
  assert.equal(memory.attributes['aria-pressed'],'true');
  assert.equal(browser.ids.evidenceSearch.value,'不匹配');
  browser.ids.resetEvidence.fire('click');
  assert.match(browser.ids.evidenceRecords.textContent,/散步/);
});

test('failure and rejected-change summaries retain safe labels without leaking raw diagnostics', () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.operations={state:'ready',data:{notices:[{
    label:'发送失败',reason_label:'通道暂不可用',occurred_at:'2026-08-12T20:00:00+08:00',
    raw_error:'SECRET_DIAGNOSTIC',reason_code:'INTERNAL_REASON',
  }]}};
  payload.sections.relationship_lifecycle={state:'ready',data:{typed_change_terminals:[{
    target_stage_label:'朋友',status:'rejected',status_label:'被拒绝',occurred_at:'2026-08-12T20:00:00+08:00',
    commitment_code:'PRIVATE_CODE',cursor:{world_revision:99},
  }]}};
  const items=client.explorer.records(payload,client);
  assert.equal(items.length,2);
  assert.match(JSON.stringify(items),/发送失败|通道暂不可用/);
  assert.match(items.find(i=>i.kind==='relationship_terminal').detail,/未生效/);
  assert.doesNotMatch(JSON.stringify(items),/SECRET_DIAGNOSTIC|PRIVATE_CODE|INTERNAL_REASON|world_revision/);
});

test('open record survives snapshot updates and an in-flight fetch cannot replace a paused display', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{highlights:[{kind:'plan',title:'散步',status_label:'计划中'}]}};
  let finish;
  const delayed=new Promise(resolve=>{finish=resolve;});
  const browser=browserWith([responseFor(payload),responseFor(payload),delayed]);
  await browser.flush();
  const row=browser.ids.evidenceRecords.children[0];row.open=true;row.fire('toggle');
  await browser.poll();
  assert.equal(browser.ids.evidenceRecords.children[0].open,true);
  const inFlight=browser.poll();browser.ids.pauseDisplay.fire('click');
  const newer=JSON.parse(JSON.stringify(payload));newer.sections.overview_life.data.highlights[0].title='新计划';
  finish(responseFor(newer));await inFlight;
  assert.match(browser.ids.evidenceRecords.textContent,/散步/);
  assert.doesNotMatch(browser.ids.evidenceRecords.textContent,/新计划/);
});

test('stalled snapshot fetch aborts and does not invent an empty snapshot', async () => {
  let signal;
  await assert.rejects(client.capture((_url,options)=>{
    signal=options.signal;return new Promise(()=>{});
  },null,{timeoutMs:1}),/timed out/);
  assert.equal(signal.aborted,true);
});

test('record body distinguishes withheld or unread fields from structured records', () => {
  assert.match(client.explorer.recordBody({detail:'',values:[{label:'正文状态',text:'尚未送达'}]}),/尚未送达/);
  assert.match(client.explorer.recordBody({detail:'',values:[{label:'终态',text:'是'}]}),/结构化字段/);
  assert.equal(client.explorer.recordBody({detail:'已读取的正文',values:[]}), '已读取的正文');
});

test('fulfilled and expired expectations stay in history but leave unfinished items', () => {
  const payload={sections:{operations:{state:'ready',data:{highlights:[
    {kind:'response_expectation',kind_label:'回应期待',title:'旧等待',status_code:'expired'},
    {kind:'response_expectation',kind_label:'回应期待',title:'已收到',status_code:'fulfilled'},
    {kind:'response_expectation',kind_label:'回应期待',title:'仍待评估',status_code:'open'},
  ]}}}};
  assert.deepEqual(client.pendingView(payload)[1].items.map(item=>item.title),['仍待评估']);
  assert.equal(client.visibleHighlights(payload,'operations',['response_expectation']).length,3);
});
