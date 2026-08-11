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
<title>World v2 Dashboard 登录</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#d9cdbc;color:#3f342d;font-family:"PingFang SC",system-ui,sans-serif}
main{width:min(420px,calc(100% - 32px));padding:28px;background:#f7eedf;border:3px solid #684f42;box-shadow:6px 6px 0 #b79c84}
h1{font-size:20px}label,input,button{display:block;width:100%}input,button{margin-top:10px;padding:11px;font:inherit;box-sizing:border-box}button{background:#557f78;color:white;border:0}p{line-height:1.6;font-size:13px}
</style></head><body><main><h1>World v2 Dashboard</h1>
<p>请输入本机配置的 operator token。凭证只通过本次 POST 提交，不会写入 URL、页面脚本或浏览器存储。</p>
<form method="post" action="/world-v2/dashboard/session" autocomplete="off">
<label for="operator-token">Operator token</label><input id="operator-token" name="operator_token" type="password" required autocomplete="current-password">
<button type="submit">进入只读 Dashboard</button></form></main></body></html>"""


UNAVAILABLE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>World v2 Dashboard unavailable</title></head><body>
<main><h1>World v2 Dashboard unavailable</h1><p>只读 World v2 host 尚未初始化。不会回退到旧运行时。</p></main>
</body></html>"""


DASHBOARD_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>World v2 Dashboard</title><style>
:root{font-family:"PingFang SC",system-ui,sans-serif;color:#3f342d;background:#d9cdbc}*{box-sizing:border-box}body{margin:0;min-height:100vh}.bar{padding:14px 24px;background:#4d3b34;color:#fff8ea;display:flex;justify-content:space-between;align-items:center;gap:16px}.bar h1{font-size:18px;margin:0}.bar-actions{display:flex;align-items:center;gap:14px}.capture-state{font:12px ui-monospace,SFMono-Regular,monospace}.logout{margin:0}.logout button{border:1px solid #d9cdbc;border-radius:5px;background:transparent;color:#fff8ea;padding:5px 9px;font:12px inherit;cursor:pointer}.wrap{max-width:1440px;margin:auto;padding:22px;display:grid;grid-template-columns:minmax(0,1.55fr) minmax(330px,.72fr);align-items:start;gap:18px}.room,.panel,.section-card{background:#f7eedf;border:3px solid #684f42;box-shadow:5px 5px 0 #b79c84}.room{position:relative;overflow:hidden;aspect-ratio:7/4}.room iframe{display:block;border:0;background:#211b1a;image-rendering:pixelated;pointer-events:none;position:absolute;top:0;left:0;width:1120px;height:640px;transform-origin:top left}.room-overlay{position:absolute;inset:0;display:grid;place-items:center;background:rgba(33,27,26,.58);color:#fff8ea;font:14px ui-monospace,SFMono-Regular,monospace;z-index:1}.room-overlay[hidden]{display:none}.room-edit{position:absolute;z-index:2;top:10px;right:10px;padding:7px 10px;border:1px solid #fff3d5;border-radius:6px;background:rgba(77,59,52,.88);color:#fff8ea;font-size:12px;text-decoration:none;box-shadow:0 2px 8px rgba(0,0,0,.28)}.room-edit:focus-visible,.logout button:focus-visible{outline:3px solid #e8c568;outline-offset:2px}.side{display:grid;gap:18px}.panel,.section-card{padding:16px}.panel h2,.section-card h2{margin:0;font-size:16px}.meta,.tree{margin:0;display:grid;grid-template-columns:minmax(110px,.42fr) minmax(0,1fr);gap:7px 10px}.meta dt,.tree dt{font:11px ui-monospace,SFMono-Regular,monospace;color:#80685b;overflow-wrap:anywhere}.meta dd,.tree dd{margin:0;min-width:0;overflow-wrap:anywhere}.tree ul{margin:0;padding-left:20px}.tree code,.meta code{font:11px ui-monospace,SFMono-Regular,monospace}.section-grid{grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));align-items:start;gap:18px}.section-card[data-section="runtime_operations"]{grid-column:1/-1}.section-head{display:flex;justify-content:space-between;gap:12px;align-items:start;padding-bottom:12px;border-bottom:1px solid #d7c7b7}.section-key{display:block;margin-top:3px;color:#80685b;font:10px ui-monospace,SFMono-Regular,monospace}.section-state{border-radius:999px;padding:3px 8px;background:#d8e4dc;color:#3d6d63;font:11px ui-monospace,SFMono-Regular,monospace}.section-state[data-state="unavailable"],.section-state[data-state="degraded"]{background:#edd6d2;color:#90483f}.section-state[data-state="empty"],.section-state[data-state="disabled"]{background:#e8e0d2;color:#766455}.section-body{display:grid;gap:14px;padding-top:14px}.section-subtitle{margin:0 0 7px;font-size:12px;color:#6f584d}.metric-grid,.signal-grid,.notice-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:8px}.metric,.signal,.notice{min-width:0;border:1px solid #d5c3b1;background:#fff8eb;padding:8px 10px}.metric span,.signal span,.notice span{display:block;color:#725e53;font-size:11px;overflow-wrap:anywhere}.metric strong,.signal strong,.notice strong{display:block;margin-top:3px;font-size:16px}.signal strong,.notice strong{font-size:12px;color:#3d6d63}.notice code{display:block;margin-top:4px;color:#80685b;font:10px ui-monospace,SFMono-Regular,monospace;overflow-wrap:anywhere}.zero-metrics,.section-contract,.section-items{border-top:1px dashed #cbb6a3;padding-top:10px}.zero-metrics summary,.section-contract summary,.section-items summary{cursor:pointer;color:#735d50;font-size:12px}.zero-metrics .metric-grid,.section-contract .tree,.section-items .tree{margin-top:10px}.error{color:#c87b73}@media(max-width:900px){.wrap{grid-template-columns:1fr}.section-grid{grid-template-columns:1fr}.section-card[data-section="runtime_operations"]{grid-column:auto}}@media(max-width:520px){.bar{padding:12px 14px}.wrap{padding:14px}.capture-state{display:none}.metric-grid,.signal-grid,.notice-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style></head><body><header class="bar"><h1>World v2 Dashboard</h1><div class="bar-actions"><span id="captureState" class="capture-state">unavailable</span><form class="logout" method="post" action="/world-v2/dashboard/logout"><button type="submit">退出</button></form></div></header>
<main class="wrap"><section class="room"><iframe id="roomVisual" src="/pixel-home/index.html?embed=1" title="World v2 room renderer" aria-label="World v2 room renderer" scrolling="no"></iframe><div id="roomOverlay" class="room-overlay">unavailable</div><a class="room-edit" href="/pixel-home/index.html?edit=1" target="_blank" rel="noopener" aria-label="在独立页面编辑渲染房间">✎ 编辑房间</a></section>
<aside class="side"><section class="panel"><h2>Snapshot</h2><dl id="snapshotMeta" class="meta"></dl></section></aside><div id="sectionGrid" class="section-grid" aria-live="polite"></div></main>
<script src="/world-v2/dashboard/app.js?v=world-v2-dashboard-home.1-ui2" defer></script></body></html>"""


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
  function sectionLabel(sectionId,section){
    if(record(section)&&typeof section.label==='string'&&section.label.trim())return section.label;
    return sectionId;
  }
  function metricGroups(metrics){
    const grouped={active:[],zero:[]};
    if(!Array.isArray(metrics))return grouped;
    for(const metric of metrics){
      if(!record(metric))continue;
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
    roomMessageFrom,
    sectionLabel,
    metricGroups,
    snapshotFromCapture,
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
  function showRoomMessage(message){
    roomMessage=message;
    const ready=message.state==='ready';
    roomOverlay.hidden=ready;
    roomOverlay.textContent=message.state;
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
        term.textContent=key;
        const detail=document.createElement('dd');
        appendTree(detail,item);
        list.append(term,detail);
      }
      parent.appendChild(list);
      return;
    }
    appendScalar(parent,value);
  }
  function renderMeta(snapshot){
    snapshotMeta.replaceChildren();
    const fields=['schema_version','policy_version','snapshot_hash','owner','cursor','logical_time','generated_at'];
    for(const key of fields){
      if(!(key in snapshot))continue;
      const term=document.createElement('dt');
      term.textContent=key;
      const detail=document.createElement('dd');
      appendTree(detail,snapshot[key]);
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
        label.textContent=typeof metric.label==='string'?metric.label:String(metric.key||'metric');
        const value=document.createElement('strong');
        value.textContent=String(metric.count??0);
        tile.append(label,value);
        grid.appendChild(tile);
      }
      return grid;
    }
    if(groups.active.length){
      const title=document.createElement('h3');
      title.className='section-subtitle';
      title.textContent='当前计数';
      parent.append(title,buildGrid(groups.active));
    }
    if(groups.zero.length){
      const details=document.createElement('details');
      details.className='zero-metrics';
      const summary=document.createElement('summary');
      summary.textContent='零值字段 ('+groups.zero.length+')';
      details.append(summary,buildGrid(groups.zero));
      parent.appendChild(details);
    }
  }
  function appendSignalGrid(parent,signals){
    if(!Array.isArray(signals)||!signals.length)return;
    const title=document.createElement('h3');
    title.className='section-subtitle';
    title.textContent='运行态信号';
    const grid=document.createElement('div');
    grid.className='signal-grid';
    for(const signal of signals){
      if(!isRecord(signal))continue;
      const tile=document.createElement('div');
      tile.className='signal';
      const label=document.createElement('span');
      label.textContent=typeof signal.label==='string'?signal.label:String(signal.key||'signal');
      const value=document.createElement('strong');
      value.textContent=typeof signal.state_label==='string'?signal.state_label:String(signal.state||'unavailable');
      tile.append(label,value);
      grid.appendChild(tile);
    }
    parent.append(title,grid);
  }
  function appendNoticeGrid(parent,notices){
    if(!Array.isArray(notices)||!notices.length)return;
    const title=document.createElement('h3');
    title.className='section-subtitle';
    title.textContent='提示';
    const grid=document.createElement('div');
    grid.className='notice-grid';
    for(const notice of notices){
      if(!isRecord(notice))continue;
      const tile=document.createElement('div');
      tile.className='notice';
      const label=document.createElement('span');
      label.textContent=String(notice.signal_label||notice.label||notice.signal||notice.kind||'notice');
      const reason=document.createElement('strong');
      reason.textContent=String(notice.reason_label||'');
      const code=document.createElement('code');
      code.textContent=String(notice.reason_code||'');
      tile.append(label,reason,code);
      grid.appendChild(tile);
    }
    parent.append(title,grid);
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
    summary.textContent='契约、cursor 与 coverage';
    details.appendChild(summary);
    appendTree(details,contract);
    parent.appendChild(details);
  }
  function appendSectionData(parent,section){
    const data=isRecord(section&&section.data)?section.data:null;
    if(!data)return;
    appendMetricGrid(parent,data.metrics);
    appendSignalGrid(parent,data.signals);
    appendNoticeGrid(parent,data.notices);
    const remaining={};
    for(const [key,value] of Object.entries(data)){
      if(key==='metrics'||key==='signals'||key==='notices'||key==='highlights')continue;
      remaining[key]=value;
    }
    if(Object.keys(remaining).length){
      const container=document.createElement('div');
      appendTree(container,remaining);
      parent.appendChild(container);
    }
    if(Array.isArray(data.highlights)&&data.highlights.length){
      const details=document.createElement('details');
      details.className='section-items';
      const summary=document.createElement('summary');
      summary.textContent='条目 ('+data.highlights.length+')';
      details.appendChild(summary);
      appendTree(details,data.highlights);
      parent.appendChild(details);
    }
  }
  function renderSections(snapshot){
    sectionGrid.replaceChildren();
    for(const [sectionId,section] of Object.entries(snapshot.sections)){
      const card=document.createElement('section');
      card.className='section-card';
      card.dataset.section=sectionId;
      const head=document.createElement('div');
      head.className='section-head';
      const titleWrap=document.createElement('div');
      const title=document.createElement('h2');
      title.textContent=DashboardHomeClient.sectionLabel(sectionId,section);
      const key=document.createElement('small');
      key.className='section-key';
      key.textContent=sectionId;
      titleWrap.append(title,key);
      const state=document.createElement('span');
      state.className='section-state';
      state.textContent=section&&typeof section.state==='string'?section.state:'unavailable';
      state.dataset.state=state.textContent;
      head.append(titleWrap,state);
      card.appendChild(head);
      const body=document.createElement('div');
      body.className='section-body';
      appendSectionData(body,section);
      appendSectionContract(body,section);
      card.appendChild(body);
      sectionGrid.appendChild(card);
    }
  }
  function renderSnapshot(snapshot){
    renderMeta(snapshot);
    renderSections(snapshot);
    captureState.textContent='ready';
    captureState.classList.remove('error');
    showRoomMessage(DashboardHomeClient.roomMessageFrom(snapshot));
  }
  async function loadDashboardHome(){
    try{
      const result=await DashboardHomeClient.capture(window.fetch.bind(window),snapshotEtag);
      const snapshot=DashboardHomeClient.snapshotFromCapture(result,lastSnapshot);
      if(result.kind==='not_modified'){
        lastSnapshot=snapshot;
        captureState.textContent='ready';
        captureState.classList.remove('error');
        showRoomMessage(DashboardHomeClient.roomMessageFrom(snapshot));
        return;
      }
      snapshotEtag=result.etag;
      lastSnapshot=snapshot;
      renderSnapshot(snapshot);
    }catch(error){
      console.error('Dashboard refresh failed',error instanceof Error?error.message:'unknown');
      const state=lastSnapshot?'stale':'unavailable';
      captureState.textContent=state;
      captureState.classList.add('error');
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
