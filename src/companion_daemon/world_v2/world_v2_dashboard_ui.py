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
<title>沈知栀 · 现在</title><style>
:root{font-family:"PingFang SC",system-ui,sans-serif;color:#3f342d;background:#d9cdbc}
*{box-sizing:border-box}
body{margin:0;min-height:100vh}
.bar{padding:14px 24px;background:#4d3b34;color:#fff8ea;display:flex;justify-content:space-between;align-items:center;gap:16px}
.bar h1{font-size:18px;margin:0}
.header-clock{margin:4px 0 0;font-size:12px;color:#e8d7c4}
.bar-actions{display:flex;align-items:center;gap:14px}
.capture-state{font-size:12px;color:#e8d7c4}
.logout{margin:0}
.logout button{border:1px solid #d9cdbc;border-radius:5px;background:transparent;color:#fff8ea;padding:5px 9px;font:12px inherit;cursor:pointer}
.wrap{max-width:1440px;margin:auto;padding:22px;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(330px,.72fr);align-items:start;gap:18px}
.room,.panel,.section-card{background:#f7eedf;border:3px solid #684f42;box-shadow:5px 5px 0 #b79c84}
.room{position:relative;overflow:hidden;aspect-ratio:7/4}
.room iframe{display:block;border:0;background:#211b1a;image-rendering:pixelated;pointer-events:none;position:absolute;top:0;left:0;width:1120px;height:640px;transform-origin:top left}
.room-overlay{position:absolute;inset:0;display:grid;place-items:center;background:rgba(33,27,26,.58);color:#fff8ea;font-size:15px;z-index:1}
.room-overlay[hidden]{display:none}
.room-edit{position:absolute;z-index:2;top:10px;right:10px;padding:7px 10px;border:1px solid #fff3d5;border-radius:6px;background:rgba(77,59,52,.88);color:#fff8ea;font-size:12px;text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.28)}
.room-edit:focus-visible,.logout button:focus-visible{outline:3px solid #e8c568;outline-offset:2px}
.side{display:grid;gap:18px}
.panel,.section-card{padding:16px}
.panel h2,.section-card h2{margin:0;font-size:16px}
.world-clock{margin:8px 0 0;font-size:13px;color:#6f584d}
.now-story{display:grid;gap:8px;margin-top:12px}
.now-line{padding:10px 12px;background:#fff8eb;border:1px solid #d5c3b1}
.now-line .k{display:block;font-size:11px;color:#725e53}
.now-line .v{display:block;margin-top:3px;font-size:15px;line-height:1.45}
.now-line .d{display:block;margin-top:4px;font-size:12px;color:#6f584d;line-height:1.45}
.now-line.missing .v{color:#80685b;font-size:13px;font-weight:400}
.more-sections{grid-column:1/-1;display:grid;gap:18px}
.more-sections>summary{cursor:pointer;color:#735d50;font-size:13px;padding:8px 0}
.tech-details{margin-top:12px;border-top:1px dashed #cbb6a3;padding-top:10px}
.tech-details summary,.zero-metrics summary,.section-contract summary{cursor:pointer;color:#735d50;font-size:12px}
.meta,.tree{margin:10px 0 0;display:grid;grid-template-columns:minmax(110px,.42fr) minmax(0,1fr);gap:7px 10px}
.meta dt,.tree dt{font-size:11px;color:#80685b;overflow-wrap:anywhere}
.meta dd,.tree dd{margin:0;min-width:0;overflow-wrap:anywhere}
.tree ul{margin:0;padding-left:20px}
.tree code,.meta code{font:11px ui-monospace,SFMono-Regular,monospace}
.section-grid{grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:18px}
.section-card[data-section="runtime_operations"]{grid-column:1/-1}
.section-head{display:flex;justify-content:space-between;gap:12px;align-items:start;padding-bottom:12px;border-bottom:1px solid #d7c7b7}
.section-state{border-radius:999px;padding:3px 8px;background:#d8e4dc;color:#3d6d63;font-size:11px}
.section-state[data-state="unavailable"],.section-state[data-state="degraded"],.section-state[data-state="stale"]{background:#edd6d2;color:#90483f}
.section-state[data-state="empty"],.section-state[data-state="disabled"]{background:#e8e0d2;color:#766455}
.section-body{display:grid;gap:14px;padding-top:14px}
.section-subtitle{margin:0 0 7px;font-size:12px;color:#6f584d}
.section-note{margin:0;font-size:13px;line-height:1.5;color:#5c4a41}
.metric-grid,.signal-grid,.notice-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px}
.metric,.signal,.notice{min-width:0;border:1px solid #d5c3b1;background:#fff8eb;padding:8px 10px}
.metric span,.signal span,.notice span{display:block;color:#725e53;font-size:11px;overflow-wrap:anywhere}
.metric strong,.signal strong,.notice strong{display:block;margin-top:3px;font-size:16px}
.signal strong,.notice strong{font-size:13px;color:#3d6d63;line-height:1.4}
.highlight-list{display:grid;gap:8px}
.highlight{padding:10px 12px;background:#fff8eb;border:1px solid #d5c3b1}
.highlight-top{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.kind-tag{font-size:11px;color:#557f78}
.when{font-size:11px;color:#80685b;white-space:nowrap}
.highlight h3{margin:4px 0 0;font-size:15px;line-height:1.4}
.status-pill{display:inline-block;margin-top:6px;border-radius:999px;padding:2px 8px;background:#d8e4dc;color:#3d6d63;font-size:11px}
.highlight .detail{margin:6px 0 0;font-size:13px;color:#5c4a41;line-height:1.45}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:11px;background:#f0e4d4;padding:2px 7px;border-radius:999px}
.zero-metrics,.section-contract{border-top:1px dashed #cbb6a3;padding-top:10px}
.zero-metrics .metric-grid,.section-contract .tree{margin-top:10px}
.truncated{margin:0;font-size:12px;color:#6f584d}
.error{color:#c87b73}
@media(max-width:900px){.wrap{grid-template-columns:1fr}.section-grid{grid-template-columns:1fr}.section-card[data-section="runtime_operations"]{grid-column:auto}}
@media(max-width:520px){.bar{padding:12px 14px}.wrap{padding:14px}.capture-state{display:none}.metric-grid,.signal-grid,.notice-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style></head><body><header class="bar"><div><h1>沈知栀 · 现在</h1><p id="headerClock" class="header-clock"></p></div><div class="bar-actions"><span id="captureState" class="capture-state">正在打开</span><form class="logout" method="post" action="/world-v2/dashboard/logout"><button type="submit">退出</button></form></div></header>
<main class="wrap"><section class="room"><iframe id="roomVisual" src="/pixel-home/index.html?embed=1" title="知栀的房间" aria-label="知栀的房间" scrolling="no"></iframe><div id="roomOverlay" class="room-overlay">房间暂时看不到</div><a class="room-edit" href="/pixel-home/index.html?edit=1" target="_blank" rel="noopener" aria-label="在独立页面编辑渲染房间">✎ 编辑房间</a></section>
<aside class="side"><section class="panel"><h2>这一刻</h2><p id="worldClock" class="world-clock"></p><div id="nowStory" class="now-story"></div><details class="tech-details"><summary>技术信息</summary><dl id="snapshotMeta" class="meta"></dl></details></section></aside><div id="sectionGrid" class="section-grid" aria-live="polite"></div></main>
<script src="/world-v2/dashboard/app.js?v=world-v2-dashboard-home.1-ui4" defer></script></body></html>"""


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
    facts_memory_inner:'心里',
    relationship_lifecycle:'身边的人',
    operations:'说话和行动',
    perception_media:'看见的和照片',
    authority_privacy:'权限和隐私',
    ledger_qualification:'账本核对',
    runtime_operations:'系统现在怎么样',
  };
  const KIND_HEADINGS={
    plan:'在做什么',
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
    return STATE_LABELS[state]||state;
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
    if(!record(item))return null;
    const values=[];
    if(Array.isArray(item.values)){
      for(const entry of item.values){
        if(!record(entry))continue;
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
      kindLabel:KIND_HEADINGS[item.kind]||item.kind_label||item.kind||'条目',
      title:headline,
      status:status&&status!==headline?status:'',
      detail:typeof item.detail==='string'?item.detail:'',
      when:typeof item.occurred_at==='string'?item.occurred_at:'',
      values:values,
    };
  }
  function latestHighlight(highlights,kind){
    if(!Array.isArray(highlights))return null;
    for(let index=highlights.length-1;index>=0;index-=1){
      const item=highlights[index];
      if(record(item)&&item.kind===kind)return item;
    }
    return null;
  }
  function sectionData(snapshot,sectionId){
    const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections[sectionId]:null;
    return record(section)&&record(section.data)?section.data:null;
  }
  function nowStory(snapshot){
    const lines=[];
    const add=(label,item,empty)=>{
      const view=highlightView(item);
      if(!view||!view.title){
        lines.push({label:label,text:empty,detail:'',missing:true});
        return;
      }
      const bits=[view.title];
      if(view.status)bits.push(view.status);
      lines.push({label:label,text:bits.join(' · '),detail:view.detail,missing:false});
    };
    const life=sectionData(snapshot,'overview_life');
    const inner=sectionData(snapshot,'facts_memory_inner');
    const rel=sectionData(snapshot,'relationship_lifecycle');
    const ops=sectionData(snapshot,'operations');
    const lifeHighlights=life&&life.highlights;
    const innerHighlights=inner&&inner.highlights;
    const relHighlights=rel&&rel.highlights;
    const opsHighlights=ops&&ops.highlights;
    add('在做什么',latestHighlight(lifeHighlights,'plan'),'还没记下正在做的事');
    add('人在哪',latestHighlight(lifeHighlights,'location'),'位置还没记下');
    add('身子',latestHighlight(lifeHighlights,'resource'),'体力这些还没记下');
    add('心情',latestHighlight(innerHighlights,'affect_episode'),'这会儿没有记下的心情');
    add('和你',latestHighlight(relHighlights,'relationship_state'),'关系还没记下');
    add('对你的印象',latestHighlight(relHighlights,'private_impression'),'还没有记下对你的印象');
    add(
      '她在等',
      latestHighlight(opsHighlights,'response_expectation')||latestHighlight(relHighlights,'interaction_bid'),
      '这轮没有在等你回',
    );
    add(
      '最近说话',
      latestHighlight(opsHighlights,'expression_plan')||latestHighlight(opsHighlights,'action'),
      '最近没有发出的话',
    );
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
    return DATA_FIELD_LABELS[key]||key;
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
  const captureState=byId('captureState');
  const headerClock=byId('headerClock');
  const worldClock=byId('worldClock');
  const nowStory=byId('nowStory');
  const snapshotMeta=byId('snapshotMeta');
  const sectionGrid=byId('sectionGrid');
  const roomFrame=byId('roomVisual');
  const roomHost=roomFrame?roomFrame.closest('.room'):null;
  const roomOverlay=byId('roomOverlay');
  const ROOM_FRAME_WIDTH=1120;
  const ROOM_FRAME_HEIGHT=640;
  const ROOM_FRAME_INSET=8;
  let roomMessage=DashboardHomeClient.unavailableRoomMessage();
  let lastSnapshot=null;
  let snapshotEtag=null;

  function fitRoomFrame(){
    if(!roomFrame||!roomHost)return;
    const scale=Math.max(0,Math.min(
      (roomHost.clientWidth-ROOM_FRAME_INSET*2)/ROOM_FRAME_WIDTH,
      (roomHost.clientHeight-ROOM_FRAME_INSET*2)/ROOM_FRAME_HEIGHT,
    ));
    const x=(roomHost.clientWidth-ROOM_FRAME_WIDTH*scale)/2;
    const y=(roomHost.clientHeight-ROOM_FRAME_HEIGHT*scale)/2;
    roomFrame.style.transform='translate('+x+'px,'+y+'px) scale('+scale+')';
  }
  function pushRoomMessage(){
    if(roomFrame&&roomFrame.contentWindow)roomFrame.contentWindow.postMessage(roomMessage,window.location.origin);
  }
  function setCaptureState(state,isError){
    if(!captureState)return;
    captureState.textContent=DashboardHomeClient.captureStateLabel(state);
    captureState.classList.toggle('error',Boolean(isError));
  }
  function showRoomMessage(message){
    roomMessage=message;
    const ready=message.state==='ready';
    if(roomOverlay){
      roomOverlay.hidden=ready;
      roomOverlay.textContent=DashboardHomeClient.roomOverlayText(message.state);
    }
    pushRoomMessage();
  }
  function appendScalar(parent,value){
    const code=document.createElement('code');
    code.textContent=value===null?'null':String(value);
    parent.appendChild(code);
  }
  function appendTree(parent,value){
    if(Array.isArray(value)){
      if(!value.length){appendScalar(parent,'[]');return;}
      const list=document.createElement('ul');
      for(const item of value){
        const row=document.createElement('li');
        appendTree(row,item);
        list.appendChild(row);
      }
      parent.appendChild(list);
      return;
    }
    if(value!==null&&typeof value==='object'){
      const list=document.createElement('dl');
      list.className='tree';
      for(const [key,item] of Object.entries(value)){
        const term=document.createElement('dt');
        term.textContent=DashboardHomeClient.metaLabel(key);
        const detail=document.createElement('dd');
        appendTree(detail,item);
        list.append(term,detail);
      }
      parent.appendChild(list);
      return;
    }
    appendScalar(parent,value);
  }
  function renderClocks(snapshot){
    const clock=DashboardHomeClient.formatClock(snapshot.logical_time);
    const generated=DashboardHomeClient.formatClock(snapshot.generated_at);
    if(headerClock)headerClock.textContent=clock?('世界时间 '+clock):'';
    if(!worldClock)return;
    if(clock&&generated)worldClock.textContent='世界时间 '+clock+' · 页面生成于 '+generated;
    else if(clock)worldClock.textContent='世界时间 '+clock;
    else if(generated)worldClock.textContent='页面生成于 '+generated;
    else worldClock.textContent='';
  }
  function renderNowStory(snapshot){
    if(!nowStory)return;
    nowStory.replaceChildren();
    const lines=DashboardHomeClient.nowStory(snapshot);
    if(!lines.length){
      const empty=document.createElement('p');
      empty.className='now-empty';
      empty.textContent='这一刻还没有可看的摘要。';
      nowStory.appendChild(empty);
      return;
    }
    for(const line of lines){
      const card=document.createElement('div');
      card.className='now-line';
      if(line.missing)card.classList.add('missing');
      const kind=document.createElement('span');
      kind.className='k';
      kind.textContent=line.label;
      const value=document.createElement('span');
      value.className='v';
      value.textContent=line.text;
      card.append(kind,value);
      if(line.detail){
        const detail=document.createElement('span');
        detail.className='d';
        detail.textContent=line.detail;
        card.appendChild(detail);
      }
      nowStory.appendChild(card);
    }
  }
  function renderMeta(snapshot){
    if(!snapshotMeta)return;
    snapshotMeta.replaceChildren();
    const fields=['logical_time','generated_at','world_id','schema_version','policy_version','snapshot_hash','owner','cursor'];
    for(const key of fields){
      if(!(key in snapshot))continue;
      const term=document.createElement('dt');
      term.textContent=DashboardHomeClient.metaLabel(key);
      const detail=document.createElement('dd');
      if(key==='logical_time'||key==='generated_at'){
        detail.textContent=DashboardHomeClient.formatClock(snapshot[key])||String(snapshot[key]);
      }else{
        appendTree(detail,snapshot[key]);
      }
      snapshotMeta.append(term,detail);
    }
  }
  function appendMetricGrid(parent,metrics){
    const groups=DashboardHomeClient.metricGroups(metrics);
    function buildGrid(items){
      const grid=document.createElement('div');
      grid.className='metric-grid';
      for(const metric of items){
        const tile=document.createElement('div');
        tile.className='metric';
        const label=document.createElement('span');
        label.textContent=typeof metric.label==='string'?metric.label:String(metric.key||'计数');
        const value=document.createElement('strong');
        value.textContent=String(metric.count??0);
        tile.append(label,value);
        grid.appendChild(tile);
      }
      return grid;
    }
    if(groups.active.length)parent.appendChild(buildGrid(groups.active));
    if(groups.zero.length){
      const details=document.createElement('details');
      details.className='zero-metrics';
      const summary=document.createElement('summary');
      summary.textContent='其余为零的计数 ('+groups.zero.length+')';
      details.append(summary,buildGrid(groups.zero));
      parent.appendChild(details);
    }
  }
  function appendSignalGrid(parent,signals){
    if(!Array.isArray(signals)||!signals.length)return;
    const attention=[];
    const ok=[];
    for(const signal of signals){
      if(!isRecord(signal))continue;
      if(signal.state==='ready')ok.push(signal);
      else attention.push(signal);
    }
    function paint(items){
      const grid=document.createElement('div');
      grid.className='signal-grid';
      for(const signal of items){
        const tile=document.createElement('div');
        tile.className='signal';
        const label=document.createElement('span');
        label.textContent=typeof signal.label==='string'?signal.label:String(signal.key||'信号');
        const value=document.createElement('strong');
        value.textContent=typeof signal.state_label==='string'?signal.state_label:DashboardHomeClient.stateLabel(signal.state);
        tile.append(label,value);
        grid.appendChild(tile);
      }
      return grid;
    }
    if(attention.length){
      const title=document.createElement('h3');
      title.className='section-subtitle';
      title.textContent='现在不太稳';
      parent.append(title,paint(attention));
    }
    if(ok.length){
      const details=document.createElement('details');
      details.className='zero-metrics';
      const summary=document.createElement('summary');
      summary.textContent='其余正常 ('+ok.length+')';
      details.append(summary,paint(ok));
      parent.appendChild(details);
    }
  }
  function appendNoticeGrid(parent,notices){
    if(!Array.isArray(notices)||!notices.length)return;
    const title=document.createElement('h3');
    title.className='section-subtitle';
    title.textContent='需要留意';
    const grid=document.createElement('div');
    grid.className='notice-grid';
    for(const notice of notices){
      if(!isRecord(notice))continue;
      const tile=document.createElement('div');
      tile.className='notice';
      const label=document.createElement('span');
      label.textContent=String(notice.signal_label||notice.label||'提示');
      const reason=document.createElement('strong');
      reason.textContent=String(notice.reason_label||DashboardHomeClient.stateLabel(notice.severity)||'');
      tile.append(label,reason);
      grid.appendChild(tile);
    }
    parent.append(title,grid);
  }
  function appendHighlightCard(parent,item){
    const view=DashboardHomeClient.highlightView(item);
    if(!view)return;
    const card=document.createElement('article');
    card.className='highlight';
    const top=document.createElement('div');
    top.className='highlight-top';
    const kind=document.createElement('span');
    kind.className='kind-tag';
    kind.textContent=view.kindLabel;
    top.appendChild(kind);
    if(view.when){
      const when=document.createElement('span');
      when.className='when';
      when.textContent=DashboardHomeClient.formatRelative(view.when);
      top.appendChild(when);
    }
    const title=document.createElement('h3');
    title.textContent=view.title||view.kindLabel;
    card.append(top,title);
    if(view.status){
      const status=document.createElement('span');
      status.className='status-pill';
      status.textContent=view.status;
      card.appendChild(status);
    }
    if(view.detail){
      const detail=document.createElement('p');
      detail.className='detail';
      detail.textContent=view.detail;
      card.appendChild(detail);
    }
    if(view.values.length){
      const chips=document.createElement('div');
      chips.className='chips';
      for(const entry of view.values){
        const chip=document.createElement('span');
        chip.className='chip';
        chip.textContent=entry.label?entry.label+' '+entry.text:entry.text;
        chips.appendChild(chip);
      }
      card.appendChild(chips);
    }
    parent.appendChild(card);
  }
  function appendHighlights(parent,highlights){
    if(!Array.isArray(highlights)||!highlights.length)return;
    const groups=[];
    const index=new Map();
    for(const item of highlights){
      if(!isRecord(item))continue;
      const key=String(item.kind_label||item.kind||'条目');
      if(!index.has(key)){
        const group={label:key,items:[]};
        index.set(key,group);
        groups.push(group);
      }
      index.get(key).items.push(item);
    }
    for(const group of groups){
      const title=document.createElement('h3');
      title.className='section-subtitle';
      title.textContent=group.label;
      const list=document.createElement('div');
      list.className='highlight-list';
      for(const item of group.items)appendHighlightCard(list,item);
      parent.append(title,list);
    }
  }
  function appendLabeledValues(parent,data){
    const tiles=[];
    for(const [key,value] of Object.entries(data||{})){
      const label=DashboardHomeClient.dataFieldLabel(key);
      if(!label)continue;
      if(value!==null&&typeof value==='object')continue;
      tiles.push([label,value]);
    }
    if(!tiles.length)return;
    const grid=document.createElement('div');
    grid.className='metric-grid';
    for(const [label,value] of tiles){
      const tile=document.createElement('div');
      tile.className='metric';
      const name=document.createElement('span');
      name.textContent=label;
      const strong=document.createElement('strong');
      strong.textContent=String(value);
      tile.append(name,strong);
      grid.appendChild(tile);
    }
    parent.appendChild(grid);
  }
  function appendTerminals(parent,terminals){
    if(!Array.isArray(terminals)||!terminals.length)return;
    const title=document.createElement('h3');
    title.className='section-subtitle';
    title.textContent='已经结束的关系变化';
    const list=document.createElement('div');
    list.className='highlight-list';
    for(const item of terminals){
      if(!isRecord(item))continue;
      const card=document.createElement('article');
      card.className='highlight';
      const top=document.createElement('div');
      top.className='highlight-top';
      const kind=document.createElement('span');
      kind.className='kind-tag';
      kind.textContent=item.status_label||item.status||'关系变化';
      top.appendChild(kind);
      if(item.occurred_at){
        const when=document.createElement('span');
        when.className='when';
        when.textContent=DashboardHomeClient.formatRelative(item.occurred_at);
        top.appendChild(when);
      }
      const heading=document.createElement('h3');
      heading.textContent=item.target_stage_label||item.target_stage||'关系阶段';
      card.append(top,heading);
      list.appendChild(card);
    }
    parent.append(title,list);
  }
  function appendRuntimeExtras(parent,data){
    const extras=[];
    if(isRecord(data.expression_episode)&&typeof data.expression_episode.mode_label==='string'){
      extras.push(['表达节奏',data.expression_episode.mode_label]);
    }
    if(isRecord(data.semantic_recall)&&typeof data.semantic_recall.semantic_embedding_label==='string'){
      extras.push(['语义记忆',data.semantic_recall.semantic_embedding_label]);
    }
    if(!extras.length)return;
    const grid=document.createElement('div');
    grid.className='signal-grid';
    for(const [label,value] of extras){
      const tile=document.createElement('div');
      tile.className='signal';
      const name=document.createElement('span');
      name.textContent=label;
      const strong=document.createElement('strong');
      strong.textContent=value;
      tile.append(name,strong);
      grid.appendChild(tile);
    }
    parent.appendChild(grid);
  }
  function appendSectionContract(parent,section){
    const contract={};
    for(const [key,value] of Object.entries(section||{})){
      if(key==='label'||key==='data')continue;
      contract[key]=value;
    }
    const details=document.createElement('details');
    details.className='section-contract';
    const summary=document.createElement('summary');
    summary.textContent='技术细节';
    details.appendChild(summary);
    appendTree(details,contract);
    parent.appendChild(details);
  }
  function appendSectionData(parent,section){
    const data=isRecord(section&&section.data)?section.data:null;
    if(!data)return;
    if(typeof data.qualification_label==='string'&&data.qualification_label){
      const note=document.createElement('p');
      note.className='section-note';
      note.textContent=data.qualification_label;
      parent.appendChild(note);
    }
    appendMetricGrid(parent,data.metrics);
    appendSignalGrid(parent,data.signals);
    appendNoticeGrid(parent,data.notices);
    appendRuntimeExtras(parent,data);
    appendLabeledValues(parent,data);
    appendTerminals(parent,data.typed_change_terminals);
    appendHighlights(parent,data.highlights);
    if(isRecord(section.coverage)&&section.coverage.truncated){
      const note=document.createElement('p');
      note.className='truncated';
      note.textContent='这里只列出最近几条，还有没展开的。';
      parent.appendChild(note);
    }
  }
  function renderSections(snapshot){
    if(!sectionGrid)return;
    sectionGrid.replaceChildren();
    const secondary=document.createElement('details');
    secondary.className='more-sections';
    const secondarySummary=document.createElement('summary');
    secondarySummary.textContent='系统内部核对';
    secondary.appendChild(secondarySummary);
    let hasSecondary=false;
    for(const [sectionId,section] of Object.entries(snapshot.sections)){
      if(sectionId==='room')continue;
      const card=document.createElement('section');
      card.className='section-card';
      card.dataset.section=sectionId;
      const head=document.createElement('div');
      head.className='section-head';
      const title=document.createElement('h2');
      title.textContent=DashboardHomeClient.sectionHeading(sectionId,section);
      const state=document.createElement('span');
      state.className='section-state';
      const stateCode=section&&typeof section.state==='string'?section.state:'unavailable';
      state.textContent=DashboardHomeClient.stateLabel(stateCode);
      state.dataset.state=stateCode;
      head.append(title,state);
      card.appendChild(head);
      const body=document.createElement('div');
      body.className='section-body';
      appendSectionData(body,section);
      appendSectionContract(body,section);
      card.appendChild(body);
      if(sectionId==='authority_privacy'||sectionId==='ledger_qualification'){
        secondary.appendChild(card);
        hasSecondary=true;
      }else{
        sectionGrid.appendChild(card);
      }
    }
    if(hasSecondary)sectionGrid.appendChild(secondary);
  }
  function renderSnapshot(snapshot){
    renderClocks(snapshot);
    renderNowStory(snapshot);
    renderMeta(snapshot);
    renderSections(snapshot);
    setCaptureState('ready',false);
    showRoomMessage(DashboardHomeClient.roomMessageFrom(snapshot));
  }
  async function loadDashboardHome(){
    try{
      const result=await DashboardHomeClient.capture(window.fetch.bind(window),snapshotEtag);
      const snapshot=DashboardHomeClient.snapshotFromCapture(result,lastSnapshot);
      if(result.kind==='not_modified'){
        lastSnapshot=snapshot;
        setCaptureState('ready',false);
        showRoomMessage(DashboardHomeClient.roomMessageFrom(snapshot));
        return;
      }
      snapshotEtag=result.etag;
      lastSnapshot=snapshot;
      renderSnapshot(snapshot);
    }catch(error){
      console.error('Dashboard refresh failed',error instanceof Error?error.message:'unknown');
      const state=lastSnapshot?'stale':'unavailable';
      setCaptureState(state,true);
      showRoomMessage(
        lastSnapshot
          ? DashboardHomeClient.roomMessageFrom(lastSnapshot,state)
          : DashboardHomeClient.unavailableRoomMessage(state),
      );
    }
  }
  if(roomFrame){
    roomFrame.addEventListener('load',()=>{fitRoomFrame();pushRoomMessage();});
    fitRoomFrame();
    if(typeof ResizeObserver==='function')new ResizeObserver(fitRoomFrame).observe(roomHost);
    else window.addEventListener('resize',fitRoomFrame);
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
