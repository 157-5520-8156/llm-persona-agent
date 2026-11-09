const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

const client = require(process.env.DASHBOARD_APP_JS_PATH);
const stage = client.stage;

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------
const NOW = '2026-11-03T11:16:00+08:00';
function snapshotFixture(overrides = {}) {
  const base = {
    schema_version: 'world-v2-dashboard-home.1',
    snapshot_hash: 'b'.repeat(64),
    generated_at: '2026-11-03T03:19:05Z',
    logical_time: NOW,
    cursor: { world_revision: 41885, deliberation_revision: 46269, ledger_sequence: 88154 },
    sections: {
      room: { state: 'unavailable', render_state: { route: { scene_id: 'unavailable', action_id: 'idle', availability: 'unavailable' } } },
      overview_life: {
        state: 'ready',
        data: {
          metrics: [{ key: 'experiences', label: '经历', count: 575 }],
          highlights: [
            { kind: 'plan', title: '旧计划', detail: '旧计划', status_code: 'abandoned', status_label: '已放弃', occurred_at: '2026-11-03T03:00:00Z', values: [] },
            { kind: 'plan', title: '去上古代汉语', detail: '出门去文科楼上十点的古代汉语', status_code: 'active', status_label: '进行中', occurred_at: '2026-11-03T01:55:02Z',
              values: [{ key: 'scheduled_start', label: '计划开始', value: '2026-11-03T02:00:00+00:00' }, { key: 'scheduled_end', label: '计划结束', value: '2026-11-03T03:30:00+00:00' },
                { key: 'intent_source', label: '意图来源', value: 'day_open', value_label: '当日自主安排' }] },
            { kind: 'experience', title: '最后只买到一个糖包', detail: '肉包屉空了', status_code: 'committed', status_label: '已记下', occurred_at: '2026-11-03T00:45:03Z', values: [] },
            { kind: 'world_occurrence', title: '对读练习收束', detail: '教师回到讲台串讲要点', status_code: 'settled', status_label: '已结算', occurred_at: '2026-11-03T03:01:00Z', values: [] },
            { kind: 'biographical_coordinate', title: '生平坐标', detail: '父母决定下学期起生活费减半。', occurred_at: '2026-10-27T22:15:00+08:00', values: [] },
            { kind: 'life_arc', title: 'dynamic', detail: '', occurred_at: '2026-10-26T07:40:00Z', values: [] },
          ],
          agenda: {
            state: 'ready', week_start: '2026-11-02', timezone: 'Asia/Shanghai', page: 0, page_count: 2, truncated: true,
            week_slots: [{ date: '2026-11-04', hour: 20, known_count: 3, kinds: { plan: 3 }, labels: [], first_page: 1, last_page: 1 }],
            entries: [
              { index: 0, kind: 'planning', time_kind: 'timed', title: '周二古代汉语', status: '已公布', starts_at: '2026-11-03T10:00:00+08:00', ends_at: '2026-11-03T11:30:00+08:00', detail: '课表', content_state: 'read', related: [] },
              { index: 1, kind: 'experience', time_kind: 'timed', title: '撑到点，上课去', status: '已发生', starts_at: '2026-11-03T09:25:00+08:00', ends_at: '2026-11-03T10:05:00+08:00', detail: '复习了一遍文选', content_state: 'read', related: [2] },
              { index: 2, kind: 'world', time_kind: 'timed', title: '被经历覆盖的世界事件', status: '已结算', starts_at: '2026-11-03T10:05:00+08:00', ends_at: '2026-11-03T10:05:00+08:00', detail: '', content_state: 'read', related: [] },
              { index: 3, kind: 'world', time_kind: 'timed', title: '窗外开始下雨', status: '已结算', starts_at: '2026-11-02T23:51:00+08:00', ends_at: '2026-11-02T23:51:00+08:00', detail: '零星雨点', content_state: 'read', related: [] },
              { index: 4, kind: 'plan', time_kind: 'timed', title: '跨夜整理', status: '已完成', starts_at: '2026-11-02T23:30:00+08:00', ends_at: '2026-11-03T01:00:00+08:00', detail: '跨夜整理', content_state: 'read', related: [] },
              { index: 5, kind: 'expectation', time_kind: 'window', title: '他回一句他那边怎么样', status: '进行中', starts_at: '2026-11-03T12:30:00+08:00', ends_at: '2026-11-03T15:00:00+08:00', detail: '回应期待', content_state: 'read', related: [] },
              { index: 6, kind: 'outlook', time_kind: 'period', title: '十一月前半程收尾', status: '候选机会 · 未定', starts_at: '2026-11-01T00:00:00Z', ends_at: '2026-11-30T00:00:00Z', detail: '阶段说明', content_state: 'read', related: [] },
              { index: 7, kind: 'plan', time_kind: 'timed', title: '保留的私密计划', status: '已完成', starts_at: '2026-11-03T08:00:00+08:00', ends_at: '2026-11-03T09:00:00+08:00', detail: 'secret', content_state: 'withheld', related: [] },
            ],
            worldline: {
              review_at: '2026-11-08T09:00:00+08:00',
              horizon_end: '2026-12-02T11:35:00Z',
              threads: [
                { title: '社长选举', status: '展开中', situation: '名单已落定', open_question: '她会怎么表态？', developments: ['名单晒出', '答辩临近'] },
                { title: '评优材料', status: '展开中', situation: '整包待交', open_question: '', developments: [] },
              ],
              stages: [
                { title: '十一月前半程收尾', starts_on: '2026-11-01', ends_on: '2026-11-29', situation: '几条钱路挤在同一窗口', status: '当前章节' },
                { title: '期末与考试季', starts_on: '2026-11-30', ends_on: '2027-01-24', situation: '复习', status: '远期章节' },
              ],
              opportunities: [
                { index: 0, after_indexes: [], thread_indexes: [0], title: '答辩前夜的图书馆', detail: '两拨候选人核稿', lane_label: '环境机缘', opens_at: '2026-11-04T11:00:00Z', closes_at: '2026-11-04T15:00:00Z', status: '待进入窗口' },
                { index: 1, after_indexes: [0], thread_indexes: [0, 1], title: '周五说明会核单', detail: '前四格局摆上台面', lane_label: '环境机缘', opens_at: '2026-11-06T00:30:00Z', closes_at: '2026-11-07T14:00:00Z', status: '待进入窗口' },
                { index: 2, after_indexes: [], thread_indexes: [1], title: '已经发生的交件', detail: '交齐了', lane_label: '环境机缘', opens_at: '2026-11-03T01:00:00Z', closes_at: '2026-11-03T02:00:00Z', status: '已落实为世界事件' },
              ],
              convergences: [{ decision_label: '延续原世界线', thread_titles: [], reason: '未改变规划' }],
            },
          },
        },
      },
      facts_memory_inner: {
        state: 'ready',
        data: {
          metrics: [{ key: 'memory_candidates', label: '记忆候选', count: 678 }, { key: 'facts', label: '事实', count: 55 }],
          highlights: [
            { kind: 'affect_episode', title: '暖 25%', status_code: 'active', status_label: '进行中', occurred_at: '2026-11-02T16:58:23Z', values: [{ key: 'peak_intensity_bp', label: '最浓的一下', value: 2542, value_label: '25%' }] },
            { kind: 'memory_candidate', title: '边界', status_code: 'active', status_label: '已保留', occurred_at: NOW, values: [{ key: 'retrieval_strength_bp', label: '提取强度', value: 7155 }, { key: 'retention_rationales', label: '保留依据', value: '与边界有关' }] },
            { kind: 'memory_candidate', title: '未记录强度', status_code: 'active', status_label: '已保留', occurred_at: NOW, values: [] },
            { kind: 'character_core', title: '她这个人已经立住了', occurred_at: '2026-08-06T12:16:28Z', values: [] },
          ],
        },
      },
      relationship_lifecycle: {
        state: 'ready',
        data: {
          metrics: [{ key: 'npcs', label: '人物', count: 34 }],
          highlights: [
            { kind: 'relationship_state', title: '和你', status_code: 'friend', status_label: '朋友', occurred_at: NOW, values: [
              { key: 'trust_bp', label: '信任', value: 1800, value_label: '18%' }, { key: 'closeness_bp', label: '亲近', value: 2100, value_label: '21%' },
              { key: 'respect_bp', label: '尊重', value: 1900, value_label: '19%' }, { key: 'reliability_bp', label: '可靠', value: 600, value_label: '6%' },
              { key: 'mutuality_bp', label: '相互', value: 1700, value_label: '17%' }, { key: 'repair_confidence_bp', label: '修复信心', value: 400, value_label: '4%' }] },
            { kind: 'relationship_state', title: '与林晚', status_code: 'stranger', status_label: '陌生', occurred_at: NOW, values: [{ key: 'closeness_bp', label: '亲近', value: 60, value_label: '1%' }] },
            { kind: 'npc', title: '林晚', status_code: 'active', status_label: '现有人物', occurred_at: NOW, values: [] },
            { kind: 'npc', title: '人物', status_code: 'active', status_label: '现有人物', occurred_at: NOW, values: [] },
            { kind: 'npc', title: '人物', status_code: 'active', status_label: '现有人物', occurred_at: NOW, values: [] },
            { kind: 'relationship_signal', title: '跟他说话不用挑时候', detail: '一来一回挺顺', occurred_at: NOW, values: [] },
            { kind: 'private_impression', title: '不应出现的私人印象', detail: 'PRIVATE-REFLECTION', occurred_at: NOW, values: [] },
          ],
        },
      },
      operations: {
        state: 'ready',
        data: {
          metrics: [{ key: 'proactive_actions', label: '主动与跟进行动', count: 197 }],
          mechanisms: [{ key: 'silence_appraisal', label: '未回复后的评估', recorded_count: 59, open_count: 0, claimed_count: 0, terminal_count: 59, attempt_count: 245 }],
          highlights: [
            { kind: 'response_expectation', title: '他随口接一句', status_code: 'open', status_label: '进行中', occurred_at: '2026-11-03T01:00:40Z', values: [{ key: 'pressure_bp', label: '在意', value: 2500, value_label: '25%' }, { key: 'expires_at', label: '这份盼头看到', value: '2026-11-03T03:30:40+00:00', value_label: '11月3日 11:30' }] },
            { kind: 'response_expectation', title: '已经过期的期待', status_code: 'expired', status_label: '已失效', occurred_at: '2026-11-03T02:00:40Z', values: [] },
          ],
        },
      },
      perception_media: {
        state: 'ready',
        data: {
          metrics: [],
          highlights: [
            { kind: 'external_perception', title: '【日常】窃听风云4（UP主：脱缰凯Kk）', detail: '封面扫到了，图一乐', occurred_at: '2026-11-02T12:32:09Z', values: [{ key: 'canonical_url', label: '链接', value: 'https://www.bilibili.com/video/BV13D4w6cEVS' }] },
            { kind: 'external_perception', title: '可疑链接', detail: '不应成为链接', occurred_at: '2026-11-02T10:32:09Z', values: [{ key: 'canonical_url', label: '链接', value: 'javascript:alert(1)' }] },
            { kind: 'media_delivery', title: '书店角落', detail: '书店角落照片', occurred_at: '2026-08-19T15:56:33Z', values: [{ key: 'image_url', label: '照片画面', value: '/world-v2/dashboard/media/preview:media:abc/image' }] },
            { kind: 'media_preview', title: '外链图片', detail: '外链', occurred_at: '2026-08-19T15:56:33Z', values: [{ key: 'image_url', label: '照片画面', value: 'https://evil.example/x.png' }] },
            { kind: 'external_signal', title: '【热门】导弹般坠机', detail: '', occurred_at: NOW, values: [] },
          ],
        },
      },
      authority_privacy: { state: 'ready', data: { metrics: [], highlights: [] } },
      ledger_qualification: { state: 'ready', data: { qualification_label: '仅展示账本证据，不代表生产资格通过', metrics: [{ key: 'committed_world_event_refs', label: '已提交世界事件', count: 41885 }] } },
      runtime_operations: { state: 'ready', data: { signals: [{ key: 'scheduler', label: '时钟还在走', state: 'ready' }], notices: [] } },
    },
  };
  return Object.assign(base, overrides);
}
const studioFixture = () => ({
  status: 'ready',
  inner_drama: {
    turns: [
      { turn_index: 3155, mode: 'proactive', mode_label: '她主动找你', time_display: '11-03 11:00', user_text: '【主动找你 / 生活事件与心绪触发】',
        sent_bubbles: ['古代汉语下课了', '你那边呢'], inner_state_summary: '一上午脑子被文选塞满了', noticed: ['他半天没动静'], about_us: '不慌', why_us: '一来一回顺', stuck_with_me: [] },
      { turn_index: 3105, mode: 'reply', mode_label: '回你的消息', time_display: '11-03 00:47', user_text: '睡之前可以给我发条语音吗',
        sent_bubbles: ['好呀'], inner_state_summary: '有点被当回事的暖', noticed: [], about_us: '', why_us: '', stuck_with_me: ['明早的课'] },
    ],
    active_affect: [{ emotion_kind: 'warmth', emotion_label: '暖意', intensity_pct: 42, residual_pct: 5, opened_at_display: '11-02 18:04', expires_at_display: '11-03 00:58' }],
    appraisals: [{ idx: 610, confidence_pct: 72, accepted_at_display: '11-03 10:05', hypotheses: [{ meaning: '他在补觉', weight_pct: 67 }, { meaning: '他在忙', weight_pct: 33 }] }],
    private_impressions: [{ idx: 118, reflection_summary: '照发，语气放平', confidence_pct: 50, accepted_at_display: '11-03 08:15' }],
    intentions: [],
  },
  interaction_station: { summary: { pending_pickup_count: 0, in_transit_count: 0, completed_count: 1, total_count: 1 }, presets: [], items: [
    { interaction_id: 'interaction:x', kind: 'instant_delivery', kind_label: '即时外卖', direction: 'user_to_companion', title: '把子肉盖饭', detail: '热汤', status: 'delivered', status_label: '已送达', outcome_text: '骑手送到楼下' }] },
});
const voiceFixture = () => ({ voice_notes: [
  { voice_id: 'v1', text: '快去休息啦', duration_seconds: 3.47, audio_url: '/world-v2/dashboard/voice/v1/audio' },
  { voice_id: 'v2', text: '外部音频', duration_seconds: 1, audio_url: 'https://evil.example/a.mp3' },
] });

// ---------------------------------------------------------------------------
// Pure view models
// ---------------------------------------------------------------------------
test('present view prefers the active plan and keeps only open expectations', () => {
  const p = stage.present(snapshotFixture(), client);
  assert.equal(p.activity.view.detail, '出门去文科楼上十点的古代汉语');
  assert.equal(p.activityIsActive, true);
  assert.equal(stage.hm(p.window.start), '10:00');
  assert.equal(p.intentSource, '当日自主安排');
  assert.deepEqual(p.waits.map(w => w.view.title), ['他随口接一句']);
  const noActive = snapshotFixture();
  noActive.sections.overview_life.data.highlights = noActive.sections.overview_life.data.highlights.filter(h => h.status_code !== 'active');
  const fallback = stage.present(noActive, client);
  assert.equal(fallback.activityIsActive, false);
  assert.equal(fallback.activity.view.title, '旧计划');
});

test('week strips clip at midnight, hide covered world events and withheld rows, and mark unloaded slots', () => {
  const w = stage.weekStrips(snapshotFixture(), new Set());
  assert.equal(w.days.length, 7);
  const mon = w.days[0], tue = w.days[1];
  const overnight = mon.acts.find(a => a.entry.index === 4);
  assert.equal(overnight.start, 1410);
  assert.equal(overnight.end, 1440);
  assert.ok(tue.acts.find(a => a.entry.index === 4));
  assert.equal(tue.dots.some(d => d.entry.index === 2), false, 'world event covered by an experience stays nested');
  assert.ok(mon.dots.some(d => d.entry.index === 3));
  assert.equal([...tue.acts, ...tue.course].some(s => s.entry.index === 7), false, 'withheld rows never render');
  assert.equal(tue.course[0].entry.title, '周二古代汉语');
  assert.equal(tue.windows[0].entry.kind, 'expectation');
  assert.equal(tue.today, true);
  assert.equal(w.phases.length, 1);
  assert.deepEqual(w.days[2].unloaded.map(u => [u.hour, u.count, u.firstPage]), [[20, 3, 1]]);
  const hidden = stage.weekStrips(snapshotFixture(), new Set(['plan']));
  assert.equal(hidden.days[1].acts.some(a => a.kind === 'plan'), false);
  assert.equal(stage.weekStrips({ sections: { overview_life: { state: 'unavailable' } } }), null);
});

test('route map places nodes by recorded time, draws only explicit prerequisites and marks cross-route nodes', () => {
  const wl = snapshotFixture().sections.overview_life.data.agenda.worldline;
  const map = stage.routeMap(wl, NOW, { pxPerDay: 100 });
  assert.equal(map.lanes.length, 2);
  const [a, b, done] = [0, 1, 2].map(i => map.nodes.find(n => n.index === i));
  assert.ok(b.x > a.x);
  assert.equal(b.crossing, true);
  assert.deepEqual(b.lanes, [0, 1]);
  assert.equal(done.tone, 'realized');
  assert.equal(a.tone, 'candidate');
  assert.deepEqual(map.edges.map(e => [e.from, e.to]), [['opp-0', 'opp-1']]);
  assert.ok(Math.abs(map.nowX - (map.padLeft + (Date.parse(NOW) - map.start) / 86400000 * 100)) < 0.01);
  assert.equal(map.ordered[0].index, 2, 'stepping follows opening time');
  assert.ok(map.reviewX > map.nowX);
  const dup = stage.routeMap({ threads: [{ title: '同名' }, { title: '同名' }], opportunities: [{ index: 0, thread_indexes: [1], title: 'x', opens_at: '2026-11-04T00:00:00Z' }] }, NOW);
  assert.deepEqual(dup.nodes[0].lanes, [1]);
  const orphan = stage.routeMap({ threads: [{ title: '主线' }], opportunities: [{ index: 0, title: '无归属', opens_at: '2026-11-04T00:00:00Z' }] }, NOW);
  assert.equal(orphan.lanes.at(-1).index, -1);
  const undated = stage.routeMap({ threads: [{ title: '主线' }], opportunities: [{ index: 0, thread_indexes: [0], title: '无时间' }] }, NOW);
  assert.equal(undated.nodes.length, 0);
  assert.equal(undated.undated.length, 1);
  assert.equal(stage.routeMap(null, NOW), null);
});

test('chapters are proportional and locate the recorded world time', () => {
  const ch = stage.chapters(snapshotFixture().sections.overview_life.data.agenda.worldline, NOW);
  assert.equal(ch.stages.length, 2);
  assert.equal(ch.stages[0].current, true);
  assert.ok(Math.abs(ch.stages[0].width + ch.stages[1].width - 100) < 1e-9);
  assert.ok(ch.nowPct > 0 && ch.nowPct < ch.stages[0].width);
});

test('bonds read recorded dimensions only and never invent a complete radar', () => {
  const b = stage.bonds(snapshotFixture(), client);
  assert.equal(b.you.stage, '朋友');
  assert.equal(b.you.complete, true);
  assert.equal(b.you.dims['亲近'], 21);
  assert.deepEqual(b.people.map(p => [p.name, p.state && p.state.stage]), [['林晚', '陌生']]);
  assert.equal(b.unnamed, 2);
  assert.equal(b.npcTotal, 34);
  const partial = snapshotFixture();
  partial.sections.relationship_lifecycle.data.highlights[0].values.pop();
  assert.equal(stage.bonds(partial, client).you.complete, false);
});

test('memory field keeps missing strength as missing', () => {
  const field = stage.memoryField(snapshotFixture(), client);
  assert.equal(field.items.length, 2);
  assert.equal(field.items[0].strength, 71.55);
  assert.equal(field.items[1].strength, null);
  for (const item of field.items) {
    assert.ok(item.x - item.r >= 0 && item.x + item.r <= field.width);
  }
});

test('trigger labels are not mistaken for user messages and turns are chronological', () => {
  assert.equal(stage.isTriggerText('【主动找你 / 生活事件与心绪触发】'), true);
  assert.equal(stage.isTriggerText('睡之前可以给我发条语音吗'), false);
  assert.deepEqual(stage.turns(studioFixture()).map(t => t.turn_index), [3105, 3155]);
  assert.equal(stage.phase('2026-11-03T23:30:00+08:00'), 'night');
  assert.equal(stage.phase('2026-11-03T11:00:00+08:00'), 'day');
});

// ---------------------------------------------------------------------------
// DOM adapter: exercises the shipped script without a browser.  It refuses
// innerHTML so every dynamic value must be rendered as text nodes.
// ---------------------------------------------------------------------------
class Style {
  setProperty(k, v) { this[k] = v; }
  removeProperty(k) { delete this[k]; }
}
class Element {
  constructor(tag = 'div', doc = null) {
    this.tagName = tag; this.children = []; this.dataset = {}; this.attributes = {}; this.events = {};
    this.hidden = false; this.open = false; this.disabled = false; this.value = ''; this.className = '';
    this.ownText = ''; this.style = new Style(); this.parentNode = null; this.doc = doc;
    this.scrollTop = 0; this.scrollLeft = 0; this.scrollHeight = 0; this.clientHeight = 0; this.clientWidth = 0;
    const self = this;
    this.classList = {
      add(...n) { const s = new Set(self.className.split(' ').filter(Boolean)); n.forEach(x => s.add(x)); self.className = [...s].join(' '); },
      remove(...n) { const s = new Set(self.className.split(' ').filter(Boolean)); n.forEach(x => s.delete(x)); self.className = [...s].join(' '); },
      toggle(n, on) { const s = new Set(self.className.split(' ').filter(Boolean)); const want = on === undefined ? !s.has(n) : on; want ? s.add(n) : s.delete(n); self.className = [...s].join(' '); return want; },
      contains(n) { return self.className.split(' ').includes(n); },
    };
  }
  set textContent(v) { this.ownText = String(v); this.children = []; }
  get textContent() { return this.ownText + this.children.map(c => c.textContent).join(''); }
  set innerHTML(_v) { throw new Error('unsafe HTML rendering'); }
  get innerHTML() { throw new Error('unsafe HTML rendering'); }
  descendants() { return this.children.flatMap(c => [c, ...c.descendants()]); }
  matches(sel) {
    if (sel.startsWith('.')) return this.className.split(' ').includes(sel.slice(1));
    const attr = /^\[([a-z-]+)(?:="([^"]*)")?\]$/.exec(sel);
    if (attr) {
      const [, name, val] = attr;
      const v = name.startsWith('data-') ? this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] ?? this.attributes[name] : this.attributes[name];
      return val === undefined ? v !== undefined : v === val;
    }
    return this.tagName === sel;
  }
  querySelector(sel) { return this.descendants().find(n => n.matches(sel)) || null; }
  querySelectorAll(sel) { return this.descendants().filter(n => n.matches(sel)); }
  get firstChild() { return this.children[0] || null; }
  focus() {} scrollIntoView() {}
  showModal() { this.open = true; } close() { this.open = false; }
  adopt(kids) { for (const k of kids) { if (k && typeof k === 'object') k.parentNode = this; } return kids.map(k => (typeof k === 'string' ? this.doc.createTextNode(k) : k)); }
  append(...kids) { this.children.push(...this.adopt(kids)); }
  prepend(...kids) { this.children.unshift(...this.adopt(kids)); }
  appendChild(k) { this.append(k); return k; }
  removeChild(k) { this.children = this.children.filter(c => c !== k); k.parentNode = null; }
  replaceChildren(...kids) { this.ownText = ''; this.children = this.adopt(kids); }
  setAttribute(n, v) { this.attributes[n] = String(v); if (n.startsWith('data-')) this.dataset[n.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(v); }
  getAttribute(n) { return this.attributes[n] ?? null; }
  addEventListener(n, h) { (this.events[n] ||= []).push(h); }
  fire(n, e = {}) { for (const h of this.events[n] || []) h({ target: this, currentTarget: this, preventDefault() {}, stopPropagation() {}, ...e }); }
}
function respond(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, headers: { get: n => (n.toLowerCase() === 'etag' ? '"e' + Math.random() + '"' : null) }, json: async () => payload };
}
function browser({ home = [], studio = studioFixture(), voice = voiceFixture(), cost = null } = {}) {
  const ids = new Map();
  const document = {
    events: {}, body: null, documentElement: null,
    createElement: tag => new Element(tag, document),
    createElementNS: (_ns, tag) => new Element(tag, document),
    createTextNode: text => { const n = new Element('#text', document); n.textContent = text; return n; },
    getElementById: id => { if (!ids.has(id)) { const e = new Element('div', document); e.id = id; document.body.append(e); ids.set(id, e); } return ids.get(id); },
    querySelectorAll: sel => document.body.querySelectorAll(sel),
    addEventListener(n, h) { (this.events[n] ||= []).push(h); },
    fire(n, e) { for (const h of this.events[n] || []) h({ preventDefault() {}, ...e }); },
  };
  document.body = new Element('body', document);
  document.documentElement = new Element('html', document);
  for (const z of ['fit', 'near']) { const b = new Element('button', document); b.setAttribute('data-route-zoom', z); document.body.append(b); }
  const requests = [];
  let poll = null;
  const window = {
    scrollY: 0, innerHeight: 900,
    matchMedia: q => ({ matches: q.includes('reduce') }),
    addEventListener() {},
    fetch: async (url, options = {}) => {
      requests.push([url, options]);
      if (url.startsWith('/world-v2/dashboard/home')) {
        const next = home.shift();
        if (next instanceof Error) throw next;
        assert.ok(next, 'unexpected extra home poll');
        return next;
      }
      if (url === '/world-v2/dashboard/studio') return respond(studio);
      if (url === '/world-v2/dashboard/voice') return respond(voice);
      if (url === '/world-v2/dashboard/cost') return cost ? respond(cost) : respond({}, 503);
      throw new Error('unexpected route ' + url);
    },
  };
  const timers = { setTimeout: (fn, ms) => { const t = setTimeout(fn, ms); t.unref && t.unref(); return t; }, clearTimeout };
  vm.runInNewContext(fs.readFileSync(process.env.DASHBOARD_APP_JS_PATH, 'utf8'), {
    window, document, Intl, Date, console, AbortController, Promise,
    ...timers,
    setInterval(cb, delay) { assert.equal(delay, 15000); poll = cb; return 1; },
  });
  const $ = id => document.getElementById(id);
  return { $, document, window, requests, poll: () => poll(), flush: () => new Promise(r => setImmediate(r)) };
}
async function settle(b) { for (let i = 0; i < 6; i++) await b.flush(); }

test('stage renders the authorized snapshot through text nodes only', async () => {
  const b = browser({ home: [respond(snapshotFixture())] });
  await settle(b);
  assert.equal(b.$('captureState').textContent, '已同步');
  assert.equal(b.$('heroActivity').textContent, '出门去文科楼上十点的古代汉语');
  assert.match(b.$('heroMeta').textContent, /10:00 – 11:30/);
  assert.match(b.$('heroContext').textContent, /她在等你回应/);
  assert.doesNotMatch(b.$('heroContext').textContent, /已经过期的期待/);
  assert.match(b.$('heroStats').textContent, /41,885/);
  assert.equal(b.document.body.dataset.phase, 'day');
  const all = b.document.body.textContent;
  assert.doesNotMatch(all, /PRIVATE-REFLECTION/);
  assert.doesNotMatch(all, /保留的私密计划/);
  for (const [url, options] of b.requests) assert.equal(options.method, undefined, url + ' must be a read');
});

test('a first failed capture shows an explicit unavailable state without invented numbers', async () => {
  const b = browser({ home: [new Error('owner down')] });
  await settle(b);
  assert.equal(b.$('captureState').textContent, '暂时连不上');
  assert.equal(b.$('captureNotice').hidden, false);
  assert.equal(b.$('heroActivity').textContent, '尚未取得可验证的生活快照。');
  assert.equal(b.$('heroStats').children.length, 0);
  assert.doesNotMatch(b.document.body.textContent, /owner down/);
});

test('a later failure keeps the last verified snapshot visible as stale', async () => {
  const b = browser({ home: [respond(snapshotFixture()), new Error('boom')] });
  await settle(b);
  b.poll();
  await settle(b);
  assert.equal(b.$('captureState').textContent, '数据有点旧');
  assert.equal(b.$('heroActivity').textContent, '出门去文科楼上十点的古代汉语');
});

test('pause freezes polling without writes and resume refetches', async () => {
  const b = browser({ home: [respond(snapshotFixture()), respond(snapshotFixture())] });
  await settle(b);
  const before = b.requests.filter(([u]) => u.startsWith('/world-v2/dashboard/home')).length;
  b.$('pauseDisplay').fire('click');
  assert.equal(b.$('pauseLabel').hidden, false);
  b.poll();
  await settle(b);
  assert.equal(b.requests.filter(([u]) => u.startsWith('/world-v2/dashboard/home')).length, before);
  b.$('pauseDisplay').fire('click');
  await settle(b);
  assert.equal(b.requests.filter(([u]) => u.startsWith('/world-v2/dashboard/home')).length, before + 1);
  assert.ok(b.requests.every(([, o]) => o.method === undefined));
});

test('week strips open the exact entry and navigate weeks on the read-only route', async () => {
  const b = browser({ home: [respond(snapshotFixture()), respond(snapshotFixture())] });
  await settle(b);
  const grid = b.$('weekGrid');
  const act = grid.querySelectorAll('button').find(n => n.className.includes('week-act') && n.textContent.includes('撑到点'));
  assert.ok(act);
  act.fire('click');
  assert.equal(b.$('detailDrawer').open, true);
  assert.match(b.$('drawerBody').textContent, /复习了一遍文选/);
  const related = b.$('drawerBody').querySelectorAll('button').find(n => n.textContent.includes('被经历覆盖的世界事件'));
  assert.ok(related, 'recorded relation is offered, not inferred');
  const next = b.$('weekNav').querySelectorAll('button').find(n => n.textContent.includes('下一周'));
  next.fire('click');
  await settle(b);
  assert.equal(b.requests.filter(([u]) => u.startsWith('/world-v2/dashboard/home')).at(-1)[0], '/world-v2/dashboard/home?week=2026-11-09');
});

test('fate route nodes step chronologically into the reading box', async () => {
  const b = browser({ home: [respond(snapshotFixture())] });
  await settle(b);
  const nodes = b.$('routeMap').querySelectorAll('[data-route-node]');
  assert.equal(nodes.length, 3);
  assert.match(b.$('galName').textContent, /CHAPTER 01/);
  b.$('galNext').fire('click');
  assert.match(b.$('galText').textContent, /已经发生的交件/);
  assert.equal(b.$('galCount').textContent, '1 / 3');
  b.$('galNext').fire('click');
  assert.match(b.$('galText').textContent, /答辩前夜的图书馆/);
  assert.match(b.$('routeLegend').textContent, /不代表已经发生/);
});

test('inner voice separates her bubbles from your messages and shows the selected inner summary', async () => {
  const b = browser({ home: [respond(snapshotFixture())] });
  await settle(b);
  const chat = b.$('innerChat');
  const yours = chat.querySelectorAll('.you').map(n => n.textContent);
  assert.deepEqual(yours, ['睡之前可以给我发条语音吗']);
  assert.doesNotMatch(chat.textContent, /【主动找你/);
  assert.match(b.$('innerThought').textContent, /一上午脑子被文选塞满了/);
  const older = chat.querySelectorAll('.turn').find(n => n.dataset.turn === '3105');
  older.fire('click');
  assert.match(b.$('innerThought').textContent, /有点被当回事的暖/);
  assert.match(b.$('innerThought').textContent, /明早的课/);
  assert.match(b.$('innerLive').textContent, /暖意/);
});

test('media accepts only authorized dashboard routes and https links', async () => {
  const b = browser({ home: [respond(snapshotFixture())] });
  await settle(b);
  const imgs = b.$('photoWall').querySelectorAll('img');
  assert.deepEqual(imgs.map(i => i.src), ['/world-v2/dashboard/media/preview:media:abc/image']);
  const links = b.$('perceptionFeed').querySelectorAll('a');
  assert.deepEqual(links.map(a => a.href), ['https://www.bilibili.com/video/BV13D4w6cEVS']);
  assert.equal(b.$('voiceList').querySelectorAll('.voice').length, 1);
});

test('incomplete relationship dimensions are stated instead of drawn', async () => {
  const snap = snapshotFixture();
  snap.sections.relationship_lifecycle.data.highlights[0].values.splice(2, 1);
  const b = browser({ home: [respond(snap)] });
  await settle(b);
  assert.match(b.$('bondsYou').textContent, /六项维度未完整记录/);
  assert.equal(b.$('bondsYou').querySelectorAll('polygon').length, 0);
});
