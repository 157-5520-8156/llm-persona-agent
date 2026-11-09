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

test('same-title overlapping entries retain distinct detail selections', () => {
  const entry={kind:'plan',title:'同一个标题',starts_at:'2026-10-14T10:00:00+08:00',ends_at:'2026-10-14T11:00:00+08:00'};
  assert.notEqual(client.agendaKey({...entry,index:0,detail:'第一项的细节'}),client.agendaKey({...entry,index:1,detail:'第二项的细节'}));
  assert.notEqual(client.agendaKey({...entry,index:0,detail:'相同正文'}),client.agendaKey({...entry,index:1,detail:'相同正文'}));
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
