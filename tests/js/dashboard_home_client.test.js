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
    this.open=false;
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
  querySelector(selector) {
    const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
    return descendants(this).find(node=>selector.startsWith('.')&&node.className.split(' ').includes(selector.slice(1)))||null;
  }
  querySelectorAll(selector) {
    const descendants=node=>node.children.flatMap(child=>[child,...descendants(child)]);
    if(selector==='button[data-view]')return descendants(this).filter(node=>node.tagName==='button'&&node.dataset.view);
    throw new Error('unsupported test selector '+selector);
  }
  focus() {}
  showModal() {this.open=true;}
  close() {this.open=false;}
  prepend(...children) {this.children.unshift(...children);}
  append(...children) {this.children.push(...children);}
  appendChild(child) {this.append(child);}
  replaceChildren(...children) {this.ownText='';this.children=[...children];}
  setAttribute(name,value) {this.attributes[name]=value;}
  addEventListener(name,handler) {this.events[name]=handler;}
  fire(name,event={}) {this.events[name]?.({target:this,...event});}
}
function browserWith(responses,{costData=null}={}) {
  const ids=Object.fromEntries(['agendaPanel','storyPanel','detailDrawer','drawerTitle','drawerBody','closeDrawer','viewTitle','viewDescription','viewIndex','sectionGrid','captureState','captureNotice','recordingToggle',
    'recordingTools','recordingFocus','headerClock','worldClock','nowStory',
    'mechanismMap','mechanismDetail','evidenceNode','evidenceKind','evidenceTime','evidenceSearch',
    'evidenceCount','evidenceRecords','evidencePage','previousEvidence','nextEvidence','resetEvidence',
    'evidenceCoverage','runtimeRibbon','domainDetails','pauseDisplay','pauseLabel'].map(id=>[id,new Element()]));
  if(costData){ids.costMeterPanel=new Element();ids.costMeterDetails=new Element();}
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
    if(costData&&url==='/world-v2/dashboard/cost')return responseFor(costData);
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
  assert.ok(browser.document.querySelectorAll('[data-focus-area]').every(panel=>panel.hidden===false));
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

test('week calendar clips midnight and lays out overlaps without interpreting candidates as events', () => {
  const entries = [
    {index:0,kind:'plan',title:'夜间整理',starts_at:'2026-10-12T23:30:00+08:00',ends_at:'2026-10-13T01:00:00+08:00',content_state:'read'},
    {index:1,kind:'plan',title:'另一安排',starts_at:'2026-10-13T00:30:00+08:00',ends_at:'2026-10-13T02:00:00+08:00',content_state:'read'},
    {index:2,kind:'outlook',title:'可能来信',starts_at:'2026-10-12T10:00:00+08:00',ends_at:'2026-10-14T10:00:00+08:00',content_state:'read'},
  ];
  const state={sections:{overview_life:{state:'ready',data:{agenda:{state:'ready',week_start:'2026-10-12',entries}}}}};
  const week=client.agendaView(state);
  assert.equal(week.days.length,7);
  assert.equal(week.days[0].timed[0].minute,1410);
  assert.equal(week.days[0].timed[0].endMinute,1440);
  assert.equal(week.days[1].timed.length,2);
  assert.deepEqual(week.days[1].timed.map(s=>s.lanes),[2,2]);
  assert.notEqual(week.days[1].timed[0].lane,week.days[1].timed[1].lane);
  assert.equal(week.days[0].windows.length,1);
  assert.equal(client.agendaView(state,'world').shown.length,0);
  assert.equal(client.shiftWeek('2026-12-28',7),'2027-01-04');
});

test('week capture forwards only the selected date on the existing route', async () => {
  let url;
  await client.capture(async value=>{url=value;return {ok:true,status:304};},'old',{week:'2026-10-12'});
  assert.equal(url,'/world-v2/dashboard/home?week=2026-10-12');
  await client.capture(async value=>{url=value;return {ok:true,status:304};},'old',{week:'2026-10-12',page:0});
  assert.equal(url,'/world-v2/dashboard/home?week=2026-10-12&page=0');
});

test('pagination controls remain usable after snapshot loading completes', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{metrics:[],highlights:[],agenda:{
    state:'ready',outlook_state:'disabled',week_start:'2026-10-12',entries:[],
    known_count:192,withheld_count:0,truncated:true,page:1,page_count:2,
  }}};
  const previous=JSON.parse(JSON.stringify(payload));previous.sections.overview_life.data.agenda.page=0;
  const browser=browserWith([responseFor(payload),responseFor(previous)]);
  await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const buttons=descendants(browser.ids.agendaPanel).filter(node=>node.tagName==='button');
  const prev=buttons.find(node=>node.textContent==='上一页');
  assert.equal(prev.disabled,false);
  assert.equal(buttons.find(node=>node.textContent==='下一页').disabled,true);
  prev.fire('click');await browser.flush();
  assert.equal(browser.requests[1][0],'/world-v2/dashboard/home?page=0');
  assert.match(browser.ids.agendaPanel.textContent,/第 1 \/ 2 页/);
});


test('same-title overlapping entries retain distinct detail selections', () => {
  const entry={kind:'plan',title:'同一个标题',starts_at:'2026-10-14T10:00:00+08:00',ends_at:'2026-10-14T11:00:00+08:00'};
  assert.notEqual(client.agendaKey({...entry,index:0,detail:'第一项的细节'}),client.agendaKey({...entry,index:1,detail:'第二项的细节'}));
  assert.notEqual(client.agendaKey({...entry,index:0,detail:'相同正文'}),client.agendaKey({...entry,index:1,detail:'相同正文'}));
});


test('shipped calendar navigation and selection use the readonly date route and exact entry', async () => {
  const payload=snapshot({state:'unavailable'});
  const base={kind:'plan',title:'相同标题',starts_at:'2026-10-12T10:00:00+08:00',ends_at:'2026-10-12T11:00:00+08:00',content_state:'read',related:[]};
  payload.sections.overview_life={state:'ready',data:{metrics:[],highlights:[],agenda:{
    state:'ready',outlook_state:'disabled',week_start:'2026-10-12',entries:[
      {...base,index:0,status:'已计划',detail:'第一份详情'},
      {...base,index:1,status:'已结束',detail:'第二份详情'},
    ],known_count:2,withheld_count:0,truncated:false,
  }}};
  const next=JSON.parse(JSON.stringify(payload));next.sections.overview_life.data.agenda.week_start='2026-10-19';
  const browser=browserWith([responseFor(payload),responseFor(next)]);
  await browser.flush();
  assert.equal(browser.ids.captureState.textContent,'已同步');
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  descendants(browser.ids.agendaPanel).find(node=>node.dataset.weekCell==='2026-10-12-10').fire('click');
  const second=descendants(browser.ids.drawerBody).find(node=>node.dataset.agendaIndex==='1');
  second.fire('click');
  assert.match(browser.ids.drawerBody.textContent,/第二份详情/);
  assert.doesNotMatch(browser.ids.drawerBody.textContent,/第一份详情/);
  browser.ids.closeDrawer.fire('click');
  const selected=client.agendaView(payload,'plan','已结束');
  assert.deepEqual(selected.shown.map(entry=>entry.index),[1]);
  descendants(browser.ids.agendaPanel).find(node=>node.tagName==='button'&&node.textContent==='下一周').fire('click');
  await browser.flush();
  assert.equal(browser.requests[1][0],'/world-v2/dashboard/home?week=2026-10-19');
  assert.match(browser.ids.agendaPanel.textContent,/2026-10-19/);
});

test('story hierarchy, recorded relationship radar and non-overlapping photo buttons render interactively', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{metrics:[],highlights:[],agenda:{
    state:'ready',outlook_state:'ready',week_start:'2026-10-12',entries:[],
    known_count:0,withheld_count:0,truncated:false,
    worldline:{
      review_at:'2026-10-19T12:00:00+08:00',
      horizon_end:'2026-11-15T12:00:00+08:00',
      stages:[
        {title:'秋日新篇',situation:'适应新学期节奏',starts_on:'2026-09-01',ends_on:'2026-10-01',status:'已走过'},
        {title:'期中深耕',situation:'准备课程汇报与微格试讲',starts_on:'2026-10-02',ends_on:'2026-11-01',status:'当前阶段'},
      ],
      threads:[
        {
          title:'现当代文学汇报线',
          status:'进行中',
          situation:'正在图书馆核对张爱玲选段批注',
          open_question:'汇报时能否把细腻的文本观察讲出彩？',
          developments:['确定选段与大纲','在阅览室补齐两处意象对照'],
        },
      ],
      opportunities:[
        {
          title:'试讲排练邀约',
          detail:'室友陈诺提议晚上一起去空教室过一遍PPT',
          lane_label:'学业推进',
          status:'已就绪',
          thread_titles:['现当代文学汇报线'],
          opens_at:'2026-10-13T18:00:00+08:00',
          closes_at:'2026-10-13T21:00:00+08:00',
        },
      ],
      convergences:[],
    },
  }}};
  payload.sections.relationship_lifecycle={state:'ready',data:{metrics:[],highlights:[{
    kind:'relationship_state',kind_label:'关系状态',title:'朋友',status_code:'friend',status_label:'朋友',
    values:[
      {key:'trust_bp',label:'信任',value:7800},
      {key:'closeness_bp',label:'亲近',value:7400},
      {key:'respect_bp',label:'尊重',value:8200},
      {key:'reliability_bp',label:'可靠',value:8000},
      {key:'mutuality_bp',label:'相互',value:7600},
      {key:'repair_confidence_bp',label:'修复信心',value:7200},
    ],
  }]}};
  payload.sections.perception_media={state:'ready',data:{metrics:[],highlights:[{
    kind:'media_delivery',kind_label:'媒体投递',title:'日常分享随手拍',status_code:'delivered',status_label:'已送达',
    detail:'在宿舍里挑选并发送一张下午在书店拍的角落照片。',
    occurred_at:'2026-10-12T17:30:00+08:00',
    values:[{key:'image_url',label:'照片画面',value:'/world-v2/dashboard/media/media-preview:test/image'}],
  }]}};
  const browser=browserWith([responseFor(payload)]);
  await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  assert.match(browser.ids.storyPanel.textContent,/故事地图/);
  assert.match(browser.ids.storyPanel.textContent,/汇报时能否把细腻的文本观察讲出彩/);
  const stepNode=descendants(browser.ids.storyPanel).find(n=>n.dataset.storyPick==='note-0');
  assert.ok(stepNode);
  stepNode.fire('click');
  assert.match(browser.ids.drawerBody.textContent,/确定选段与大纲/);
  assert.equal(browser.ids.detailDrawer.open,true);
  const radarSvg=descendants(browser.ids.sectionGrid).find(n=>n.tagName==='svg');
  assert.ok(radarSvg);
  assert.match(browser.ids.sectionGrid.textContent,/你们之间/);
  const polaroid=descendants(browser.ids.sectionGrid).find(n=>n.className==='polaroid-card');
  assert.ok(polaroid);
  assert.match(polaroid.textContent,/书店拍的角落照片/);
});

test('single page keeps all sections visible and restores collapsed panels after recording', async () => {
  const payload=snapshot({state:'unavailable'});
  const browser=browserWith([responseFor(payload),responseFor(payload)]);await browser.flush();
  assert.equal(browser.body.dataset.view,'single');
  assert.equal(browser.ids.agendaPanel.hidden,false);
  assert.equal(browser.ids.storyPanel.hidden,false);
  const panels=browser.document.querySelectorAll('[data-focus-area]');
  assert.ok(panels.every(p=>!p.hidden));
  const relationship=panels.find(p=>p.dataset.focusArea==='relationships');
  assert.equal(relationship.open,false);
  browser.ids.recordingToggle.fire('click');
  browser.ids.recordingFocus.value='life';browser.ids.recordingFocus.fire('change');
  assert.equal(browser.ids.storyPanel.hidden,true);
  browser.ids.recordingToggle.fire('click');
  assert.equal(relationship.open,false);
  await browser.poll();
  assert.equal(browser.ids.agendaPanel.hidden,false);
  assert.equal(browser.ids.storyPanel.hidden,false);
  assert.ok(browser.requests.every(([url,options])=>url==='/world-v2/dashboard/home'&&!options.method));
});

test('missing affect strength and relationship dimensions never receive invented gauge values', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.facts_memory_inner={state:'ready',data:{highlights:[{kind:'affect_episode',title:'平静',status_label:'活跃'}]}};
  payload.sections.relationship_lifecycle={state:'ready',data:{highlights:[{kind:'relationship_state',title:'和你',status_label:'朋友',values:[{key:'trust_bp',label:'信任',value:1750}]}]}};
  const browser=browserWith([responseFor(payload)]);await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const nodes=descendants(browser.ids.sectionGrid);
  assert.equal(nodes.filter(n=>n.className==='mood-bar-fill').length,0);
  assert.equal(nodes.filter(n=>n.className==='radar-svg').length,0);
  assert.match(browser.ids.sectionGrid.textContent,/信任18%/);
  assert.doesNotMatch(browser.ids.sectionGrid.textContent,/50%/);
});

test('week retains seven days and opens exact details only on click', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{highlights:[],agenda:{state:'ready',week_start:'2026-08-10',entries:[
    {index:1,kind:'experience',title:'周一的经历',detail:'周一正文',status:'已发生',time_kind:'timed',starts_at:'2026-08-10T10:00:00+08:00',ends_at:'2026-08-10T11:00:00+08:00'},
    {index:2,kind:'experience',title:'周三的经历',detail:'周三正文',status:'已发生',time_kind:'timed',starts_at:'2026-08-12T10:00:00+08:00',ends_at:'2026-08-12T11:00:00+08:00'},
  ]}}};
  const browser=browserWith([responseFor(payload),responseFor(payload)]);await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const nodes=descendants(browser.ids.agendaPanel);
  assert.equal(nodes.filter(n=>n.className==='week-day-heading').length,7);
  assert.match(browser.ids.agendaPanel.textContent,/周一的经历/);
  assert.match(browser.ids.agendaPanel.textContent,/周三的经历/);
  assert.doesNotMatch(browser.ids.agendaPanel.textContent,/周一正文/);
  nodes.find(n=>n.dataset.weekCell==='2026-08-10-10').fire('click');
  assert.equal(browser.ids.detailDrawer.open,true);
  assert.match(browser.ids.drawerBody.textContent,/周一正文/);
  await browser.poll();
  assert.match(browser.ids.drawerBody.textContent,/周一正文/);
  assert.match(browser.ids.agendaPanel.textContent,/周三的经历/);
  browser.ids.closeDrawer.fire('click');assert.equal(browser.ids.detailDrawer.open,false);
});

test('a paged home snapshot renders immediately without merging other cursor snapshots', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{highlights:[],agenda:{state:'ready',week_start:'2026-08-10',page:7,page_count:8,truncated:true,known_count:755,entries:[
    {index:2,kind:'experience',title:'本页记录',detail:'正文',status:'已发生',time_kind:'point',starts_at:'2026-08-12T10:00:00+08:00',ends_at:'2026-08-12T11:00:00+08:00'},
  ]}}};
  const before=JSON.stringify(payload);
  const browser=browserWith([responseFor(payload)]);await browser.flush();
  assert.equal(browser.ids.captureState.textContent,'已同步');
  assert.equal(browser.requests.length,1);
  assert.match(browser.ids.agendaPanel.textContent,/当前只显示其中 1 条/);
  assert.match(browser.ids.agendaPanel.textContent,/本页记录/);
  assert.match(browser.ids.agendaPanel.textContent,/未载入/);
  assert.equal(JSON.stringify(payload),before);
});

test('present view excludes terminal expectations but keeps their records available', () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.operations={state:'ready',data:{highlights:[
    {kind:'response_expectation',title:'已经失效的期待',status_code:'expired',occurred_at:'2026-08-12T21:00:00+08:00'},
    {kind:'response_expectation',title:'仍然开放的期待',status_code:'open',occurred_at:'2026-08-12T20:00:00+08:00'},
  ]}};
  assert.equal(client.nowStory(payload)[5].text,'仍然开放的期待');
  assert.equal(client.visibleHighlights(payload,'operations').length,2);
});

test('structured prose reads only an explicitly reported utterance and preserves its boundary', () => {
  const value=JSON.stringify({kind:'npc_statement',claim_authority:'speaker_report_only',speaker_ref:'npc:test',utterance:'<script>untrusted</script>她说还没有决定。'});
  assert.deepEqual(client.readableText(value),{text:'<script>untrusted</script>她说还没有决定。',label:'人物说法 · 不代表已核实事实',structured:true});
  assert.deepEqual(client.readableText('{"private_notes":"not narrative"}'),{text:'',label:'结构化记录',structured:true});
  assert.equal(client.readableText('原有的叙述。').text,'原有的叙述。');
});

test('agenda nests explicit relationships without inventing parentage from overlapping times', async () => {
  const payload=snapshot({state:'unavailable'});
  const common={starts_at:'2026-08-12T10:00:00+08:00',ends_at:'2026-08-12T11:00:00+08:00',time_kind:'point',status:'已发生',content_state:'read'};
  payload.sections.overview_life={state:'ready',data:{highlights:[],agenda:{state:'ready',week_start:'2026-08-10',entries:[
    {...common,index:1,kind:'planning',title:'已公布课程',related:[]},
    {...common,index:2,kind:'experience',title:'明确关联的经历',related:[1]},
    {...common,index:3,kind:'experience',title:'只是同时发生',related:[]},
  ]}}};
  const browser=browserWith([responseFor(payload)]);await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  descendants(browser.ids.agendaPanel).find(n=>n.dataset.weekCell==='2026-08-12-10').fire('click');
  assert.match(browser.ids.drawerBody.textContent,/只是同时发生/);
  descendants(browser.ids.drawerBody).find(n=>n.dataset.agendaIndex==='2').fire('click');
  const links=descendants(browser.ids.drawerBody).filter(n=>n.className==='agenda-related');
  assert.equal(links.length,1);
  assert.match(links[0].textContent,/已公布课程/);
  assert.doesNotMatch(browser.ids.drawerBody.textContent,/只是同时发生/);
});


test('cost chart uses recorded amounts and retains selected day after refresh', async () => {
  const payload=snapshot({state:'unavailable'});
  const costData={summary:{today_total_cny:3,days_7_total_cny:5},daily_series:[
    {date:'2026-08-11',total_cny:2,background_cny:1.5,interactive_cny:.5,calls:4,total_tokens:500},
    {date:'2026-08-12',total_cny:3,background_cny:3,interactive_cny:0,calls:6,total_tokens:900},
  ],by_purpose:[{category:'后台生活',label:'生活选择',days_7_cny:5,days_7_calls:10,today_calls:6}],by_model:[],recent_records:[]};
  const browser=browserWith([responseFor(payload),responseFor(payload)],{costData});await browser.flush();
  browser.ids.costMeterDetails.open=true;browser.ids.costMeterDetails.fire('toggle');await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  let nodes=descendants(browser.ids.costMeterPanel);
  assert.match(nodes.find(n=>n.className==='cost-day-detail').textContent,/2026-08-12 · ¥3.00/);
  const chartDay=nodes.find(n=>n.attributes['aria-label']==='2026-08-11 成本 ¥2.00');
  chartDay.fire('keydown',{key:'Enter',preventDefault(){}});
  assert.match(nodes.find(n=>n.className==='cost-day-detail').textContent,/2026-08-11 · ¥2.00/);
  await browser.poll();await browser.flush();
  nodes=descendants(browser.ids.costMeterPanel);
  assert.match(nodes.find(n=>n.className==='cost-day-detail').textContent,/2026-08-11 · ¥2.00/);
  assert.equal(nodes.find(n=>n.className==='cost-date-select').value,'2026-08-11');
  assert.match(nodes.find(n=>n.className==='cost-legend').textContent,/后台生活¥5.00/);
});

test('empty cost period avoids an empty trend plot and folds inactive modules', async () => {
  const payload=snapshot({state:'unavailable'});
  const costData={summary:{},daily_series:[{date:'2026-08-12',total_cny:0}],by_purpose:[
    {category:'后台生活',label:'旧模块',days_7_cny:0,days_7_calls:0,today_cny:0,today_calls:0},
  ],by_model:[],recent_records:[]};
  const browser=browserWith([responseFor(payload)],{costData});await browser.flush();
  browser.ids.costMeterDetails.open=true;browser.ids.costMeterDetails.fire('toggle');await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const nodes=descendants(browser.ids.costMeterPanel);
  assert.equal(nodes.filter(n=>n.className==='cost-trend-svg').length,0);
  const archived=nodes.find(n=>n.className==='cost-inactive');
  assert.equal(archived.open,false);
  assert.match(archived.textContent,/旧模块/);
});

test('period hierarchy follows recorded publication links and leaves unrelated candidates separate', async () => {
  const payload=snapshot({state:'unavailable'});
  const period={time_kind:'period',starts_at:'2026-08-01T00:00:00+08:00',ends_at:'2026-09-01T00:00:00+08:00',status:'已公布',content_state:'read'};
  payload.sections.overview_life={state:'ready',data:{highlights:[],agenda:{state:'ready',week_start:'2026-08-10',entries:[
    {...period,index:1,kind:'planning',title:'学期规划',related:[]},
    {...period,index:2,kind:'planning',title:'已公布的阶段',related:[1]},
    {...period,index:3,kind:'outlook',title:'独立候选',related:[]},
  ]}}};
  const browser=browserWith([responseFor(payload)]);await browser.flush();
  const descendants=node=>[node,...node.children.flatMap(descendants)];
  const nodes=descendants(browser.ids.agendaPanel);
  nodes.find(n=>n.tagName==='button'&&n.textContent.startsWith('阶段背景与候选窗口')).fire('click');
  assert.match(browser.ids.drawerBody.textContent,/独立候选/);
  descendants(browser.ids.drawerBody).find(n=>n.dataset.agendaIndex==='2').fire('click');
  const links=descendants(browser.ids.drawerBody).filter(n=>n.className==='agenda-related');
  assert.equal(links.length,1);assert.match(links[0].textContent,/学期规划/);
  assert.doesNotMatch(browser.ids.drawerBody.textContent,/独立候选/);
});

test('whole-week metadata separates unloaded records from empty cells without fetching every page', () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{agenda:{state:'ready',week_start:'2026-08-10',truncated:true,entries:[],week_slots:[
    {date:'2026-08-10',hour:8,known_count:15,kinds:{planning:1,experience:14},labels:['已公布课程'],first_page:0,last_page:1},
  ]}}};
  const week=client.weekCells(payload);
  assert.equal(week.days.length,7);
  assert.equal(week.days[0].cells[4].state,'unloaded');
  assert.equal(week.days[0].cells[4].known,15);
  assert.equal(week.days[0].cells[4].labels[0],'已公布课程');
  assert.equal(week.days[1].cells[4].state,'empty');
  assert.equal(client.weekCells(payload,'planning').days[0].cells[4].known,1);
  assert.deepEqual(client.weekCells(payload,'plan').days[0].cells[4].labels,[]);
  delete payload.sections.overview_life.data.agenda.week_slots;
  assert.equal(client.weekCells(payload).days[1].cells[4].state,'unknown');
});

test('story map draws only explicit prerequisite branches and merges, never summary order', () => {
  const worldline={threads:[{title:'主线',situation:'当前概括',developments:['摘要甲','摘要乙'],open_question:'尚未确定'}],opportunities:[
    {index:0,thread_indexes:[0],title:'起点',after_indexes:[]},
    {index:1,thread_indexes:[0],title:'准备甲',after_indexes:[0]},
    {index:2,thread_indexes:[0],title:'准备乙',after_indexes:[0]},
    {index:3,thread_indexes:[0],title:'汇合',after_indexes:[1,2]},
  ]};
  const graph=client.storyGraph(worldline,0);
  assert.deepEqual(graph.edges.filter(e=>e.kind==='prerequisite').map(e=>[e.from,e.to]),[
    ['opp-0','opp-1'],['opp-0','opp-2'],['opp-1','opp-3'],['opp-2','opp-3'],
  ]);
  assert.equal(graph.edges.some(e=>e.from==='note-0'&&e.to==='note-1'),false);
  assert.ok(graph.nodes.find(n=>n.id==='opp-3').x>graph.nodes.find(n=>n.id==='opp-1').x);
  const legacy=client.storyGraph({threads:worldline.threads,opportunities:[{title:'未提供前置关系',thread_titles:['主线']}]});
  assert.equal(legacy.edges.filter(e=>e.kind==='prerequisite').length,0);
  const cyclic=client.storyGraph({threads:worldline.threads,opportunities:[
    {index:0,thread_indexes:[0],after_indexes:[1]}, {index:1,thread_indexes:[0],after_indexes:[0]},
  ]});
  assert.equal(cyclic.nodes.filter(n=>n.id.startsWith('opp-')).length,2);
});

test('story thread indices disambiguate duplicate titles and retain explicit cross-thread parents', () => {
  const graph=client.storyGraph({threads:[{title:'相同标题'},{title:'相同标题'}],opportunities:[
    {index:0,thread_indexes:[1],title:'另一条线的前置',after_indexes:[]},
    {index:1,thread_indexes:[0],title:'本线节点',after_indexes:[0]},
    {index:2,thread_indexes:[1],title:'无关节点',after_indexes:[]},
  ]},0);
  assert.ok(graph.nodes.find(n=>n.id==='opp-0'));
  assert.ok(graph.nodes.find(n=>n.id==='opp-1'));
  assert.equal(graph.nodes.find(n=>n.id==='opp-2'),undefined);
});

test('story viewport retains pan position while the authorized snapshot refreshes', async () => {
  const payload=snapshot({state:'unavailable'});
  payload.sections.overview_life={state:'ready',data:{highlights:[],agenda:{state:'ready',week_start:'2026-08-10',entries:[],worldline:{threads:[{title:'主线',developments:[]}],opportunities:[{index:0,thread_indexes:[0],after_indexes:[],title:'候选'}]}}}};
  const browser=browserWith([responseFor(payload),responseFor(payload)]);await browser.flush();
  const viewport=browser.ids.storyPanel.querySelector('.story-map-viewport');
  viewport.scrollLeft=210;viewport.scrollTop=90;
  await browser.poll();
  const refreshed=browser.ids.storyPanel.querySelector('.story-map-viewport');
  assert.equal(refreshed.scrollLeft,210);
  assert.equal(refreshed.scrollTop,90);
});
