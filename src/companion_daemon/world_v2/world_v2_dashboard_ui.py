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
from urllib.parse import urlsplit

from .dashboard_explorer_ui import EXPLORER_CSS, EXPLORER_HTML, EXPLORER_JS
from .dashboard_presentation import DASHBOARD_CSS


DASHBOARD_SESSION_COOKIE = "girl_agent_v2_dashboard_session"
DASHBOARD_SESSION_TTL_SECONDS = 8 * 60 * 60


def dashboard_http_origin(value: str) -> tuple[str, str, int] | None:
    """Parse the exact origin accepted by both dashboard HTTP hosts."""

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if (
        scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        return None
    return scheme, host, port or (443 if scheme == "https" else 80)


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
body{margin:0;min-height:100vh;display:grid;place-items:center;background:radial-gradient(circle at 50% 18%,#e9efe2 0,#f4f4ed 62%);color:#263d36;font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif}
main{width:min(420px,calc(100% - 32px));padding:32px;background:#fffef9;border:1px solid #dce2d7;border-radius:20px;box-shadow:0 12px 36px rgba(38,61,54,.07)}
h1{font-size:21px;font-weight:600;margin:0 0 8px;letter-spacing:.03em}label,input,button{display:block;width:100%}label{font-size:12px;color:#687970;margin-top:16px}input,button{margin-top:8px;padding:12px 14px;font:inherit;font-size:13px;box-sizing:border-box;border-radius:10px}input{border:1px solid #dce2d7;background:#fafbf6;color:#263d36}button{margin-top:18px;background:#315b4a;color:#fffef9;border:0;font-weight:500;cursor:pointer}p{line-height:1.7;font-size:13px;color:#687970;margin:0}
</style></head><body><main><h1>知栀看板</h1>
<p>请输入本机配置的 operator token。凭证只通过本次 POST 提交，不会写入 URL、页面脚本或浏览器存储。</p>
<form method="post" action="/world-v2/dashboard/session" autocomplete="off">
<label for="operator-token">Operator token</label><input id="operator-token" name="operator_token" type="password" required autocomplete="current-password">
<button type="submit">打开看板</button></form></main></body></html>"""


UNAVAILABLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>知栀看板还没准备好</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f4ed;color:#263d36;font-family:"PingFang SC",system-ui,sans-serif}main{max-width:420px;padding:28px 32px;background:#fffef9;border:1px solid #dce2d7;border-radius:18px;box-shadow:0 8px 28px rgba(38,61,54,.05)}h1{font-size:19px;margin:0 0 8px}p{margin:0;font-size:13px;line-height:1.7;color:#687970}</style></head><body>
<main><h1>看板还没准备好</h1><p>只读 World v2 host 尚未初始化。不会回退到旧运行时。</p></main>
</body></html>"""


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>沈知栀 · 生活现场</title><style>
</style></head><body><header class="bar"><div class="identity"><span class="monogram" aria-hidden="true">栀</span><div><h1>沈知栀 · 生活现场</h1><p class="eyebrow">A LIFE, CONTINUING</p></div></div><div class="bar-actions"><button id="recordingToggle" class="button primary" type="button" aria-pressed="false">录制模式</button><form class="logout operator-only" method="post" action="/world-v2/dashboard/logout"><button type="submit">退出登录</button></form></div></header>
<div class="status-bar"><div class="status-meta"><span id="captureState" class="capture-state" role="status">正在读取</span><p id="headerClock" class="header-clock"></p></div><span class="readonly">只读 · 不改变她的生活</span></div>
<p id="captureNotice" class="capture-notice" role="status" hidden></p>
<div id="recordingTools" class="recording-tools" hidden><label for="recordingFocus">聚焦区域</label><select id="recordingFocus"><option value="all">完整视图</option><option value="now">这一刻</option><option value="life">生活日程</option><option value="story">故事脉络</option><option value="media">照片</option><option value="memory">记忆</option><option value="emotion">情绪</option><option value="relationships">关系</option><option value="pending">未完成事项</option></select><span>仅改变显示；同步状态始终保留 · Esc 退出</span></div>
<main class="wrap"><div class="page-intro"><div><h2 id="viewTitle">她的生活，一眼展开。</h2><p id="viewDescription">一周的日常，正在生长的故事。点开一处，读得更近一点。</p></div><span class="page-index" id="viewIndex">LIFE / AT A GLANCE</span></div><section class="panel now-panel" data-focus-area="now"><div><p class="hero-label">THE PRESENT</p><h2>这一刻</h2><p class="hero-note">记录下来的这一刻</p><p id="worldClock" class="world-clock"></p></div><div id="nowStory" class="now-story"><p class="section-note">正在读取生活状态…</p></div></section><div id="sectionGrid" class="section-grid"></div><footer class="footer"><span>只展示经过授权的摘要，私密反思不在此呈现。</span><span class="operator-only">本机 owner 视图</span></footer></main>
<dialog id="detailDrawer" class="detail-drawer" aria-labelledby="drawerTitle"><div class="drawer-head"><div><p class="eyebrow">LOOK A LITTLE CLOSER</p><h2 id="drawerTitle">记录详情</h2></div><button type="button" id="closeDrawer" class="button" aria-label="关闭详情">关闭 ×</button></div><div id="drawerBody" class="drawer-body"></div></dialog><script src="/world-v2/dashboard/app.js?v=world-v2-dashboard-home.1-ui10" defer></script></body></html>"""

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
  async function capture(fetchSnapshot,etag=null,{timeoutMs=10000,week=null,page=null}={}){
    const headers={Accept:'application/json'};
    if(etag)headers['If-None-Match']=etag;
    const controller=new AbortController();
    let timer;
    const deadline=new Promise((_,reject)=>{timer=setTimeout(()=>{
      controller.abort();reject(new Error('dashboard snapshot timed out'));
    },timeoutMs);});
    const load=async()=>{
      const response=await fetchSnapshot(DATA_URL+((week!==null||page!==null)?'?'+[...(week?['week='+encodeURIComponent(week)]:[]),...(page!==null?['page='+encodeURIComponent(page)]:[])].join('&'):''),{credentials:'same-origin',headers,signal:controller.signal});
      if(response.status===304)return{kind:'not_modified',etag,snapshot:null};
      if(!response.ok)throw new Error('dashboard snapshot unavailable ('+response.status+')');
      const snapshot=validateSnapshot(await response.json());
      return{kind:'snapshot',etag:response.headers.get('etag'),snapshot};
    };
    try{return await Promise.race([load(),deadline]);}finally{clearTimeout(timer);}
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
    affect_baseline:'情绪底色',
    appraisal:'处境判断',
    attention:'注意力',
    interaction_bid:'互动期待',
    response_expectation:'回应期待',
    expectation_assessment:'等到了没有',
    private_impression:'对你的印象',
    relationship_state:'和你',
    relationship_signal:'关系信号',
    relationship_adjustment:'关系微调',
    expression_plan:'对你说的话',
    action:'发送回执',
    resource:'身子',
    goal:'目标',
    npc:'身边人物',
    life_ecology_schedule:'她的日子',
    external_perception:'刷到的内容',
    external_signal:'外部热点',
    photo_candidate:'相册随手拍',
    media_opportunity:'拍摄契机',
    media_plan:'拍摄计划',
    media_preview:'成片预览',
    media_delivery:'已发照片',
    media_inspection:'画面自检',
    character_core:'角色底色',
    fact:'已记事实',
    memory_candidate:'记忆留存',
    thread:'共同话题',
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
    const rawStatus=typeof item.status_label==='string'?item.status_label.trim():'';
    const status=({decayed:'已消退',preview:'预览'})[rawStatus]||rawStatus;
    const kindLabel=typeof item.kind_label==='string'?item.kind_label.trim():'';
    const detail=typeof item.detail==='string'?item.detail.trim():'';
    if(title==='character_media')return '生活随手拍与自拍';
    if(title==='life_share')return '日常分享随手拍';
    if(title==='一组表达')return '发给你的消息';
    if(/^[a-z0-9_.:-]+$/i.test(title)&&detail)return detail;
    const generic=!title||title===kindLabel||GENERIC_TITLES[title]===true;
    if(generic)return status||title||kindLabel;
    return title;
  }
  function readableText(value){
    if(typeof value!=='string')return {text:'',label:'',structured:false};
    try{
      const data=JSON.parse(value);
      if(record(data)||Array.isArray(data)){
        // A reported utterance keeps its original wording and attribution boundary.
        if(record(data)&&data.kind==='npc_statement'&&data.claim_authority==='speaker_report_only'&&typeof data.utterance==='string')return {text:data.utterance,label:'人物说法 · 不代表已核实事实',structured:true};
        return {text:'',label:'结构化记录',structured:true};
      }
    }catch(_e){}
    return {text:value,label:'',structured:false};
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
    const rawStatus=typeof item.status_label==='string'?item.status_label.trim():'';
    const status=({decayed:'已消退',preview:'预览'})[rawStatus]||rawStatus;
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
  function shiftWeek(week,days){
    const at=new Date(week+'T12:00:00Z');at.setUTCDate(at.getUTCDate()+days);
    return at.toISOString().slice(0,10);
  }
  function agendaKey(entry){return JSON.stringify([entry.index,entry.kind,entry.title,entry.starts_at,entry.ends_at,entry.detail]);}
  function agendaView(snapshot,filter='all',status='all'){
    const data=sectionData(snapshot,'overview_life');
    const agenda=data&&data.agenda;
    if(!record(agenda)||agenda.state==='unavailable')return null;
    const start=Date.parse(agenda.week_start+'T00:00:00+08:00');
    if(!Number.isFinite(start)||!Array.isArray(agenda.entries))return null;
    const entries=agenda.entries.filter(item=>record(item)&&item.content_state!=='withheld');
    const shown=entries.filter(item=>(status==='all'||item.status===status)&&(filter==='all'||item.kind===filter||filter==='intentions'&&['commitment','revisit','expectation'].includes(item.kind)));
    const coveredWorld=new Set();
    if(filter==='all'){
      for(const item of shown){
        if(item.kind==='experience'&&Array.isArray(item.related)){
          for(const rel of item.related)coveredWorld.add(rel);
        }
      }
    }
    const days=Array.from({length:7},(_,day)=>{
      const from=start+day*86400000,to=from+86400000;
      const windows=[],timed=[];
      for(const entry of shown){
        if(filter==='all'&&entry.kind==='world'&&(coveredWorld.has(entry.index)||entry.title==='世界事件'))continue;
        if(status==='all'&&entry.kind==='plan'&&entry.status==='已放弃')continue;
        const a=Date.parse(entry.starts_at),b=Date.parse(entry.ends_at);
        if(!Number.isFinite(a)||!Number.isFinite(b)||!(a<to&&(b>from||a===b&&a>=from)))continue;
        const segment={entry,start:Math.max(a,from),end:Math.min(b,to),lane:0,lanes:1};
        if(['period','window'].includes(entry.time_kind)||['outlook','commitment','revisit','expectation'].includes(entry.kind))windows.push(segment);
        else{
          segment.minute=(segment.start-from)/60000;
          segment.endMinute=Math.min(1440,Math.max((segment.end-from)/60000,segment.minute+36));
          timed.push(segment);
        }
      }
      timed.sort((a,b)=>a.minute-b.minute||a.endMinute-b.endMinute);
      let group=[],laneEnds=[],groupEnd=-1;
      const close=()=>{for(const segment of group)segment.lanes=laneEnds.length;group=[];laneEnds=[];};
      for(const segment of timed){
        if(segment.minute>=groupEnd)close();
        let lane=laneEnds.findIndex(end=>end<=segment.minute);
        if(lane<0)lane=laneEnds.length;
        segment.lane=lane;laneEnds[lane]=segment.endMinute;group.push(segment);
        groupEnd=Math.max(segment.endMinute,...laneEnds);
      }
      close();
      return {date:shiftWeek(agenda.week_start,day),windows,timed};
    });
    return {agenda,entries,shown,days};
  }
  function pendingView(snapshot){
    return [
      {sectionId:'relationship_lifecycle',label:'事项与承诺',
        kinds:['thread','commitment','relationship_commitment','interaction_bid']},
      {sectionId:'operations',label:'回访与回应',
        kinds:['revisit_intention','response_expectation']},
    ].map(group=>{
      const section=record(snapshot)&&record(snapshot.sections)?snapshot.sections[group.sectionId]:null;
      return {
        sectionId:group.sectionId,label:group.label,
        state:record(section)&&STATE_LABELS[section.state]?section.state:'unavailable',
        items:visibleHighlights(snapshot,group.sectionId,group.kinds).filter(item=>!(['response_expectation','revisit_intention'].includes(item.kind)&&['fulfilled','superseded','expired'].includes(item.statusCode))),
      };
    });
  }
  function nowStory(snapshot){
    const lines=[];
    const add=(label,sectionId,kinds,empty,unavailable)=>{
      const data=sectionData(snapshot,sectionId);
      const terminal=new Set(['fulfilled','superseded','expired','cancelled','closed','withdrawn']);
      const items=newestFirst(visibleHighlights(snapshot,sectionId,kinds)).filter(item=>!kinds.includes('response_expectation')||!terminal.has(item.statusCode));
      const view=(kinds&&kinds.includes('relationship_state')&&items.find(v=>v.title==='和你'))
        ||items.find(v=>v.title&&!['进行中','一项活动','世界事件','已经落进世界的事'].includes(v.title))
        ||items[0];
      lines.push({
        label,
        text:view?[view.title,view.status].filter(Boolean).join(' · '):(data?empty:unavailable),
        detail:view?view.detail:'',
        missing:!view,
      });
    };
    add('活动与计划','overview_life',['plan'],'暂无可展示的活动或计划','生活状态暂时不可用');
    if(!visibleHighlights(snapshot,'overview_life',['location']).length&&visibleHighlights(snapshot,'overview_life',['world_occurrence']).length){
      add('周围环境','overview_life',['world_occurrence'],'暂无可展示的环境记录','环境状态暂时不可用');
    }else{
      add('人在哪','overview_life',['location'],'暂无可展示的位置记录','位置状态暂时不可用');
    }
    if(!visibleHighlights(snapshot,'overview_life',['resource']).length&&visibleHighlights(snapshot,'overview_life',['experience']).length){
      add('最近经历','overview_life',['experience'],'暂无可展示的经历记录','经历记录暂时不可用');
    }else{
      add('身体状态','overview_life',['resource'],'暂无可展示的身体状态','身体状态暂时不可用');
    }
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
  function weekCells(snapshot,filter='all',status='all'){
    const view=agendaView(snapshot,filter,status);if(!view)return null;
    const complete=Array.isArray(view.agenda.week_slots);
    const slots=complete?view.agenda.week_slots:[];
    return {...view,days:view.days.map(day=>({...day,cells:Array.from({length:12},(_,i)=>{
      const hour=i*2;
      const segments=day.timed.filter(segment=>Math.floor(segment.minute/120)*2===hour);
      const slot=slots.find(item=>item.date===day.date&&item.hour===hour);
      const count=slot?(filter==='all'?slot.known_count:Number(slot.kinds?.[filter]||0)):0;
      const known=status==='all'&&complete?count:null;
      return {date:day.date,hour,segments,known,labels:status==='all'&&['all','planning'].includes(filter)?slot?.labels||[]:[],firstPage:slot?.first_page,lastPage:slot?.last_page,
        state:segments.length?'loaded':known>0?'unloaded':(complete&&status==='all'||!view.agenda.truncated)?'empty':'unknown'};
    })}))};
  }
  function storyGraph(worldline,threadIndex=0){
    if(!record(worldline)||!Array.isArray(worldline.threads)||!worldline.threads[threadIndex])return {nodes:[],edges:[],width:900,height:300};
    const thread=worldline.threads[threadIndex];
    const all=Array.isArray(worldline.opportunities)?worldline.opportunities:[];
    const identity=(opp,index)=>Number.isInteger(opp.index)?opp.index:index;
    const indexed=new Map(all.map((opp,index)=>[identity(opp,index),opp]));
    const selected=new Set(all.map((opp,index)=>({opp,index:identity(opp,index)})).filter(({opp})=>Array.isArray(opp.thread_indexes)?opp.thread_indexes.includes(threadIndex):(opp.thread_titles||[]).includes(thread.title)).map(({index})=>index));
    const includeParents=id=>{for(const parent of indexed.get(id)?.after_indexes||[]){if(indexed.has(parent)&&!selected.has(parent)){selected.add(parent);includeParents(parent);}}};
    [...selected].forEach(includeParents);
    const layers=new Map();
    for(let pass=0;pass<=selected.size;pass++){
      let changed=false;
      for(const id of selected){
        if(layers.has(id))continue;
        const parents=(indexed.get(id).after_indexes||[]).filter(p=>selected.has(p));
        if(parents.every(p=>layers.has(p))){layers.set(id,parents.length?Math.max(...parents.map(p=>layers.get(p)))+1:0);changed=true;}
      }
      if(!changed)break;
    }
    const nodes=[{id:'thread',x:24,y:34,w:214,h:100,label:'故事主线',title:thread.title,text:thread.situation,tone:'root'}];
    const edges=[];
    const notes=Array.isArray(thread.developments)?thread.developments:[];
    const explicit=all.some(opp=>Array.isArray(opp.after_indexes));
    if(explicit){nodes[0].label='故事主线 · '+notes.length+' 条进展摘要';nodes[0].text=[thread.situation,...notes].join('\\n\\n');}
    (explicit?[]:notes).forEach((text,index)=>{
      const id='note-'+index;nodes.push({id,x:290,y:34+index*118,w:214,h:94,label:'进展摘要 '+String(index+1).padStart(2,'0'),title:text,text,tone:'record'});
      edges.push({from:'thread',to:id,kind:'membership'});
    });
    if(thread.open_question){nodes.push({id:'question',x:24,y:190,w:214,h:118,label:'尚未揭晓',title:thread.open_question,text:thread.open_question,tone:'question'});edges.push({from:'thread',to:'question',kind:'membership'});}
    const rowCounts=new Map();
    [...selected].sort((a,b)=>(Date.parse(indexed.get(a).opens_at)||0)-(Date.parse(indexed.get(b).opens_at)||0)||a-b).forEach(id=>{
      const opp=indexed.get(id),layer=layers.get(id)||0,row=rowCounts.get(layer)||0;rowCounts.set(layer,row+1);
      const tone=opp.status==='已落实为世界事件'?'realized':opp.status==='候选窗口已结束'?'ended':'candidate';
      nodes.push({id:'opp-'+id,x:(explicit?290:560)+layer*248,y:34+row*118,w:214,h:94,label:opp.status||'未来候选',title:opp.title,text:opp.detail,tone,when:opp.opens_at,endsAt:opp.closes_at,threads:opp.thread_titles||[]});
      const parents=(opp.after_indexes||[]).filter(parent=>selected.has(parent));
      for(const parent of parents)if(layers.has(parent)&&layers.has(id)&&layers.get(parent)<layers.get(id))edges.push({from:'opp-'+parent,to:'opp-'+id,kind:'prerequisite'});
      if(!parents.length)edges.push({from:'thread',to:'opp-'+id,kind:'membership'});
    });
    return {nodes,edges,width:Math.max(840,...nodes.map(n=>n.x+n.w+28)),height:Math.max(350,...nodes.map(n=>n.y+n.h+30))};
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
    weekCells,
    storyGraph,
    readableText,
    lifeView,
    agendaView,
    agendaKey,
    shiftWeek,
    visibleHighlights,
    metaLabel,
    nowStory,
    pendingView,
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
  const disclosureState=new Map();
  function rememberDisclosure(node,key){
    node.open=disclosureState.get(key)||false;
    node.addEventListener('toggle',()=>disclosureState.set(key,node.open));
    return node;
  }
  const focusAreas=['all','now','mechanism','evidence','life','story','media','memory','emotion','relationships','pending'];
  let displayPaused=false;
  let resumePending=false;
  const explorer=DashboardExplorer.mount(DashboardHomeClient,{
    onShowEvidence(){if(recording){focusArea='evidence';applyPresentation();}byId('explorerDetails').open=true;byId('explorerDetails').scrollIntoView?.({block:'start'});},
  });
  const element=(tag,className,text)=>{
    const node=document.createElement(tag);
    if(className)node.className=className;
    if(text!==undefined)node.textContent=String(text);
    return node;
  };
  const svgEl=(tag,attrs={},text)=>{
    const node=document.createElementNS('http://www.w3.org/2000/svg',tag);
    for(const [k,v] of Object.entries(attrs)){if(v!==undefined&&v!==null)node.setAttribute(k,String(v));}
    if(text!==undefined)node.textContent=String(text);
    return node;
  };
  let closeLightbox=null;
  function openLightbox(imgUrl,title,metaText){
    if(closeLightbox)closeLightbox();
    const previousFocus=document.activeElement;
    const previousOverflow=document.body.style.overflow;
    const backdrop=element('div','lightbox-backdrop');
    const modal=element('div','lightbox-modal');
    modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-label','照片预览');
    const img=element('img','');img.src=imgUrl;img.alt=title||'照片预览';
    const closeBtn=element('button','button','关闭预览 ✕');closeBtn.type='button';
    const caption=element('div','lightbox-caption');
    const textWrap=element('div','');
    textWrap.append(element('strong','',title||'她的随手拍'),element('div','',metaText||''));
    caption.append(textWrap,closeBtn);
    modal.append(img,caption);
    backdrop.appendChild(modal);
    const close=()=>{if(backdrop.parentNode)backdrop.parentNode.removeChild(backdrop);document.body.style.overflow=previousOverflow;closeLightbox=null;previousFocus?.focus({preventScroll:true});};
    closeLightbox=close;
    modal.addEventListener('keydown',e=>{if(e.key==='Tab'){e.preventDefault();closeBtn.focus();}});
    closeBtn.addEventListener('click',e=>{e.stopPropagation();close();});
    backdrop.addEventListener('click',close);
    modal.addEventListener('click',e=>e.stopPropagation());
    document.body.appendChild(backdrop);document.body.style.overflow='hidden';closeBtn.focus();
  }
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
    document.body.dataset.view='single';
    byId('recordingToggle').textContent=recording?'退出录制模式':'录制模式';
    byId('recordingToggle').setAttribute('aria-pressed',String(recording));
    byId('recordingTools').hidden=!recording;
    byId('recordingFocus').value=focusArea;
    for(const panel of document.querySelectorAll('[data-focus-area]')){
      panel.hidden=recording&&focusArea!=='all'&&panel.dataset.focusArea!==focusArea;
      if(recording&&panel.tagName.toLowerCase()==='details'&&(!panel.hidden))panel.open=true;
    }
    const domains=byId('domainDetails');domains.open=true;
    domains.hidden=recording&&['now','mechanism','evidence','life','story'].includes(focusArea);
    byId('agendaPanel').hidden=recording&&!['all','life'].includes(focusArea);
    byId('storyPanel').hidden=recording&&!['all','story'].includes(focusArea);
    const explorerDetails=byId('explorerDetails');
    if(explorerDetails){explorerDetails.hidden=recording&&!['all','mechanism','evidence'].includes(focusArea);if(recording&&['mechanism','evidence'].includes(focusArea))explorerDetails.open=true;}
    const cost=byId('costMeterDetails');if(cost)cost.hidden=recording;
  }
  byId('costMeterDetails')?.addEventListener('toggle',()=>{if(byId('costMeterDetails').open)loadCostMeter();});
  const recordingOpenState=new Map();
  function toggleRecording(){
    if(!recording){for(const panel of document.querySelectorAll('[data-focus-area]'))if(panel.tagName.toLowerCase()==='details')recordingOpenState.set(panel.dataset.focusArea,panel.open);}
    if(!recording)previousScroll=window.scrollY||0;
    recording=!recording;
    if(!recording){focusArea='all';for(const panel of document.querySelectorAll('[data-focus-area]'))if(recordingOpenState.has(panel.dataset.focusArea))panel.open=recordingOpenState.get(panel.dataset.focusArea);recordingOpenState.clear();}
    applyPresentation();
    window.scrollTo(0,recording?0:previousScroll);
  }
  byId('recordingToggle').addEventListener('click',toggleRecording);
  byId('pauseDisplay').addEventListener('click',()=>{
    displayPaused=!displayPaused;
    byId('pauseDisplay').textContent=displayPaused?'恢复同步':'暂停画面';
    byId('pauseDisplay').setAttribute('aria-pressed',String(displayPaused));
    byId('pauseLabel').hidden=!displayPaused;
    if(!displayPaused){
      if(loading)resumePending=true;
      else loadDashboardHome();
    }
  });
  byId('recordingFocus').addEventListener('change',event=>{
    focusArea=focusAreas.includes(event.target.value)?event.target.value:'all';
    applyPresentation();
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){if(byId('detailDrawer')?.open){event.preventDefault();closeDetails();return;}if(closeLightbox){event.preventDefault();closeLightbox();}else if(recording)toggleRecording();}
  });
  function renderClocks(snapshot){
    const clock=DashboardHomeClient.formatClock(snapshot.logical_time);
    const generated=DashboardHomeClient.formatClock(snapshot.generated_at);
    byId('headerClock').textContent=clock?'世界时间 '+clock:'世界时间暂无记录';
    byId('worldClock').textContent=generated?'快照生成于 '+generated:'';
  }
  function orbitClock(value){
    const date=new Date(value||'');
    if(Number.isNaN(date.getTime()))return element('p','section-note','世界时间暂无记录');
    const clock=DashboardHomeClient.formatClock(value).split(' ').pop();
    const [hours,minutes]=clock.split(':').map(Number);
    const fraction=(hours*60+minutes)/1440;
    const svg=svgEl('svg',{viewBox:'0 0 240 240',class:'day-orbit',role:'img','aria-label':'世界时间 '+clock+'，24 小时刻度'});
    svg.appendChild(svgEl('title',{},'世界时间 '+clock+' · 北京时间'));
    svg.appendChild(svgEl('circle',{cx:120,cy:120,r:103,fill:'none',stroke:'currentColor','stroke-opacity':'.08'}));
    svg.appendChild(svgEl('circle',{cx:120,cy:120,r:83,fill:'none',stroke:'currentColor','stroke-opacity':'.16','stroke-width':2}));
    svg.appendChild(svgEl('circle',{cx:120,cy:120,r:83,fill:'none',stroke:'#7be2c9','stroke-width':2,'stroke-linecap':'round',pathLength:100,'stroke-dasharray':(fraction*100)+' 100',transform:'rotate(-90 120 120)',class:'orbit-progress'}));
    for(let i=0;i<48;i++){
      const angle=i*Math.PI/24-Math.PI/2,r=i%2===0?94:97;
      svg.appendChild(svgEl('line',{x1:120+r*Math.cos(angle),y1:120+r*Math.sin(angle),x2:120+100*Math.cos(angle),y2:120+100*Math.sin(angle),stroke:'currentColor','stroke-opacity':i%12===0?'.7':'.25'}));
    }
    const angle=fraction*Math.PI*2-Math.PI/2;
    svg.appendChild(svgEl('circle',{cx:120+83*Math.cos(angle),cy:120+83*Math.sin(angle),r:4,fill:'#9af4d9',class:'orbit-dot'}));
    svg.appendChild(svgEl('text',{x:120,y:108,'text-anchor':'middle',class:'orbit-caption'},'WORLD TIME'));
    svg.appendChild(svgEl('text',{x:120,y:144,'text-anchor':'middle',class:'orbit-time'},clock));
    svg.appendChild(svgEl('text',{x:120,y:167,'text-anchor':'middle',class:'orbit-zone'},'北京时间 · 24h'));
    return svg;
  }
  function appendReading(parent,value,{className='detail',key='',limit=240}={}){
    const reading=DashboardHomeClient.readableText(value);
    if(reading.label)parent.appendChild(element('span','reading-label',reading.label));
    if(reading.text){
      if(reading.text.length>limit){
        const more=rememberDisclosure(element('details','prose-disclosure'),key||value);
        const summary=element('summary','');
        summary.append(element('span','prose-preview',reading.text.slice(0,limit)+'…'),element('span','prose-toggle','展开全文'));
        more.append(summary,element('p',className,reading.text));parent.appendChild(more);
      }else parent.appendChild(element('p',className,reading.text));
    }else if(reading.structured){
      const details=rememberDisclosure(element('details','structured-detail'),key||value);
      details.append(element('summary','','查看记录原文'),element('pre','',value));parent.appendChild(details);
    }
  }
  function renderNowStory(snapshot){
    const parent=byId('nowStory');parent.replaceChildren();
    const lines=DashboardHomeClient.nowStory(snapshot);
    const line=lines[0];
    const main=element('div','present-main');
    main.appendChild(element('span','present-label',line.missing?'ACTIVITY / 暂无记录':'ACTIVITY / 活动与计划'));
    appendReading(main,line.text,{className:'present-title',key:'present-title',limit:190});
    if(line.detail&&line.detail!==line.text){
      const detail=rememberDisclosure(element('details','now-context-detail'),'present-intention');
      detail.appendChild(element('summary','','原本的打算'));appendReading(detail,line.detail,{key:'present-intention-text'});main.appendChild(detail);
    }
    const context=element('div','now-context');
    for(const index of [1,2]){
      const item=lines[index];
      const details=rememberDisclosure(element('details'),'present-'+index);
      details.appendChild(element('summary','',item.label+(item.missing?' · 暂无记录':'')));
      appendReading(details,item.detail||item.text,{key:'present-context-'+index});context.appendChild(details);
    }
    main.appendChild(context);
    parent.append(main,orbitClock(snapshot.logical_time));
    // A terminal expectation belongs in history, never in a "currently waiting" tile.
    const expectation=lines[5];
    if(!expectation.missing){
      const note=element('details','present-expectation');
      rememberDisclosure(note,'present-expectation');note.appendChild(element('summary','','互动期待 · '+expectation.text));
      if(expectation.detail)appendReading(note,expectation.detail);parent.appendChild(note);
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
        if(typeof metric.count_note==='string')tile.append(element('small','',metric.count_note));
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
  const HIDDEN_CHIP_LABELS=new Set(['正文状态','表达段数','已送达段数','可展示段数','允许引用','可供角色模型查看','事项说明','提取强度','强化次数','候选结果','观察','参与者','照片画面','把握度','重要度','已知特征','环境结果正文','经历来源','角色回应']);
  function appendHighlight(parent,view){
    let extraClass=' scrapbook-note';
    if(view.kind==='expression_plan'||view.kind==='response_expectation'||view.kind==='expectation_assessment'||view.kind==='revisit_intention')extraClass=' chat-bubble-card';
    else if(view.kind==='external_perception'||view.kind==='external_signal')extraClass=' bili-card';
    const card=element('article','highlight'+extraClass);
    card.dataset.status=view.statusCode;
    const top=element('div','highlight-top');
    top.appendChild(element('span','kind-tag',view.kindLabel));
    if(view.when)top.appendChild(element('time','when',DashboardHomeClient.formatClock(view.when)));
    const repeatsDetail=view.detail&&(view.detail===view.title||(view.title.endsWith('…')&&view.detail.startsWith(view.title.slice(0,-1))));
    card.appendChild(top);
    if(!repeatsDetail&&!(view.kind==='expression_plan'&&view.title==='发给你的消息')){
      const title=DashboardHomeClient.readableText(view.title);
      if(title.structured)appendReading(card,view.title,{key:'title-'+view.kind+'-'+view.when});
      else if(title.text)card.appendChild(element('h3','',title.text));
    }
    if(view.status)card.appendChild(element('span','status-pill',view.status));
    if(view.detail)appendReading(card,view.detail,{key:'highlight-'+view.kind+'-'+view.when+'-'+view.title});
    if(view.values.length){
      const imgVal=view.values.find(v=>v.label==='照片画面'&&v.text.startsWith('/world-v2/dashboard/media/'));
      if(imgVal){
        const frame=element('div','polaroid-frame');
        frame.style.maxWidth='240px';frame.style.marginTop='8px';frame.style.cursor='pointer';
        const img=element('img','');img.src=imgVal.text;img.alt=view.detail||view.title||'照片';img.loading='lazy';
        frame.appendChild(img);
        frame.addEventListener('click',()=>openLightbox(imgVal.text,view.detail||view.title,view.when?DashboardHomeClient.formatClock(view.when):''));
        card.appendChild(frame);
      }
      const relDims=new Set(['信任','亲近','尊重','可靠','相互','修复信心']);
      const barValues=view.kind==='relationship_state'?view.values.filter(v=>relDims.has(v.label)&&/%$/.test(v.text)):[];
      const otherValues=view.values.filter(v=>!barValues.includes(v)&&!HIDDEN_CHIP_LABELS.has(v.label));
      if(barValues.length){
        const bars=element('div','rel-bars');
        for(const item of barValues){
          const row=element('div','rel-bar-row');
          const head=element('div','rel-bar-head');
          head.append(element('span','',item.label),element('span','',item.text));
          const track=element('div','rel-bar-track');
          const fill=element('div','rel-bar-fill');
          fill.style.width=item.text;
          track.appendChild(fill);
          row.append(head,track);
          bars.appendChild(row);
        }
        card.appendChild(bars);
      }
      if(otherValues.length){
        const chips=element('div','chips');
        for(const value of otherValues){
          if(value.text.startsWith('http://')||value.text.startsWith('https://')){
            const link=element('a','chip chip-link','▶ 打开原视频 / 原链接 ↗');
            link.href=value.text;link.target='_blank';link.rel='noopener noreferrer';
            chips.appendChild(link);
          }else{
            chips.appendChild(element('span','chip',value.label+' '+value.text));
          }
        }
        card.appendChild(chips);
      }
    }
    parent.appendChild(card);
  }
  function appendHighlights(parent,views,{limit=4,key=''}={}){
    const list=element('div','highlight-list');
    for(const view of views.slice(0,limit))appendHighlight(list,view);
    parent.appendChild(list);
    if(views.length>limit){
      const details=rememberDisclosure(element('details','more-records'),key||JSON.stringify(views.map(v=>[v.kind,v.title,v.when])));
      details.appendChild(element('summary','','还有 '+(views.length-limit)+' 条记录'));
      const rest=element('div','highlight-list');
      for(const view of views.slice(limit))appendHighlight(rest,view);
      details.appendChild(rest);parent.appendChild(details);
    }
  }
  function sectionCard(title,section,area){
    const collapsible=area&&!['life','story'].includes(area);
    const card=element(collapsible?'details':'section','section-card');
    if(collapsible)rememberDisclosure(card,'panel-'+area);
    if(area)card.dataset.focusArea=area;
    const head=element(collapsible?'summary':'div','section-head');
    const state=section&&typeof section.state==='string'?section.state:'unavailable';
    card.dataset.state=state;
    const badge=element('span','section-state',DashboardHomeClient.stateLabel(state));
    badge.dataset.state=state;
    head.append(element('h2','',title),badge);
    if(collapsible){
      const kinds={relationships:['relationship_state'],emotion:['affect_episode'],recent:['experience'],memory:['memory_candidate','fact']};
      const highlights=(section?.data?.highlights||[]).filter(item=>!kinds[area]||kinds[area].includes(item.kind)).map(DashboardHomeClient.highlightView).filter(Boolean);
      const first=(area==='relationships'&&highlights.find(item=>item.title==='和你'))||highlights[0];
      const value=area==='relationships'?(first?.status||'查看关系'):area==='emotion'?(first?.title||'查看感受'):area==='media'?'照片与声音':area==='pending'?'事项与回访':area==='recent'?'最近留下的片段':area==='memory'?'记住的片段':'点开查看';
      head.appendChild(element('span','card-preview',DashboardHomeClient.readableText(value).text.slice(0,48)));
    }
    const body=element('div','section-body');
    card.append(head,body);
    return {card,body};
  }
  function appendCoverage(parent,section){
    if(isRecord(section&&section.coverage)&&section.coverage.truncated){
      parent.appendChild(element('p','truncated','这里只展示部分摘要，未列出的记录不代表不存在。'));
    }
  }
  let agendaWeek=null,agendaPage=null,agendaFilter='all',agendaStatus='all',agendaSelection=null,galSelection=null,galRouteFilter='0';
  const agendaLabels={planning:'客观规划',experience:'角色经历',plan:'角色计划',world:'实际事件',outlook:'命运候选',commitment:'约定',revisit:'再次考虑',expectation:'回应期待'};
  function photoCaption(item){
    // Historical summaries can contain a serialized envelope. Do not expose
    // protocol payloads as prose or infer a caption from their arbitrary fields.
    for(const text of [item.detail,item.title]){
      if(!text)continue;
      try{if(isRecord(JSON.parse(text))||Array.isArray(JSON.parse(text)))continue;}catch(_e){}
      return text;
    }
    return '照片记录';
  }
  function renderVisualStudio(snapshot){
    const studio=element('div','visual-studio');
    // 1. 6-Axis Relationship Hexagonal Radar Chart
    const relSec=snapshot.sections.relationship_lifecycle;
    const relCard=sectionCard('你们之间',relSec,'relationships');
    relCard.card.className='section-card studio-card';
    const relStates=DashboardHomeClient.visibleHighlights(snapshot,'relationship_lifecycle',['relationship_state']);
    const relSignals=DashboardHomeClient.visibleHighlights(snapshot,'relationship_lifecycle',['relationship_signal','relationship_adjustment']).sort((a,b)=>(Date.parse(b.when)||0)-(Date.parse(a.when)||0));
    const relState=relStates.find(r=>r.title==='和你')||relStates[0];
    const dims=['信任','亲近','尊重','可靠','相互','修复信心'];
    const dimMap={};
    if(relState&&Array.isArray(relState.values)){
      for(const v of relState.values){
        if(dims.includes(v.label)&&/%$/.test(v.text))dimMap[v.label]=Math.max(0,Math.min(100,parseFloat(v.text)||0));
      }
    }
    if(dims.every(d=>Object.hasOwn(dimMap,d))){
      const wrap=element('div','radar-wrap');
      const svg=svgEl('svg',{viewBox:'0 0 220 200',class:'radar-svg','aria-label':'六维关系雷达图'});
      const cx=110,cy=102,R=64;
      const pt=(idx,ratio)=>{
        const ang=(-Math.PI/2)+(idx*Math.PI/3);
        return [Math.round((cx+R*ratio*Math.cos(ang))*10)/10,Math.round((cy+R*ratio*Math.sin(ang))*10)/10];
      };
      for(const level of [0.33,0.66,1]){
        const pts=dims.map((_,i)=>pt(i,level).join(',')).join(' ');
        svg.appendChild(svgEl('polygon',{points:pts,fill:level===1?'rgba(45,94,76,0.05)':'none',stroke:'rgba(30,53,47,0.13)','stroke-width':'1'}));
      }
      dims.forEach((d,i)=>{
        const [ex,ey]=pt(i,1);
        svg.appendChild(svgEl('line',{x1:cx,y1:cy,x2:ex,y2:ey,stroke:'rgba(30,53,47,0.13)','stroke-width':'1'}));
        const [lx,ly]=pt(i,1.27);
        const val=dimMap[d];
        const lbl=svgEl('text',{x:lx,y:ly+4,'text-anchor':'middle','font-size':'10.5','font-weight':'700',fill:'#1e352f'},d+' '+val+'%');
        svg.appendChild(lbl);
      });
      const valPts=dims.map((d,i)=>pt(i,(dimMap[d])/100).join(',')).join(' ');
      svg.appendChild(svgEl('polygon',{points:valPts,fill:'rgba(53,107,87,0.26)',stroke:'#2d5e4c','stroke-width':'2.4'}));
      dims.forEach((d,i)=>{
        const [vx,vy]=pt(i,(dimMap[d])/100);
        svg.appendChild(svgEl('circle',{cx:vx,cy:vy,r:'4',fill:'#1a9d8a',stroke:'#fffef9','stroke-width':'1.6'}));
      });
      const summaryCol=element('div','radar-summary');
      const hero=element('div','stage-hero');
      hero.append(element('span','','当前关系阶段'),element('strong','',relState.status||relState.title||'熟悉中'));
      summaryCol.appendChild(hero);
      const bars=element('div','rel-bars');
      for(const d of dims){
        const pct=(dimMap[d])+'%';
        const row=element('div','rel-bar-row');
        const head=element('div','rel-bar-head');
        head.append(element('span','',d),element('span','',pct));
        const track=element('div','rel-bar-track');
        const fill=element('div','rel-bar-fill');fill.style.width=pct;
        track.appendChild(fill);row.append(head,track);bars.appendChild(row);
      }
      summaryCol.appendChild(element('p','section-note','六项已记录的关系维度，不作好坏评分。'));
      const dimensions=rememberDisclosure(element('details','relationship-dimensions'),'relationship-dimensions');dimensions.append(element('summary','','查看六项维度'),bars);summaryCol.appendChild(dimensions);
      wrap.append(svg,summaryCol);
      relCard.body.appendChild(wrap);
      if(relSignals.length){
        const sigWrap=element('div','mood-gauges');
        for(const sig of relSignals.slice(0,1)){
          const item=element('div','appraisal-quote');
          const top=element('div','mood-row-top');
          top.append(element('strong','',sig.kindLabel),element('span','when',sig.when?DashboardHomeClient.formatClock(sig.when):''));
          item.append(top,element('div','',sig.detail||sig.title));
          sigWrap.appendChild(item);
        }
        relCard.body.appendChild(sigWrap);
        if(relSignals.length>1){const more=rememberDisclosure(element('details','more-records'),'relationship-signals');more.appendChild(element('summary','','更早的关系记录 · '+(relSignals.length-1)));appendHighlights(more,relSignals.slice(1));relCard.body.appendChild(more);}
      }
    }else{
      if(relState)appendHighlight(relCard.body,relState);
      else empty(relCard.body,(!relSec||relSec.state==='unavailable')?'关系记录暂时不可用。':'暂无可展示的关系记录。');
    }
    studio.appendChild(relCard.card);

    // 2. Mood Spectrum & Inner Appraisals
    const emoSec=snapshot.sections.facts_memory_inner;
    const emoCard=sectionCard('心情与感受',emoSec,'emotion');
    emoCard.card.className='section-card studio-card';
    const affects=DashboardHomeClient.visibleHighlights(snapshot,'facts_memory_inner',['affect_episode','affect_baseline']);
    const appraisals=DashboardHomeClient.visibleHighlights(snapshot,'facts_memory_inner',['appraisal']).sort((a,b)=>(Date.parse(b.when)||0)-(Date.parse(a.when)||0)).slice(0,2);
    const resources=DashboardHomeClient.visibleHighlights(snapshot,'overview_life',['resource','attention']).slice(0,3);
    if(affects.length||appraisals.length||resources.length){
      const gauges=element('div','mood-gauges');
      for(const af of affects.slice(0,2)){
        const row=element('div','mood-row');
        const top=element('div','mood-row-top');
        top.append(element('strong','',af.title),element('span','status-pill',af.status||'当前心境'));
        row.appendChild(top);
        const percentage=af.values.find(v=>/^(?:100|[0-9]{1,2})(?:[.][0-9]+)?%$/.test(v.text));
        if(percentage){
          const bar=element('div','mood-bar');bar.setAttribute('aria-hidden','true');
          const fill=element('div','mood-bar-fill');fill.style.width=percentage.text;bar.appendChild(fill);row.appendChild(bar);
          row.appendChild(element('span','when',percentage.label+' '+percentage.text));
        }
        gauges.appendChild(row);
      }
      for(const res of resources){
        const pctVal=(res.values||[]).find(v=>/^(?:100|[0-9]{1,2})(?:[.][0-9]+)?%$/.test(v.text));
        const row=element('div','mood-row');
        const top=element('div','mood-row-top');
        top.append(element('strong','',res.title+' ('+(res.status||res.kindLabel)+')'),element('span','when',pctVal?pctVal.text:(res.when?DashboardHomeClient.formatClock(res.when):'')));
        row.appendChild(top);
        if(pctVal){
          const bar=element('div','mood-bar');bar.setAttribute('aria-hidden','true');
          const fill=element('div','mood-bar-fill');fill.style.width=pctVal.text;bar.appendChild(fill);row.appendChild(bar);
        }
        gauges.appendChild(row);
      }
      emoCard.body.appendChild(gauges);
      if(appraisals.length){
        const notesGrid=element('div','mood-gauges');
        for(const ap of appraisals){
          const q=element('div','appraisal-quote');
          const head=element('div','mood-row-top');
          head.append(element('strong','','当时的处境判断'),element('span','when',ap.when?DashboardHomeClient.formatClock(ap.when):''));
          q.appendChild(head);appendReading(q,ap.detail||ap.title,{key:'appraisal-'+ap.when,limit:110});
          notesGrid.appendChild(q);
        }
        emoCard.body.appendChild(notesGrid);
      }
    }else{
      empty(emoCard.body,(!emoSec||emoSec.state==='unavailable')?'情绪记录暂时不可用。':'暂无可展示的情绪与处境判断。');
    }
    studio.appendChild(emoCard.card);

    // 3. Scattered Overlapping Polaroid Desk & 35mm Film Strip
    const mediaSec=snapshot.sections.perception_media;
    const mediaCard=sectionCard('留住的瞬间',mediaSec,'media');
    mediaCard.card.className='section-card studio-card';
    const mediaItems=DashboardHomeClient.visibleHighlights(snapshot,'perception_media',['media_delivery','media_preview','media_inspection','photo_candidate']).sort((a,b)=>(Date.parse(b.when)||0)-(Date.parse(a.when)||0));
    const seenUrls=new Set();
    const photos=[];
    const notes=[];
    for(const item of mediaItems){
      const imgVal=(item.values||[]).find(v=>v.label==='照片画面'&&v.text.startsWith('/world-v2/dashboard/media/'));
      if(imgVal){
        if(seenUrls.has(imgVal.text))continue;
        seenUrls.add(imgVal.text);
        photos.push({item,url:imgVal.text});
      }else if(item.detail||item.title){
        notes.push(item);
      }
    }
    if(photos.length||notes.length){
      if(photos.length){
        const desk=element('div','polaroid-desk');
        const grid=element('div','gallery-grid');
        for(const {item,url} of photos){
          const pCard=element('button','polaroid-card');pCard.type='button';
          pCard.appendChild(element('span','polaroid-tape',''));
          const frame=element('div','polaroid-frame');
          const img=element('img','');img.src=url;img.alt=photoCaption(item);img.loading='lazy';
          const badge=element('span','polaroid-badge',item.kind==='media_delivery'?'已发给你':(item.status||'拍立得成片'));
          frame.append(img,badge);
          const caption=photoCaption(item);
          const timeStr=item.when?DashboardHomeClient.formatClock(item.when):'';
          const meta=element('div','polaroid-meta');
          meta.append(element('span','','INSTANT · '+item.kindLabel),element('span','',timeStr));
          pCard.append(frame,element('p','polaroid-caption',caption),meta);
          pCard.addEventListener('click',()=>openLightbox(url,caption,item.kindLabel+(timeStr?' · '+timeStr:'')));
          grid.appendChild(pCard);
        }
        desk.appendChild(grid);
        mediaCard.body.appendChild(desk);
      }
      if(notes.length){
        const strip=element('div','film-strip');
        notes.slice(0,4).forEach((n,idx)=>{
          const frame=element('div','film-frame');
          const head=element('div','film-frame-head');
          head.append(element('span','','35MM · 0'+(idx+1)+'A'),element('span','',n.when?DashboardHomeClient.formatClock(n.when):n.kindLabel));
          frame.append(head,element('div','',n.detail||n.title));
          strip.appendChild(frame);
        });
        const more=rememberDisclosure(element('details','more-records'),'media-notes');
        more.append(element('summary','','拍摄相关记录 · '+notes.length),strip);mediaCard.body.appendChild(more);
      }
    }else{
      empty(mediaCard.body,(!mediaSec||mediaSec.state==='unavailable')?'相册记录暂时不可用。':'本次快照暂无可展示的照片。');
    }
    const voiceHost=element('div','mood-gauges');
    mediaCard.body.appendChild(voiceHost);
    if(typeof fetch==='function'){
      fetch('/world-v2/dashboard/voice',{credentials:'same-origin'}).then(r=>r&&r.ok?r.json():null).then(data=>{
        const vNotes=data&&Array.isArray(data.voice_notes)?data.voice_notes:[];
        if(!vNotes.length)return;
        voiceHost.replaceChildren();
        for(const vn of vNotes.slice(0,6)){
          const box=element('div','appraisal-quote');
          const top=element('div','mood-row-top');
          const dur=vn.duration_seconds?(' · '+vn.duration_seconds+'s'):'';

          top.append(element('strong','','语音片段'+dur),element('span','when',vn.created_at?DashboardHomeClient.formatClock(vn.created_at):''));
          const textLine=element('div','','「'+(vn.text||'')+'」');
          const audio=element('audio','');
          audio.controls=true;audio.preload='none';audio.src=vn.audio_url;
          audio.style.cssText='width:100%;height:32px;margin-top:6px;border-radius:999px;';
          box.append(top,textLine,audio);
          voiceHost.appendChild(box);
        }
      }).catch(()=>{});
    }
    studio.appendChild(mediaCard.card);
    sectionGrid.appendChild(studio);
  }
  let drawerOrigin=null;
  function closeDetails(){
    const dialog=byId('detailDrawer');if(!dialog)return;
    if(dialog.close)dialog.close();else dialog.open=false;
    drawerOrigin?.focus?.({preventScroll:true});drawerOrigin=null;
  }
  function openDetails(title,content,snapshot=null){
    const dialog=byId('detailDrawer');if(!dialog)return;
    if(!dialog.open)drawerOrigin=document.activeElement;
    byId('drawerTitle').textContent=title;
    byId('drawerBody').replaceChildren(content);
    if(snapshot?.logical_time)byId('drawerBody').appendChild(element('p','drawer-provenance','记录快照 · '+DashboardHomeClient.formatClock(snapshot.logical_time)));
    if(!dialog.open){if(dialog.showModal)dialog.showModal();else dialog.open=true;}
    byId('closeDrawer')?.focus?.({preventScroll:true});
  }
  byId('closeDrawer')?.addEventListener('click',closeDetails);
  byId('detailDrawer')?.addEventListener('cancel',event=>{event.preventDefault();closeDetails();});
  byId('detailDrawer')?.addEventListener('click',event=>{if(event.target===byId('detailDrawer'))closeDetails();});
  let pendingAgendaCell=null;
  function changeAgendaPage(page){
    if(displayPaused||loading)return;
    agendaPage=page;snapshotEtag=null;loadDashboardHome();
  }
  function showAgendaEntry(snapshot,entry,entries){
    agendaSelection=DashboardHomeClient.agendaKey(entry);
    const content=element('div','agenda-reading');
    content.append(element('span','kind-tag',agendaLabels[entry.kind]),element('span','status-pill',entry.status),element('p','agenda-detail-time',DashboardHomeClient.formatClock(entry.starts_at)+' — '+DashboardHomeClient.formatClock(entry.ends_at)));
    appendReading(content,entry.detail||entry.title,{className:'agenda-detail-body',key:'agenda-reading-'+entry.index,limit:1000});
    if(entry.content_state==='unavailable')content.appendChild(element('p','section-note','原文暂时不可用，未根据标题补写。'));
    if(entry.truncated)content.appendChild(element('p','section-note','当前正文为节选。'));
    const related=(entry.related||[]).map(index=>entries.find(item=>item.index===index)).filter(Boolean);
    if(related.length){
      content.appendChild(element('h3','section-subtitle','已记录的关联'));
      for(const item of related){const button=element('button','agenda-related',agendaLabels[item.kind]+' · '+item.title);button.type='button';button.addEventListener('click',()=>showAgendaEntry(snapshot,item,entries));content.appendChild(button);}
    }
    openDetails(DashboardHomeClient.readableText(entry.title).text||'记录详情',content,snapshot);
  }
  function showAgendaCell(snapshot,cell,view){
    const content=element('div','agenda-cell-reading');
    const entries=cell.segments.map(segment=>segment.entry);
    content.appendChild(element('p','section-note',cell.known===null?'当前页载入 '+entries.length+' 条；分页未覆盖的记录不代表不存在。':'此时段已知 '+cell.known+' 条记录，当前筛选载入 '+entries.length+' 条。'));
    for(const entry of entries){
      const button=element('button','drawer-record');button.type='button';button.dataset.agendaIndex=String(entry.index);
      button.append(element('span','when',DashboardHomeClient.formatClock(entry.starts_at)+' · '+agendaLabels[entry.kind]),element('strong','',DashboardHomeClient.readableText(entry.title).text||'结构化记录'),element('span','status-pill',entry.status));
      button.addEventListener('click',()=>showAgendaEntry(snapshot,entry,view.entries));content.appendChild(button);
    }
    if(!entries.length)empty(content,cell.state==='empty'?'当前范围没有符合筛选条件的记录。':'这段记录尚未载入当前页。');
    if(Number.isInteger(cell.firstPage)&&Number.isInteger(cell.lastPage)){
      const pages=element('div','drawer-page-controls');
      const current=Number(view.agenda.page||0);
      for(const [label,target] of [['读取这一段',cell.firstPage],['上一页',Math.max(cell.firstPage,current-1)],['下一页',Math.min(cell.lastPage,current+1)]]){
        const button=element('button','button',label);button.type='button';button.disabled=displayPaused||target===current;
        button.addEventListener('click',()=>{if(displayPaused||loading)return;pendingAgendaCell={date:cell.date,hour:cell.hour};button.disabled=true;changeAgendaPage(target);});pages.appendChild(button);
      }
      content.append(pages,element('p','section-note','详情页 '+(current+1)+' / '+view.agenda.page_count+' · 每次只读取一个完整快照'));
    }
    openDetails(cell.date+' · '+String(cell.hour).padStart(2,'0')+':00—'+String(cell.hour+2).padStart(2,'0')+':00',content,snapshot);
  }
  function renderAgenda(body,snapshot){
    const view=DashboardHomeClient.weekCells(snapshot,agendaFilter,agendaStatus);
    if(!view){empty(body,'周课表暂时不可用。');return;}
    const {agenda,entries,days}=view;
    const controls=element('div','agenda-controls');
    const navigation=element('div','agenda-navigation');
    for(const [label,offset] of [['上一周',-7],['本周',null],['下一周',7]]){
      const button=element('button','button',label);button.type='button';button.disabled=displayPaused;
      button.addEventListener('click',()=>{if(displayPaused||loading)return;agendaWeek=offset===null?null:DashboardHomeClient.shiftWeek(agenda.week_start,offset);agendaPage=null;agendaSelection=null;snapshotEtag=null;loadDashboardHome();});navigation.appendChild(button);
    }
    navigation.appendChild(element('strong','agenda-range',days[0].date+' — '+days[6].date));
    const filter=element('select','agenda-filter');filter.setAttribute('aria-label','筛选当前页课表内容');
    for(const [value,label] of [['all','全部记录'],['planning','客观课表'],['plan','她的计划'],['experience','亲身经历'],['world','世界事件'],['outlook','未来候选']]){const option=element('option','',label);option.value=value;filter.appendChild(option);}
    filter.value=agendaFilter;filter.addEventListener('change',()=>{agendaFilter=filter.value;renderLifePanel();});
    const status=element('select','agenda-filter');status.setAttribute('aria-label','筛选课表状态');
    for(const code of ['all',...new Set(entries.map(entry=>entry.status))]){const option=element('option','',code==='all'?'全部状态':code);option.value=code;status.appendChild(option);}
    status.value=agendaStatus;status.addEventListener('change',()=>{agendaStatus=status.value;renderLifePanel();});
    controls.append(navigation,filter,status);body.appendChild(controls);
    const help=element('div','week-help');help.append(element('span','','北京时间 · 按开始时段归档，点开查看完整起止时间'),element('span','','浅色：尚未载入 · 空格不代表空闲'));
    body.appendChild(help);
    if(agenda.page_count>1)body.appendChild(element('p','section-note','第 '+(Number(agenda.page||0)+1)+' / '+agenda.page_count+' 页 · 当前只显示其中 '+entries.length+' 条正文'));
    if(agenda.truncated)body.appendChild(element('p','section-note',Array.isArray(agenda.week_slots)?'整周分布已载入；正文按页读取，当前第 '+(Number(agenda.page||0)+1)+' / '+agenda.page_count+' 页。':'这份快照仅含部分记录，其他日期标记为“未载入”，不视作无事发生。'));
    for(const [key,label] of [['planning_state','客观课表'],['outlook_state','未来候选']])if(agenda[key]==='unavailable')body.appendChild(element('p','agenda-warning',label+'暂不可用，本周视图不完整。'));
    const scroller=element('div','agenda-scroll week-scroll');scroller.tabIndex=0;scroller.setAttribute('aria-label','一周课表，可横向与纵向滚动');
    const table=element('div','week-table');table.setAttribute('role','table');table.setAttribute('aria-label','周一至周日的生活课表');
    const head=element('div','week-row week-head');head.setAttribute('role','row');head.appendChild(element('div','week-corner','时段'));
    const logical=Date.parse(snapshot.logical_time);const today=Number.isFinite(logical)?new Date(logical+8*3600000).toISOString().slice(0,10):null;
    days.forEach((day,index)=>{
      const cell=element('div','week-day-heading');cell.setAttribute('role','columnheader');cell.dataset.today=String(day.date===today);
      cell.append(element('span','','周'+'一二三四五六日'[index]),element('strong','',day.date.slice(5).replace('-',' / ')));
      head.appendChild(cell);
    });table.appendChild(head);
    for(let row=0;row<12;row++){
      const line=element('div','week-row');line.setAttribute('role','row');
      const label=element('div','week-hour',String(row*2).padStart(2,'0')+':00');label.setAttribute('role','rowheader');line.appendChild(label);
      for(const day of days){
        const cell=day.cells[row],holder=element('div','week-cell');holder.setAttribute('role','cell');holder.dataset.state=cell.state;
        if(cell.state==='empty'){holder.appendChild(element('span','week-empty','—'));}
        else{
          const button=element('button','week-event');button.type='button';button.dataset.weekCell=day.date+'-'+cell.hour;
          const first=cell.segments[0]?.entry;button.dataset.kind=first?.kind||'unloaded';
          button.setAttribute('aria-label',day.date+' '+cell.hour+' 时，'+(first?.title||'查看此时段记录'));
          if(first){button.append(element('span','week-kind',agendaLabels[first.kind]),element('strong','week-title',DashboardHomeClient.readableText(first.title).text||'结构化记录'));}
          else button.appendChild(element('strong','week-title',cell.labels.length?cell.labels.join(' / '):cell.known>0?cell.known+' 条记录':'未载入'));
          const total=cell.known===null?cell.segments.length:cell.known;
          button.appendChild(element('span','week-count',first?(cell.segments.length>1?'已载入 '+cell.segments.length+' 条 · 点开':first.status):(total?'点开读取详情':'查看分页')));
          button.addEventListener('click',()=>{if(cell.segments.length===1&&(cell.known===null||cell.known===1))showAgendaEntry(snapshot,cell.segments[0].entry,view.entries);else showAgendaCell(snapshot,cell,view);});holder.appendChild(button);
        }
        line.appendChild(holder);
      }table.appendChild(line);
    }
    scroller.appendChild(table);body.appendChild(scroller);
    const windows=days.flatMap(day=>day.windows.map(segment=>segment.entry));
    const unique=[...new Map(windows.map(entry=>[DashboardHomeClient.agendaKey(entry),entry])).values()];
    const footer=element('div','week-footer');
    if(unique.length){const button=element('button','button','阶段背景与候选窗口 · '+unique.length);button.type='button';button.addEventListener('click',()=>{
      const content=element('div','agenda-windows');
      for(const entry of unique){const record=element('button','drawer-record');record.type='button';record.dataset.agendaIndex=String(entry.index);record.append(element('span','kind-tag',agendaLabels[entry.kind]),element('strong','',entry.title),element('span','status-pill',entry.status));record.addEventListener('click',()=>showAgendaEntry(snapshot,entry,entries));content.appendChild(record);}
      openDetails('阶段背景与候选窗口',content,snapshot);
    });footer.appendChild(button);}
    if(agenda.page_count>1)for(const [label,target] of [['上一页',Number(agenda.page||0)-1],['当前时间',null],['下一页',Number(agenda.page||0)+1]]){const button=element('button','button',label);button.type='button';button.disabled=displayPaused||(target!==null&&(target<0||target>=agenda.page_count));button.addEventListener('click',()=>changeAgendaPage(target));footer.appendChild(button);}
    footer.appendChild(element('span','section-note','七天始终同屏排列 · 小屏可左右滑动'));body.appendChild(footer);
    if(pendingAgendaCell){const cell=days.find(day=>day.date===pendingAgendaCell.date)?.cells.find(cell=>cell.hour===pendingAgendaCell.hour);pendingAgendaCell=null;if(cell)showAgendaCell(snapshot,cell,view);}
  }
  let storyZoom=null,storyResetScroll=false;
  function renderWorldline(snapshot){
    const section=snapshot.sections.overview_life;
    const wl=section?.state!=='unavailable'&&section?.data?.agenda?.worldline;
    const {card,body}=sectionCard('故事地图',section,'story');
    if(!wl){empty(body,'暂时没有可展示的故事脉络。');return card;}
    const threads=Array.isArray(wl.threads)?wl.threads:[];
    if(!threads[Number(galRouteFilter)])galRouteFilter='0';
    const toolbar=element('div','story-toolbar');
    const picker=element('select','story-route-select');picker.setAttribute('aria-label','选择故事线');picker.dataset.storyRoute='picker';
    threads.forEach((thread,index)=>{const option=element('option','',String(index+1).padStart(2,'0')+' · '+thread.title);option.value=String(index);picker.appendChild(option);});
    picker.value=galRouteFilter;picker.addEventListener('change',()=>{galRouteFilter=picker.value;galSelection=null;storyZoom=null;storyResetScroll=true;renderLifePanel();});toolbar.appendChild(picker);
    const stages=Array.isArray(wl.stages)?wl.stages:[],current=stages.find(stage=>['当前章节','当前阶段'].includes(stage.status));
    const chapters=element('button','button',current?'章节 · '+current.title:'章节与展望');chapters.type='button';
    chapters.addEventListener('click',()=>{
      const content=element('div','chapter-list');
      for(const stage of stages){const entry=element('article','chapter-entry');entry.append(element('span','kind-tag',stage.status),element('h3','',stage.title),element('p','when',[stage.starts_on,stage.ends_on].join(' — ')));appendReading(entry,stage.situation);content.appendChild(entry);}
      for(const change of wl.convergences||[]){const entry=element('article','chapter-entry');entry.appendChild(element('span','kind-tag',change.decision_label));appendReading(entry,change.reason);content.appendChild(entry);}
      openDetails('章节与世界线变化',content,snapshot);
    });toolbar.appendChild(chapters);
    const graph=DashboardHomeClient.storyGraph(wl,Number(galRouteFilter));
    const scroller=element('div','story-map-viewport');scroller.tabIndex=0;scroller.setAttribute('aria-label','故事分支地图，可横向与纵向滚动');
    const extent=element('div','story-map-extent'),canvas=element('div','story-map-canvas');
    canvas.style.width=graph.width+'px';canvas.style.height=graph.height+'px';
    const svg=svgEl('svg',{viewBox:'0 0 '+graph.width+' '+graph.height,width:graph.width,height:graph.height,class:'story-connections','aria-hidden':'true'});
    const defs=svgEl('defs');const arrow=svgEl('marker',{id:'story-arrow',viewBox:'0 0 8 8',refX:7,refY:4,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});arrow.appendChild(svgEl('path',{d:'M0 0L8 4L0 8Z',fill:'#75d9be'}));defs.appendChild(arrow);svg.appendChild(defs);
    for(const edge of graph.edges){
      const from=graph.nodes.find(n=>n.id===edge.from),to=graph.nodes.find(n=>n.id===edge.to);
      const x1=from.x+from.w,y1=from.y+from.h/2,x2=to.x,y2=to.y+to.h/2;
      const mid=x1+(x2-x1)*.5;
      const route=from.x===to.x?'M'+(from.x+from.w/2)+' '+(from.y+from.h)+' V'+to.y
        :(edge.kind==='membership'&&to.id.startsWith('opp-')||x2-x1>300)?'M'+x1+' '+y1+' H'+(x1+18)+' V12 H'+(x2-20)+' V'+y2+' H'+x2
        :'M'+x1+' '+y1+' H'+mid+' V'+y2+' H'+x2;
      const path=svgEl('path',{d:route,class:'story-edge '+edge.kind,'marker-end':edge.kind==='prerequisite'?'url(#story-arrow)':undefined});svg.appendChild(path);
    }
    canvas.appendChild(svg);
    const nodeButtons=[];
    for(const node of graph.nodes){
      const button=element('button','story-node');button.type='button';button.dataset.storyPick=node.id;button.dataset.tone=node.tone;
      button.style.left=node.x+'px';button.style.top=node.y+'px';button.style.width=node.w+'px';button.style.height=node.h+'px';button.setAttribute('aria-pressed',String(galSelection===node.id));
      button.append(element('span','story-node-label',node.label),element('strong','story-node-title',node.title));
      button.addEventListener('click',()=>{
        galSelection=node.id;for(const [other,id] of nodeButtons)other.setAttribute('aria-pressed',String(id===node.id));
        const content=element('div','story-node-reading');content.appendChild(element('span','kind-tag',node.label));
        if(node.when)content.appendChild(element('p','when',DashboardHomeClient.formatClock(node.when)+' — '+DashboardHomeClient.formatClock(node.endsAt)));
        appendReading(content,node.text||node.title,{limit:1600});
        if(node.tone==='candidate'||node.tone==='realized'||node.tone==='ended')content.appendChild(element('p','section-note','这里保留候选的原说明；实际结果和角色经历在生活记录中另记。'));
        if(node.threads?.length)content.appendChild(element('p','section-note','关联故事线：'+node.threads.join(' / ')));
        openDetails(node.title,content,snapshot);
      });nodeButtons.push([button,node.id]);canvas.appendChild(button);
    }
    extent.appendChild(canvas);scroller.appendChild(extent);
    const fittedZoom=()=>window.matchMedia?.('(max-width:600px)')?.matches?1:Math.max(.65,Math.min(1,(scroller.clientWidth||1100)/graph.width));
    const applyZoom=()=>{const scale=storyZoom??fittedZoom();canvas.style.transform='scale('+scale+')';extent.style.width=graph.width*scale+'px';extent.style.height=graph.height*scale+'px';scroller.style.height=Math.min(window.matchMedia?.('(max-width:600px)')?.matches?340:378,Math.max(240,graph.height*scale+8))+'px';};applyZoom();window.requestAnimationFrame?.(applyZoom);
    const zoom=element('div','story-zoom');
    for(const [label,delta] of [['缩小',-.15],['适应视图',0],['放大',.15]]){const button=element('button','button',label);button.type='button';button.addEventListener('click',()=>{storyZoom=delta?Math.max(.55,Math.min(1.6,(storyZoom??fittedZoom())+delta)):null;applyZoom();});zoom.appendChild(button);}
    toolbar.appendChild(zoom);body.append(toolbar,scroller);
    const legend=element('div','story-map-legend');legend.append(element('span','legend-realized','已落实'),element('span','legend-candidate','未来候选'),element('span','legend-ended','窗口已结束'),element('span','','箭头：已记录的前置条件 · 虚线：主线归属'));
    body.append(legend,element('p','section-note','进展摘要与未决问题是作者的概括，不自动构成因果链。未来候选不代表已经发生或角色已知。'));
    return card;
  }
  function renderLifePanel(){
    if(lastSnapshot){renderLife(lastSnapshot);applyPresentation();}
  }
  function renderLife(snapshot){
    const host=byId('agendaPanel');
    const storyScroll=byId('storyPanel')?.querySelector('.story-map-viewport');
    const storyPosition=storyScroll&&!storyResetScroll?{left:storyScroll.scrollLeft,top:storyScroll.scrollTop}:null;storyResetScroll=false;
    const scroll=host.querySelector('.agenda-scroll');
    const position=scroll?{left:scroll.scrollLeft,top:scroll.scrollTop}:null;
    const focus=document.activeElement;
    const focusIndex=focus&&focus.dataset.agendaIndex;
    const storyFocus=['storyRoute','storyPick'].find(key=>focus?.dataset?.[key]!==undefined);
    const storyFocusValue=storyFocus?focus.dataset[storyFocus]:null;
    const filterFocused=focus&&focus.classList.contains('agenda-filter');
    const section=snapshot.sections.overview_life;
    const worldlineCard=renderWorldline(snapshot);
    const {card,body}=sectionCard('这一周的生活',section,'life');
    renderAgenda(body,snapshot);
    host.replaceChildren(card);
    if(byId('storyPanel'))byId('storyPanel').replaceChildren(worldlineCard);
    const nextStory=byId('storyPanel')?.querySelector('.story-map-viewport');
    if(nextStory&&storyPosition){const restore=()=>{nextStory.scrollLeft=storyPosition.left;nextStory.scrollTop=storyPosition.top;};restore();window.requestAnimationFrame?.(restore);}
    const nextScroll=host.querySelector('.agenda-scroll');
    if(nextScroll&&position){nextScroll.scrollLeft=position.left;nextScroll.scrollTop=position.top;}
    else if(nextScroll)nextScroll.scrollTop=344;
    if(storyFocus){const attr={storyRoute:'data-story-route',storyPick:'data-story-pick'}[storyFocus];byId('storyPanel')?.querySelector('['+attr+'="'+storyFocusValue+'"]')?.focus({preventScroll:true});}
    if(focusIndex!==undefined)host.querySelector('[data-agenda-index="'+focusIndex+'"]')?.focus({preventScroll:true});
    else if(filterFocused)host.querySelector('.agenda-filter')?.focus({preventScroll:true});
  }
  function renderDomain(snapshot,{title,sectionId,area,kinds,extraSources=[],note,filterViews}){
    const section=snapshot.sections[sectionId];
    const {card,body}=sectionCard(title,section,area);
    body.appendChild(element('p','section-note',note));
    const data=isRecord(section&&section.data)?section.data:null;
    if((!data||section.state==='unavailable')&&!extraSources.length)empty(body,title+'记录暂时不可用。');
    else{
      let views=DashboardHomeClient.visibleHighlights(snapshot,sectionId,kinds);
      for(const src of extraSources){
        views=views.concat(DashboardHomeClient.visibleHighlights(snapshot,src.sectionId,src.kinds));
      }
      if(Array.isArray(kinds)){
        views=views.slice().sort((a,b)=>(Date.parse(b.when)||0)-(Date.parse(a.when)||0));
      }
      let extraNote=null;
      if(typeof filterViews==='function'){
        const res=filterViews(views);
        views=res.views;extraNote=res.note;
      }
      if(area==='recent')body.className+=' recent-timeline';
      if(views.length)appendHighlights(body,views,{limit:area==='recent'?3:4,key:'domain-'+area});
      else empty(body,'暂无可展示的'+title+'摘要。');
      if(extraNote)body.appendChild(element('p','section-note',extraNote));
      appendCoverage(body,section);
    }
    sectionGrid.appendChild(card);
  }
  function renderPending(snapshot){
    const groups=DashboardHomeClient.pendingView(snapshot);
    const available=groups.filter(group=>group.state!=='unavailable');
    const state=!available.length?'unavailable':available.length!==groups.length?'degraded'
      :groups.some(group=>group.items.length)?'ready':'empty';
    const {card,body}=sectionCard('未完成事项',{state},'pending');
    body.appendChild(element('p','section-note','待续事项及相关记录，保留原有状态；不据时间推断此刻仍在等待。'));
    const terminalCodes=new Set(['resolved','completed','cancelled','fulfilled','superseded','expired','closed','withdrawn']);
    const openCount=groups.reduce((n,g)=>n+g.items.filter(v=>!terminalCodes.has(v.statusCode)).length,0);
    card.dataset.empty=String(openCount===0&&available.length===groups.length);
    const overview=element('div','pending-overview');
    overview.append(element('strong','',openCount?openCount+' 项待续':'暂无开放事项'),element('span','section-note','当前可见记录 · 承诺与回访'));
    body.appendChild(overview);
    const disclosure=rememberDisclosure(element('details','pending-disclosure'),'pending-all');
    disclosure.appendChild(element('summary','','查看事项与历史记录'));
    const columns=element('div','life-columns');
    for(const group of groups){
      const column=element('div','life-column');
      column.appendChild(element('h3','section-subtitle',group.label));
      if(group.state==='unavailable')empty(column,'这部分事项记录暂时不可用。');
      else if(!group.items.length)empty(column,'暂无可展示的事项记录。');
      else{
        const terminal=new Set(['resolved','completed','cancelled','fulfilled','superseded','expired','closed','withdrawn']);
        const current=group.items.filter(item=>!terminal.has(item.statusCode));
        const archived=group.items.filter(item=>terminal.has(item.statusCode));
        if(current.length)appendHighlights(column,current,{limit:3,key:'pending-'+group.sectionId});
        else empty(column,'本次可见记录中没有开放事项。');
        if(archived.length){
          const archive=rememberDisclosure(element('details','more-records'),'archived-'+group.sectionId);
          archive.appendChild(element('summary','','已结束的记录 · '+archived.length));
          appendHighlights(archive,archived);column.appendChild(archive);
        }
      }
      appendCoverage(column,snapshot.sections[group.sectionId]);
      columns.appendChild(column);
    }
    disclosure.appendChild(columns);
    if(openCount||available.length!==groups.length)body.appendChild(columns);
    else body.appendChild(disclosure);
    sectionGrid.appendChild(card);
  }
  function renderOperations(snapshot){
    const details=rememberDisclosure(element('details','operator-details operator-only'),'operator');details.dataset.focusArea='operations';
    details.appendChild(element('summary','','状态与诊断细节'));
    const grid=element('div','operator-grid');
    for(const id of ['authority_privacy','ledger_qualification','runtime_operations','operations','perception_media']){
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
        for(const label of [data.expression_episode?.mode_label,data.semantic_recall?.semantic_embedding_label]){
          if(typeof label==='string')body.appendChild(element('p','section-note',label));
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
    explorer.update(snapshot);
    sectionGrid.replaceChildren();
    renderLife(snapshot);
    renderVisualStudio(snapshot);
    renderDomain(snapshot,{
      title:'她看过的世界',sectionId:'perception_media',area:'perception',
      kinds:['external_perception'],note:'外部内容与她留下的看法。',
    });
    renderDomain(snapshot,{
      title:'你们的对话',sectionId:'operations',area:'chat',
      kinds:['expression_plan'],note:'表达记录保留原有发送状态。',
      filterViews(views){return {views:views.filter(v=>v.detail||v.title!=='主动找你'),note:null};},
    });
    renderDomain(snapshot,{
      title:'最近的生活',sectionId:'overview_life',area:'recent',
      kinds:['experience'],note:'按记录时间排列，顺序不表示因果。',
    });
    renderDomain(snapshot,{
      title:'记忆的片段',sectionId:'facts_memory_inner',area:'memory',
      kinds:['memory_candidate','fact'],note:'记忆候选与已确认事实，保留各自状态。',
    });
    renderDomain(snapshot,{
      title:'身边的人与心愿',sectionId:'overview_life',area:'people',
      kinds:['aspiration','biographical_coordinate'],
      extraSources:[{sectionId:'relationship_lifecycle',kinds:['npc']}],
      note:'人物、心愿和生活背景。',
      filterViews(views){
        const kept=views.filter(v=>!(v.kind==='npc'&&!v.detail&&['人物','身边人物','现有人物'].includes(v.title)));
        return {views:kept,note:kept.length<views.length?'未提供姓名或内容的人物条目已收起，可在运行观察中核对。':null};
      },
    });
    renderPending(snapshot);
    renderOperations(snapshot);
    applyPresentation();
    setCaptureState('ready');
    if(byId('costMeterDetails')?.open)loadCostMeter();
  }
  async function loadCostMeter(){
    const host=byId('costMeterPanel');
    if(!host||displayPaused)return;
    try{
      const resp=await window.fetch('/world-v2/dashboard/cost',{credentials:'same-origin',headers:{Accept:'application/json'}});
      if(!resp.ok)throw new Error('cost unavailable');
      const data=await resp.json();
      if(!isRecord(data)||!isRecord(data.summary))throw new Error('cost unavailable');
      renderCostMeter(host,data);
    }catch(_e){host.replaceChildren(element('p','section-note','成本记录暂时不可用。'));}
  }
  function fmtTokens(n){
    const v=Number(n||0);
    if(v>=1e6)return (v/1e6).toFixed(2)+'M';
    if(v>=1e3)return (v/1e3).toFixed(1)+'k';
    return String(v);
  }
  let selectedCostDay=null;
  function renderCostMeter(host,data){
    const s=data.summary;
    const money=value=>'¥'+Number(value||0).toFixed(2);
    const wrap=element('div','cost-meter-wrap');
    const kpis=element('div','cost-kpi-grid');
    for(const [label,value,note] of [
      ['今日用量成本',money(s.today_total_cny),'API '+money(s.today_api_cny)+' / 订阅折算 '+money(s.today_sub_equiv_cny)],
      ['近 7 日日均',money(s.days_7_avg_cny),'近 7 日合计 '+money(s.days_7_total_cny)],
      ['今日模型调用',String(s.today_calls||0)+' 次',fmtTokens(s.today_total_tokens)+' tokens · API 缓存命中 '+(Number(s.today_cache_hit_rate||0)*100).toFixed(1)+'%'],
    ]){
      const card=element('div','cost-kpi-card');
      card.append(element('span','cost-kpi-label',label),element('strong','cost-kpi-value',value),element('span','cost-kpi-sub',note));kpis.appendChild(card);
    }
    wrap.append(kpis,element('p','section-note','统计日期 '+String(s.today_date||'暂无')+' · 北京时间。成本包含订阅用量折算，不等同于实际付款账单。'));
    const purposes=Array.isArray(data.by_purpose)?data.by_purpose:[];
    const groups=new Map();
    for(const p of purposes){
      const name=p.category||'其他';
      if(!groups.has(name))groups.set(name,{name,total:0,today:0,items:[]});
      const group=groups.get(name);group.total+=Number(p.days_7_cny||0);group.today+=Number(p.today_cny||0);group.items.push(p);
    }
    const categories=[...groups.values()].sort((a,b)=>b.total-a.total);
    const colors=['#188f81','#80bfb1','#d5ad67','#687f91','#bba7c9'];
    const chartGrid=element('div','cost-chart-grid');
    const series=Array.isArray(data.daily_series)?data.daily_series:[];
    if(series.some(day=>Number(day.total_cny)>0)){
      const box=element('section','cost-panel-box cost-trend');
      box.append(element('h3','section-subtitle','每日成本'),element('p','section-note','深绿：后台与感知 · 金色：聊天。选择一天查看用量。'));
      const svg=svgEl('svg',{viewBox:'0 0 660 230',class:'cost-trend-svg',role:'group','aria-label':'每日成本，按左右键或 Tab 选择日期'});
      const max=Math.max(.01,...series.map(d=>Number(d.total_cny||0)));
      const top=20,bottom=190,left=42,plotWidth=604,step=plotWidth/series.length;
      for(const ratio of [0,.5,1]){
        const y=bottom-ratio*(bottom-top);
        svg.appendChild(svgEl('line',{x1:left,y1:y,x2:650,y2:y,stroke:'#dce9e4','stroke-dasharray':'3 5'}));
        svg.appendChild(svgEl('text',{x:34,y:y+4,'text-anchor':'end',class:'chart-axis'},(max*ratio).toFixed(1)));
      }
      const detail=element('div','cost-day-detail');detail.setAttribute('aria-live','polite');
      const nodes=[];
      const picker=element('select','cost-date-select');picker.setAttribute('aria-label','选择成本统计日期');
      for(const day of series){const option=element('option','',day.date+' · '+money(day.total_cny));option.value=day.date;picker.appendChild(option);}
      const select=index=>{
        const d=series[index];selectedCostDay=d.date;picker.value=d.date;
        nodes.forEach((node,i)=>node.setAttribute('aria-pressed',String(i===index)));
        detail.replaceChildren(element('strong','',String(d.date)+' · '+money(d.total_cny)),element('span','',d.calls+' 次调用 · '+fmtTokens(d.total_tokens)+' tokens'),element('span','', '后台 '+money(d.background_cny)+' / 聊天 '+money(d.interactive_cny)));
      };
      series.forEach((d,i)=>{
        const x=left+i*step+step/2,width=Math.min(26,step*.6);
        const h=Number(d.total_cny||0)/max*(bottom-top),fg=Number(d.interactive_cny||0)/max*(bottom-top);
        const node=svgEl('g',{role:'button',tabindex:0,'aria-label':d.date+' 成本 '+money(d.total_cny),class:'cost-day'});
        node.appendChild(svgEl('title',{},d.date+' · '+money(d.total_cny)));
        node.appendChild(svgEl('rect',{x:x-step/2+1,y:12,width:step-2,height:bottom-10,rx:5,class:'cost-day-hit'}));
        node.appendChild(svgEl('rect',{x:x-width/2,y:bottom-h,width,height:Math.max(h,1),rx:4,fill:'#248f80'}));
        if(fg>0)node.appendChild(svgEl('rect',{x:x-width/2,y:bottom-Math.min(h,fg),width,height:Math.min(h,fg),rx:2,fill:'#d5ad67'}));
        if(i%Math.max(1,Math.ceil(series.length/7))===0||i===series.length-1)node.appendChild(svgEl('text',{x,y:214,'text-anchor':'middle',class:'chart-axis'},String(d.label||d.date).slice(-5)));
        node.addEventListener('click',()=>select(i));
        node.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();select(i);}if(['ArrowLeft','ArrowRight'].includes(e.key)){e.preventDefault();const next=(i+(e.key==='ArrowRight'?1:-1)+nodes.length)%nodes.length;select(next);nodes[next].focus();}});
        nodes.push(node);svg.appendChild(node);
      });
      picker.addEventListener('change',()=>select(series.findIndex(day=>day.date===picker.value)));
      const previous=series.findIndex(day=>day.date===selectedCostDay);select(previous<0?series.length-1:previous);box.append(picker,svg,detail);chartGrid.appendChild(box);
    }
    const total=categories.reduce((sum,c)=>sum+c.total,0);
    const distribution=element('section','cost-panel-box cost-distribution');
    distribution.appendChild(element('h3','section-subtitle','成本流向 / 近 7 日'));
    const ring=svgEl('svg',{viewBox:'0 0 200 200',class:'cost-donut',role:'img','aria-label':'已分类模块近七日成本合计 '+money(total)});
    ring.appendChild(svgEl('circle',{cx:100,cy:100,r:72,fill:'none',stroke:'#e4eeea','stroke-width':17}));
    let offset=0;
    categories.forEach((group,i)=>{
      const ratio=total>0?group.total/total*100:0;
      if(ratio>0){
        const arc=svgEl('circle',{cx:100,cy:100,r:72,fill:'none',stroke:colors[i%colors.length],'stroke-width':17,pathLength:100,'stroke-dasharray':ratio+' '+(100-ratio),'stroke-dashoffset':-offset,transform:'rotate(-90 100 100)'});
        arc.appendChild(svgEl('title',{},group.name+' '+money(group.total)));ring.appendChild(arc);
      }
      offset+=ratio;
    });
    ring.appendChild(svgEl('text',{x:100,y:95,'text-anchor':'middle',class:'donut-label'},'已分类用量'));
    ring.appendChild(svgEl('text',{x:100,y:122,'text-anchor':'middle',class:'donut-total'},money(total)));
    distribution.appendChild(ring);
    const legend=element('div','cost-legend');
    categories.forEach((group,i)=>{const row=element('div','cost-legend-row');const dot=element('i','');dot.style.background=colors[i%colors.length];row.append(dot,element('span','',group.name),element('strong','',money(group.total)));legend.appendChild(row);});
    distribution.appendChild(legend);chartGrid.appendChild(distribution);wrap.appendChild(chartGrid);
    const split=element('div','cost-split-grid');
    const purposeBox=element('section','cost-panel-box');
    purposeBox.append(element('h3','section-subtitle','功能用量'),element('p','section-note','类别 → 功能。展开查看今日与近 7 日用量。'));
    for(const group of categories){
      const details=rememberDisclosure(element('details','cost-category'),'cost-category-'+group.name);
      const summary=element('summary','');summary.append(element('strong','',group.name),element('span','cost-number',money(group.total)),element('span','disclosure-plus','+'));
      const list=element('div','cost-purpose-list');
      const inactive=rememberDisclosure(element('details','cost-inactive'),'cost-inactive-'+group.name);
      const unused=group.items.filter(p=>!Number(p.days_7_calls)&&!Number(p.today_calls)&&!Number(p.days_7_cny)&&!Number(p.today_cny));
      inactive.appendChild(element('summary','','近 7 日未调用的模块 · '+unused.length));
      for(const p of group.items.slice().sort((a,b)=>Number(b.days_7_cny||0)-Number(a.days_7_cny||0))){
        const row=element('div','cost-purpose-row');
        const title=element('div','cost-purpose-head');title.append(element('strong','',p.label||p.purpose),element('span','cost-number',money(p.days_7_cny)));
        const meter=svgEl('svg',{viewBox:'0 0 300 4',preserveAspectRatio:'none',class:'cost-purpose-meter','aria-hidden':'true'});
        meter.append(svgEl('rect',{width:300,height:4,rx:2,fill:'#e3ede8'}),svgEl('rect',{width:group.total?Math.max(0,Number(p.days_7_cny||0)/group.total*300):0,height:4,rx:2,fill:'#3a9b8b'}));
        row.append(title,meter,element('p','cost-purpose-meta','今日 '+money(p.today_cny)+' · '+Number(p.today_calls||0)+' 次 / 7 日 '+Number(p.days_7_calls||0)+' 次 · '+fmtTokens(p.days_7_tokens)+' tokens'));(unused.includes(p)?inactive:list).appendChild(row);
      }
      if(unused.length)list.appendChild(inactive);
      details.append(summary,list);purposeBox.appendChild(details);
    }
    if(!categories.length)empty(purposeBox,'暂无可展示的分类用量。');
    const modelsBox=element('section','cost-panel-box');modelsBox.appendChild(element('h3','section-subtitle','模型与计费通道'));
    const models=Array.isArray(data.by_model)?data.by_model:[];
    for(const m of models){
      const detail=rememberDisclosure(element('details','cost-category cost-model'),'cost-model-'+m.model+'-'+m.billing_mode);
      const summary=element('summary','');const label=element('div','');label.append(element('strong','',m.model),element('span','cost-model-mode',m.billing_mode==='subscription'?'订阅折算':'API 计费'));
      summary.append(label,element('span','cost-number',money(m.days_7_cny)),element('span','disclosure-plus','+'));
      detail.append(summary,element('p','cost-purpose-meta','今日 '+money(m.today_cny)+' · '+Number(m.today_calls||0)+' 次 · '+fmtTokens(m.today_tokens)+' tokens'),element('p','cost-purpose-meta','缓存命中 '+(Number(m.cache_hit_rate||0)*100).toFixed(1)+'% · 平均 '+(Number(m.avg_latency_ms||0)/1000).toFixed(1)+' 秒 · 累计 '+money(m.all_time_cny)));
      modelsBox.appendChild(detail);
    }
    modelsBox.appendChild(element('p','section-note','右侧金额为近 7 日用量成本。'));
    split.append(purposeBox,modelsBox);wrap.appendChild(split);
    const audit=rememberDisclosure(element('details','cost-audit'),'cost-audit');
    audit.appendChild(element('summary','','历史累计 '+money(s.all_time_total_cny)+' · '+fmtTokens(s.all_time_calls)+' 次调用 · 查看最近流水'));
    const records=element('div','cost-recent-list');
    for(const r of (Array.isArray(data.recent_records)?data.recent_records:[]).slice(0,10)){
      const row=element('div','cost-recent-row');row.append(element('time','cost-recent-time',r.time||''),element('span','cost-recent-purpose',r.purpose_label||r.purpose),element('span','cost-recent-model',r.model||''),element('span','cost-recent-tok',fmtTokens(r.total_tokens)),element('strong','cost-recent-cny','¥'+Number(r.cost_cny||0).toFixed(4)));records.appendChild(row);
    }
    audit.appendChild(records);wrap.appendChild(audit);host.replaceChildren(wrap);
  }
  async function loadDashboardHome(){
    if(loading||displayPaused)return;
    loading=true;
    try{
      const result=await DashboardHomeClient.capture(window.fetch.bind(window),snapshotEtag,{week:agendaWeek,page:agendaPage});
      // A response already in flight must not replace a deliberately frozen view.
      if(displayPaused)return;
      const snapshot=DashboardHomeClient.snapshotFromCapture(result,lastSnapshot);
      if(result.kind==='not_modified'){
        setCaptureState('ready');
        return;
      }
      // Render the authorized page immediately. Additional pages are explicit
      // navigation, never stitched across changing World snapshot cursors.
      snapshotEtag=result.etag;
      lastSnapshot=snapshot;
      renderSnapshot(snapshot);
    }catch(error){
      if(displayPaused)return;
      // The payload and upstream error never become visible page content.
      setCaptureState(lastSnapshot?'stale':'unavailable');
      if(pendingAgendaCell){pendingAgendaCell=null;byId('drawerBody')?.appendChild(element('p','agenda-warning','这次读取没有成功，请关闭后重试。'));}
      if(!lastSnapshot){
        explorer.update({sections:{}});
        renderNowStory({sections:{}});
        sectionGrid.replaceChildren();
        empty(sectionGrid,'尚未取得可验证的生活快照。');
      }
    }finally{loading=false;if(resumePending){resumePending=false;loadDashboardHome();}}
  }
  explorer.update({sections:{}});
  applyPresentation();
  loadDashboardHome();
  setInterval(loadDashboardHome,15000);
}
"""

# Keep the explorer's presentation and pure view model separate from the
# authenticated shell. Both ship as Python package code, with no asset-loader
# or build-time dependency and no new browser data route.
DASHBOARD_HTML = (
    DASHBOARD_HTML.replace("</style>", EXPLORER_CSS + DASHBOARD_CSS + "</style>")
    .replace(
        '<div id="sectionGrid" class="section-grid"></div>',
        '<div id="agendaPanel"></div><div id="storyPanel"></div>'
        '<details id="costMeterDetails" class="domain-details operator-only">'
        '<summary>成本与用量</summary>'
        '<div id="costMeterPanel" class="section-card"><p class="section-note">正在加载实时成本计价数据…</p></div>'
        '</details>'
        '<details id="domainDetails" class="domain-details" open>'
        '<summary>生活记录</summary>'
        '<div id="sectionGrid" class="section-grid"></div>'
        '</details>'
        '<details id="explorerDetails" class="domain-details">'
        '<summary>运行机制与记录检索</summary>'
        + EXPLORER_HTML
        + '</details>',
    )
    .replace('<option value="now">这一刻</option>', '<option value="now">这一刻</option><option value="mechanism">运行机制</option><option value="evidence">记录浏览</option>')
    .replace('<span class="readonly">只读 · 不改变她的生活</span>', '<div class="view-tools"><span id="pauseLabel" class="pause-label" hidden>画面已暂停 · 不暂停角色进程</span><button id="pauseDisplay" type="button" class="button" aria-pressed="false">暂停画面</button><span class="readonly">只读 · 不改变她的生活</span></div>')
)
DASHBOARD_APP_JS = DASHBOARD_APP_JS.replace(
    "if(typeof module!=='undefined'&&module.exports)",
    EXPLORER_JS + "\nif(typeof module!=='undefined'&&module.exports)",
)


__all__ = [
    "DASHBOARD_APP_JS",
    "DASHBOARD_HTML",
    "DASHBOARD_SESSION_COOKIE",
    "DASHBOARD_SESSION_TTL_SECONDS",
    "DashboardSessionCodec",
    "LOGIN_HTML",
    "UNAVAILABLE_HTML",
]
