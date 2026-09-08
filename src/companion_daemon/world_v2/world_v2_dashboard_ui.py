"""Authenticated, read-only browser shell for the owner World v2 Dashboard.

The browser reads one versioned owner snapshot and renders only fields already
authorized by that contract.  It never reads process health, raw World domain
refs, or a second World host, and it does not infer a room scene when the owner
reports stale or unavailable state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time


DASHBOARD_SESSION_COOKIE = "girl_agent_v2_dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = 8 * 60 * 60


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


class DashboardSessionCodec:
    """Issue instance-bound sessions without placing the operator token in a cookie."""

    def __init__(self, *, operator_token: str, instance_secret: bytes) -> None:
        if not operator_token.strip():
            raise ValueError("dashboard session requires a configured operator token")
        if len(instance_secret) < 32:
            raise ValueError("dashboard session instance secret must be at least 32 bytes")
        self._key = hmac.new(
            instance_secret,
            b"world-v2-dashboard-session\0" + operator_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()

    def issue(self, *, now: int | None = None) -> str:
        issued_at = int(time.time()) if now is None else now
        expires_at = issued_at + DASHBOARD_SESSION_TTL_SECONDS
        nonce = _b64(secrets.token_bytes(18))
        body = f"v1.{expires_at}.{nonce}"
        signature = _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{signature}"

    def verify(self, value: str | None, *, now: int | None = None) -> bool:
        if not value or len(value) > 512:
            return False
        parts = value.split(".")
        if len(parts) != 4 or parts[0] != "v1":
            return False
        try:
            expires_at = int(parts[1])
        except ValueError:
            return False
        current = int(time.time()) if now is None else now
        if expires_at <= current or expires_at > current + DASHBOARD_SESSION_TTL_SECONDS:
            return False
        body = ".".join(parts[:3])
        expected = _b64(hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest())
        return hmac.compare_digest(expected, parts[3])


LOGIN_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知栀看板登录</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#d9cdbc;color:#3f342d;font-family:"PingFang SC",system-ui,sans-serif}
main{width:min(420px,calc(100% - 32px));padding:28px;background:#f7eedf;border:3px solid #684f42;box-shadow:6px 6px 0 #b79c84}
h1{font-size:20px}label,input,button{display:block;width:100%}input,button{margin-top:10px;padding:11px;font:inherit;box-sizing:border-box}button{background:#557f78;color:white;border:0}p{line-height:1.6;font-size:13px}
</style></head><body><main><h1>知栀看板</h1>
<p>请输入本机配置的 operator token。凭证只通过本次 POST 提交，不会写入 URL、页面脚本或浏览器存储。</p>
<form method="post" action="/world-v2/dashboard/session" autocomplete="off">
<label for="operator-token">Operator token</label><input id="operator-token" name="operator_token" type="password" required autocomplete="current-password">
<button type="submit">打开看板</button></form></main></body></html>"""


UNAVAILABLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知栀看板还没准备好</title></head><body>
<main><h1>看板还没准备好</h1><p>只读 World v2 host 尚未初始化。不会回退到旧运行时。</p></main>
</body></html>"""


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>沈知栀 · 生活现场</title><style>
:root{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;color:#263d36;background:#f4f4ed;font-synthesis:none;--ink:#263d36;--muted:#687970;--line:#dce2d7;--paper:#fffef9;--green:#315b4a;--soft:#e7eee2;--amber:#835e25}
*{box-sizing:border-box}body{margin:0;min-height:100vh}button,select{font:inherit}button,a,select{-webkit-tap-highlight-color:transparent}button,select{cursor:pointer}button:focus-visible,a:focus-visible,select:focus-visible,summary:focus-visible{outline:3px solid #a4b89a;outline-offset:4px}[hidden]{display:none!important}
.bar{max-width:1440px;margin:auto;padding:24px 40px;display:flex;align-items:center;justify-content:space-between;gap:24px;border-bottom:1px solid var(--line)}.identity{display:flex;gap:14px;align-items:center}.monogram{display:grid;place-items:center;width:44px;height:44px;background:var(--green);color:#fffef2;border-radius:14px;font-size:23px;font-family:serif}.bar h1{font-size:18px;font-weight:600;letter-spacing:.05em;margin:0}.eyebrow{margin:5px 0 0;color:var(--muted);font-size:10px;letter-spacing:.16em}.bar-actions{display:flex;align-items:center;gap:10px}.button{border:1px solid var(--line);background:var(--paper);border-radius:8px;padding:9px 14px;font-size:12px;color:var(--ink)}.button.primary{background:var(--green);color:#fff;border-color:var(--green)}.logout{margin:0}.logout button{background:none;border:none;padding:8px;color:var(--muted);font:12px inherit;cursor:pointer}
.status-bar{max-width:1440px;margin:auto;padding:18px 40px 0;display:flex;align-items:center;justify-content:space-between;gap:16px;font-size:12px;color:var(--muted)}.status-meta{display:flex;align-items:center;gap:12px}.capture-state{display:inline-flex;align-items:center;gap:7px;color:var(--green)}.capture-state:before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}.capture-state.error{color:#965240}.header-clock{margin:0}.readonly{letter-spacing:.04em}.capture-notice{max-width:1360px;margin:14px auto 0;padding:12px 16px;background:#f6ead7;border:1px solid #e8d5b4;border-radius:10px;color:#754e29;font-size:13px;line-height:1.6}.recording-tools{max-width:1440px;margin:16px auto 0;padding:0 40px;display:flex;align-items:center;gap:12px;font-size:12px;color:var(--muted)}.recording-tools select{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:8px 32px 8px 12px;color:var(--ink)}
.wrap{max-width:1440px;margin:auto;padding:24px 40px 48px;display:grid;gap:24px}.panel,.section-card{background:var(--paper);border:1px solid var(--line);border-radius:18px;overflow:hidden;min-width:0}.now-panel{display:grid;grid-template-columns:minmax(210px,.55fr) minmax(0,1.6fr);padding:28px;gap:32px;background:linear-gradient(115deg,#e8eee0,#fafbf3)}.hero-label{font-size:10px;letter-spacing:.16em;color:#6b7f69;margin:0 0 15px}.now-panel h2{font-size:34px;font-weight:500;letter-spacing:.05em;margin:0}.hero-note{margin:12px 0;color:var(--muted);line-height:1.8;font-size:12px;max-width:240px}.world-clock{font-size:12px;color:var(--muted);line-height:1.7;margin:14px 0 0}.now-story{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px 22px;align-content:center}.now-line{min-width:0;border-left:2px solid #c5d2bd;padding-left:12px}.now-line .k{display:block;font-size:11px;color:var(--muted)}.now-line .v{display:block;margin-top:7px;font-size:15px;line-height:1.6;overflow-wrap:anywhere}.now-line .d{display:block;margin-top:4px;color:var(--muted);font-size:12px;line-height:1.6}.now-line.missing .v{color:var(--muted);font-size:13px}
.section-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;align-items:start}.section-card{padding:24px}.section-card[data-focus-area="life"]{grid-column:1/-1}.section-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.section-head h2{font-size:18px;font-weight:600;margin:0;letter-spacing:.03em}.section-state{border-radius:999px;padding:4px 9px;background:var(--soft);color:var(--green);font-size:11px;white-space:nowrap}.section-state[data-state="unavailable"],.section-state[data-state="degraded"],.section-state[data-state="stale"]{background:#f4e3d7;color:#91523a}.section-state[data-state="empty"],.section-state[data-state="disabled"]{background:#eeeee7;color:var(--muted)}.section-body{padding-top:16px;display:grid;gap:16px}.section-note{font-size:12px;color:var(--muted);line-height:1.8;margin:0}.empty-state{padding:24px 16px;border:1px dashed var(--line);border-radius:10px;color:var(--muted);font-size:13px;line-height:1.8;text-align:center}.section-subtitle{font-size:12px;color:var(--muted);font-weight:500;margin:0 0 10px}.life-columns{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:30px}.life-column{min-width:0}.activity-list,.highlight-list{display:grid;gap:10px}.highlight{padding:16px;background:#f8f9f3;border:1px solid #e3e8dc;border-radius:12px;min-width:0}.highlight-top{display:flex;justify-content:space-between;align-items:baseline;gap:10px}.kind-tag{color:#66816b;font-size:10px;letter-spacing:.06em}.when{font-size:10px;color:var(--muted);font-variant-numeric:tabular-nums}.highlight h3{font-size:15px;line-height:1.65;font-weight:500;margin:8px 0 0;overflow-wrap:anywhere}.status-pill{display:inline-block;margin-top:8px;border-radius:5px;padding:3px 7px;background:var(--soft);font-size:10px;color:var(--green)}.highlight[data-status="planned"] .status-pill,.highlight[data-status="pending"] .status-pill{background:#f0e9d9;color:var(--amber)}.highlight .detail{font-size:12px;line-height:1.8;color:var(--muted);margin:8px 0 0;overflow-wrap:anywhere}.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}.chip{font-size:10px;line-height:1.5;color:var(--muted);background:#eef1e8;border-radius:4px;padding:3px 6px;overflow-wrap:anywhere}.timeline{list-style:none;padding:0;margin:0;display:grid;gap:12px}.timeline li{display:grid;grid-template-columns:9px minmax(0,1fr);gap:12px;align-items:start}.timeline-dot{margin-top:20px;width:7px;height:7px;border-radius:50%;background:#91a888}.timeline .highlight{background:transparent;border:0;border-bottom:1px solid var(--line);padding:12px 0 16px;border-radius:0}.timeline li:last-child .highlight{border:0}.timeline h3{margin-top:4px}.timeline-caption{font-size:11px;color:var(--muted);margin:0 0 10px;line-height:1.8}
.metric-grid,.signal-grid,.notice-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:8px}.metric,.signal,.notice{padding:11px 12px;border:1px solid var(--line);border-radius:9px;background:#fcfcf6;min-width:0}.metric span,.signal span,.notice span{display:block;color:var(--muted);font-size:10px}.metric strong,.signal strong,.notice strong{display:block;margin-top:6px;font-size:20px;font-weight:500}.signal strong,.notice strong{font-size:12px;line-height:1.6}.zero-metrics,.section-contract{border-top:1px solid var(--line);padding-top:12px}.zero-metrics summary,.section-contract summary{font-size:11px;color:var(--muted);cursor:pointer}.zero-metrics .metric-grid{margin-top:10px}.truncated{font-size:11px;color:var(--muted);margin:0;line-height:1.8}.operator-details{grid-column:1/-1;padding:18px 0;border-top:1px solid var(--line)}.operator-details>summary{font-size:12px;color:var(--muted);cursor:pointer}.operator-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px;padding-top:18px}.meta{display:grid;grid-template-columns:auto 1fr;gap:8px 18px;font-size:11px;color:var(--muted)}.meta dd{margin:0}.footer{font-size:11px;color:var(--muted);display:flex;justify-content:space-between;gap:16px;line-height:1.8}.footer a{color:inherit}
body.recording .operator-only{display:none!important}body.recording .bar{padding-top:28px}body.recording .wrap{gap:28px}body.recording .hero-note,body.recording .section-note{font-size:14px}body.recording .now-line .v{font-size:19px}body.recording .highlight h3{font-size:20px}body.recording .highlight .detail{font-size:15px}body.recording .kind-tag,body.recording .when,body.recording .chip,body.recording .status-pill{font-size:12px}body.recording .section-head h2{font-size:23px}body.recording[data-focus]:not([data-focus="all"]) .section-grid{grid-template-columns:1fr}body.recording[data-focus]:not([data-focus="all"]) .highlight-list{grid-template-columns:repeat(2,minmax(0,1fr))}body.recording .section-card{padding:30px}
@media(min-width:1500px){.wrap{padding-top:32px}}@media(max-width:1000px){.section-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.now-panel{grid-template-columns:1fr;gap:22px}.now-panel h2{font-size:28px}.hero-note{max-width:none}.life-columns{gap:20px}}@media(max-width:680px){.bar{padding:18px;gap:8px}.bar h1{font-size:15px}.monogram{width:36px;height:36px}.eyebrow,.readonly{display:none}.status-bar{padding:16px 18px 0;align-items:start}.status-meta{align-items:start;flex-direction:column;gap:6px}.wrap{padding:18px;gap:16px}.section-grid,.operator-grid,.life-columns{grid-template-columns:1fr}.now-panel,.section-card{padding:20px}.now-story{grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.bar-actions{gap:4px}.button{font-size:11px;padding:8px}.capture-notice{margin:12px 18px 0}.recording-tools{padding:0 18px;flex-wrap:wrap}body.recording[data-focus]:not([data-focus="all"]) .highlight-list{grid-template-columns:1fr}.footer{flex-direction:column;gap:4px}}
@media(prefers-reduced-motion:no-preference){.button{transition:background .15s}.button:hover{filter:brightness(.97)}}
</style></head><body><header class="bar"><div class="identity"><span class="monogram" aria-hidden="true">栀</span><div><h1>沈知栀 · 生活现场</h1><p class="eyebrow">A LIFE, CONTINUING</p></div></div><div class="bar-actions"><button id="recordingToggle" class="button primary" type="button" aria-pressed="false">录制模式</button><form class="logout operator-only" method="post" action="/world-v2/dashboard/logout"><button type="submit">退出登录</button></form></div></header>
<div class="status-bar"><div class="status-meta"><span id="captureState" class="capture-state" role="status">正在读取</span><p id="headerClock" class="header-clock"></p></div><span class="readonly">只读 · 不改变她的生活</span></div>
<p id="captureNotice" class="capture-notice" role="status" hidden></p>
<div id="recordingTools" class="recording-tools" hidden><label for="recordingFocus">聚焦区域</label><select id="recordingFocus"><option value="all">完整视图</option><option value="now">这一刻</option><option value="life">生活进展</option><option value="memory">记忆</option><option value="emotion">情绪</option><option value="relationships">关系</option></select><span>仅改变显示；同步状态始终保留 · Esc 退出</span></div>
<main class="wrap"><section class="panel now-panel" data-focus-area="now"><div><p class="hero-label">THE PRESENT</p><h2>这一刻</h2><p class="hero-note">生活有自己的时间。<br>这里是已经记录下来的状态。</p><p id="worldClock" class="world-clock"></p></div><div id="nowStory" class="now-story"><p class="section-note">正在读取生活状态…</p></div></section><div id="sectionGrid" class="section-grid"></div><footer class="footer"><span>只展示经过授权的摘要，私密反思不在此呈现。</span><span class="operator-only">本机 owner 视图 · <a href="/pixel-home/index.html" target="_blank" rel="noopener">独立房间页面</a></span></footer></main>
<script src="/world-v2/dashboard/app.js?v=world-v2-dashboard-home.1-ui5" defer></script></body></html>"""

DASHBOARD_APP_JS = """'use strict';
const DashboardHomeClient=(()=>{
  const DATA_URL='/world-v2/dashboard/home';
  const SCHEMA_VERSION='world-v2-dashboard-home.1';
  const ROOM_MESSAGE_TYPE='pixel-home-state';
  const ROOM_MESSAGE_VERSION=2;
  const record=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  function validateSnapshot(payload){
    if(!record(payload)||payload.schema_version!==SCHEMA_VERSION)throw new Error('unsupported dashboard snapshot');
    if(typeof payload.snapshot_hash!=='string'||!/^[0-9a-f]{64}$/.test(payload.snapshot_hash))throw new Error('invalid dashboard snapshot hash');
    if(!record(payload.cursor)||!record(payload.sections)||!record(payload.sections.room))throw new Error('dashboard room section unavailable');
    return payload;
  }
  async function capture(fetchSnapshot,etag=null){
    const headers={Accept:'application/json'};
    if(etag)headers['If-None-Match']=etag;
    const response=await fetchSnapshot(DATA_URL,{credentials:'same-origin',headers});
    if(response.status===304)return{kind:'not_modified',etag,snapshot:null};
    if(!response.ok)throw new Error('dashboard snapshot unavailable ('+response.status+')');
    const snapshot=validateSnapshot(await response.json());
    return{kind:'snapshot',etag:response.headers.get('etag'),snapshot};
  }
  function snapshotFromCapture(result,previousSnapshot=null){
    if(result&&result.kind==='snapshot')return validateSnapshot(result.snapshot);
    if(result&&result.kind==='not_modified'&&previousSnapshot)return validateSnapshot(previousSnapshot);
    throw new Error('dashboard snapshot cache unavailable');
  }
  function roomState(value){
    if(value==='ready'||value==='stale')return value;
    return'unavailable';
  }
  function unavailableRoomMessage(state='unavailable',logicalTime=null){
    return{
      type:ROOM_MESSAGE_TYPE,
      v:ROOM_MESSAGE_VERSION,
      state:roomState(state),
      route:null,
      logical_time:typeof logicalTime==='string'?logicalTime:null,
    };
  }
  function roomMessageFrom(payload,overrideState=null){
    const snapshot=validateSnapshot(payload);
    const room=snapshot.sections.room;
    const state=roomState(overrideState||room.state);
    const logicalTime=typeof snapshot.logical_time==='string'?snapshot.logical_time:null;
    if(state!=='ready')return unavailableRoomMessage(state,logicalTime);
    const route=record(room.render_state)&&record(room.render_state.route)?room.render_state.route:null;
    if(!route||typeof route.scene_id!=='string'||!route.scene_id||typeof route.action_id!=='string'||!route.action_id||typeof route.availability!=='string'||!route.availability||route.availability==='unavailable')return unavailableRoomMessage('unavailable',logicalTime);
    return{
      type:ROOM_MESSAGE_TYPE,
      v:ROOM_MESSAGE_VERSION,
      state:'ready',
      route:{
        scene_id:route.scene_id,
        action_id:route.action_id,
        availability:route.availability,
      },
      logical_time:logicalTime,
    };
  }
  const STATE_LABELS={
    ready:'正常',
    empty:'暂无',
    unavailable:'暂时看不到',
    degraded:'不太稳',
    stale:'数据有点旧',
    disabled:'未启用',
    warming:'还在热身',
    busy:'忙着',
  };
  const SECTION_HEADINGS={
    overview_life:'生活',
    facts_memory_inner:'记忆与情绪',
    relationship_lifecycle:'身边的人',
    operations:'说话和行动',
    perception_media:'看见的和照片',
    authority_privacy:'权限和隐私',
    ledger_qualification:'账本核对',
    runtime_operations:'系统现在怎么样',
  };
  const KIND_HEADINGS={
    plan:'活动与计划',
    location:'人在哪',
    affect_episode:'心情',
    attention:'注意力',
    interaction_bid:'她在等',
    response_expectation:'她在等',
    private_impression:'对你的印象',
    relationship_state:'和你',
    expression_plan:'最近说话',
    action:'行动',
    resource:'身子',
    goal:'目标',
    npc:'人物',
    life_ecology_schedule:'她的日子',
  };
  const GENERIC_TITLES={
    '位置状态已更新':true,
    '目标状态':true,
    '世界事件':true,
    '表达计划':true,
    '表达节拍':true,
    '媒体检查':true,
    '承诺':true,
    '生平坐标':true,
    '情感 episode':true,
    '生活生态调度':true,
    '对你说的话':true,
  };
  const META_LABELS={
    logical_time:'世界时间',
    generated_at:'页面生成',
    world_id:'世界',
    schema_version:'快照版本',
    policy_version:'展示策略',
    snapshot_hash:'快照校验',
    owner:'实例',
    cursor:'账本游标',
  };
  const DATA_FIELD_LABELS={
    relationship_state_count:'关系状态',
    commitment_count:'关系承诺',
    interaction_act_count:'互动行为',
    typed_change_candidate_count:'待处理的关系变化',
    typed_change_lookup_count:'已核对的关系变化',
    typed_change_terminal_count:'已结束的关系变化',
    typed_change_rejected_count:'被拒绝的关系变化',
    typed_change_stale_count:'过期的关系变化',
    typed_change_unsettled_count:'还没落地的关系变化',
    ledger_schema_version:'账本版本',
    reducer_bundle_version:'投影版本',
    field_policy_count:'字段策略',
    typed_summary_field_count:'摘要字段',
    count_only_field_count:'只计数的字段',
    metadata_field_count:'元数据字段',
    intentionally_withheld_field_count:'故意不展示的字段',
  };
  function sectionLabel(sectionId,section){
    if(record(section)&&typeof section.label==='string'&&section.label.trim())return section.label;
    return sectionId;
  }
  function sectionHeading(sectionId,section){
    return SECTION_HEADINGS[sectionId]||sectionLabel(sectionId,section);
  }
  function stateLabel(state){
    if(typeof state!=='string'||!state)return STATE_LABELS.unavailable;
    return STATE_LABELS[state]||STATE_LABELS.unavailable;
  }
  function captureStateLabel(state){
    if(state==='ready')return '已同步';
    if(state==='stale')return '数据有点旧';
    return '暂时连不上';
  }
  function roomOverlayText(state){
    if(state==='stale')return '房间画面有点旧';
    if(state==='ready')return '';
    return '房间暂时看不到';
  }
  function formatClock(value){
    if(typeof value!=='string'||!value)return '';
    const date=new Date(value);
    if(Number.isNaN(date.getTime()))return value;
    const parts=new Intl.DateTimeFormat('zh-CN',{
      timeZone:'Asia/Shanghai',
      month:'numeric',
      day:'numeric',
      hour:'2-digit',
      minute:'2-digit',
      hour12:false,
    }).formatToParts(date);
    const read=type=>(parts.find(part=>part.type===type)||{}).value;
    return Number(read('month'))+'月'+Number(read('day'))+'日 '+read('hour')+':'+read('minute');
  }
  function formatRelative(value,nowMs){
    if(typeof value!=='string'||!value)return '';
    const then=new Date(value).getTime();
    if(Number.isNaN(then))return formatClock(value);
    const delta=Math.round(((nowMs==null?Date.now():nowMs)-then)/1000);
    if(delta<45)return '刚刚';
    if(delta<3600)return Math.floor(delta/60)+' 分钟前';
    if(delta<86400)return Math.floor(delta/3600)+' 小时前';
    if(delta<86400*7)return Math.floor(delta/86400)+' 天前';
    return formatClock(value);
  }
  function formatScalar(key,value,valueLabel){
    if(typeof valueLabel==='string'&&valueLabel.trim())return valueLabel;
    if(typeof value==='boolean')return value?'是':'否';
    if(value==null)return '';
    if(typeof key==='string'&&key.endsWith('_bp')&&typeof value==='number')return Math.round(value/100)+'%';
    if(typeof value==='string'&&/^\\d{4}-\\d{2}-\\d{2}T/.test(value))return formatClock(value);
    return String(value);
  }
  function highlightHeadline(item){
    const title=typeof item.title==='string'?item.title.trim():'';
    const status=typeof item.status_label==='string'?item.status_label.trim():'';
    const kindLabel=typeof item.kind_label==='string'?item.kind_label.trim():'';
    const generic=!title||title===kindLabel||GENERIC_TITLES[title]===true;
    if(generic)return status||title||kindLabel;
    return title;
  }
  function highlightView(item){
    if(!record(item)||item.privacy_class==='withhold'||item.kind==='private_impression')return null;
    let detail=typeof item.detail==='string'?item.detail:'';
    const values=[];
    if(Array.isArray(item.values)){
      for(const entry of item.values){
        if(!record(entry))continue;
        if(entry.key==='intention'&&typeof entry.value==='string'){detail=entry.value;continue;}
        const text=formatScalar(entry.key,entry.value,entry.value_label);
        if(!text)continue;
        values.push({
          label:typeof entry.label==='string'?entry.label:String(entry.key||''),
          text:text,
        });
      }
    }
    const headline=highlightHeadline(item);
    const status=typeof item.status_label==='string'?item.status_label.trim():'';
    return{
      kind:typeof item.kind==='string'?item.kind:'',
      kindLabel:KIND_HEADINGS[item.kind]||item.kind_label||'记录',
      statusCode:typeof item.status_code==='string'?item.status_code:'',
      title:headline,
      status:status&&status!==headline?status:'',
      detail:detail,
      when:typeof item.occurred_at==='string'?item.occurred_at:'',
      values:values,
    };
  }
  function sectionData(snapshot,sectionId){
    const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections[sectionId]:null;
    if(!record(section)||section.state==='unavailable')return null;
    return record(section.data)?section.data:null;
  }
  function visibleHighlights(snapshot,sectionId,kinds=null){
    const data=sectionData(snapshot,sectionId);
    if(!data||!Array.isArray(data.highlights))return [];
    return data.highlights.filter(item=>record(item)&&(!kinds||kinds.includes(item.kind)))
      .map(highlightView).filter(Boolean);
  }
  function newestFirst(items){
    return items.slice().sort((a,b)=>{
      const first=Date.parse(a.when),second=Date.parse(b.when);
      return (Number.isFinite(second)?second:0)-(Number.isFinite(first)?first:0);
    });
  }
  function lifeView(snapshot){
    const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections.overview_life:null;
    const state=record(section)&&STATE_LABELS[section.state]?section.state:'unavailable';
    if(state==='unavailable')return {state,activities:[],timeline:[]};
    return {
      state,
      activities:newestFirst(visibleHighlights(snapshot,'overview_life',['plan'])),
      timeline:newestFirst(visibleHighlights(snapshot,'overview_life',[
        'world_occurrence','outcome_observation','experience','life_arc','aspiration','biographical_coordinate',
      ])),
    };
  }
  function nowStory(snapshot){
    const lines=[];
    const add=(label,sectionId,kinds,empty,unavailable)=>{
      const data=sectionData(snapshot,sectionId);
      const view=newestFirst(visibleHighlights(snapshot,sectionId,kinds))[0];
      lines.push({
        label,
        text:view?[view.title,view.status].filter(Boolean).join(' · '):(data?empty:unavailable),
        detail:view?view.detail:'',
        missing:!view,
      });
    };
    add('活动与计划','overview_life',['plan'],'暂无可展示的活动或计划','生活状态暂时不可用');
    add('人在哪','overview_life',['location'],'暂无可展示的位置记录','位置状态暂时不可用');
    add('身体状态','overview_life',['resource'],'暂无可展示的身体状态','身体状态暂时不可用');
    add('情绪记录','facts_memory_inner',['affect_episode'],'暂无可展示的情绪记录','情绪记录暂时不可用');
    add('关系状态','relationship_lifecycle',['relationship_state'],'暂无可展示的关系记录','关系状态暂时不可用');
    add('互动状态','operations',['response_expectation'],'暂无可展示的互动状态','互动状态暂时不可用');
    return lines;
  }
  function isNoiseMetric(metric){
    const key=typeof metric.key==='string'?metric.key:'';
    const label=typeof metric.label==='string'?metric.label:'';
    return /proposal_ids$|_transitions$|_proposals$/.test(key)||label.includes('提议标识')||label.endsWith('变更')||label.endsWith('提议');
  }
  function metaLabel(key){
    return META_LABELS[key]||key;
  }
  function dataFieldLabel(key){
    if(key==='typed_change_terminals'||key==='qualification_status'||key==='qualification_label'||key==='notice_count'||key==='expression_episode'||key==='semantic_recall')return null;
    return DATA_FIELD_LABELS[key]||null;
  }
  function metricGroups(metrics){
    const grouped={active:[],zero:[]};
    if(!Array.isArray(metrics))return grouped;
    for(const metric of metrics){
      if(!record(metric)||isNoiseMetric(metric))continue;
      (metric.count===0?grouped.zero:grouped.active).push(metric);
    }
    return grouped;
  }
  return{
    DATA_URL,
    ROOM_MESSAGE_TYPE,
    ROOM_MESSAGE_VERSION,
    SCHEMA_VERSION,
    capture,
    captureStateLabel,
    dataFieldLabel,
    formatClock,
    formatRelative,
    highlightView,
    lifeView,
    visibleHighlights,
    metaLabel,
    nowStory,
    roomMessageFrom,
    roomOverlayText,
    sectionHeading,
    sectionLabel,
    metricGroups,
    snapshotFromCapture,
    stateLabel,
    unavailableRoomMessage,
    validateSnapshot,
  };
})();
if(typeof module!=='undefined'&&module.exports)module.exports=DashboardHomeClient;
if(typeof window!=='undefined')window.DashboardHomeClient=DashboardHomeClient;
if(typeof document!=='undefined'){
  const byId=id=>document.getElementById(id);
  const isRecord=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
  const sectionGrid=byId('sectionGrid');
  let lastSnapshot=null;
  let snapshotEtag=null;
  let loading=false;
  let recording=false;
  let focusArea='all';
  let previousScroll=0;
  const focusAreas=['all','now','life','memory','emotion','relationships'];
  const element=(tag,className,text)=>{
    const node=document.createElement(tag);
    if(className)node.className=className;
    if(text!==undefined)node.textContent=String(text);
    return node;
  };
  function setCaptureState(state){
    const badge=byId('captureState');
    if(badge){
      badge.textContent=DashboardHomeClient.captureStateLabel(state);
      badge.classList.toggle('error',state!=='ready');
    }
    const notice=byId('captureNotice');
    if(notice){
      notice.hidden=state==='ready';
      notice.textContent=state==='stale'
        ?'连接暂时中断。以下保留上次同步的记录，可能已不是最新状态。'
        :state==='unavailable'?'暂时无法读取生活状态，请稍后重试。':'';
    }
  }
  function applyPresentation(){
    document.body.classList.toggle('recording',recording);
    document.body.dataset.focus=focusArea;
    byId('recordingToggle').textContent=recording?'退出录制模式':'录制模式';
    byId('recordingToggle').setAttribute('aria-pressed',String(recording));
    byId('recordingTools').hidden=!recording;
    byId('recordingFocus').value=focusArea;
    for(const panel of document.querySelectorAll('[data-focus-area]')){
      panel.hidden=recording&&focusArea!=='all'&&panel.dataset.focusArea!==focusArea;
    }
  }
  function toggleRecording(){
    if(!recording)previousScroll=window.scrollY||0;
    recording=!recording;
    if(!recording)focusArea='all';
    applyPresentation();
    window.scrollTo(0,recording?0:previousScroll);
  }
  byId('recordingToggle').addEventListener('click',toggleRecording);
  byId('recordingFocus').addEventListener('change',event=>{
    focusArea=focusAreas.includes(event.target.value)?event.target.value:'all';
    applyPresentation();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&recording)toggleRecording();
  });
  function renderClocks(snapshot){
    const clock=DashboardHomeClient.formatClock(snapshot.logical_time);
    const generated=DashboardHomeClient.formatClock(snapshot.generated_at);
    byId('headerClock').textContent=clock?'世界时间 '+clock:'世界时间暂无记录';
    byId('worldClock').textContent=generated?'快照生成于 '+generated:'';
  }
  function renderNowStory(snapshot){
    const parent=byId('nowStory');
    parent.replaceChildren();
    for(const line of DashboardHomeClient.nowStory(snapshot)){
      const card=element('div','now-line'+(line.missing?' missing':''));
      card.append(element('span','k',line.label),element('span','v',line.text));
      // Full intention belongs to the life card; the present stays a summary.
      parent.appendChild(card);
    }
  }
  function empty(parent,text){parent.appendChild(element('p','empty-state',text));}
  function appendMetricGrid(parent,metrics){
    const groups=DashboardHomeClient.metricGroups(metrics);
    function grid(items){
      const tiles=element('div','metric-grid');
      for(const metric of items){
        if(typeof metric.count!=='number'||typeof metric.label!=='string')continue;
        const tile=element('div','metric');
        tile.append(element('span','',metric.label),element('strong','',metric.count));
        tiles.appendChild(tile);
      }
      return tiles;
    }
    if(groups.active.length)parent.appendChild(grid(groups.active));
    if(groups.zero.length){
      const details=element('details','zero-metrics');
      details.append(element('summary','','其余为零的计数 ('+groups.zero.length+')'),grid(groups.zero));
      parent.appendChild(details);
    }
  }
  function appendHighlight(parent,view){
    const card=element('article','highlight');
    card.dataset.status=view.statusCode;
    const top=element('div','highlight-top');
    top.appendChild(element('span','kind-tag',view.kindLabel));
    if(view.when)top.appendChild(element('time','when',DashboardHomeClient.formatClock(view.when)));
    card.append(top,element('h3','',view.title||view.kindLabel));
    if(view.status)card.appendChild(element('span','status-pill',view.status));
    if(view.detail)card.appendChild(element('p','detail',view.detail));
    if(view.values.length){
      const chips=element('div','chips');
      for(const value of view.values)chips.appendChild(element('span','chip',value.label+' '+value.text));
      card.appendChild(chips);
    }
    parent.appendChild(card);
  }
  function appendHighlights(parent,views){
    const list=element('div','highlight-list');
    for(const view of views)appendHighlight(list,view);
    parent.appendChild(list);
  }
  function sectionCard(title,section,area){
    const card=element('section','section-card');
    if(area)card.dataset.focusArea=area;
    const head=element('div','section-head');
    const state=section&&typeof section.state==='string'?section.state:'unavailable';
    const badge=element('span','section-state',DashboardHomeClient.stateLabel(state));
    badge.dataset.state=state;
    head.append(element('h2','',title),badge);
    const body=element('div','section-body');
    card.append(head,body);
    return {card,body};
  }
  function appendCoverage(parent,section){
    if(isRecord(section&&section.coverage)&&section.coverage.truncated){
      parent.appendChild(element('p','truncated','这里只展示部分摘要，未列出的记录不代表不存在。'));
    }
  }
  function renderLife(snapshot){
    const section=snapshot.sections.overview_life;
    const {card,body}=sectionCard('生活进展',section,'life');
    const view=DashboardHomeClient.lifeView(snapshot);
    body.appendChild(element('p','section-note','计划尚未发生；活动结束也不代表目标已经达成。'));
    if(view.state==='unavailable')empty(body,'生活记录暂时不可用。');
    else{
      const columns=element('div','life-columns');
      const activities=element('div','life-column');
      activities.appendChild(element('h3','section-subtitle','活动与计划'));
      if(view.activities.length)appendHighlights(activities,view.activities);
      else empty(activities,'暂无可展示的活动或计划。');
      const history=element('div','life-column');
      history.append(element('h3','section-subtitle','最近的生活记录'),element('p','timeline-caption','按记录时间排列，仅显示摘要；相邻记录不表示因果关系。'));
      if(view.timeline.length){
        const list=element('ol','timeline');
        for(const item of view.timeline){
          const row=element('li','');
          const dot=element('span','timeline-dot');
          dot.setAttribute('aria-hidden','true');
          row.appendChild(dot);
          appendHighlight(row,item);
          list.appendChild(row);
        }
        history.appendChild(list);
      }else empty(history,'暂无可展示的生活进展记录。');
      columns.append(activities,history);
      body.appendChild(columns);
      appendCoverage(body,section);
    }
    sectionGrid.appendChild(card);
  }
  function renderDomain(snapshot,{title,sectionId,area,kinds,metricKeys,note}){
    const section=snapshot.sections[sectionId];
    const {card,body}=sectionCard(title,section,area);
    body.appendChild(element('p','section-note',note));
    const data=isRecord(section&&section.data)?section.data:null;
    if(!data||section.state==='unavailable')empty(body,title+'记录暂时不可用。');
    else{
      const views=DashboardHomeClient.visibleHighlights(snapshot,sectionId,kinds);
      if(views.length)appendHighlights(body,views);
      else empty(body,'暂无可展示的'+title+'摘要。');
      const metrics=Array.isArray(data.metrics)?data.metrics:[];
      appendMetricGrid(body,metrics.filter(item=>isRecord(item)&&(!metricKeys||metricKeys.includes(item.key))));
      appendCoverage(body,section);
    }
    sectionGrid.appendChild(card);
  }
  function renderOperations(snapshot){
    const details=element('details','operator-details operator-only');
    details.appendChild(element('summary','','运维与其他记录'));
    const grid=element('div','operator-grid');
    for(const id of ['operations','perception_media','authority_privacy','ledger_qualification','runtime_operations']){
      const section=snapshot.sections[id];
      if(!isRecord(section))continue;
      const {card,body}=sectionCard(DashboardHomeClient.sectionHeading(id,section),section,null);
      const data=isRecord(section.data)?section.data:{};
      if(section.state==='unavailable')empty(body,'这部分记录暂时不可用。');
      else{
        if(typeof data.qualification_label==='string')body.appendChild(element('p','section-note',data.qualification_label));
        appendMetricGrid(body,data.metrics);
        appendHighlights(body,DashboardHomeClient.visibleHighlights(snapshot,id));
        for(const signal of Array.isArray(data.signals)?data.signals:[]){
          if(!isRecord(signal))continue;
          body.appendChild(element('p','section-note',String(signal.label||'状态')+' · '+DashboardHomeClient.stateLabel(signal.state)));
        }
        for(const notice of Array.isArray(data.notices)?data.notices:[]){
          if(!isRecord(notice))continue;
          body.appendChild(element('p','section-note',String(notice.signal_label||notice.label||'提示')+' · '+String(notice.reason_label||'暂时不可用')));
        }
        for(const [key,value] of Object.entries(data)){
          const label=DashboardHomeClient.dataFieldLabel(key);
          if(label&&typeof value==='number')body.appendChild(element('p','section-note',label+' · '+value));
        }
        appendCoverage(body,section);
      }
      const coverage=section.coverage;
      if(isRecord(coverage)){
        const info=element('details','section-contract');
        info.append(element('summary','','技术细节'),element('p','section-note','已展示 '+coverage.included_count+' / 已知 '+coverage.known_count+' 条摘要'));
        body.appendChild(info);
      }
      grid.appendChild(card);
    }
    details.appendChild(grid);
    sectionGrid.appendChild(details);
  }
  function renderSnapshot(snapshot){
    renderClocks(snapshot);
    renderNowStory(snapshot);
    sectionGrid.replaceChildren();
    renderLife(snapshot);
    renderDomain(snapshot,{
      title:'记忆',sectionId:'facts_memory_inner',area:'memory',
      kinds:['fact','memory_candidate','character_core'],metricKeys:['facts','memory_candidates'],
      note:'候选、保留与遗忘分别记录。记忆候选不等于已确认事实。',
    });
    renderDomain(snapshot,{
      title:'情绪',sectionId:'facts_memory_inner',area:'emotion',
      kinds:['affect_episode','affect_baseline','appraisal'],metricKeys:['appraisals','affect_episodes'],
      note:'这里是已记录的情绪与情境评估摘要，不是完整内心独白。',
    });
    renderDomain(snapshot,{
      title:'关系',sectionId:'relationship_lifecycle',area:'relationships',kinds:null,
      metricKeys:['relationship_states','relationship_commitments','npcs','private_impressions'],
      note:'承诺、关系与人物各有记录。私人印象只显示数量。',
    });
    renderOperations(snapshot);
    applyPresentation();
    setCaptureState('ready');
  }
  async function loadDashboardHome(){
    if(loading)return;
    loading=true;
    try{
      const result=await DashboardHomeClient.capture(window.fetch.bind(window),snapshotEtag);
      const snapshot=DashboardHomeClient.snapshotFromCapture(result,lastSnapshot);
      if(result.kind==='not_modified'){
        setCaptureState('ready');
        return;
      }
      snapshotEtag=result.etag;
      lastSnapshot=snapshot;
      renderSnapshot(snapshot);
    }catch(error){
      // The payload and upstream error never become visible page content.
      setCaptureState(lastSnapshot?'stale':'unavailable');
      if(!lastSnapshot){
        renderNowStory({sections:{}});
        sectionGrid.replaceChildren();
        empty(sectionGrid,'尚未取得可验证的生活快照。');
      }
    }finally{loading=false;}
  }
  loadDashboardHome();
  setInterval(loadDashboardHome,15000);
}
"""


__all__ = [
    "DASHBOARD_APP_JS",
    "DASHBOARD_HTML",
    "DASHBOARD_SESSION_COOKIE",
    "DASHBOARD_SESSION_TTL_SECONDS",
    "DashboardSessionCodec",
    "LOGIN_HTML",
    "UNAVAILABLE_HTML",
]
