"""Browser render layer (part 1) for the owner Dashboard "stage" presentation.

Shared helpers, detail dialog, hero, inner voice and the fate route map.  All
rendering goes through DOM nodes and ``textContent``; no fetched string is ever
parsed as HTML.  The layer reads the same authorized endpoints as before.
"""

STAGE_RENDER_CORE_JS = r"""
  const C=DashboardHomeClient,S=DashboardStage,X=DashboardExplorer;
  const $=id=>document.getElementById(id);
  const isRec=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const arr=v=>Array.isArray(v)?v:[];
  const NS='http://www.w3.org/2000/svg';
  function h(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined&&text!==null)n.textContent=String(text);return n;}
  function s(tag,attrs={},text){const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs)){if(v!==undefined&&v!==null)n.setAttribute(k,String(v));}if(text!==undefined&&text!==null)n.textContent=String(text);return n;}
  function on(node,name,fn){if(node&&node.addEventListener)node.addEventListener(name,fn);return node;}
  function btn(cls,text,fn,label){const b=h('button',cls,text);b.type='button';if(label)b.setAttribute('aria-label',label);on(b,'click',fn);return b;}
  function activate(node,fn){node.setAttribute('role','button');node.setAttribute('tabindex','0');on(node,'click',fn);on(node,'keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault&&e.preventDefault();fn();}});return node;}
  const readable=v=>C.readableText(v).text||'';
  const clip=(t,n)=>{const v=String(t||'');return v.length>n?v.slice(0,n)+'…':v;};
  const num=n=>Number(n||0).toLocaleString('en-US');
  const money=v=>'¥'+Number(v||0).toFixed(2);
  const tokens=n=>{const v=Number(n||0);return v>=1e6?(v/1e6).toFixed(2)+'M':v>=1e3?(v/1e3).toFixed(1)+'k':String(v);};
  const clock=v=>C.formatClock(v);
  const reduced=()=>!!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  const raf=fn=>typeof window.requestAnimationFrame==='function'?window.requestAnimationFrame(fn):fn();
  function empty(text){return h('p','empty',text);}
  function chip(text,cls=''){return h('span','chip '+cls,text);}

  // --- state ---------------------------------------------------------------
  let snap=null,etag=null,loading=false,paused=false,resumePending=false,presenting=false;
  let studio=null,costData=null,voiceData=null,lastSync=null,costLoadedAt=0,engineSeen=false;
  let agendaWeek=null,agendaPage=null;
  const weekHidden=new Set();
  let turnSel=null;
  let routeSel=null,routeZoom='fit',routeScroll=null,routeModel=null;
  let costDay=null,mechSel='choice',evidencePage=0;
  const counted=new Set();

  // --- reveal & motion ---------------------------------------------------------
  const io=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{
    for(const e of entries){if(e.isIntersecting){e.target.classList.add('in');io.unobserve(e.target);if(e.target.id==='engine'){engineSeen=true;loadCost();}}}
  },{threshold:.08}):null;
  for(const node of document.querySelectorAll('.reveal')){if(io)io.observe(node);else{node.classList.add('in');if(node.id==='engine')engineSeen=true;}}
  function countUp(node,value,key){
    const final=num(value);
    if(counted.has(key)||reduced()||typeof window.requestAnimationFrame!=='function'){node.textContent=final;counted.add(key);return;}
    counted.add(key);
    const t0=Date.now(),dur=1400;
    const step=()=>{const p=Math.min(1,(Date.now()-t0)/dur),e=1-Math.pow(1-p,4);node.textContent=num(Math.round(value*e));if(p<1)window.requestAnimationFrame(step);};
    step();
  }
  const typing=new WeakMap();
  function typeInto(node,text,key){
    const st=typing.get(node)||{key:null,timer:null};typing.set(node,st);
    if(st.timer){clearTimeout(st.timer);st.timer=null;}
    if(key===st.key||reduced()){node.textContent=text;st.key=key;return;}
    st.key=key;node.textContent='';let i=0;
    const tick=()=>{i=Math.min(text.length,i+2);node.textContent=text.slice(0,i);st.timer=i<text.length?setTimeout(tick,16):null;};
    tick();
    if(!st.bound){st.bound=true;on(node,'click',()=>{const cur=typing.get(node);if(cur&&cur.timer){clearTimeout(cur.timer);cur.timer=null;node.textContent=cur.full||node.textContent;}});}
    st.full=text;
  }
  on(document,'pointermove',e=>{
    const card=e.target&&typeof e.target.closest==='function'?e.target.closest('.card'):null;
    if(!card||!card.getBoundingClientRect)return;
    const r=card.getBoundingClientRect();
    card.style.setProperty('--mx',(e.clientX-r.left)+'px');card.style.setProperty('--my',(e.clientY-r.top)+'px');
  });

  // --- detail dialog & lightbox -------------------------------------------------
  let drawerOrigin=null;
  function closeDetail(){const d=$('detailDrawer');if(!d)return;if(d.close)d.close();else d.open=false;drawerOrigin&&drawerOrigin.focus&&drawerOrigin.focus({preventScroll:true});drawerOrigin=null;}
  function openDetail(title,content,eyebrow='LOOK A LITTLE CLOSER'){
    const d=$('detailDrawer');if(!d)return;
    if(!d.open)drawerOrigin=document.activeElement;
    $('drawerTitle').textContent=title;$('drawerEyebrow').textContent=eyebrow;
    const body=$('drawerBody');body.replaceChildren(content);
    if(snap&&snap.logical_time)body.appendChild(h('p','provenance','记录快照 · 世界时间 '+clock(snap.logical_time)));
    if(!d.open){if(d.showModal)d.showModal();else d.open=true;}
    $('closeDrawer')&&$('closeDrawer').focus&&$('closeDrawer').focus({preventScroll:true});
  }
  on($('closeDrawer'),'click',closeDetail);
  on($('detailDrawer'),'cancel',e=>{e.preventDefault&&e.preventDefault();closeDetail();});
  on($('detailDrawer'),'click',e=>{if(e.target===$('detailDrawer'))closeDetail();});
  function reading(value,cls='reading'){
    const r=C.readableText(value),wrap=h('div','reading-wrap');
    if(r.label)wrap.appendChild(h('span','reading-label',r.label));
    if(r.text)wrap.appendChild(h('p',cls,r.text));
    else if(r.structured){const d=h('details','structured');d.append(h('summary','','查看记录原文'),h('pre','',value));wrap.appendChild(d);}
    return wrap;
  }
  function viewDetail(view,extra){
    const box=h('div','detail-view');
    const tags=h('div','detail-tags');tags.append(chip(view.kindLabel,'k'));if(view.status)tags.append(chip(view.status,'s'));if(view.when)tags.append(chip(clock(view.when),'t'));
    box.append(tags);
    if(view.title&&view.title!==view.detail)box.append(reading(view.title,'detail-title'));
    if(view.detail)box.append(reading(view.detail));
    if(view.values.length){
      const grid=h('dl','detail-values');
      for(const v of view.values){if(v.text.startsWith('/world-v2/'))continue;grid.append(h('dt','',v.label),h('dd','',v.text));}
      box.append(grid);
    }
    if(extra)box.append(extra);
    openDetail(readable(view.title)||view.kindLabel,box,view.kindLabel.toUpperCase?view.kindLabel:'RECORD');
  }
  function openEntry(entry,entries){
    const box=h('div','detail-view');
    const tags=h('div','detail-tags');
    tags.append(chip(S.KIND_LABELS[entry.kind]||entry.kind,'k kind-'+entry.kind),chip(entry.status||'','s'),chip(clock(entry.starts_at)+(entry.ends_at&&entry.ends_at!==entry.starts_at?' — '+clock(entry.ends_at):''),'t'));
    box.append(tags,reading(entry.detail||entry.title));
    if(entry.content_state==='unavailable')box.append(h('p','muted','原文暂时不可用，未根据标题补写。'));
    if(entry.truncated)box.append(h('p','muted','当前正文为节选。'));
    const related=arr(entry.related).map(i=>arr(entries).find(e=>e.index===i)).filter(Boolean);
    if(related.length){
      box.append(h('h4','detail-sub','已记录的关联'));
      for(const r of related)box.append(btn('related','↳ '+(S.KIND_LABELS[r.kind]||r.kind)+' · '+clip(readable(r.title),48),()=>openEntry(r,entries)));
    }
    openDetail(clip(readable(entry.title)||'记录详情',60),box,(S.KIND_LABELS[entry.kind]||'RECORD'));
  }
  let lightboxClose=null;
  function lightbox(url,title,meta){
    if(lightboxClose)lightboxClose();
    const back=h('div','lightbox'),fig=h('figure','lightbox-fig');
    const img=h('img');img.src=url;img.alt=title||'照片';
    const cap=h('figcaption');cap.append(h('strong','',title||'她的随手拍'),h('span','',meta||''));
    const close=btn('tool ghost','关闭 ✕',e=>{e&&e.stopPropagation&&e.stopPropagation();done();});
    fig.append(img,cap,close);back.append(fig);
    const prev=document.body.style.overflow;
    const done=()=>{back.parentNode&&back.parentNode.removeChild(back);document.body.style.overflow=prev;lightboxClose=null;};
    lightboxClose=done;on(back,'click',done);on(fig,'click',e=>e.stopPropagation&&e.stopPropagation());
    document.body.appendChild(back);document.body.style.overflow='hidden';close.focus&&close.focus();
  }

  // --- capture state ------------------------------------------------------------
  function setCaptureState(state){
    const pill=$('syncPill');if(pill)pill.dataset.state=state;
    $('captureState').textContent=C.captureStateLabel(state);
    const notice=$('captureNotice');
    notice.hidden=state==='ready';
    notice.textContent=state==='stale'?'连接暂时中断。以下保留上次同步的记录，可能已不是最新状态。':state==='unavailable'?'暂时无法读取生活状态，请稍后重试。':'';
  }
  function tickSync(){
    const node=$('syncAgo');if(!node)return;
    if(lastSync===null){node.textContent='';return;}
    const sec=Math.max(0,Math.round((Date.now()-lastSync)/1000));
    node.textContent=paused?'已暂停':sec<5?'刚刚':sec<60?sec+' 秒前':Math.floor(sec/60)+' 分钟前';
  }

  // --- 01 hero ----------------------------------------------------------------------
  const HERO_STATS=[
    ['ledger_qualification','committed_world_event_refs','世界事件','她的世界已经推进的步数'],
    ['overview_life','experiences','亲身经历','被记进生活的经历'],
    ['facts_memory_inner','memory_candidates','记忆','被保留下来的记忆候选'],
    ['operations','proactive_actions','主动联系','她主动或跟进的行动'],
    ['operations','expression_beats','说出口的话','已记录的表达段落'],
    ['relationship_lifecycle','npcs','身边的人','登记在册的人物'],
  ];
  function contextTile(label,text,onOpen,cls=''){
    const tile=h('div','ctx '+cls);
    tile.append(h('span','ctx-label',label),h('p','ctx-text',clip(text,92)));
    if(onOpen)activate(tile,onOpen);
    return tile;
  }
  function renderHero(sn){
    document.body.dataset.phase=S.phase(sn.logical_time);
    $('heroDate').textContent=S.longDate(sn.logical_time)||'世界时间暂无记录';
    $('headerClock').textContent=sn.logical_time?'世界时间 '+clock(sn.logical_time):'';
    const p=S.present(sn,C);
    const act=p.activity&&p.activity.view;
    $('heroKicker').textContent=act?(p.activityIsActive?'她的安排 · 进行中':'最近的一条安排'):'活动与计划';
    const activityText=act?(readable(act.detail)||readable(act.title)||act.title):((sn.sections&&sn.sections.overview_life&&sn.sections.overview_life.state==='unavailable')?'生活状态暂时不可用':'暂无可展示的活动或计划');
    const hero=$('heroActivity');hero.textContent=activityText;hero.dataset.long=String(activityText.length>48);
    const meta=$('heroMeta');meta.replaceChildren();
    if(act){
      if(act.status)meta.append(chip(act.status,'status'));
      if(p.window)meta.append(chip(S.hm(p.window.start)+' – '+S.hm(p.window.end),'time'));
      if(p.intentSource)meta.append(chip(p.intentSource,'src'));
      meta.append(btn('chip link','读原文 →',()=>viewDetail(act)));
    }
    const ctx=$('heroContext');ctx.replaceChildren();
    if(p.occurrence)ctx.append(contextTile('周围正在发生',readable(p.occurrence.view.detail)||p.occurrence.view.title,()=>viewDetail(p.occurrence.view)));
    if(p.experience)ctx.append(contextTile('刚刚经历',readable(p.experience.view.title),()=>viewDetail(p.experience.view)));
    if(p.waits.length){
      const w=p.waits[0];
      const pressure=S.rawValue(w.raw,'pressure_bp');
      const tile=contextTile('她在等你回应',readable(w.view.title),()=>viewDetail(w.view),'wait');
      const until=S.rawLabel(w.raw,'expires_at');
      const foot=h('div','ctx-foot');
      if(Number.isFinite(pressure)){const bar=h('span','ctx-bar');const fill=h('i');fill.style.width=Math.max(0,Math.min(100,pressure/100))+'%';bar.append(fill);foot.append(h('span','','在意 '+Math.round(pressure/100)+'%'),bar);}
      if(until)foot.append(h('span','','看到 '+until));
      tile.append(foot);ctx.append(tile);
    }else if(p.affect){
      ctx.append(contextTile('心情',p.affect.view.title,()=>viewDetail(p.affect.view)));
    }
    if(!ctx.children.length)ctx.append(empty('当前快照没有可展示的环境与经历摘要。'));
    renderDial(sn);
    const stats=$('heroStats');stats.replaceChildren();
    for(const [sectionId,key,label,note] of HERO_STATS){
      const value=S.metric(sn,sectionId,key);if(value===null)continue;
      const cell=h('div','odo');const n=h('strong','odo-num','0');
      cell.append(n,h('span','odo-label',label),h('small','odo-note',note));stats.append(cell);
      countUp(n,value,'stat-'+key);
    }
  }
  function renderDial(sn){
    const host=$('dayDial');host.replaceChildren();
    const t=S.ms(sn.logical_time);
    const strips=S.weekStrips(sn,new Set());
    const today=strips?strips.days.find(d=>d.today):null;
    const cx=200,cy=200;
    const ang=m=>m/1440*Math.PI*2-Math.PI/2;
    const pt=(r,m)=>[+(cx+r*Math.cos(ang(m))).toFixed(2),+(cy+r*Math.sin(ang(m))).toFixed(2)];
    const arc=(r,a,b)=>{b=Math.min(1439.5,Math.max(b,a+5));const [x1,y1]=pt(r,a),[x2,y2]=pt(r,b);return 'M'+x1+' '+y1+' A'+r+' '+r+' 0 '+(b-a>720?1:0)+' 1 '+x2+' '+y2;};
    const nowMin=t!==null?S.minuteOfDay(t):null;
    const svg=s('svg',{viewBox:'0 0 400 400',class:'dial-svg',role:'group','aria-label':'今天的 24 小时生活表盘'});
    const defs=s('defs');
    const g1=s('linearGradient',{id:'dialElapsed',x1:'0',y1:'0',x2:'1',y2:'1'});g1.append(s('stop',{offset:'0','stop-color':'#86ccff','stop-opacity':'.15'}),s('stop',{offset:'1','stop-color':'#f4ead5','stop-opacity':'.9'}));
    const glow=s('filter',{id:'dialGlow',x:'-50%',y:'-50%',width:'200%',height:'200%'});glow.append(s('feGaussianBlur',{stdDeviation:'4'}));
    const halo=s('radialGradient',{id:'dialHalo'});halo.append(s('stop',{offset:'0','stop-color':'#fff','stop-opacity':'.10'}),s('stop',{offset:'1','stop-color':'#fff','stop-opacity':'0'}));
    defs.append(g1,glow,halo);svg.append(defs);
    svg.append(s('circle',{cx,cy,r:190,fill:'url(#dialHalo)'}),s('circle',{cx,cy,r:186,class:'dial-rim'}));
    svg.append(s('path',{d:arc(176,360,1080),class:'dial-daylight'}));
    for(let i=0;i<96;i++){const m=i*15,major=i%12===0,mid=i%4===0;const [x1,y1]=pt(major?160:mid?166:170,m),[x2,y2]=pt(176,m);svg.append(s('line',{x1,y1,x2,y2,class:'dial-tick'+(major?' major':mid?' mid':'')}));}
    for(let hr=0;hr<24;hr+=3){const [x,y]=pt(150,hr*60);svg.append(s('text',{x,y:y+4,class:'dial-hour'+(hr%6===0?' major':'')},String(hr)));}
    if(nowMin!==null&&today)svg.append(s('path',{d:arc(186,0,nowMin),class:'dial-elapsed'}));
    const segs=s('g',{class:'dial-segs'});
    if(today){
      for(const c of today.course){const p=s('path',{d:arc(132,c.start,c.end),class:'dial-seg k-planning'});p.append(s('title',{},S.hm(today.from+c.start*60000)+' '+readable(c.entry.title)));activate(p,()=>openEntry(c.entry,strips.entries));p.setAttribute('aria-label','课表 '+readable(c.entry.title));segs.append(p);}
      for(const a of today.acts){const r=116-Math.min(a.lane,2)*11;const p=s('path',{d:arc(r,a.start,a.end),class:'dial-seg k-'+a.kind+(a.abandoned?' abandoned':'')});p.append(s('title',{},S.hm(today.from+a.start*60000)+' '+readable(a.entry.title)));activate(p,()=>openEntry(a.entry,strips.entries));p.setAttribute('aria-label',(S.KIND_LABELS[a.kind]||'')+' '+clip(readable(a.entry.title),30));segs.append(p);}
      for(const w of today.windows){const r=86-Math.min(w.lane,3)*6;const p=s('path',{d:arc(r,w.start,w.end),class:'dial-seg thin k-'+w.kind});p.append(s('title',{},readable(w.entry.title)));on(p,'click',()=>openEntry(w.entry,strips.entries));segs.append(p);}
      for(const d of today.dots){const [x,y]=pt(144,d.start);const c=s('circle',{cx:x,cy:y,r:2.6,class:'dial-dot'});c.append(s('title',{},readable(d.entry.title)));on(c,'click',()=>openEntry(d.entry,strips.entries));segs.append(c);}
    }
    svg.append(segs);
    if(nowMin!==null){
      const [hx,hy]=pt(186,nowMin),[ox,oy]=pt(158,nowMin),[ix,iy]=pt(54,nowMin);
      const phase=S.phase(sn.logical_time);
      svg.append(s('line',{x1:ix,y1:iy,x2:hx,y2:hy,class:'dial-hand'}));
      svg.append(s('circle',{cx:ox,cy:oy,r:13,class:'dial-orb-glow '+phase,filter:'url(#dialGlow)'}),s('circle',{cx:ox,cy:oy,r:7,class:'dial-orb '+phase}));
    }
    const hm=t!==null?S.hm(t):'--:--';
    svg.append(s('text',{x:cx,y:cy-8,class:'dial-time'},hm));
    svg.append(s('text',{x:cx,y:cy+18,class:'dial-sub'},t!==null?'周'+S.weekday(t)+' · 世界时间':'世界时间暂无记录'));
    const count=today?today.course.length+today.acts.length+today.dots.length:0;
    svg.append(s('text',{x:cx,y:cy+40,class:'dial-sub dim'},today?'今天 '+count+' 段记录':'今天不在本页'));
    host.append(svg);
    const legend=h('div','dial-legend');
    for(const [k,label] of [['planning','课表'],['plan','计划'],['experience','经历'],['expectation','期待'],['world','世界']])legend.append(h('span','lg k-'+k,label));
    host.append(legend);
  }

  // --- 02 inner voice ------------------------------------------------------------------
  const EMOTION_HUE={warmth:'#ffb46e',joy:'#ffd86e',happiness:'#ffd86e',calm:'#7fe0c4',relief:'#8fe6c8',sadness:'#7ea9ff',loneliness:'#8f9cff',anxiety:'#b892ff',fear:'#a98cff',anger:'#ff7f6e',irritation:'#ff9a6e',shame:'#f5a0bb',embarrassment:'#f5a0bb',affection:'#ff9fc0',longing:'#e7a6ff'};
  function renderInner(){
    const drama=isRec(studio)&&isRec(studio.inner_drama)?studio.inner_drama:null;
    const live=$('innerLive');live.replaceChildren();
    const chat=$('innerChat');
    if(!drama){chat.replaceChildren(empty(studio===null?'正在读取她的回合记录…':'回合记录暂时不可用。'));$('innerThought').replaceChildren(empty('选择左侧一段对话后，这里显示她当时的内心。'));return;}
    for(const af of arr(drama.active_affect).slice(0,2)){
      const pill=h('span','live-pill');pill.style.setProperty('--hue',EMOTION_HUE[af.emotion_kind]||'#9fd9c9');
      pill.append(h('i','pulse'),h('strong','',String(af.emotion_label||af.emotion_kind||'情绪')),h('span','',Number(af.intensity_pct||0).toFixed(0)+'%'));
      live.append(pill);
    }
    const turns=S.turns(studio).slice(-10);
    $('phoneStatus').textContent=turns.length?'最近 '+turns.length+' 个回合':'暂无回合';
    if(turnSel===null||!turns.some(t=>t.turn_index===turnSel))turnSel=turns.length?turns[turns.length-1].turn_index:null;
    const prevBottom=chat.scrollHeight-chat.scrollTop-chat.clientHeight<40;
    chat.replaceChildren();
    if(!turns.length)chat.append(empty('暂无已记录的回合。'));
    for(const t of turns){
      const group=h('div','turn'+(t.turn_index===turnSel?' sel':''));group.setAttribute('role','listitem');
      group.dataset.turn=String(t.turn_index);
      group.append(h('div','turn-time',t.time_display&&t.time_display!=='—'?t.time_display:'时间未标注'));
      if(t.mode==='reply'&&t.user_text&&!S.isTriggerText(t.user_text))group.append(h('div','bubble you',t.user_text));
      else if(t.mode==='proactive')group.append(h('div','turn-sys','她主动找你'));
      const bubbles=arr(t.sent_bubbles).filter(b=>typeof b==='string'&&b);
      if(bubbles.length)for(const b of bubbles)group.append(h('div','bubble her',b));
      else group.append(h('div','turn-sys muted','这一回合没有可见消息'));
      activate(group,()=>{turnSel=t.turn_index;renderInner();});
      group.setAttribute('aria-label','查看 '+(t.time_display||'')+' 这一回合她的内心');
      chat.append(group);
    }
    if(prevBottom||!chat.dataset.scrolled){chat.scrollTop=chat.scrollHeight;chat.dataset.scrolled='1';}
    renderThought(turns.find(t=>t.turn_index===turnSel));
    const aps=$('innerAppraisals');aps.replaceChildren();
    for(const ap of arr(drama.appraisals).slice(0,4)){
      const card=h('article','appraisal');
      card.append(h('div','ap-head',(ap.accepted_at_display||'')+' · 把握 '+Number(ap.confidence_pct||0)+'%'));
      for(const hy of arr(ap.hypotheses)){
        const row=h('div','hyp');const w=Math.max(0,Math.min(100,Number(hy.weight_pct)||0));
        const bar=h('span','hyp-bar');const fill=h('i');fill.style.width=w+'%';bar.append(fill);
        row.append(h('p','hyp-text',hy.meaning||''),bar,h('span','hyp-w',w+'%'));card.append(row);
      }
      aps.append(card);
    }
    if(!aps.children.length)aps.append(empty('暂无处境评估记录。'));
    const imps=$('innerImpressions');imps.replaceChildren();
    for(const imp of arr(drama.private_impressions).slice(0,3)){
      const card=h('article','impression');
      card.append(h('span','imp-when',imp.accepted_at_display||''),h('p','imp-text',clip(imp.reflection_summary,180)));
      if(String(imp.reflection_summary||'').length>180)activate(card,()=>openDetail('私人手记',reading(imp.reflection_summary),'PRIVATE NOTE'));
      imps.append(card);
    }
    if(!imps.children.length)imps.append(empty('暂无手记。'));
  }
  function renderThought(t){
    const box=$('innerThought');box.replaceChildren();
    if(!t){box.append(empty('暂无可展示的内心记录。'));return;}
    const head=h('header','thought-head');
    head.append(h('span','thought-badge','心里'),h('span','thought-when',[t.time_display,t.mode_label].filter(v=>v&&v!=='—').join(' · ')));
    const main=h('blockquote','thought-main');
    box.append(head,main);
    typeInto(main,String(t.inner_state_summary||'这一回合没有记录内心摘要。'),'turn-'+t.turn_index);
    const noticed=arr(t.noticed).filter(Boolean);
    if(noticed.length){const row=h('div','thought-row');row.append(h('span','thought-label','她注意到'));for(const n of noticed)row.append(h('span','thought-chip',n));box.append(row);}
    if(t.about_us||t.why_us){
      const pair=h('div','thought-pair');
      if(t.about_us){const a=h('div');a.append(h('span','thought-label','她怎么看你们'),h('p','',t.about_us));pair.append(a);}
      if(t.why_us){const b=h('div');b.append(h('span','thought-label','她这么觉得的缘由'),h('p','',t.why_us));pair.append(b);}
      box.append(pair);
    }
    const stuck=arr(t.stuck_with_me).filter(Boolean);
    if(stuck.length){const row=h('div','thought-row');row.append(h('span','thought-label','放不下的事'));for(const n of stuck)row.append(h('span','thought-chip warm',n));box.append(row);}
    box.append(h('footer','thought-foot','Turn #'+t.turn_index+(t.evaluated_world_revision?' · 世界版本 r'+t.evaluated_world_revision:'')+' · 内心摘要由角色自己写下'));
  }

  // --- 03 fate route -----------------------------------------------------------------------
  function worldline(sn){const sec=sn&&sn.sections&&sn.sections.overview_life;return sec&&sec.state!=='unavailable'&&isRec(sec.data)&&isRec(sec.data.agenda)?sec.data.agenda.worldline:null;}
  const TONE_LABEL={realized:'已落实',candidate:'未来机缘',ended:'窗口已过'};
  function renderFate(sn){
    const wl=worldline(sn);
    const ribbon=$('chapterRibbon'),gutter=$('routeGutter'),mapHost=$('routeMap'),viewport=$('routeViewport');
    const ch=S.chapters(wl,sn.logical_time);
    ribbon.replaceChildren();
    if(ch){
      const track=h('div','chapter-track');
      ch.stages.forEach((st,i)=>{
        const seg=h('button','chapter'+(st.current?' current':''));seg.type='button';
        seg.style.left=st.left+'%';seg.style.width=st.width+'%';
        seg.append(h('span','ch-no','CH.'+String(i+1).padStart(2,'0')),h('span','ch-title',st.title));
        seg.setAttribute('aria-label','第 '+(i+1)+' 章 '+st.title+' '+st.startsOn+' 至 '+st.endsOn);
        on(seg,'click',()=>{routeSel={type:'stage',index:i};galShow();});
        track.append(seg);
      });
      if(ch.nowPct!==null){const m=h('span','chapter-now');m.style.left=ch.nowPct+'%';m.append(h('b','','NOW'));track.append(m);}
      ribbon.append(track);
    }
    const base=S.routeMap(wl,sn.logical_time);
    if(!base){gutter.replaceChildren();mapHost.replaceChildren(empty('暂时没有可展示的故事脉络。'));routeModel=null;galShow();return;}
    const days=(base.end-base.start)/S.DAY;
    const vw=viewport.clientWidth||1100;
    const fit=Math.max(56,Math.min(180,(vw-base.padLeft-230)/days));
    const map=routeZoom==='near'?S.routeMap(wl,sn.logical_time,{pxPerDay:210}):S.routeMap(wl,sn.logical_time,{pxPerDay:fit});
    routeModel=map;
    // gutter: route cards stay pinned while the map scrolls
    gutter.replaceChildren();gutter.style.height=map.height+'px';
    for(const lane of map.lanes){
      const card=h('button','lane-card');card.type='button';card.style.top=(lane.y-30)+'px';card.style.setProperty('--lane',lane.color);
      card.append(h('span','lane-letter',lane.letter),h('span','lane-title',clip(lane.title,26)),h('span','lane-status',lane.status||'故事线'));
      on(card,'click',()=>{routeSel={type:'lane',index:lane.index};galShow();});
      gutter.append(card);
    }
    const svg=s('svg',{viewBox:'0 0 '+map.width+' '+map.height,width:map.width,height:map.height,class:'route-svg',role:'group','aria-label':'故事线与未来机缘'});
    const defs=s('defs');
    const glow=s('filter',{id:'routeGlow',x:'-50%',y:'-50%',width:'200%',height:'200%'});glow.append(s('feGaussianBlur',{stdDeviation:'3.5'}));
    const arrow=s('marker',{id:'routeArrow',viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'7',markerHeight:'7',orient:'auto-start-reverse'});arrow.append(s('path',{d:'M0 0L10 5L0 10Z',fill:'#f4ead5'}));
    const fog=s('linearGradient',{id:'routeFog',x1:'0',x2:'1',y1:'0',y2:'0'});fog.append(s('stop',{offset:'0','stop-color':'#0b0f1a','stop-opacity':'0'}),s('stop',{offset:'1','stop-color':'#0b0f1a','stop-opacity':'.9'}));
    const nowGrad=s('linearGradient',{id:'routeNow',x1:'0',x2:'0',y1:'0',y2:'1'});nowGrad.append(s('stop',{offset:'0','stop-color':'#f4ead5','stop-opacity':'.9'}),s('stop',{offset:'1','stop-color':'#f4ead5','stop-opacity':'0'}));
    defs.append(glow,arrow,fog,nowGrad);svg.append(defs);
    const bg=s('g',{class:'route-bg'});
    for(const d of map.days){
      if(d.weekend)bg.append(s('rect',{x:d.x,y:0,width:d.w,height:map.height,class:'route-weekend'}));
      bg.append(s('line',{x1:d.x,y1:40,x2:d.x,y2:map.height,class:'route-dayline'+(d.today?' today':'')}));
      if(d.w>=44||d.weekday==='一')bg.append(s('text',{x:d.x+6,y:18,class:'route-date'+(d.today?' today':'')},d.label),s('text',{x:d.x+6,y:32,class:'route-wd'},'周'+d.weekday));
    }
    for(const b of map.stageBands){bg.append(s('rect',{x:b.x1,y:40,width:Math.max(0,b.x2-b.x1),height:3,class:'route-stage'+(b.current?' current':'')}));}
    bg.append(s('rect',{x:0,y:40,width:map.nowX,height:map.height-40,class:'route-past'}));
    svg.append(bg);
    if(map.reviewX!==null){const g=s('g',{class:'route-review'});g.append(s('line',{x1:map.reviewX,y1:44,x2:map.reviewX,y2:map.height}),s('text',{x:map.reviewX+6,y:map.height-10},'命运复核 · '+S.md(map.review)));g.append(s('title',{},'这天命运作者会复核未来走向，不代表届时一定改变'));svg.append(g);}
    const lanesG=s('g',{class:'route-lanes'});
    for(const lane of map.lanes){
      const x0=map.padLeft-110;
      lanesG.append(s('line',{x1:x0,y1:lane.y,x2:map.nowX,y2:lane.y,stroke:lane.color,class:'lane-past'}));
      lanesG.append(s('line',{x1:x0,y1:lane.y,x2:map.nowX,y2:lane.y,stroke:lane.color,class:'lane-glow',filter:'url(#routeGlow)'}));
      lanesG.append(s('line',{x1:map.nowX,y1:lane.y,x2:map.lineEnd,y2:lane.y,stroke:lane.color,class:'lane-future'}));
      if(lane.developments.length){
        const g=s('g',{class:'recap',transform:'translate('+(x0+28)+' '+lane.y+')'});
        g.append(s('rect',{x:-22,y:-13,width:44,height:26,rx:13,stroke:lane.color}),s('text',{x:0,y:4},'前情'));
        g.append(s('title',{},'前情提要 · '+lane.developments.length+' 条进展摘要'));
        activate(g,()=>{routeSel={type:'recap',index:lane.index};galShow();});g.setAttribute('aria-label',lane.title+' 前情提要');
        lanesG.append(g);
      }
      if(lane.question){
        const qx=map.lineEnd+30;
        const fork=s('g',{class:'fork'});
        for(const dy of [-34,0,34])fork.append(s('path',{d:'M'+(qx+14)+' '+lane.y+' C '+(qx+60)+' '+lane.y+', '+(qx+70)+' '+(lane.y+dy)+', '+(qx+130)+' '+(lane.y+dy),stroke:lane.color}));
        lanesG.append(fork);
        const g=s('g',{class:'question',transform:'translate('+qx+' '+lane.y+')'});
        g.append(s('rect',{x:-14,y:-14,width:28,height:28,transform:'rotate(45)',stroke:lane.color}),s('text',{x:0,y:5},'?'));
        g.append(s('title',{},'尚未揭晓'));activate(g,()=>{routeSel={type:'question',index:lane.index};galShow();});g.setAttribute('aria-label',lane.title+' 尚未揭晓的问题');
        lanesG.append(g);
      }
    }
    svg.append(lanesG);
    const linkG=s('g',{class:'route-links'});
    for(const n of map.nodes){
      if(n.x2!==null&&n.x2>n.x+2)linkG.append(s('rect',{x:n.x,y:n.y-4,width:n.x2-n.x,height:8,rx:4,fill:n.colors[0],class:'node-window'}));
      if(n.crossing||n.stack){
        n.laneYs.forEach((ly,i)=>{
          if(Math.abs(ly-n.y)<1)return;
          const c=n.colors[i];
          linkG.append(s('path',{d:'M'+(n.x-64)+' '+ly+' C '+(n.x-30)+' '+ly+', '+(n.x-30)+' '+n.y+', '+(n.x-12)+' '+n.y,stroke:c,class:'junction'}));
          linkG.append(s('path',{d:'M'+(n.x+12)+' '+n.y+' C '+(n.x+30)+' '+n.y+', '+(n.x+30)+' '+ly+', '+(n.x+64)+' '+ly,stroke:c,class:'junction out'}));
        });
      }
    }
    for(const e of map.edges)linkG.append(s('path',{d:'M'+(e.x1+14)+' '+e.y1+' C '+((e.x1+e.x2)/2)+' '+e.y1+', '+((e.x1+e.x2)/2)+' '+e.y2+', '+(e.x2-16)+' '+e.y2,class:'prereq','marker-end':'url(#routeArrow)'}));
    svg.append(linkG);
    const nowG=s('g',{class:'route-now'});
    nowG.append(s('rect',{x:map.nowX-1,y:40,width:2,height:map.height-40,fill:'url(#routeNow)'}),s('rect',{x:map.nowX-22,y:44,width:44,height:18,rx:9,class:'now-pill'}),s('text',{x:map.nowX,y:57},'NOW'),s('circle',{cx:map.nowX,cy:53,r:12,class:'now-pulse'}));
    svg.append(nowG);
    const textW=t=>[...String(t)].reduce((w,c)=>w+(c.charCodeAt(0)>255?11.6:6.4),0);
    const labels=new Map(),occupied=map.nodes.map(n=>({x1:n.x-15,y1:n.y-15,x2:n.x+15,y2:n.y+15}));
    const hit=r=>occupied.some(o=>r.x1<o.x2&&r.x2>o.x1&&r.y1<o.y2&&r.y2>o.y1);
    for(const n of map.nodes){
      const date=n.opensAt!==null?S.md(n.opensAt)+' '+S.hm(n.opensAt):'时间未标注';
      const title=clip(n.title,map.pxPerDay>150?16:11);
      const w=textW(date+'  '+title)+24;
      let best=null;
      const cands=[[16,-11],[-w/2,20],[-w/2,-42],[-w-16,-11],[-w/2,50],[-w/2,-72],[16,30],[16,-52],[-w/2,80],[-w/2,-102]];
      for(const [dx,dy] of cands){
        const r={x1:n.x+dx,y1:n.y+dy,x2:n.x+dx+w,y2:n.y+dy+22};
        if(r.y1<66||r.y2>map.height-24||r.x1<map.padLeft-100)continue;
        if(!hit(r)){best=r;break;}
        if(!best)best=r;
      }
      if(!best)best={x1:n.x-w/2,y1:n.y+20,x2:n.x+w/2,y2:n.y+42};
      occupied.push(best);labels.set(n.id,{x:best.x1,y:best.y1,w,date,title});
    }
    const nodesG=s('g',{class:'route-nodes'});
    for(const n of map.nodes){
      const sel=routeSel&&routeSel.type==='node'&&routeSel.id===n.id;
      const g=s('g',{class:'rnode '+n.tone+(sel?' sel':''),transform:'translate('+n.x+' '+n.y+')','data-route-node':n.id});
      g.append(s('circle',{r:19,class:'rnode-halo',fill:n.colors[0]}));
      if(n.colors.length>1){
        const step=360/n.colors.length;
        n.colors.forEach((c,i)=>{const a1=(i*step-90)*Math.PI/180,a2=((i+1)*step-90)*Math.PI/180;g.append(s('path',{d:'M'+(12*Math.cos(a1)).toFixed(2)+' '+(12*Math.sin(a1)).toFixed(2)+' A12 12 0 0 1 '+(12*Math.cos(a2)).toFixed(2)+' '+(12*Math.sin(a2)).toFixed(2),stroke:c,class:'rnode-ring'}));});
      }else g.append(s('circle',{r:12,stroke:n.colors[0],class:'rnode-ring'}));
      g.append(s('circle',{r:n.tone==='realized'?7:4,class:'rnode-core',fill:n.tone==='ended'?'#5a6472':n.colors[0]}));
      const lab=labels.get(n.id);
      g.append(s('rect',{x:lab.x-n.x,y:lab.y-n.y,width:lab.w,height:22,rx:11,class:'rnode-pill'}));
      const tx=s('text',{x:lab.x-n.x+12,y:lab.y-n.y+15,class:'rnode-title'});
      tx.append(s('tspan',{class:'rnode-date'},lab.date+'  '),s('tspan',{},lab.title));g.append(tx);
      g.append(s('title',{},n.title));
      activate(g,()=>{routeSel={type:'node',id:n.id};galShow();for(const other of nodesG.children)other.classList.toggle('sel',other===g);});
      g.setAttribute('aria-label',(TONE_LABEL[n.tone]||'')+' '+n.title);
      nodesG.append(g);
    }
    svg.append(nodesG);
    svg.append(s('rect',{x:map.lineEnd-40,y:40,width:map.width-map.lineEnd+40,height:map.height-40,fill:'url(#routeFog)',class:'route-fogrect'}));
    const keep=mapHost.children.length?viewport.scrollLeft:null;
    mapHost.replaceChildren(svg);mapHost.style.width=map.width+'px';mapHost.style.height=map.height+'px';
    viewport.style.height=(map.height+14)+'px';
    if(routeScroll==='reset'||keep===null){viewport.scrollLeft=Math.max(0,map.nowX-(vw*.28));}else viewport.scrollLeft=keep;
    routeScroll=null;
    const legend=$('routeLegend');legend.replaceChildren(
      h('span','lg realized','已落实'),h('span','lg candidate','未来机缘'),h('span','lg ended','窗口已过'),h('span','lg cross','多线交汇'),h('span','lg fork','未决分支'),
      h('span','lg-note','未来机缘不代表已经发生，也不代表她已经知道。进展摘要与未决问题是作者概括，不构成因果链。'));
    if(map.undated.length)legend.append(h('span','lg-note',map.undated.length+' 个机缘未标注时间，未画入时间轴。'));
    galShow();
  }
  function galShow(){
    const name=$('galName'),text=$('galText'),meta=$('galMeta'),count=$('galCount');
    meta.replaceChildren();count.textContent='';
    const wl=worldline(snap),map=routeModel;
    const ch=S.chapters(wl,snap&&snap.logical_time);
    let sel=routeSel;
    if(!sel){const cur=ch&&ch.stages.findIndex(st=>st.current);sel=cur!==null&&cur>=0?{type:'stage',index:cur}:map&&map.lanes.length?{type:'lane',index:map.lanes[0].index}:null;}
    if(!sel){name.textContent='命运线';typeInto(text,'暂时没有可读的故事。','gal-empty');return;}
    const lane=i=>map&&map.lanes.find(l=>l.index===i);
    let label='',body='',key='';
    name.style.removeProperty&&name.style.removeProperty('--lane');
    if(sel.type==='stage'&&ch&&ch.stages[sel.index]){
      const st=ch.stages[sel.index];label='CHAPTER '+String(sel.index+1).padStart(2,'0')+' · '+st.title;body=st.situation||'这一章暂无概述。';key='stage-'+sel.index+body.length;
      meta.append(chip(st.status||'章节'),chip(st.startsOn+' — '+st.endsOn,'t'));
    }else if(['lane','recap','question'].includes(sel.type)&&lane(sel.index)){
      const l=lane(sel.index);name.style.setProperty('--lane',l.color);
      if(sel.type==='lane'){label='ROUTE '+l.letter+' · '+l.title;body=l.situation||'这条故事线暂无概述。';meta.append(chip(l.status||'故事线'));}
      else if(sel.type==='recap'){label='前情提要 · '+l.title;body=l.developments.map((d,i)=>(i+1)+'. '+d).join('\n');meta.append(chip(l.developments.length+' 条进展摘要'),chip('作者概括 · 不代表先后因果','t'));}
      else{label='尚未揭晓 · '+l.title;body=l.question;meta.append(chip('未决分支','q'));}
      key=sel.type+'-'+sel.index+body.length;
    }else if(sel.type==='node'&&map){
      const n=map.nodes.find(x=>x.id===sel.id)||map.undated.find(x=>x.id===sel.id);
      if(n){
        const idx=map.ordered.indexOf(n);
        label=(n.laneLabel||'机缘')+' · '+(TONE_LABEL[n.tone]||n.status);body=n.detail||n.title;key=n.id+body.length;
        name.style.setProperty('--lane',n.colors[0]);
        meta.append(chip(n.status||TONE_LABEL[n.tone]));
        if(n.opensAt!==null)meta.append(chip(S.md(n.opensAt)+' '+S.hm(n.opensAt)+(n.closesAt!==null?' — '+S.md(n.closesAt)+' '+S.hm(n.closesAt):''),'t'));
        n.threads.forEach((title,i)=>{const c=chip(clip(title,16),'lane');c.style.setProperty('--lane',n.colors[i]);meta.append(c);});
        if(idx>=0)count.textContent=(idx+1)+' / '+map.ordered.length;
        if(n.title!==body)body=n.title+'\n\n'+body;
      }
    }
    name.textContent=label||'命运线';
    typeInto(text,body||'暂无内容。','gal-'+key);
  }
  function galStep(delta){
    const map=routeModel;if(!map||!map.ordered.length)return;
    let idx=routeSel&&routeSel.type==='node'?map.ordered.findIndex(n=>n.id===routeSel.id):-1;
    idx=idx<0?(delta>0?0:map.ordered.length-1):(idx+delta+map.ordered.length)%map.ordered.length;
    const n=map.ordered[idx];routeSel={type:'node',id:n.id};
    const vp=$('routeViewport');if(vp&&vp.clientWidth)vp.scrollLeft=Math.max(0,n.x-vp.clientWidth/2);
    for(const g of document.querySelectorAll('[data-route-node]'))g.classList.toggle('sel',g.getAttribute('data-route-node')===n.id);
    galShow();
  }
  on($('galPrev'),'click',()=>galStep(-1));on($('galNext'),'click',()=>galStep(1));
  on($('routeViewport'),'keydown',e=>{if(e.key==='ArrowRight'&&e.shiftKey){e.preventDefault&&e.preventDefault();galStep(1);}if(e.key==='ArrowLeft'&&e.shiftKey){e.preventDefault&&e.preventDefault();galStep(-1);}});
  for(const b of document.querySelectorAll('[data-route-zoom]'))on(b,'click',()=>{
    routeZoom=b.getAttribute('data-route-zoom');routeScroll='reset';
    for(const o of document.querySelectorAll('[data-route-zoom]'))o.setAttribute('aria-pressed',String(o===b));
    if(snap)renderFate(snap);
  });
  (function dragPan(){
    const vp=$('routeViewport');if(!vp)return;let down=null;
    on(vp,'pointerdown',e=>{if(e.button!==0||(e.target&&e.target.closest&&e.target.closest('[role="button"]')))return;down={x:e.clientX,left:vp.scrollLeft};vp.classList.add('dragging');});
    on(window,'pointermove',e=>{if(down)vp.scrollLeft=down.left-(e.clientX-down.x);});
    on(window,'pointerup',()=>{down=null;vp.classList.remove('dragging');});
  })();
"""
