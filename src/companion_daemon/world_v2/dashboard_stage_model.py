"""Pure view models for the owner Dashboard "stage" presentation.

Everything here reads the already-redacted owner DTOs (home snapshot, studio,
cost, voice) and only re-arranges recorded values for display: layout
coordinates, ordering, grouping and labels.  Nothing in this module chooses
what the character does, infers causality between records, or fabricates a
value that the owner contract did not provide.  Missing data stays missing.
"""

STAGE_MODEL_JS = r"""
const DashboardStage=(()=>{
  const DAY=86400000,TZ=8*3600000;
  const rec=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const list=v=>Array.isArray(v)?v:[];
  const ms=v=>{if(typeof v!=='string'||!v)return null;const t=Date.parse(v);return Number.isFinite(t)?t:null;};
  const dayKey=t=>new Date(t+TZ).toISOString().slice(0,10);
  const dayStart=t=>Date.parse(dayKey(t)+'T00:00:00+08:00');
  const minuteOfDay=t=>(((t+TZ)%DAY)+DAY)%DAY/60000;
  const WEEKDAYS='日一二三四五六';
  const weekday=t=>WEEKDAYS[new Date(t+TZ).getUTCDay()];
  const pad=n=>String(n).padStart(2,'0');
  const TERMINAL=new Set(['fulfilled','superseded','expired','cancelled','closed','withdrawn','resolved','completed','abandoned','decayed']);
  const PALETTE=['#f6a6c1','#86ccff','#ffcf86','#9ef0c4','#c6b4ff','#ff9f8c'];
  const KIND_LABELS={planning:'客观课表',plan:'她的计划',experience:'亲身经历',world:'世界事件',outlook:'命运候选',commitment:'约定',revisit:'再想一想',expectation:'回应期待'};
  function hm(t){const d=new Date(t+TZ);return pad(d.getUTCHours())+':'+pad(d.getUTCMinutes());}
  function md(t){const d=new Date(t+TZ);return (d.getUTCMonth()+1)+'/'+d.getUTCDate();}
  function longDate(iso){
    const t=ms(iso);if(t===null)return '';
    const d=new Date(t+TZ);
    return (d.getUTCMonth()+1)+'月'+d.getUTCDate()+'日 · 周'+weekday(t)+' · '+hm(t);
  }
  // Display palette only: the hero sky follows the recorded world clock.
  function phase(iso){
    const t=ms(iso);if(t===null)return 'night';
    const h=minuteOfDay(t)/60;
    return h<5?'night':h<7.5?'dawn':h<16.5?'day':h<19?'dusk':'night';
  }
  function pairs(snapshot,client,sectionId,kinds){
    const section=rec(snapshot)&&rec(snapshot.sections)?snapshot.sections[sectionId]:null;
    if(!rec(section)||section.state==='unavailable'||!rec(section.data))return [];
    return list(section.data.highlights).filter(h=>rec(h)&&(!kinds||kinds.includes(h.kind)))
      .map(h=>({raw:h,view:client.highlightView(h)})).filter(p=>p.view);
  }
  const newest=items=>items.slice().sort((a,b)=>(ms(b.view?b.view.when:b.when)||0)-(ms(a.view?a.view.when:a.when)||0));
  function rawValue(raw,key){
    const entry=list(raw&&raw.values).find(v=>rec(v)&&v.key===key);
    return entry?entry.value:null;
  }
  function rawLabel(raw,key){
    const entry=list(raw&&raw.values).find(v=>rec(v)&&v.key===key);
    return entry&&typeof entry.value_label==='string'?entry.value_label:null;
  }
  function metric(snapshot,sectionId,key){
    const section=rec(snapshot)&&rec(snapshot.sections)?snapshot.sections[sectionId]:null;
    if(!rec(section)||section.state==='unavailable'||!rec(section.data))return null;
    const found=list(section.data.metrics).find(m=>rec(m)&&m.key===key);
    return found&&Number.isFinite(found.count)?found.count:null;
  }
  function present(snapshot,client){
    const plans=newest(pairs(snapshot,client,'overview_life',['plan']));
    const active=plans.find(p=>p.view.statusCode==='active')||null;
    const activity=active||plans[0]||null;
    let span=null;
    if(activity){
      const a=ms(rawValue(activity.raw,'scheduled_start')),b=ms(rawValue(activity.raw,'scheduled_end'));
      if(a!==null&&b!==null)span={start:a,end:b};
    }
    const first=(sectionId,kinds,filter=()=>true)=>newest(pairs(snapshot,client,sectionId,kinds)).find(p=>filter(p))||null;
    const waits=newest(pairs(snapshot,client,'operations',['response_expectation'])).filter(p=>!TERMINAL.has(p.view.statusCode));
    const bonds=pairs(snapshot,client,'relationship_lifecycle',['relationship_state']);
    return {
      activity,activityIsActive:!!active,window:span,
      intentSource:activity?rawLabel(activity.raw,'intent_source'):null,
      occurrence:first('overview_life',['world_occurrence'],p=>!!(p.view.detail||'').trim()),
      experience:first('overview_life',['experience']),
      location:first('overview_life',['location']),
      affect:first('facts_memory_inner',['affect_episode'],p=>!TERMINAL.has(p.view.statusCode)),
      bond:(bonds.find(p=>p.view.title==='和你')||null),
      waits,
    };
  }
  // ---- Week strips -------------------------------------------------------
  function intervalLanes(items,startKey,endKey){
    const sorted=items.slice().sort((a,b)=>a[startKey]-b[startKey]||a[endKey]-b[endKey]);
    const ends=[];
    for(const item of sorted){
      let lane=ends.findIndex(end=>end<=item[startKey]);
      if(lane<0)lane=ends.length;
      item.lane=lane;ends[lane]=item[endKey];
    }
    return Math.max(1,ends.length);
  }
  function weekStrips(snapshot,hidden){
    const off=hidden instanceof Set?hidden:new Set();
    const section=rec(snapshot)&&rec(snapshot.sections)?snapshot.sections.overview_life:null;
    const agenda=rec(section)&&section.state!=='unavailable'&&rec(section.data)?section.data.agenda:null;
    if(!rec(agenda)||agenda.state==='unavailable')return null;
    const start=Date.parse(agenda.week_start+'T00:00:00+08:00');
    if(!Number.isFinite(start))return null;
    const entries=list(agenda.entries).filter(e=>rec(e)&&e.content_state!=='withheld');
    const covered=new Set();
    for(const e of entries)if(e.kind==='experience')for(const r of list(e.related))covered.add(r);
    const phases=[],seenPhase=new Set();
    const now=ms(snapshot.logical_time);
    const days=[];
    for(let d=0;d<7;d++){
      const from=start+d*DAY,to=from+DAY;
      const day={index:d,date:dayKey(from+DAY/2),from,to,weekday:'一二三四五六日'[d],today:now!==null&&now>=from&&now<to,
        nowMinute:now!==null&&now>=from&&now<to?(now-from)/60000:null,course:[],acts:[],dots:[],windows:[],unloaded:[],actLanes:1,windowLanes:0,counts:{}};
      for(const e of entries){
        const a=ms(e.starts_at),b0=ms(e.ends_at);
        if(a===null)continue;
        const b=b0===null?a:Math.max(a,b0);
        if(!(a<to&&(b>from||(a===b&&a>=from))))continue;
        const isWindow=['expectation','revisit','commitment'].includes(e.kind)||e.time_kind==='window';
        const isPeriod=e.time_kind==='period'||e.kind==='outlook';
        if(isPeriod){
          const key=JSON.stringify([e.index,e.kind]);
          if(!seenPhase.has(key)){seenPhase.add(key);phases.push(e);}
          continue;
        }
        if(off.has(e.kind))continue;
        if(e.kind==='world'&&(covered.has(e.index)||e.title==='世界事件'))continue;
        const s=Math.max(a,from),f=Math.min(Math.max(b,a),to);
        const seg={entry:e,start:(s-from)/60000,end:Math.max((f-from)/60000,(s-from)/60000),kind:e.kind,abandoned:e.kind==='plan'&&e.status==='已放弃'};
        day.counts[e.kind]=(day.counts[e.kind]||0)+1;
        if(isWindow)day.windows.push({...seg,end:Math.max(seg.end,seg.start+20)});
        else if(e.kind==='world')day.dots.push(seg);
        else if(e.kind==='planning')day.course.push({...seg,end:Math.max(seg.end,seg.start+30)});
        else day.acts.push({...seg,end:Math.max(seg.end,seg.start+24)});
      }
      day.actLanes=intervalLanes(day.acts,'start','end');
      day.windowLanes=day.windows.length?intervalLanes(day.windows,'start','end'):0;
      intervalLanes(day.course,'start','end');
      days.push(day);
    }
    // Whole-week slot counts let us mark "known but not on this page"
    // without treating unloaded hours as empty time.
    if(Array.isArray(agenda.week_slots)){
      for(const slot of agenda.week_slots){
        if(!rec(slot)||!Number.isInteger(slot.hour))continue;
        const day=days.find(item=>item.date===slot.date);if(!day)continue;
        const loaded=[...day.course,...day.acts,...day.dots,...day.windows].some(seg=>Math.floor(seg.start/120)*2===slot.hour);
        if(!loaded&&Number(slot.known_count)>0)day.unloaded.push({hour:slot.hour,count:Number(slot.known_count),labels:list(slot.labels).filter(l=>typeof l==='string'),firstPage:slot.first_page});
      }
    }
    return {agenda,start,entries,days,phases,
      page:Number(agenda.page||0),pageCount:Number(agenda.page_count||1),truncated:!!agenda.truncated,
      kinds:[...new Set(entries.map(e=>e.kind))].filter(k=>Object.hasOwn(KIND_LABELS,k)&&!['outlook'].includes(k))};
  }
  // ---- Route (story) map -------------------------------------------------
  function routeMap(worldline,nowIso,{pxPerDay=132}={}){
    if(!rec(worldline))return null;
    const threads=list(worldline.threads).filter(rec);
    const opps=list(worldline.opportunities).filter(rec);
    const stages=list(worldline.stages).filter(rec);
    if(!threads.length&&!opps.length)return null;
    let now=ms(nowIso);
    const openTimes=opps.map(o=>ms(o.opens_at)).filter(t=>t!==null);
    if(now===null)now=openTimes.length?Math.min(...openTimes):null;
    if(now===null)return null;
    const start=dayStart(now)-2*DAY;
    let last=now+12*DAY;
    for(const o of opps){const t=ms(o.closes_at)??ms(o.opens_at);if(t!==null)last=Math.max(last,t);}
    last=Math.min(last,start+70*DAY);
    const end=dayStart(last)+DAY;
    const PAD_L=150,PAD_R=210,TOP=118,GAP=132;
    const xOf=t=>PAD_L+(Math.max(start,Math.min(end,t))-start)/DAY*pxPerDay;
    const laneIndexes=o=>{
      let idx=list(o.thread_indexes).filter(i=>Number.isInteger(i)&&i>=0&&i<threads.length);
      if(!idx.length&&Array.isArray(o.thread_titles))idx=threads.map((t,i)=>o.thread_titles.includes(t.title)?i:-1).filter(i=>i>=0);
      return [...new Set(idx)].sort((a,b)=>a-b);
    };
    const lanes=threads.map((t,i)=>({index:i,key:'lane-'+i,letter:String.fromCharCode(65+i),title:String(t.title||'未命名故事线'),status:typeof t.status==='string'?t.status:'',
      situation:typeof t.situation==='string'?t.situation:'',question:typeof t.open_question==='string'?t.open_question:'',
      developments:list(t.developments).filter(s=>typeof s==='string'&&s.trim()),color:PALETTE[i%PALETTE.length],y:TOP+i*GAP}));
    let orphan=null;
    if(opps.some(o=>!laneIndexes(o).length)){
      orphan={index:-1,key:'lane-orphan',letter:'∗',title:'未归属的机缘',status:'',situation:'这些候选没有记录所属故事线。',question:'',developments:[],color:'#9aa8b5',y:TOP+lanes.length*GAP};
      lanes.push(orphan);
    }
    const laneOf=i=>lanes.find(l=>l.index===i);
    const nodes=[],undated=[];
    opps.forEach((o,i)=>{
      const id=Number.isInteger(o.index)?o.index:i;
      const open=ms(o.opens_at),close=ms(o.closes_at);
      const idx=laneIndexes(o);
      const members=idx.length?idx.map(laneOf):[orphan];
      const status=typeof o.status==='string'?o.status:'';
      const tone=status==='已落实为世界事件'?'realized':status==='候选窗口已结束'?'ended':'candidate';
      const node={id:'opp-'+id,index:id,title:String(o.title||'未命名机缘'),detail:typeof o.detail==='string'?o.detail:'',
        laneLabel:typeof o.lane_label==='string'?o.lane_label:'',status,tone,opensAt:open,closesAt:close,
        lanes:members.map(l=>l.index),colors:members.map(l=>l.color),after:list(o.after_indexes).filter(Number.isInteger),
        baseY:members.reduce((sum,l)=>sum+l.y,0)/members.length,laneYs:members.map(l=>l.y),
        crossing:members.length>1,threads:members.map(l=>l.title)};
      if(open===null&&close===null){undated.push(node);return;}
      node.x=xOf(open??close);node.x2=close!==null&&open!==null?xOf(close):null;node.y=node.baseY;
      nodes.push(node);
    });
    // Stack nodes that would visually collide on the same lane height; the
    // horizontal position always stays the recorded opening time.
    nodes.sort((a,b)=>a.x-b.x||a.index-b.index);
    const placed=[];
    for(const node of nodes){
      let k=0;
      const clash=y=>placed.some(p=>Math.abs(p.x-node.x)<150&&Math.abs(p.y-y)<38);
      while(clash(node.y)&&k<6){k++;node.y=node.baseY+(k%2?1:-1)*Math.ceil(k/2)*40;}
      node.stack=k;placed.push(node);
    }
    const byIndex=new Map(nodes.map(n=>[n.index,n]));
    const edges=[];
    for(const node of nodes)for(const parent of node.after){const from=byIndex.get(parent);if(from&&from!==node)edges.push({from:from.id,to:node.id,x1:from.x,y1:from.y,x2:node.x,y2:node.y});}
    const width=Math.round(xOf(end)+PAD_R),height=TOP+Math.max(1,lanes.length)*GAP-24;
    const days=[];
    for(let t=start;t<end;t+=DAY)days.push({x:xOf(t),w:pxPerDay,label:md(t),weekday:weekday(t),weekend:['六','日'].includes(weekday(t)),today:dayKey(t)===dayKey(now)});
    const stageBands=stages.map((st,i)=>{
      const a=Date.parse(String(st.starts_on)+'T00:00:00+08:00'),b=Date.parse(String(st.ends_on)+'T00:00:00+08:00')+DAY;
      if(!Number.isFinite(a)||!Number.isFinite(b)||b<=start||a>=end)return null;
      return {index:i,title:String(st.title||''),x1:xOf(a),x2:xOf(b),current:['当前章节','当前阶段'].includes(st.status)};
    }).filter(Boolean);
    const review=ms(worldline.review_at);
    const ordered=nodes.slice().sort((a,b)=>(a.opensAt??a.closesAt)-(b.opensAt??b.closesAt)||a.index-b.index);
    return {start,end,now,nowX:xOf(now),reviewX:review!==null&&review>=start&&review<=end?xOf(review):null,review,
      pxPerDay,width,height,padLeft:PAD_L,lineEnd:xOf(end)+28,lanes,nodes,ordered,undated,edges,days,stageBands};
  }
  function chapters(worldline,nowIso){
    const stages=list(rec(worldline)?worldline.stages:null).filter(rec).map((st,i)=>({
      index:i,title:String(st.title||'未命名章节'),status:typeof st.status==='string'?st.status:'',situation:typeof st.situation==='string'?st.situation:'',
      startsOn:String(st.starts_on||''),endsOn:String(st.ends_on||''),
      a:Date.parse(String(st.starts_on)+'T00:00:00+08:00'),b:Date.parse(String(st.ends_on)+'T00:00:00+08:00')+DAY,
    })).filter(st=>Number.isFinite(st.a)&&Number.isFinite(st.b)&&st.b>st.a);
    if(!stages.length)return null;
    const from=Math.min(...stages.map(s=>s.a)),to=Math.max(...stages.map(s=>s.b)),span=to-from;
    const now=ms(nowIso);
    for(const st of stages){st.left=(st.a-from)/span*100;st.width=(st.b-st.a)/span*100;st.current=['当前章节','当前阶段'].includes(st.status);}
    return {stages,from,to,nowPct:now!==null&&now>=from&&now<=to?(now-from)/span*100:null};
  }
  // ---- Relationships -----------------------------------------------------
  const GENERIC_PEOPLE=new Set(['人物','身边人物','现有人物','']);
  function bonds(snapshot,client){
    const states=pairs(snapshot,client,'relationship_lifecycle',['relationship_state']);
    const dims=['信任','亲近','尊重','可靠','相互','修复信心'];
    const read=pair=>{
      const out={};
      for(const v of pair.view.values){const m=/^(100|[0-9]{1,2})(?:[.][0-9]+)?%$/.exec(v.text);if(dims.includes(v.label)&&m)out[v.label]=parseFloat(v.text);}
      return out;
    };
    const you=states.find(p=>p.view.title==='和你')||null;
    const others=states.filter(p=>p!==you).map(p=>({name:p.view.title.replace(/^与/,''),stage:p.view.status||'',dims:read(p),view:p.view}));
    const npcs=pairs(snapshot,client,'relationship_lifecycle',['npc']).map(p=>p.view);
    const named=[...new Set(npcs.map(v=>v.title).filter(t=>!GENERIC_PEOPLE.has(t)))];
    const people=named.map(name=>({name,state:others.find(o=>o.name===name)||null}));
    for(const o of others)if(!people.some(p=>p.name===o.name))people.push({name:o.name,state:o});
    const unnamed=npcs.filter(v=>GENERIC_PEOPLE.has(v.title)).length;
    return {
      you:you?{stage:you.view.status||'',dims:read(you),view:you.view,complete:dims.every(d=>Object.hasOwn(read(you),d))}:null,
      dims,people,unnamed,npcTotal:metric(snapshot,'relationship_lifecycle','npcs'),
      signals:newest(pairs(snapshot,client,'relationship_lifecycle',['relationship_signal'])).map(p=>p.view),
      adjustments:newest(pairs(snapshot,client,'relationship_lifecycle',['relationship_adjustment'])).map(p=>({view:p.view,stage:rawLabel(p.raw,'stage_after')})),
      threads:newest(pairs(snapshot,client,'relationship_lifecycle',['thread'])).map(p=>p.view),
    };
  }
  // ---- Memory ------------------------------------------------------------
  function memoryField(snapshot,client){
    const items=pairs(snapshot,client,'facts_memory_inner',['memory_candidate']).map(p=>{
      const rawStrength=rawValue(p.raw,'retrieval_strength_bp');
      const strength=typeof rawStrength==='number'?rawStrength:NaN;
      return {view:p.view,strength:Number.isFinite(strength)&&strength>=0&&strength<=10000?strength/100:null,
        rationale:rawValue(p.raw,'retention_rationales'),source:rawLabel(p.raw,'source_kind'),reinforced:rawValue(p.raw,'reinforcement_count')};
    });
    const W=460,H=300;
    const placed=[];
    const sorted=items.slice().sort((a,b)=>(b.strength??0)-(a.strength??0));
    sorted.forEach((item,i)=>{
      const r=item.strength===null?16:14+Math.sqrt(item.strength)*4.2;
      let angle=i*2.399963,dist=0,x=W/2,y=H/2,guard=0;
      while(guard++<400){
        x=W/2+Math.cos(angle)*dist;y=H/2+Math.sin(angle)*dist*.72;
        if(!placed.some(p=>Math.hypot(p.x-x,p.y-y)<p.r+r+6))break;
        dist+=4;angle+=.35;
      }
      item.x=Math.max(r+4,Math.min(W-r-4,x));item.y=Math.max(r+4,Math.min(H-r-4,y));item.r=r;placed.push(item);
    });
    return {items:sorted,width:W,height:H};
  }
  // ---- Inner voice -------------------------------------------------------
  function isTriggerText(text){return typeof text==='string'&&/^【[^】]*】$/.test(text.trim());}
  function turns(studio){
    const drama=rec(studio)&&rec(studio.inner_drama)?studio.inner_drama:{};
    return list(drama.turns).filter(rec).slice().sort((a,b)=>Number(a.turn_index)-Number(b.turn_index));
  }
  // ---- Cost --------------------------------------------------------------
  function costSeries(cost){
    const days=list(rec(cost)?cost.daily_series:null).filter(rec);
    const max=Math.max(0,...days.map(d=>Number(d.total_cny)||0));
    return {days,max};
  }
  return {DAY,TZ,PALETTE,KIND_LABELS,TERMINAL,ms,dayKey,dayStart,minuteOfDay,weekday,hm,md,longDate,phase,pairs,rawValue,rawLabel,metric,
    present,weekStrips,routeMap,chapters,bonds,memoryField,isTriggerText,turns,costSeries};
})();
DashboardHomeClient.stage=DashboardStage;
"""
