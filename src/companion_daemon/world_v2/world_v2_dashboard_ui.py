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

from .dashboard_explorer_ui import EXPLORER_JS
from .dashboard_stage_css import STAGE_CSS
from .dashboard_stage_html import STAGE_BODY_HTML
from .dashboard_stage_model import STAGE_MODEL_JS
from .dashboard_stage_render_core import STAGE_RENDER_CORE_JS
from .dashboard_stage_render_rest import STAGE_RENDER_REST_JS


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
"""

# The stage presentation reads the same authorized owner endpoints as before.
# Pure view models stay importable by tests; DOM rendering only runs in a
# browser document.
DASHBOARD_HTML = (
    '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    '<meta name="color-scheme" content="dark"><title>沈知栀 · 生活现场</title>'
    "<style>" + STAGE_CSS + "</style></head><body data-phase=\"night\">"
    + STAGE_BODY_HTML
    + '<script src="/world-v2/dashboard/app.js?v=world-v2-dashboard-home.1-stage1" defer></script>'
    "</body></html>"
)
DASHBOARD_APP_JS = DASHBOARD_APP_JS.replace(
    "if(typeof module!=='undefined'&&module.exports)",
    EXPLORER_JS + STAGE_MODEL_JS + "\nif(typeof module!=='undefined'&&module.exports)",
) + (
    "if(typeof document!=='undefined'){\n"
    + STAGE_RENDER_CORE_JS
    + STAGE_RENDER_REST_JS
    + "}\n"
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
