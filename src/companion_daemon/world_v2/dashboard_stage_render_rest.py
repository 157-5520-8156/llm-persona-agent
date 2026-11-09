"""Browser render layer (part 2) for the owner Dashboard "stage" presentation.

Week strips, relationships, mind & memory, perception/media, the interaction
station, the operator engine room and the poll/presentation loop.
"""

STAGE_RENDER_REST_JS = r"""
  // --- 04 week -----------------------------------------------------------------------------
  const WEEK_KINDS=['planning','plan','experience','world','expectation','revisit','commitment'];
  function renderWeek(sn){
    const w=S.weekStrips(sn,weekHidden);
    const nav=$('weekNav'),grid=$('weekGrid'),legend=$('weekLegend'),phases=$('weekPhases'),foot=$('weekFoot');
    nav.replaceChildren();grid.replaceChildren();legend.replaceChildren();phases.replaceChildren();foot.replaceChildren();
    if(!w){grid.append(empty('周视图暂时不可用。'));return;}
    const go=(week,page)=>{if(paused||loading)return;agendaWeek=week;agendaPage=page;etag=null;load();};
    nav.append(btn('tool','‹ 上一周',()=>go(C.shiftWeek(w.agenda.week_start,-7),null)),btn('tool','本周',()=>go(null,null)),btn('tool','下一周 ›',()=>go(C.shiftWeek(w.agenda.week_start,7),null)));
    nav.append(h('span','week-range',w.days[0].date.slice(5).replace('-','/')+' — '+w.days[6].date.slice(5).replace('-','/')));
    if(w.pageCount>1){
      const pager=h('span','pager');
      const prev=btn('tool sm','‹',()=>go(agendaWeek,w.page-1),'上一页');prev.disabled=paused||w.page<=0;
      const next=btn('tool sm','›',()=>go(agendaWeek,w.page+1),'下一页');next.disabled=paused||w.page>=w.pageCount-1;
      pager.append(prev,h('span','','第 '+(w.page+1)+' / '+w.pageCount+' 页'),next);nav.append(pager);
    }
    for(const ph of w.phases.slice(0,3)){
      const p=btn('phase','',()=>openEntry(ph,w.entries));
      p.append(h('span','phase-k',S.KIND_LABELS[ph.kind]||'阶段'),h('span','phase-t',clip(readable(ph.title),30)));
      phases.append(p);
    }
    if(w.phases.length>3)phases.append(btn('phase more','全部 '+w.phases.length+' 个阶段与候选 →',()=>{
      const box=h('div','detail-view');
      for(const ph of w.phases)box.append(btn('related',(S.KIND_LABELS[ph.kind]||'阶段')+' · '+clip(readable(ph.title),60),()=>openEntry(ph,w.entries)));
      openDetail('阶段背景与命运候选',box,'PHASES');
    }));
    for(const k of WEEK_KINDS){
      if(!w.kinds.includes(k))continue;
      const c=btn('legend-chip k-'+k,S.KIND_LABELS[k],()=>{if(weekHidden.has(k))weekHidden.delete(k);else weekHidden.add(k);renderWeek(snap);renderDial(snap);});
      c.setAttribute('aria-pressed',String(!weekHidden.has(k)));legend.append(c);
    }
    const axis=h('div','week-axis');axis.append(h('span','week-corner',''));
    const ticks=h('div','week-ticks');
    for(let hr=0;hr<=24;hr+=3){const t=h('span','',String(hr).padStart(2,'0'));t.style.left=(hr/24*100)+'%';ticks.append(t);}
    axis.append(ticks);grid.append(axis);
    const hasCourse=w.days.some(d=>d.course.length);
    for(const day of w.days){
      const row=h('div','week-row'+(day.today?' today':''));
      const label=h('div','week-day');label.append(h('strong','','周'+day.weekday),h('span','',day.date.slice(5).replace('-','/')));
      if(day.today)label.append(h('em','','今天'));
      const track=h('div','week-track');
      const COURSE=hasCourse?26:0,LANE=24,acts=day.acts.length?day.actLanes:0;
      const windowsTop=COURSE+acts*LANE+18;
      track.style.height=Math.max(56,windowsTop+day.windowLanes*7+8)+'px';
      for(const u of day.unloaded){
        const b=btn('week-unloaded','',()=>{if(Number.isInteger(u.firstPage))go(agendaWeek,u.firstPage);},'此时段有 '+u.count+' 条记录未在本页');
        b.style.left=(u.hour/24*100)+'%';b.style.width=(2/24*100)+'%';b.title=(u.labels.length?u.labels.join(' / ')+' · ':'')+u.count+' 条记录未在本页，点击读取';
        track.append(b);
      }
      const place=(node,seg)=>{node.style.left=(seg.start/1440*100)+'%';node.style.width=Math.max(.35,(seg.end-seg.start)/1440*100)+'%';};
      for(const c of day.course){
        const b=btn('week-course','',()=>openEntry(c.entry,w.entries));place(b,c);b.style.top='2px';
        b.append(h('span','',clip(readable(c.entry.title),18)));b.title=S.hm(day.from+c.start*60000)+' '+readable(c.entry.title);track.append(b);
      }
      for(const a of day.acts){
        const b=btn('week-act k-'+a.kind+(a.abandoned?' abandoned':''),'',()=>openEntry(a.entry,w.entries));place(b,a);b.style.top=(COURSE+a.lane*LANE+2)+'px';
        b.append(h('span','',clip(readable(a.entry.title),40)));b.title=S.hm(day.from+a.start*60000)+' · '+(S.KIND_LABELS[a.kind]||'')+' · '+a.entry.status+'\n'+readable(a.entry.title);track.append(b);
      }
      for(const d of day.dots){
        const b=btn('week-dot','',()=>openEntry(d.entry,w.entries),'世界事件 '+clip(readable(d.entry.title),30));b.style.left=(d.start/1440*100)+'%';b.style.top=(windowsTop-12)+'px';
        b.title=S.hm(day.from+d.start*60000)+' 世界事件\n'+readable(d.entry.title);track.append(b);
      }
      for(const win of day.windows){
        const b=btn('week-window k-'+win.kind,'',()=>openEntry(win.entry,w.entries),(S.KIND_LABELS[win.kind]||'')+' '+clip(readable(win.entry.title),30));place(b,win);b.style.top=(windowsTop+win.lane*7)+'px';
        b.title=(S.KIND_LABELS[win.kind]||'')+' · '+win.entry.status+'\n'+readable(win.entry.title);track.append(b);
      }
      if(day.nowMinute!==null){const n=h('span','week-now');n.style.left=(day.nowMinute/1440*100)+'%';n.append(h('b','',S.hm(day.from+day.nowMinute*60000)));track.append(n);}
      row.append(label,track);grid.append(row);
    }
    foot.append(h('span','','北京时间 · 胶囊=计划/经历 · 细线=回应期待与再想一想 · 圆点=世界事件'));
    if(w.truncated||w.pageCount>1)foot.append(h('span','','斜纹＝该时段已有记录但不在本页，点开读取；空白不代表她空闲。'));
    for(const [key,label] of [['planning_state','客观课表'],['outlook_state','未来候选']])if(w.agenda[key]==='unavailable')foot.append(h('span','warn',label+'暂不可用，本周视图不完整。'));
  }

  // --- 05 bonds ----------------------------------------------------------------------------
  function radar(dims,values,size=240){
    const svg=s('svg',{viewBox:'0 0 '+size+' '+size,class:'radar',role:'img','aria-label':'六维关系：'+dims.map(d=>d+' '+values[d]+'%').join('，')});
    const c=size/2,R=size*.32;
    const pt=(i,r)=>{const a=-Math.PI/2+i*Math.PI*2/dims.length;return [(c+R*r*Math.cos(a)).toFixed(1),(c+R*r*Math.sin(a)).toFixed(1)];};
    for(const lv of [.25,.5,.75,1])svg.append(s('polygon',{points:dims.map((_,i)=>pt(i,lv).join(',')).join(' '),class:'radar-grid'}));
    dims.forEach((d,i)=>{const [x,y]=pt(i,1);svg.append(s('line',{x1:c,y1:c,x2:x,y2:y,class:'radar-axis'}));const [lx,ly]=pt(i,1.3);svg.append(s('text',{x:lx,y:+ly+4,class:'radar-label'},d),s('text',{x:lx,y:+ly+17,class:'radar-val'},values[d]+'%'));});
    // Values are tiny early in a relationship; draw them on a readable scale
    // while labels keep the exact recorded percentage.
    const max=Math.max(...dims.map(d=>values[d]));
    const scale=max>0&&max<50?50:100;
    svg.append(s('polygon',{points:dims.map((d,i)=>pt(i,Math.min(1,values[d]/scale)).join(',')).join(' '),class:'radar-shape'}));
    dims.forEach((d,i)=>{const [x,y]=pt(i,Math.min(1,values[d]/scale));svg.append(s('circle',{cx:x,cy:y,r:3.5,class:'radar-dot'}));});
    svg.append(s('text',{x:size-6,y:size-6,class:'radar-scale'},'刻度 0–'+scale+'%'));
    return svg;
  }
  function renderBonds(sn){
    const b=S.bonds(sn,C);
    const map=$('bondsMap');map.replaceChildren();
    const W=520,H=430,cx=W/2,cy=H/2;
    const svg=s('svg',{viewBox:'0 0 '+W+' '+H,class:'constellation-svg',role:'group','aria-label':'她身边的人'});
    const defs=s('defs');const g=s('radialGradient',{id:'coreGlow'});g.append(s('stop',{offset:'0','stop-color':'#f4ead5','stop-opacity':'.55'}),s('stop',{offset:'1','stop-color':'#f4ead5','stop-opacity':'0'}));defs.append(g);svg.append(defs);
    for(const r of [92,148,196])svg.append(s('circle',{cx,cy,r,class:'orbit',style:'--dur:'+(r*.6)+'s'}));
    const closeness=d=>Number.isFinite(d&&d['亲近'])?d['亲近']:null;
    const nodes=[];
    if(b.you)nodes.push({name:'你',stage:b.you.stage,angle:-28,r:closeness(b.you.dims)!==null?76+(1-closeness(b.you.dims)/100)*110:120,you:true,close:closeness(b.you.dims),onOpen:()=>viewDetail(b.you.view)});
    const people=b.people.slice(0,7);
    people.forEach((p,i)=>{const cl=p.state?closeness(p.state.dims):null;nodes.push({name:p.name,stage:p.state?p.state.stage:'',angle:160+i*(140/Math.max(1,people.length-1||1)),r:cl!==null?92+(1-cl/100)*100:178,close:cl,onOpen:p.state?()=>viewDetail(p.state.view):null});});
    const edges=s('g',{class:'bond-edges'}),dots=s('g',{class:'bond-nodes'});
    nodes.forEach((n,i)=>{
      const a=n.angle*Math.PI/180,x=cx+Math.cos(a)*n.r,y=cy+Math.sin(a)*n.r;
      edges.append(s('line',{x1:cx,y1:cy,x2:x,y2:y,class:'bond-edge'+(n.you?' you':'')+(n.stage==='陌生'?' far':''),'stroke-width':n.close!==null?1+n.close/8:1}));
      const grp=s('g',{class:'bond-node'+(n.you?' you':''),transform:'translate('+x.toFixed(1)+' '+y.toFixed(1)+')'});
      const fl=s('g',{class:'float',style:'--d:'+(i*.7)+'s'});grp.append(fl);
      fl.append(s('circle',{r:n.you?20:13,class:'bond-halo'}),s('circle',{r:n.you?9:6,class:'bond-core'}));
      fl.append(s('text',{y:n.you?36:28,class:'bond-name'},n.name),s('text',{y:n.you?51:42,class:'bond-stage'},n.stage||'尚无关系记录'));
      if(n.onOpen){activate(grp,n.onOpen);grp.setAttribute('aria-label',n.name+' '+(n.stage||''));}
      dots.append(grp);
    });
    if(b.unnamed){
      const a=110*Math.PI/180;const gx=cx+Math.cos(a)*196,gy=cy+Math.sin(a)*196;
      const grp=s('g',{class:'bond-cluster',transform:'translate('+gx.toFixed(1)+' '+gy.toFixed(1)+')'});
      for(let i=0;i<Math.min(b.unnamed,9);i++)grp.append(s('circle',{cx:Math.cos(i*2.4)*(6+i*2),cy:Math.sin(i*2.4)*(6+i*2),r:2.2}));
      grp.append(s('text',{y:-22},'+'+b.unnamed+' 位未具名'));dots.append(grp);
    }
    svg.append(edges);
    svg.append(s('circle',{cx,cy,r:64,fill:'url(#coreGlow)'}));
    const core=s('g',{class:'bond-me',transform:'translate('+cx+' '+cy+')'});
    const petals=s('g',{class:'me-petals'});
    for(let i=0;i<6;i++)petals.append(s('path',{d:'M0 -5 C 9 -10 10 -24 0 -29 C -10 -24 -9 -10 0 -5Z',transform:'rotate('+i*60+')'}));
    core.append(petals,s('circle',{r:5,class:'me-heart'}),s('text',{y:50,class:'bond-name me'},'沈知栀'));
    svg.append(core,dots);
    map.append(svg);
    if(b.npcTotal!==null)map.append(h('p','map-note','登记人物 '+b.npcTotal+' 位 · 距离按已记录的「亲近」排布，无记录者置于外环'));
    const you=$('bondsYou');you.replaceChildren();
    you.append(h('p','eyebrow','YOU · 和你'));
    if(!b.you){you.append(empty('暂无你们之间的关系记录。'));}
    else{
      you.append(h('h3','stage-name',b.you.stage||'关系阶段未标注'));
      if(b.you.complete)you.append(radar(b.dims,b.you.dims));
      else you.append(h('p','muted','六项维度未完整记录，不绘制雷达。'));
    }
    for(const sig of b.signals.slice(0,2)){
      const q=h('figure','bond-quote');q.append(h('blockquote','',readable(sig.title)));if(sig.detail)q.append(h('figcaption','',readable(sig.detail)));
      activate(q,()=>viewDetail(sig));you.append(q);
    }
    const open=b.threads.filter(t=>!S.TERMINAL.has(t.statusCode));
    if(open.length){const box=h('div','bond-threads');box.append(h('span','thought-label','还挂在心上的事'));for(const t of open.slice(0,3))box.append(btn('thread-item',clip(readable(t.detail)||t.title,60),()=>viewDetail(t)));you.append(box);}
    if(b.adjustments.length){const latest=b.adjustments[0];you.append(btn('adjust-line','最近一次关系微调：'+clip(readable(latest.view.title),36)+(latest.stage?' → '+latest.stage:''),()=>viewDetail(latest.view)));}
  }

  // --- 06 mind -----------------------------------------------------------------------------
  function renderMind(sn){
    const mood=$('moodOrb');mood.replaceChildren();
    const affects=isRec(studio)&&isRec(studio.inner_drama)?arr(studio.inner_drama.active_affect):[];
    const homeAffect=S.present(sn,C).affect;
    mood.append(h('h3','card-title','此刻的情绪'));
    const af=affects[0];
    let label=null,pct=null,hue='#9fd9c9',sub='';
    if(af){label=String(af.emotion_label||af.emotion_kind||'情绪');pct=Number(af.intensity_pct);hue=EMOTION_HUE[af.emotion_kind]||hue;sub=['余温 '+Number(af.residual_pct||0)+'%',af.opened_at_display?'始于 '+af.opened_at_display:'',af.expires_at_display?'淡去 '+af.expires_at_display:''].filter(Boolean).join(' · ');}
    else if(homeAffect){label=homeAffect.view.title;const pk=homeAffect.view.values.find(v=>/%$/.test(v.text));pct=pk?parseFloat(pk.text):null;sub=homeAffect.view.status||'';}
    if(label){
      const size=Number.isFinite(pct)?60+pct*.9:80;
      const svg=s('svg',{viewBox:'0 0 240 240',class:'orb-svg',role:'img','aria-label':label+(Number.isFinite(pct)?' '+pct+'%':'')});
      const defs=s('defs');const rg=s('radialGradient',{id:'orbFill',cx:'40%',cy:'35%'});rg.append(s('stop',{offset:'0','stop-color':'#fff','stop-opacity':'.95'}),s('stop',{offset:'.35','stop-color':hue,'stop-opacity':'.9'}),s('stop',{offset:'1','stop-color':hue,'stop-opacity':'.05'}));
      const bl=s('filter',{id:'orbBlur',x:'-50%',y:'-50%',width:'200%',height:'200%'});bl.append(s('feGaussianBlur',{stdDeviation:'14'}));defs.append(rg,bl);svg.append(defs);
      svg.append(s('circle',{cx:120,cy:120,r:size*.9,fill:hue,opacity:'.35',filter:'url(#orbBlur)',class:'orb-aura'}));
      svg.append(s('circle',{cx:120,cy:120,r:size*.62,fill:'url(#orbFill)',class:'orb-core'}));
      for(const r of [96,108])svg.append(s('circle',{cx:120,cy:120,r,class:'orb-ring'}));
      if(Number.isFinite(pct))svg.append(s('circle',{cx:120,cy:120,r:108,class:'orb-meter',stroke:hue,pathLength:'100','stroke-dasharray':pct+' 100',transform:'rotate(-90 120 120)'}));
      mood.append(svg,h('p','orb-label',label),h('p','orb-pct',Number.isFinite(pct)?Math.round(pct)+'%':''),h('p','muted center',sub));
    }else mood.append(empty('当前没有进行中的情绪记录。'));
    const kp=h('div','mini-kpis');
    for(const [sec,key,lab] of [['facts_memory_inner','affect_episodes','心情记录'],['facts_memory_inner','appraisals','情境评估']]){const v=S.metric(sn,sec,key);if(v!==null){const k=h('div');k.append(h('strong','',num(v)),h('span','',lab));kp.append(k);}}
    mood.append(kp);
    const mem=$('memoryField');mem.replaceChildren();mem.append(h('h3','card-title','被保留下来的记忆'));
    const field=S.memoryField(sn,C);
    if(field.items.length){
      const svg=s('svg',{viewBox:'0 0 '+field.width+' '+field.height,class:'memory-svg',role:'group','aria-label':'记忆候选，圆越大提取强度越高'});
      field.items.forEach((m,i)=>{
        const g=s('g',{class:'mem',transform:'translate('+m.x.toFixed(1)+' '+m.y.toFixed(1)+')'});
        const fl=s('g',{class:'float',style:'--d:'+(i*.4)+'s'});g.append(fl);
        fl.append(s('circle',{r:m.r,class:'mem-bubble',style:'--a:'+(m.strength===null?.3:.18+m.strength/160)}));
        if(m.r>22)fl.append(s('text',{y:-2,class:'mem-title'},clip(m.view.title,6)),s('text',{y:13,class:'mem-val'},m.strength===null?'强度未记录':m.strength.toFixed(0)+'%'));
        g.append(s('title',{},m.view.title+(m.rationale?' · '+m.rationale:'')));
        activate(g,()=>viewDetail(m.view));g.setAttribute('aria-label','记忆 '+m.view.title);
        svg.append(g);
      });
      mem.append(svg);
    }else mem.append(empty('当前快照没有可展示的记忆候选。'));
    const mk=h('div','mini-kpis');
    for(const [sec,key,lab] of [['facts_memory_inner','memory_candidates','记忆候选'],['facts_memory_inner','facts','已记事实'],['facts_memory_inner','prehistory_records','启动前人生片段']]){const v=S.metric(sn,sec,key);if(v!==null){const k=h('div');k.append(h('strong','',num(v)),h('span','',lab));mk.append(k);}}
    mem.append(mk,h('p','map-note','圆的大小＝提取强度；标题是她保留这段记忆的理由，正文按隐私规则不展示。'));
    const bio=$('biography');bio.replaceChildren();bio.append(h('h3','card-title','人生坐标'));
    const items=[...S.pairs(sn,C,'overview_life',['biographical_coordinate','life_arc','aspiration']),...S.pairs(sn,C,'facts_memory_inner',['character_core'])]
      .map(p=>p.view).filter(v=>(v.detail||'').trim()||!/^[a-z0-9_.:-]+$/i.test(v.title)).sort((a,b)=>(Date.parse(b.when)||0)-(Date.parse(a.when)||0));
    const tl=h('ol','timeline');
    for(const v of items.slice(0,6)){
      const li=h('li','tl-item');li.append(h('time','',v.when?clock(v.when):'时间未标注'),h('strong','',v.kindLabel),h('p','',clip(readable(v.detail)||v.title,90)));
      activate(li,()=>viewDetail(v));tl.append(li);
    }
    if(items.length)bio.append(tl);else bio.append(empty('暂无可展示的人生坐标。'));
  }

  // --- 07 seen -----------------------------------------------------------------------------
  let audio=null,audioId=null;
  function renderSeen(sn){
    const ticker=$('signalTicker');ticker.replaceChildren();
    const signals=S.pairs(sn,C,'perception_media',['external_signal']).map(p=>p.view);
    if(signals.length){
      const lane=h('div','ticker-lane');
      for(let rep=0;rep<2;rep++)for(const sig of signals){const it=h('span','tick-item');it.append(h('b','','HOT'),h('span','',clip(sig.title,60)));lane.append(it);}
      lane.style.setProperty('--dur',Math.max(30,signals.length*12)+'s');
      ticker.append(h('span','ticker-label','外面的世界'),lane);
    }else ticker.hidden=true;
    const feed=$('perceptionFeed');feed.replaceChildren();
    const seen=S.pairs(sn,C,'perception_media',['external_perception']).sort((a,b)=>(Date.parse(b.view.when)||0)-(Date.parse(a.view.when)||0));
    for(const {raw,view} of seen.slice(0,6)){
      const url=String(S.rawValue(raw,'canonical_url')||'');
      const note=S.rawValue(raw,'epistemic_notes');
      const card=h('article','card video');
      const m=/^【([^】]+)】(.*?)(?:（UP主：(.*)）)?$/.exec(view.title||'');
      const head=h('div','video-head');
      head.append(h('span','video-badge',/bilibili\.com/.test(url)?'bilibili':'外部内容'));
      if(m&&m[1])head.append(h('span','video-cat',m[1]));
      if(view.when)head.append(h('time','',clock(view.when)));
      card.append(head,h('h4','video-title',m?m[2]:view.title));
      if(m&&m[3])card.append(h('p','video-up','UP主 · '+m[3]));
      if(view.detail)card.append(h('blockquote','video-take',readable(view.detail)));
      const foot=h('div','video-foot');
      if(typeof note==='string'&&note){const d=h('details','video-note');d.append(h('summary','','她的认知备注'),h('p','',note));foot.append(d);}
      if(/^https:\/\//.test(url)){const a=h('a','video-link','打开原视频 ↗');a.href=url;a.target='_blank';a.rel='noopener noreferrer';foot.append(a);}
      card.append(foot);feed.append(card);
    }
    if(!feed.children.length)feed.append(empty('这次快照里没有她看过的外部内容。'));
    const wall=$('photoWall');wall.replaceChildren();
    const urls=new Set();
    const media=S.pairs(sn,C,'perception_media',['media_delivery','media_preview']).sort((a,b)=>(Date.parse(b.view.when)||0)-(Date.parse(a.view.when)||0));
    let i=0;
    for(const {view} of media){
      const img=view.values.find(v=>v.text.startsWith('/world-v2/dashboard/media/'));
      if(!img||urls.has(img.text))continue;urls.add(img.text);
      const cap=C.readableText(view.detail).text||C.readableText(view.title).text||'照片记录';
      const card=h('button','polaroid');card.type='button';card.style.setProperty('--r',((i%2?1:-1)*(1.5+(i%3)))+'deg');i++;
      const im=h('img');im.src=img.text;im.alt=cap;im.loading='lazy';
      card.append(im,h('span','pol-cap',clip(cap,22)),h('span','pol-meta',view.kind==='media_delivery'?'已发给你':'成片预览'));
      on(card,'click',()=>lightbox(img.text,cap,(view.when?clock(view.when):'')));
      wall.append(card);
    }
    if(!wall.children.length)wall.append(empty('本次快照暂无可展示的照片。'));
    renderVoice();
  }
  function renderVoice(){
    const host=$('voiceList');host.replaceChildren();
    const notes=isRec(voiceData)?arr(voiceData.voice_notes).filter(isRec):[];
    if(!notes.length){host.append(empty(voiceData===null?'正在读取语音…':'暂无语音记录。'));return;}
    for(const vn of notes.slice(0,5)){
      if(typeof vn.audio_url!=='string'||!vn.audio_url.startsWith('/world-v2/dashboard/voice/'))continue;
      const row=h('div','voice'+(audioId===vn.voice_id?' playing':''));
      const play=btn('voice-play',audioId===vn.voice_id?'❚❚':'▶',()=>{
        if(!audio&&typeof Audio==='function')audio=new Audio();
        if(!audio)return;
        if(audioId===vn.voice_id&&!audio.paused){audio.pause();audioId=null;renderVoice();return;}
        audio.src=vn.audio_url;audioId=vn.voice_id;audio.play&&audio.play().catch(()=>{});
        audio.onended=()=>{audioId=null;renderVoice();};
        audio.ontimeupdate=()=>{const bar=row.querySelector&&row.querySelector('.wave');if(bar&&audio.duration)bar.style.setProperty('--p',(audio.currentTime/audio.duration*100)+'%');};
        renderVoice();
      },'播放语音');
      const wave=h('span','wave');
      // Decorative bars only; they do not claim to be the recording's waveform.
      for(let k=0;k<32;k++){const bar=h('i');bar.style.height=(30+Math.abs(Math.sin(k*1.7+String(vn.voice_id).length))*70)+'%';wave.append(bar);}
      const body=h('div','voice-body');body.append(h('p','voice-text','「'+String(vn.text||'')+'」'),wave);
      row.append(play,body,h('span','voice-dur',(Number(vn.duration_seconds)||0).toFixed(1)+'s'));
      host.append(row);
    }
  }

  // --- 08 station ------------------------------------------------------------------------------
  function renderStation(){
    const st=isRec(studio)&&isRec(studio.interaction_station)?studio.interaction_station:{};
    const sum=isRec(st.summary)?st.summary:{};
    $('interactionSummaryBadges').replaceChildren(chip('待签收 '+Number(sum.pending_pickup_count||0),'warm'),chip('配送中 '+Number(sum.in_transit_count||0),'cool'),chip('已入账 '+Number(sum.completed_count||0),'ok'));
    const bar=$('interactionPresetBar');
    if(bar&&!bar.children.length&&arr(st.presets).length){
      for(const p of arr(st.presets))bar.append(btn('ix-preset',String(p.label||p.title),()=>{
        $('ixKindSelect').value=String(p.kind||'instant_delivery');$('ixTitleInput').value=String(p.title||'');$('ixDetailInput').value=String(p.detail||'');$('ixNoteInput').value=String(p.sender_note||'');$('ixPlaceInput').value=String(p.target_place||'');
      }));
    }
    const list=$('interactionInboxList');list.replaceChildren();
    const items=arr(st.items).filter(isRec);
    if(!items.length){list.append(empty(studio===null?'正在同步交互记录…':'还没有寄送记录。可以在右侧发起一次。'));return;}
    const STEPS=['发起','在途','送达','写入账本'];
    for(const it of items){
      const card=h('article','card ix-item s-'+String(it.status||''));
      const top=h('div','ix-top');top.append(chip(String(it.kind_label||it.kind),'k'),h('span','ix-dir',it.direction==='companion_to_user'?'她 → 你':'你 → 她'));
      card.append(top,h('h4','ix-title',String(it.title||'未命名交互')),h('p','ix-detail',String(it.detail||'')));
      if(it.sender_note)card.append(h('p','ix-note','附言：「'+it.sender_note+'」'));
      if(it.receiver_note)card.append(h('p','ix-note r','签收：「'+it.receiver_note+'」'));
      const stage=it.status==='in_transit'?1:it.status==='ready_for_pickup'?2:['delivered','received'].includes(it.status)?3:0;
      const steps=h('ol','ix-steps');STEPS.forEach((label,i)=>steps.append(h('li',i<=stage?'done':'',label)));card.append(steps);
      const meta=h('div','ix-meta');meta.append(h('span','',String(it.target_place||'')),h('span','','发起 '+String(it.created_at_display||'—')));
      if(it.settled_at_display)meta.append(h('span','','结算 '+it.settled_at_display));
      card.append(meta);
      if(it.status==='in_transit'){
        const p=h('div','ix-progress');const f=h('i');f.style.width=Math.max(0,Math.min(100,Number(it.progress_pct)||0))+'%';p.append(f);card.append(p);
        card.append(btn('tool primary','立即送达并结算',e=>receive(it.interaction_id,'',e.currentTarget||e.target)));
      }else if(it.status==='ready_for_pickup'){
        const input=h('input','ix-input');input.type='text';input.placeholder='拆箱时的第一句话（可选，会写进她的世界）';
        card.append(input,btn('tool primary','签收并拆开',e=>receive(it.interaction_id,input.value,e.currentTarget||e.target)));
      }else if(it.outcome_text)card.append(h('p','ix-outcome',String(it.outcome_text)));
      list.append(card);
    }
  }
  async function receive(id,note,button){
    if(!id||typeof window.fetch!=='function')return;
    if(button){button.disabled=true;button.textContent='正在写入世界账本…';}
    try{
      const r=await window.fetch('/world-v2/dashboard/interactions/'+encodeURIComponent(id)+'/receive',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json'},body:JSON.stringify({receiver_note:note||''})});
      if(r&&r.ok){const res=await r.json();if(res&&isRec(res.studio)){studio=res.studio;renderStudioParts();}setTimeout(load,1500);}
    }catch(_e){}finally{if(button)button.disabled=false;}
  }
  on($('interactionDispatchForm'),'submit',async ev=>{
    ev.preventDefault&&ev.preventDefault();
    if(typeof window.fetch!=='function')return;
    const title=($('ixTitleInput').value||'').trim(),msg=$('ixFeedbackMsg'),submit=$('ixSubmitBtn');
    if(!title){msg.hidden=false;msg.textContent='请先填写名称';return;}
    const eta=Number($('ixEtaSelect').value||0);
    submit.disabled=true;submit.textContent='正在写入…';
    try{
      const r=await window.fetch('/world-v2/dashboard/interactions',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json',Accept:'application/json'},
        body:JSON.stringify({kind:$('ixKindSelect').value||'instant_delivery',title,detail:($('ixDetailInput').value||'').trim()||title,sender_note:($('ixNoteInput').value||'').trim(),target_place:($('ixPlaceInput').value||'').trim(),eta_minutes:eta})});
      if(r&&r.ok){
        const res=await r.json();
        for(const id of ['ixTitleInput','ixDetailInput','ixNoteInput'])$(id).value='';
        msg.hidden=false;msg.textContent=eta<=0?'已写入世界账本，世界正在结算她的感知与反应。':'已发起配送，倒计时结束后由世界结算入账。';
        if(res&&isRec(res.studio)){studio=res.studio;renderStudioParts();}
        setTimeout(load,1500);
      }else{msg.hidden=false;msg.textContent='提交没有成功，请稍后重试';}
    }catch(_e){msg.hidden=false;msg.textContent='提交没有成功，请稍后重试';}
    finally{submit.disabled=false;submit.textContent='写入世界账本';}
  });

  // --- 09 engine ---------------------------------------------------------------------------------
  const MECH_POS={world:[40,30],memory:[40,170],inner:[40,310],choice:[360,144],life:[700,30],expression:[700,170],feedback:[700,310],boundary:[370,316]};
  let explorerItems=[];
  function renderEngine(sn){
    explorerItems=X.records(sn,C);
    const host=$('mechanismMap');host.replaceChildren();
    const svg=s('svg',{viewBox:'0 0 940 470',class:'mech-svg',role:'group','aria-label':'角色运行机制，可选择的环节'});
    const defs=s('defs');const rg=s('linearGradient',{id:'roleFill',x1:'0',y1:'0',x2:'1',y2:'1'});rg.append(s('stop',{offset:'0','stop-color':'#f6a6c1','stop-opacity':'.35'}),s('stop',{offset:'1','stop-color':'#c6b4ff','stop-opacity':'.25'}));
    const mk=s('marker',{id:'mechArrow',viewBox:'0 0 10 10',refX:'9',refY:'5',markerWidth:'6',markerHeight:'6',orient:'auto-start-reverse'});mk.append(s('path',{d:'M0 0L10 5L0 10Z',fill:'#7f8fa0'}));
    defs.append(rg,mk);svg.append(defs);
    const paths=['M240 76 C 300 76, 300 190, 360 190','M240 216 H 360','M240 356 C 300 356, 300 230, 360 230','M580 190 C 640 190, 640 76, 700 76','M580 216 H 700','M800 122 V 170','M800 262 V 310','M800 402 V 446 H 140 V 402','M470 316 V 264'];
    paths.forEach((d,i)=>{svg.append(s('path',{d,class:'mech-path','marker-end':'url(#mechArrow)'}),s('path',{d,class:'mech-flow'+(i===7?' back':''),style:'--d:'+(i*.35)+'s'}));});
    svg.append(s('text',{x:470,y:464,class:'mech-caption'},'↺ 结果成为后续判断的材料'));
    for(const node of X.nodes){
      const [x,y]=MECH_POS[node.id]||[0,0];const role=!!node.role;const w=role?220:200,hgt=role?120:92;
      const state=X.nodeState(sn,node),count=explorerItems.filter(i=>X.inNode(i,node.id)).length;
      const g=s('g',{class:'mech-node'+(role?' role':'')+(node.id==='boundary'?' shield':'')+(mechSel===node.id?' sel':''),transform:'translate('+x+' '+y+')','data-mech':node.id});
      g.append(s('rect',{width:w,height:hgt,rx:16}));
      g.append(s('text',{x:16,y:24,class:'mech-k'},role?'CHARACTER · 角色决定':'SYSTEM · 系统'));
      g.append(s('text',{x:16,y:role?56:50,class:'mech-t'},node.title),s('text',{x:16,y:role?80:70,class:'mech-s'},node.subtitle));
      g.append(s('text',{x:16,y:role?104:85,class:'mech-c'},state==='unavailable'?'摘要暂不可用':count+' 条可见摘要'));
      activate(g,()=>{mechSel=node.id;renderEngine(snap);});g.setAttribute('aria-label',node.title);
      svg.append(g);
    }
    host.append(svg);
    const node=X.nodes.find(n=>n.id===mechSel)||X.nodes[0];
    const det=$('mechanismDetail');det.replaceChildren();
    const related=explorerItems.filter(i=>X.inNode(i,node.id));
    det.append(h('span','mech-auth'+(node.role?' role':''),node.authority),h('h3','',node.title),h('p','',node.explanation),h('p','limit',node.limit));
    const ul=h('ul','mech-ev');
    for(const it of related.slice(0,3)){const li=h('li');li.append(h('span','',[it.kindLabel,clip(it.title,30)].join(' · ')),h('time','',it.when?clock(it.when):'未标注'));activate(li,()=>viewDetail(it));ul.append(li);}
    if(!related.length)ul.append(h('li','muted','暂无可展示的记录，不能据此判断这个机制没有运行。'));
    det.append(ul,btn('tool','查看此环节的全部记录 ('+related.length+')',()=>{$('evidenceNode').value=node.id;evidencePage=0;renderEvidence();const t=$('evidenceSearch');t&&t.scrollIntoView&&t.scrollIntoView({behavior:'smooth',block:'center'});}));
    const sig=$('runtimeSignals');sig.replaceChildren();
    const rt=sn.sections&&sn.sections.runtime_operations;
    if(!rt||rt.state==='unavailable'||!isRec(rt.data))sig.append(empty('运行信号暂不可用，不能据此判断角色沉默。'));
    else{
      for(const g of arr(rt.data.signals).filter(isRec)){const r=h('div','signal');r.dataset.state=String(g.state||'');r.append(h('i','light'),h('span','',String(g.label||g.key)),h('em','',C.stateLabel(g.state)));sig.append(r);}
      for(const n of arr(rt.data.notices).filter(isRec))sig.append(h('p','notice',String(n.signal_label||'提示')+' · '+String(n.reason_label||'暂时不可用')));
    }
    const pb=$('processBars');pb.replaceChildren();
    const ops=sn.sections&&sn.sections.operations;
    const rows=ops&&ops.state!=='unavailable'&&isRec(ops.data)?arr(ops.data.mechanisms).filter(r=>isRec(r)&&Number.isInteger(r.recorded_count)):[];
    const maxRec=Math.max(1,...rows.map(r=>r.recorded_count));
    for(const r of rows.sort((a,b)=>b.recorded_count-a.recorded_count)){
      const row=h('div','proc');const bar=h('div','proc-bar');bar.style.width=(r.recorded_count/maxRec*100)+'%';
      const tot=Math.max(1,r.recorded_count);
      for(const [k,v] of [['term',r.terminal_count],['claim',r.claimed_count],['open',r.open_count]]){const seg=h('i',k);seg.style.width=(v/tot*100)+'%';bar.append(seg);}
      row.append(h('span','proc-l',r.label),h('div','proc-track'),h('span','proc-n',num(r.recorded_count)));
      row.children[1].append(bar);row.title=r.label+'：已终结 '+r.terminal_count+' · 已认领 '+r.claimed_count+' · 待处理 '+r.open_count+' · 受理尝试 '+r.attempt_count+(r.scope_note?'\n'+r.scope_note:'');
      pb.append(row);
    }
    if(!rows.length)pb.append(empty('当前服务未提供分机制统计，不能按零次处理。'));
    else pb.append(h('p','map-note','绿：已终结 · 黄：已认领 · 粉：待处理。终结可能是完成、无操作或技术失败。'));
    const led=$('ledgerStats');led.replaceChildren();
    const LEDGER=[['ledger_qualification','committed_world_event_refs','已提交世界事件'],['ledger_qualification','clock_transition_history','世界时钟变更'],['ledger_qualification','proposal_ids','决策提议'],['ledger_qualification','model_result_audits','模型结果审计'],['operations','acceptance_decisions','接受决定'],['ledger_qualification','terminal_execution_receipts','终态执行回执']];
    const lg=h('div','ledger-grid');
    for(const [sec,key,lab] of LEDGER){const v=S.metric(sn,sec,key);if(v===null)continue;const c=h('div','ledger-cell');const n=h('strong','','0');c.append(n,h('span','',lab));lg.append(c);countUp(n,v,'ledger-'+key);}
    led.append(lg);
    const lq=sn.sections&&sn.sections.ledger_qualification;
    if(lq&&isRec(lq.data)){
      const vers=h('p','map-note',[lq.data.ledger_schema_version,lq.data.reducer_bundle_version].filter(Boolean).join(' · '));led.append(vers);
      if(typeof lq.data.qualification_label==='string')led.append(h('p','qual',lq.data.qualification_label));
    }
    if(sn.cursor)$('footerMeta').textContent='账本序列 '+num(sn.cursor.ledger_sequence)+' · 世界版本 r'+num(sn.cursor.world_revision)+' · 快照 '+String(sn.snapshot_hash||'').slice(0,10);
    const tech=$('techDetails'),wasOpen=!!(tech.firstChild&&tech.firstChild.open);
    const techBox=h('details','tech');techBox.open=wasOpen;techBox.append(h('summary','','技术细节'));
    const cov=h('div','coverage');
    for(const [id,section] of Object.entries(sn.sections||{})){
      if(!isRec(section))continue;
      const c=section.coverage;
      cov.append(h('div','cov-row',C.sectionHeading(id,section)+' · '+C.stateLabel(section.state)+(isRec(c)?' · 已展示 '+c.included_count+' / 已知 '+c.known_count+(c.truncated?' · 已截取':''):'')));
    }
    const ac=h('div','authority');
    for(const v of S.pairs(sn,C,'authority_privacy',null).map(p=>p.view))ac.append(chip(v.kindLabel+' · '+v.title+(v.status?' · '+v.status:'')));
    techBox.append(cov,ac);tech.replaceChildren(techBox);
    const kind=$('evidenceKind'),prior=kind.value;kind.replaceChildren();const all=h('option','','全部类型');all.value='all';kind.append(all);
    const kinds=new Map(explorerItems.map(i=>[i.kind,i.kindLabel]));for(const [k,l] of kinds){const o=h('option','',l);o.value=k;kind.append(o);}kind.value=kinds.has(prior)?prior:'all';
    const nodeSel=$('evidenceNode');if(nodeSel.children.length<=1)for(const n of X.nodes){const o=h('option','',n.title);o.value=n.id;nodeSel.append(o);}
    renderEvidence();
  }
  function renderEvidence(){
    const result=X.filterRecords(explorerItems,{node:$('evidenceNode').value||'all',kind:$('evidenceKind').value||'all',query:$('evidenceSearch').value||'',hours:'all'},snap&&(snap.logical_time||snap.generated_at));
    const size=8,pages=Math.max(1,Math.ceil(result.length/size));evidencePage=Math.min(evidencePage,pages-1);
    $('evidenceCount').textContent='匹配 '+result.length+' / 本次可见 '+explorerItems.length+' 条摘要 · 按记录时间倒序；相邻不表示因果';
    const host=$('evidenceRecords');host.replaceChildren();
    if(!result.length)host.append(empty('没有匹配的可见摘要。'));
    for(const it of result.slice(evidencePage*size,(evidencePage+1)*size)){
      const row=h('details','ev');const sum=h('summary');
      sum.append(h('span','ev-k',it.kindLabel),h('span','ev-t',clip(it.title||it.kindLabel,80)),h('span','ev-m',[it.status,it.when?clock(it.when):'未标注'].filter(Boolean).join(' · ')));
      const body=h('div','ev-body');body.append(h('p','',X.recordBody(it)));
      if(it.values.length){const dl=h('dl','detail-values');for(const v of it.values){dl.append(h('dt','',v.label),h('dd','',v.text));}body.append(dl);}
      body.append(h('p','map-note','来自：'+it.sectionLabel+'。这是授权摘要，不是完整推理或跨记录因果证明。'));
      row.append(sum,body);host.append(row);
    }
    $('evidencePage').textContent=(evidencePage+1)+' / '+pages;$('previousEvidence').disabled=evidencePage===0;$('nextEvidence').disabled=evidencePage>=pages-1;
  }
  on($('evidenceSearch'),'input',()=>{evidencePage=0;renderEvidence();});
  on($('evidenceNode'),'change',()=>{evidencePage=0;renderEvidence();});
  on($('evidenceKind'),'change',()=>{evidencePage=0;renderEvidence();});
  on($('previousEvidence'),'click',()=>{evidencePage=Math.max(0,evidencePage-1);renderEvidence();});
  on($('nextEvidence'),'click',()=>{evidencePage++;renderEvidence();});
  function renderCost(){
    const host=$('costPanel');if(!host)return;
    if(!isRec(costData)||!isRec(costData.summary)){host.replaceChildren(empty(costData===false?'成本记录暂时不可用。':'正在读取成本记录…'));return;}
    const sm=costData.summary;host.replaceChildren();
    const head=h('div','cost-head');head.append(h('h3','card-title','成本与用量'),h('span','muted','统计日 '+String(sm.today_date||'')+' · 含订阅折算，不等于实际账单'));
    const kpis=h('div','cost-kpis');
    for(const [lab,val,sub] of [['今日',money(sm.today_total_cny),'API '+money(sm.today_api_cny)+' · 订阅折算 '+money(sm.today_sub_equiv_cny)],['近 7 日',money(sm.days_7_total_cny),'日均 '+money(sm.days_7_avg_cny)],['今日调用',num(sm.today_calls)+' 次',tokens(sm.today_total_tokens)+' tokens · 缓存 '+(Number(sm.today_cache_hit_rate||0)*100).toFixed(1)+'%'],['累计',money(sm.all_time_total_cny),num(sm.all_time_calls)+' 次调用']]){
      const k=h('div','cost-kpi');k.append(h('span','',lab),h('strong','',val),h('small','',sub));kpis.append(k);
    }
    host.append(head,kpis);
    const {days,max}=S.costSeries(costData);
    const body=h('div','cost-body');
    if(days.length&&max>0){
      const W=640,H=200,L=36,B=170,step=(W-L-10)/days.length;
      const svg=s('svg',{viewBox:'0 0 '+W+' '+H,class:'cost-svg',role:'group','aria-label':'每日成本'});
      for(const r of [0,.5,1]){const y=B-r*(B-16);svg.append(s('line',{x1:L,y1:y,x2:W-6,y2:y,class:'cost-grid'}),s('text',{x:L-6,y:y+4,class:'cost-axis end'},(max*r).toFixed(1)));}
      const detail=h('p','cost-day');
      const select=i=>{const d=days[i];costDay=d.date;detail.textContent=d.date+' · '+money(d.total_cny)+' · '+num(d.calls)+' 次 · 后台 '+money(d.background_cny)+' / 聊天 '+money(d.interactive_cny);for(const [j,g] of bars.entries())g.classList.toggle('sel',j===i);};
      const bars=[];
      days.forEach((d,i)=>{
        const x=L+i*step+step*.18,w=step*.64,hh=(Number(d.total_cny)||0)/max*(B-16),fg=Math.min(hh,(Number(d.interactive_cny)||0)/max*(B-16));
        const g=s('g',{class:'cost-bar'});g.append(s('rect',{x:L+i*step,y:8,width:step,height:B,class:'cost-hit'}),s('rect',{x,y:B-hh,width:w,height:Math.max(1,hh),rx:3,class:'cost-bg'}));
        if(fg>0)g.append(s('rect',{x,y:B-fg,width:w,height:fg,rx:2,class:'cost-fg'}));
        if(i%2===0||i===days.length-1)g.append(s('text',{x:x+w/2,y:B+18,class:'cost-axis'},String(d.label||d.date).slice(-5)));
        g.append(s('title',{},d.date+' · '+money(d.total_cny)));activate(g,()=>select(i));g.setAttribute('aria-label',d.date+' 成本 '+money(d.total_cny));
        bars.push(g);svg.append(g);
      });
      const left=h('div','cost-chart');left.append(svg,detail);
      const recent=arr(costData.recent_records).filter(isRec).slice(0,6);
      if(recent.length){
        const list=h('div','cost-recent');list.append(h('p','thought-label','最近的调用'));
        for(const r of recent){const row=h('div','cost-rec');row.append(h('time','',String(r.time||'')),h('span','',String(r.purpose_label||r.purpose||'')),h('span','muted',String(r.model||'')),h('span','cost-num',tokens(r.total_tokens)),h('strong','cost-num','¥'+Number(r.cost_cny||0).toFixed(4)));list.append(row);}
        left.append(list);
      }
      body.append(left);
      const prev=days.findIndex(d=>d.date===costDay);select(prev<0?days.length-1:prev);
    }
    const models=arr(costData.by_model).filter(isRec).sort((a,b)=>Number(b.days_7_cny||0)-Number(a.days_7_cny||0));
    const total=models.reduce((n,m)=>n+Number(m.days_7_cny||0),0);
    const right=h('div','cost-models');right.append(h('p','thought-label','近 7 日 · 按模型'));
    const COLORS=['#9ef0c4','#86ccff','#ffcf86','#f6a6c1','#c6b4ff'];
    models.forEach((m,i)=>{const row=h('div','cost-model');const share=total>0?Number(m.days_7_cny||0)/total*100:0;const b=h('span','cost-model-bar');const f=h('i');f.style.width=share+'%';f.style.background=COLORS[i%COLORS.length];b.append(f);
      row.append(h('strong','',String(m.model)),h('span','muted',m.billing_mode==='subscription'?'订阅折算':'API'),b,h('span','cost-num',money(m.days_7_cny)));right.append(row);});
    const purposes=arr(costData.by_purpose).filter(isRec).sort((a,b)=>Number(b.days_7_cny||0)-Number(a.days_7_cny||0)).slice(0,6);
    if(purposes.length){right.append(h('p','thought-label','花在哪里'));for(const p of purposes){const row=h('div','cost-purpose');row.append(h('span','',String(p.label||p.purpose)),h('span','cost-num',money(p.days_7_cny)));right.append(row);}}
    body.append(right);host.append(body);
  }
  async function loadCost(){
    if(paused||typeof window.fetch!=='function')return;
    if(Date.now()-costLoadedAt<60000&&costData)return;
    costLoadedAt=Date.now();
    try{const r=await window.fetch('/world-v2/dashboard/cost',{credentials:'same-origin',headers:{Accept:'application/json'}});if(!r||!r.ok)throw new Error('cost');const d=await r.json();costData=isRec(d)&&isRec(d.summary)?d:false;}
    catch(_e){costData=false;}
    renderCost();
  }

  // --- nav, presentation, pause --------------------------------------------------------------
  const scenes=()=>[...document.querySelectorAll('[data-scene]')].filter(n=>!(presenting&&n.classList.contains('operator-only')));
  let activeNav='now';
  function setActiveNav(id){
    activeNav=id;
    for(const a of document.querySelectorAll('[data-nav]')){
      const hit=a.getAttribute('data-nav')===id;a.classList.toggle('active',hit);
      if(hit){const ink=$('navInk');if(ink&&a.offsetWidth){ink.style.width=a.offsetWidth+'px';ink.style.transform='translateX('+a.offsetLeft+'px)';}}
    }
    if(presenting){const list=scenes(),i=list.findIndex(n=>n.id===id);if(i>=0)$('presentIndex').textContent=String(i+1).padStart(2,'0')+' / '+String(list.length).padStart(2,'0');}
  }
  if(typeof IntersectionObserver==='function'){
    const spy=new IntersectionObserver(es=>{for(const e of es)if(e.isIntersecting)setActiveNav(e.target.id);},{rootMargin:'-45% 0px -50% 0px'});
    for(const sc of document.querySelectorAll('[data-scene]'))spy.observe(sc);
  }
  function currentScene(){const list=scenes();const y=(window.scrollY||0)+(window.innerHeight||800)*.4;let cur=0;list.forEach((n,i)=>{if((n.offsetTop||0)<=y)cur=i;});return cur;}
  function goScene(delta){const list=scenes();if(!list.length)return;const i=Math.max(0,Math.min(list.length-1,currentScene()+delta));list[i].scrollIntoView&&list[i].scrollIntoView({behavior:reduced()?'auto':'smooth',block:'start'});setActiveNav(list[i].id);}
  function togglePresent(){
    presenting=!presenting;document.body.classList.toggle('presenting',presenting);
    $('presentToggle').setAttribute('aria-pressed',String(presenting));$('presentToggle').textContent=presenting?'退出演示':'演示模式';
    $('presentHud').hidden=!presenting;
    if(presenting){document.documentElement.requestFullscreen&&document.documentElement.requestFullscreen().catch(()=>{});goScene(0);}
    else if(document.fullscreenElement&&document.exitFullscreen)document.exitFullscreen().catch(()=>{});
    if(snap){renderFate(snap);}
  }
  on($('presentToggle'),'click',togglePresent);
  on($('pauseDisplay'),'click',()=>{
    paused=!paused;$('pauseDisplay').textContent=paused?'恢复同步':'暂停画面';$('pauseDisplay').setAttribute('aria-pressed',String(paused));$('pauseLabel').hidden=!paused;
    document.body.classList.toggle('paused',paused);tickSync();
    if(!paused){if(loading)resumePending=true;else load();}
  });
  on(document,'keydown',e=>{
    const d=$('detailDrawer');
    if(e.key==='Escape'){if(d&&d.open){e.preventDefault&&e.preventDefault();closeDetail();return;}if(lightboxClose){lightboxClose();return;}if(presenting)togglePresent();return;}
    if(!presenting||(d&&d.open))return;
    const tag=e.target&&e.target.tagName?String(e.target.tagName).toLowerCase():'';
    if(['input','textarea','select'].includes(tag))return;
    if(['ArrowRight','ArrowDown','PageDown',' '].includes(e.key)){e.preventDefault&&e.preventDefault();goScene(1);}
    if(['ArrowLeft','ArrowUp','PageUp'].includes(e.key)){e.preventDefault&&e.preventDefault();goScene(-1);}
  });
  let resizeTimer=null;
  on(window,'resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{setActiveNav(activeNav);if(snap)renderFate(snap);},200);});
  setActiveNav('now');
  if(document.fonts&&document.fonts.ready)document.fonts.ready.then(()=>setActiveNav(activeNav)).catch(()=>{});

  // --- loading -------------------------------------------------------------------------------
  function safe(fn,label){try{fn();}catch(err){if(typeof console!=='undefined'&&console.error)console.error('dashboard '+label+' render failed',err);}}
  function renderStudioParts(){safe(renderInner,'inner');safe(renderStation,'station');if(snap)safe(()=>renderMind(snap),'mind');}
  function renderSnapshot(sn){
    safe(()=>renderHero(sn),'hero');safe(()=>renderFate(sn),'fate');safe(()=>renderWeek(sn),'week');
    safe(()=>renderBonds(sn),'bonds');safe(()=>renderMind(sn),'mind');safe(()=>renderSeen(sn),'seen');safe(()=>renderEngine(sn),'engine');
    setCaptureState('ready');
  }
  async function loadSide(){
    if(paused||typeof window.fetch!=='function')return;
    const get=async url=>{try{const r=await window.fetch(url,{credentials:'same-origin',headers:{Accept:'application/json'}});return r&&r.ok?await r.json():null;}catch(_e){return null;}};
    const [st,vo]=await Promise.all([get('/world-v2/dashboard/studio'),get('/world-v2/dashboard/voice')]);
    if(paused)return;
    studio=isRec(st)?st:(studio||false);voiceData=isRec(vo)?vo:(voiceData||false);
    renderStudioParts();safe(renderVoice,'voice');
    if(engineSeen)loadCost();
  }
  async function load(){
    if(loading||paused)return;
    loading=true;
    try{
      const result=await C.capture(window.fetch.bind(window),etag,{week:agendaWeek,page:agendaPage});
      if(paused)return;
      const next=C.snapshotFromCapture(result,snap);
      lastSync=Date.now();tickSync();
      if(result.kind==='not_modified'){setCaptureState('ready');return;}
      etag=result.etag;snap=next;
      renderSnapshot(next);
      loadSide();
    }catch(_error){
      if(paused)return;
      // Upstream payloads and errors never become page content.
      setCaptureState(snap?'stale':'unavailable');
      if(!snap){$('heroActivity').textContent='尚未取得可验证的生活快照。';}
    }finally{loading=false;if(resumePending){resumePending=false;load();}}
  }
  renderInner();renderStation();renderVoice();renderCost();
  load();
  setInterval(()=>{load();tickSync();},15000);
  if(typeof window.setTimeout==='function'||typeof setTimeout==='function'){const loop=()=>{tickSync();setTimeout(loop,1000);};setTimeout(loop,1000);}
"""
