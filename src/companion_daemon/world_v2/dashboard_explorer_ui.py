"""Read-only mechanism map and evidence explorer for the owner dashboard.

The diagram explains architecture, not inferred per-event causality. Its model
consumes only the already-redacted owner DTO. No world operations live here.
"""

EXPLORER_HTML = """
<section id="runExplorer" class="panel run-explorer" data-focus-area="mechanism" aria-labelledby="mechanismTitle">
  <div class="explorer-heading"><div><p class="hero-label">INSIDE A CONTINUING LIFE</p><h2 id="mechanismTitle">她是怎样运行的</h2><p class="section-note">点一个环节，看它的作用，以及这一刻真正留下的记录。</p></div><span class="map-label">机制示意 · 非实时执行轨迹</span></div>
  <div class="map-layout"><div><div id="mechanismMap" class="mechanism-map"></div><div class="map-legend"><span><i class="role-dot"></i>角色作判断</span><span><i class="system-dot"></i>系统提供事实与边界</span><span>连线表示机制关系，不证明记录之间的因果</span></div></div><aside id="mechanismDetail" class="mechanism-detail" aria-label="所选环节详情"></aside></div>
  <div id="runtimeRibbon" class="runtime-ribbon"></div>
</section>
<section class="panel evidence-explorer" data-focus-area="evidence" aria-labelledby="evidenceTitle">
  <div class="explorer-heading"><div><p class="hero-label">FOLLOW THE EVIDENCE</p><h2 id="evidenceTitle">翻看她的生活记录</h2><p class="section-note">搜索、筛选，再展开一条记录。计划、经历、表达和回执各自保留原有状态。</p></div><button class="button" id="resetEvidence" type="button">清除筛选</button></div>
  <div class="evidence-filters"><label>查找内容<input id="evidenceSearch" type="search" placeholder="搜索本次快照中的摘要…"></label><label>运行环节<select id="evidenceNode"><option value="all">全部环节</option></select></label><label>记录类型<select id="evidenceKind"><option value="all">全部类型</option></select></label><label>记录时间<select id="evidenceTime"><option value="all">不限时间</option><option value="24">最近 24 小时</option><option value="168">最近 7 天</option><option value="undated">未标注时间</option></select></label></div>
  <p id="evidenceCount" class="section-note" role="status"></p><div id="evidenceRecords" class="evidence-records"></div>
  <div class="evidence-pagination"><span id="evidencePage" class="section-note"></span><div><button id="previousEvidence" class="button" type="button">上一页</button> <button id="nextEvidence" class="button" type="button">下一页</button></div></div>
  <details class="coverage-details"><summary>数据范围与可见边界</summary><div id="evidenceCoverage"></div><p class="section-note">搜索只覆盖当前快照的授权摘要，不是全部历史。未展示不等于不存在；私人反思仍只计数。此页面的操作不会改变角色或触发模型调用。</p></details>
</section>
"""

EXPLORER_CSS = """
.run-explorer,.evidence-explorer{padding:28px}.explorer-heading{display:flex;justify-content:space-between;align-items:start;gap:20px;margin-bottom:22px}.explorer-heading h2{margin:0 0 8px;font-size:24px;font-weight:550;letter-spacing:.02em}.explorer-heading .hero-label{margin-bottom:10px}.map-label{font-size:11px;white-space:nowrap;background:#edf0e6;color:#647361;padding:8px 12px;border-radius:20px}.map-layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:24px}.mechanism-map{background:radial-gradient(circle at 50% 45%,#e9eee0 0,transparent 55%),#f7f8f2;border:1px solid #e2e8dc;border-radius:14px;overflow:hidden}.mechanism-map svg{display:block;width:100%;height:auto}.mechanism-map .flow{fill:none;stroke:#b3c1ad;stroke-width:1.6}.mechanism-map .return-flow{stroke-dasharray:5 5}.mechanism-map .node{cursor:pointer;outline:none}.mechanism-map .node rect{fill:#fffef9;stroke:#cbd6c5;stroke-width:1.2;transition:stroke .15s}.mechanism-map .node:hover rect{stroke:#50765b;stroke-width:2}.mechanism-map .node:focus-visible rect{stroke:#94703b;stroke-width:3}.mechanism-map .node[aria-pressed="true"] rect{stroke:#315b4a;stroke-width:2.5;fill:#edf3e7}.mechanism-map .node.role rect{fill:#315b4a;stroke:#315b4a}.mechanism-map .node.role[aria-pressed="true"] rect{fill:#254a3a;stroke:#c5a665;stroke-width:3}.mechanism-map .node text{fill:#263d36;pointer-events:none}.mechanism-map .node.role text{fill:#fffef9}.mechanism-map .node .node-number{font:10px system-ui;fill:#748c74;letter-spacing:1px}.mechanism-map .node .node-title{font-size:16px;font-weight:550}.mechanism-map .node .node-note{font-size:11px;fill:#6b7f69}.mechanism-map .node.role .node-note,.mechanism-map .node.role .node-number{fill:#ceddcb}.mechanism-map .flow-label{font-size:10px;fill:#7a8a73}.map-legend{display:flex;flex-wrap:wrap;gap:10px 18px;margin:12px 0 0;font-size:10px;color:var(--muted)}.map-legend span{display:flex;align-items:center;gap:6px}.map-legend i{width:8px;height:8px;display:inline-block;border-radius:50%}.role-dot{background:#315b4a}.system-dot{background:#bdcab5}.mechanism-detail{border-left:1px solid var(--line);padding-left:24px;min-width:0}.mechanism-detail h3{font-size:22px;margin:12px 0}.mechanism-detail .explanation{font-size:13px;line-height:1.9;margin:0 0 14px}.mechanism-detail .limit-note{font-size:11px;color:#786c56;line-height:1.8;border-left:2px solid #d1bb8e;padding-left:10px;margin:14px 0}.node-evidence{margin:12px 0;padding:0;list-style:none;display:grid;gap:8px}.node-evidence li{font-size:12px;line-height:1.7;padding:9px 11px;background:#f3f5ed;border-radius:8px;overflow-wrap:anywhere}.node-evidence time{display:block;font-size:10px;color:var(--muted)}.mechanism-detail .button{width:100%;margin-top:6px}.runtime-ribbon{border-top:1px solid var(--line);margin-top:22px;padding-top:16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}.runtime-ribbon .runtime-title{font-size:12px;font-weight:550;margin-right:8px}.runtime-chip{font-size:10px;border:1px solid var(--line);border-radius:6px;padding:5px 8px;color:var(--muted)}.runtime-chip[data-state="degraded"],.runtime-chip[data-state="unavailable"]{border-color:#dec5a1;color:#815a30;background:#faf3e8}.evidence-filters{display:grid;grid-template-columns:2fr 1fr 1fr 1fr;gap:12px;margin-bottom:16px}.evidence-filters label{font-size:11px;color:var(--muted);display:grid;gap:7px}.evidence-filters input,.evidence-filters select{min-width:0;width:100%;font:inherit;font-size:12px;padding:11px;border:1px solid var(--line);border-radius:8px;background:#fafbf6;color:var(--ink)}.evidence-filters input:focus-visible{outline:3px solid #a4b89a;outline-offset:2px}.evidence-records{margin-top:14px;min-height:160px}.evidence-record{border-top:1px solid var(--line)}.evidence-record summary{list-style:none;display:grid;grid-template-columns:115px minmax(0,1fr) 110px 20px;gap:16px;padding:16px 0;align-items:center;cursor:pointer}.evidence-record summary::-webkit-details-marker{display:none}.evidence-record summary:after{content:'+';color:#7a8d74;font-size:20px;text-align:center}.evidence-record[open] summary:after{content:'−'}.evidence-record .record-kind{font-size:11px;color:var(--muted)}.evidence-record .record-title{font-size:13px;line-height:1.7;overflow-wrap:anywhere}.evidence-record .record-meta{font-size:10px;color:var(--muted);line-height:1.8;text-align:right}.record-body{padding:0 24px 20px 131px;line-height:1.9;font-size:13px;overflow-wrap:anywhere}.record-body p{margin:0 0 12px}.record-values{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 20px}.record-value{font-size:11px;color:var(--muted)}.record-value strong{display:block;color:var(--ink);font-size:12px;font-weight:500}.record-meter{height:4px;margin-top:5px;border-radius:4px;overflow:hidden;background:#e7ecdf}.record-meter span{display:block;height:100%;background:#7c9b75}.record-source{font-size:10px!important;color:var(--muted);margin-top:12px!important}.evidence-pagination{display:flex;justify-content:space-between;align-items:center;gap:12px;border-top:1px solid var(--line);padding-top:14px}.button:disabled{opacity:.4;cursor:default}.coverage-details{border-top:1px solid var(--line);margin-top:18px;padding-top:16px}.coverage-details summary,.domain-details>summary{font-size:12px;color:var(--muted);cursor:pointer}.coverage-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:16px 0}.coverage-grid .coverage-card{font-size:11px;line-height:1.8;margin:0;background:#f4f6ee;padding:10px;border-radius:8px}.coverage-grid strong{display:block;font-weight:500;color:var(--ink)}.domain-details>.section-grid{margin-top:20px}.view-tools{display:flex;gap:8px;align-items:center}.pause-label{font-size:11px;color:#805a30}.now-panel{padding:22px 28px}.now-panel h2{font-size:29px}.now-line .v{font-size:13px}.now-story{gap:12px 22px}.now-panel .hero-note{margin:8px 0}.map-label,.node-authority{font-size:10px}.node-authority{color:#315b4a;background:#e8eee1;padding:5px 8px;border-radius:5px}.domain-details{min-width:0}body.recording .map-label{display:block}body.recording .mechanism-detail .explanation{font-size:15px}body.recording .domain-details:not([open]){display:none}
@media(max-width:1100px){.map-layout{grid-template-columns:1fr}.mechanism-detail{border-left:0;border-top:1px solid var(--line);padding:18px 0 0}.node-evidence{grid-template-columns:repeat(3,minmax(0,1fr))}.mechanism-detail .button{width:auto}.evidence-filters{grid-template-columns:2fr 1fr}.map-label{white-space:normal}.coverage-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:680px){.run-explorer,.evidence-explorer{padding:18px}.explorer-heading{gap:12px;flex-wrap:wrap;margin-bottom:16px}.explorer-heading h2{font-size:21px}.mechanism-map{overflow-x:auto}.mechanism-map svg{min-width:620px}.map-legend{line-height:1.8}.node-evidence{grid-template-columns:1fr}.evidence-filters{grid-template-columns:1fr 1fr}.evidence-record summary{grid-template-columns:68px minmax(0,1fr) 16px;gap:10px}.record-meta{display:none}.record-body{padding:0 8px 16px}.record-values{grid-template-columns:1fr}.coverage-grid{grid-template-columns:1fr 1fr}.view-tools{flex-wrap:wrap}.pause-label{width:100%}}
@media(prefers-reduced-motion:reduce){.mechanism-map .node rect{transition:none}}
"""

EXPLORER_JS = r"""
const DashboardExplorer=(()=>{
  const sections=['overview_life','facts_memory_inner','relationship_lifecycle','operations','perception_media','authority_privacy','ledger_qualification','runtime_operations'];
  const nodes=[
    {id:'world',title:'世界与感知',subtitle:'时间、事件、外部输入',x:28,y:38,authority:'系统提供环境',
      sources:{overview_life:['world_occurrence','location','resource','life_ecology_schedule'],perception_media:null},
      explanation:'世界中的事件、身体与位置，以及外部感知，为她提供可用的环境事实。机会不是指令：发生了什么，与她要不要参与，是两件事。',
      limit:'这里只能看到已获授权的环境摘要；世界事件不自动等于她亲身经历。'},
    {id:'memory',title:'事实与记忆',subtitle:'保留、遗忘、再次提取',x:28,y:178,authority:'系统提供可追溯材料',
      sources:{facts_memory_inner:['fact','memory_candidate','character_core']},
      explanation:'过去的事实与记忆为下一次判断提供材料。记忆有保留状态和提取线索；角色决定怎样理解、提起或使用它们。',
      limit:'记忆候选不等于已确认事实；此处列出记忆摘要，不证明某次回答实际检索了它。'},
    {id:'inner',title:'感受与关系',subtitle:'情境评估、情绪、牵挂',x:28,y:318,authority:'角色赋予意义',
      sources:{facts_memory_inner:['appraisal','affect_episode','affect_baseline','attention'],relationship_lifecycle:null},
      explanation:'她如何看待一件事，会留下情境评估、情绪与关系变化。这些是判断的背景，不是“某种情绪就必须说某句话”的行为脚本。',
      limit:'数字只描述已记录的状态。私人印象只计数，不展示完整内心反思。'},
    {id:'choice',title:'由她作决定',subtitle:'想做什么 · 怎么说 · 是否沉默',x:304,y:178,role:true,authority:'角色模型的决定权',
      sources:{overview_life:['plan','goal','aspiration','attention'],operations:['revisit_intention','response_expectation','expression_plan']},
      explanation:'角色模型结合当时可用的世界、记忆和关系，决定动机、态度、计划、表达和时机。系统不能代替她决定追问、主动联系或沉默。',
      limit:'下面是决定留下的摘要，不是完整思考过程。没有消息或技术失败都不能被解释为“她选择了沉默”。'},
    {id:'life',title:'行动与生活',subtitle:'计划 ≠ 尝试 ≠ 已完成',x:580,y:38,authority:'角色选择，系统结算',
      sources:{overview_life:['plan','life_arc','aspiration','biographical_coordinate','experience','outcome_observation']},
      explanation:'她形成计划后，生活行动才有机会发生。执行结果可能完成、中断或放弃，只有相应的记录才能作为发生依据。',
      limit:'已完成的活动也不保证目标达成；最新一条计划不能直接当成她此刻正在做的事。'},
    {id:'expression',title:'表达与联络',subtitle:'措辞、节奏、行动与回执',x:580,y:178,authority:'角色表达，系统执行',
      sources:{operations:null,runtime_operations:['technical_notice']},
      explanation:'她决定说什么、分几段说、是否再联系。系统负责执行授权动作并记录结果。表达已经生成、已经发送和已获回执是不同阶段。',
      limit:'技术失败不代表角色不愿说话；发送回执也不等于用户已读或已回复。'},
    {id:'feedback',title:'结果留下痕迹',subtitle:'经历、后果、待续事项',x:580,y:318,authority:'系统记账，角色理解',
      sources:{overview_life:['outcome_observation','experience'],operations:['execution_receipt','response_expectation','expectation_assessment','revisit_intention'],relationship_lifecycle:['thread','commitment','relationship_commitment','interaction_bid']},
      explanation:'行动的结果、对话的回应以及未完成事项，成为后续判断的材料。她可以据此重新看待原来的计划和关系，生活才有连续性。',
      limit:'箭头描述反馈机制，不表示下列某条结果已进入某次模型上下文，也不把相邻记录拼成因果链。'},
    {id:'boundary',title:'事实、权限与回执',subtitle:'授权 · 隐私 · 防重复执行',x:304,y:318,authority:'系统不可协商的边界',
      sources:{authority_privacy:null},
      explanation:'系统核对事实来源、事件权限、隐私与同意，并保证外部动作获授权、不重复执行、能通过回执核对和重放。它约束可执行范围，不决定她的性格和话语。',
      limit:'本图展示运行方式，不代表上线资格已经通过。记录只能证明它实际记下的内容。'},
  ];
  const object=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
  const list=v=>Array.isArray(v)?v:[];
  function records(snapshot,client){
    const result=[];
    for(const sectionId of sections){
      const section=snapshot?.sections?.[sectionId];
      if(!object(section)||section.state==='unavailable')continue;
      const summaries=[...list(section.data?.highlights)];
      for(const notice of list(section.data?.notices)){
        if(!object(notice))continue;
        summaries.push({kind:'technical_notice',kind_label:'技术提示',title:notice.signal_label||notice.label||'运行提示',
          detail:notice.reason_label||'未提供可见原因',occurred_at:notice.occurred_at||section.observed_at});
      }
      for(const terminal of list(section.data?.typed_change_terminals)){
        if(!object(terminal))continue;
        summaries.push({kind:'relationship_terminal',kind_label:'关系变更结果',title:terminal.target_stage_label||'关系阶段变更',
          status_code:terminal.status,status_label:terminal.status_label,occurred_at:terminal.occurred_at,
          detail:'此变更未生效；不应作为已发生的关系变化。'});
      }
      for(const item of summaries){
        const view=client.highlightView(item);
        if(!view)continue;
        result.push({...view,sectionId,sectionLabel:client.sectionHeading(sectionId,section),
          // Content keys preserve disclosure state, never imply entity identity.
          key:JSON.stringify([sectionId,view]),
          gauges:list(item.values).filter(v=>object(v)&&typeof v.key==='string'&&v.key.endsWith('_bp')&&Number.isFinite(v.value)&&v.value>=0&&v.value<=10000)
            .map(v=>({label:v.label,percent:v.value/100})),
        });
      }
    }
    return result.sort((a,b)=>(Date.parse(b.when)||0)-(Date.parse(a.when)||0));
  }
  function recordBody(item){
    if(item.detail)return item.detail;
    const reason=item.values.find(v=>['正文状态','环境结果正文','角色回应'].includes(v.label));
    if(reason)return reason.label+'：'+reason.text+'。';
    return item.values.length?'此记录以标题和结构化字段展示，没有单独的叙述正文。':'此记录仅提供标题和状态，当前快照未提供独立正文。';
  }
  function inNode(item,id){
    if(id==='all')return true;
    const node=nodes.find(n=>n.id===id);
    if(!node||!Object.hasOwn(node.sources,item.sectionId))return false;
    const kinds=node.sources[item.sectionId];
    return kinds===null||kinds.includes(item.kind);
  }
  function filterRecords(items,{node='all',kind='all',query='',hours='all'}={},referenceTime){
    const needle=query.trim().toLocaleLowerCase();
    const reference=Date.parse(referenceTime);
    return items.filter(item=>{
      if(!inNode(item,node)||(kind!=='all'&&item.kind!==kind))return false;
      const when=Date.parse(item.when);
      if(hours==='undated'&&Number.isFinite(when))return false;
      if(hours!=='all'&&hours!=='undated'){
        if(!Number.isFinite(reference)||!Number.isFinite(when))return false;
        const age=reference-when;
        if(age<0||age>Number(hours)*3600000)return false;
      }
      const haystack=[item.title,item.detail,item.status,item.kindLabel,item.sectionLabel,...item.values.map(v=>v.label+' '+v.text)].join(' ').toLocaleLowerCase();
      return !needle||haystack.includes(needle);
    });
  }
  function nodeState(snapshot,node){
    const states=Object.keys(node.sources).map(id=>snapshot?.sections?.[id]?.state||'unavailable');
    if(states.every(s=>s==='unavailable'))return 'unavailable';
    if(states.includes('unavailable')||states.includes('degraded'))return 'degraded';
    if(states.includes('stale'))return 'stale';
    return states.every(s=>s==='empty')?'empty':'ready';
  }
  function mount(client,{onShowEvidence=()=>{}}={}){
    const $=id=>document.getElementById(id);
    const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e;};
    const svgEl=(tag,attrs={},text)=>{const e=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,String(v));if(text!==undefined)e.textContent=text;return e;};
    let current={sections:{}},items=[],selected='choice',page=0;
    const openedRecords=new Set();
    const pageSize=8;
    const nodeElements=new Map();
    const filters=()=>({node:$('evidenceNode').value,kind:$('evidenceKind').value,query:$('evidenceSearch').value,hours:$('evidenceTime').value});
    const svg=svgEl('svg',{viewBox:'0 0 820 466',role:'group','aria-label':'角色运行机制，八个可选择的环节'});
    const defs=svgEl('defs');const marker=svgEl('marker',{id:'flowArrow',viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:5,markerHeight:5,orient:'auto-start-reverse'});
    marker.append(svgEl('path',{d:'M 0 0 L 10 5 L 0 10 z',fill:'#a6b69e'}));defs.append(marker);svg.append(defs);
    for(const d of ['M 240 84 H 265 V 217 H 304','M 240 224 H 304','M 240 364 H 270 V 239 H 304','M 516 213 H 552 V 84 H 580','M 516 231 H 580','M 792 84 H 807 V 364 H 792'])svg.append(svgEl('path',{d,class:'flow','marker-end':'url(#flowArrow)'}));
    svg.append(svgEl('path',{d:'M 684 270 V 318',class:'flow','marker-end':'url(#flowArrow)'}));
    svg.append(svgEl('path',{d:'M 684 410 V 443 H 12 V 224 H 28',class:'flow return-flow','marker-end':'url(#flowArrow)'}));
    svg.append(svgEl('path',{d:'M 410 318 V 270',class:'flow return-flow','marker-end':'url(#flowArrow)'}));
    svg.append(svgEl('text',{x:295,y:459,class:'flow-label'},'结果成为后续判断可用的材料'));
    nodes.forEach((node,index)=>{
      const group=svgEl('g',{class:'node'+(node.role?' role':''),transform:`translate(${node.x},${node.y})`,role:'button',tabindex:0,'aria-label':node.title,'aria-pressed':node.id===selected,'data-node':node.id});
      group.append(svgEl('rect',{width:212,height:92,rx:12}),svgEl('text',{x:16,y:20,class:'node-number'},String(index+1).padStart(2,'0')+' / '+(node.role?'CHARACTER':'LIFE SYSTEM')),svgEl('text',{x:16,y:45,class:'node-title'},node.title),svgEl('text',{x:16,y:65,class:'node-note'},node.subtitle));
      const count=svgEl('text',{x:16,y:81,class:'node-note'},'等待读取');group.append(count);nodeElements.set(node.id,{group,count});
      const select=()=>{selected=node.id;renderNode();};
      group.addEventListener('click',select);group.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select();}});svg.append(group);
      const option=el('option','',node.title);option.value=node.id;$('evidenceNode').append(option);
    });
    $('mechanismMap').append(svg);
    function renderNode(){
      for(const node of nodes){
        const ui=nodeElements.get(node.id),state=nodeState(current,node);
        ui.group.setAttribute('aria-pressed',String(node.id===selected));
        ui.count.textContent=state==='unavailable'?'摘要暂不可用':items.filter(i=>inNode(i,node.id)).length+' 条可见摘要'+(state!=='ready'?' · '+client.stateLabel(state):'');
      }
      const node=nodes.find(n=>n.id===selected),host=$('mechanismDetail');host.replaceChildren();
      host.append(el('span','node-authority',node.authority),el('h3','',node.title),el('p','explanation',node.explanation));
      const related=items.filter(i=>inNode(i,selected));
      host.append(el('p','section-subtitle','当前快照 · '+client.stateLabel(nodeState(current,node))+' · '+related.length+' 条摘要'));
      const evidence=el('ul','node-evidence');
      for(const item of related.slice(0,2)){
        const row=el('li','',[item.kindLabel,item.title,item.status].filter(Boolean).join(' · '));
        row.append(el('time','',item.when?client.formatClock(item.when):'未标注记录时间'));evidence.append(row);
      }
      if(!related.length)evidence.append(el('li','','暂无可展示的记录，不能据此判断这个机制没有运行。'));
      const button=el('button','button','查看此环节的记录 ('+related.length+')');button.type='button';
      button.addEventListener('click',()=>{$('evidenceNode').value=selected;$('evidenceKind').value='all';$('evidenceTime').value='all';$('evidenceSearch').value='';page=0;renderRecords();onShowEvidence();const panel=$('evidenceTitle').closest('section');panel.hidden=false;panel.scrollIntoView({block:'start'});$('evidenceSearch').focus({preventScroll:true});});
      host.append(evidence,el('p','limit-note',node.limit),button);
    }
    function renderRecords(){
      const result=filterRecords(items,filters(),current.logical_time||current.generated_at);
      const pages=Math.max(1,Math.ceil(result.length/pageSize));page=Math.min(page,pages-1);
      $('evidenceCount').textContent='匹配 '+result.length+' / 本次可见 '+items.length+' 条摘要 · 按记录时间倒序；相邻不表示因果关系';
      const host=$('evidenceRecords');host.replaceChildren();
      if(!result.length)host.append(el('p','empty-state','没有匹配的可见摘要。可清除筛选，或在下方检查数据范围。'));
      for(const item of result.slice(page*pageSize,(page+1)*pageSize)){
        const row=el('details','evidence-record'),summary=el('summary');
        row.open=openedRecords.has(item.key);
        row.addEventListener('toggle',()=>{if(row.open)openedRecords.add(item.key);else openedRecords.delete(item.key);});
        summary.append(el('span','record-kind',item.kindLabel),el('span','record-title',item.title||item.kindLabel),el('span','record-meta',[item.status,item.when?client.formatClock(item.when):'未标注时间'].filter(Boolean).join(' · ')));
        const body=el('div','record-body');
        body.append(el('p','',recordBody(item)));
        const values=el('div','record-values');
        for(const value of item.values){const cell=el('div','record-value',value.label);cell.append(el('strong','',value.text));const gauge=item.gauges.find(g=>g.label===value.label);if(gauge){const bar=el('div','record-meter');bar.setAttribute('aria-hidden','true');const fill=el('span');fill.style.width=gauge.percent+'%';bar.append(fill);cell.append(bar);}values.append(cell);}
        body.append(values,el('p','record-source','来自：'+item.sectionLabel+' · '+(item.when?client.formatClock(item.when):'时间未标注')+' · '+(item.status||item.title)+'。这是该领域的授权摘要，不是完整推理或跨记录因果证明。'));
        row.append(summary,body);host.append(row);
      }
      $('evidencePage').textContent='第 '+(page+1)+' / '+pages+' 页';$('previousEvidence').disabled=page===0;$('nextEvidence').disabled=page===pages-1;
    }
    function renderCoverage(){
      const grid=el('div','coverage-grid');
      for(const id of sections){
        const section=current.sections?.[id];if(!section)continue;
        const card=el('div','coverage-card');card.append(el('strong','',client.sectionHeading(id,section)+' · '+client.stateLabel(section.state)));
        const c=section.coverage;card.append(document.createTextNode(c?'纳入 '+c.included_count+' / 已知 '+c.known_count+(c.truncated?' · 已截取摘要':''):'覆盖范围未提供'));
        if(section.state!=='unavailable'){
          const counts=el('details','');counts.append(el('summary','','展开领域计数'));
          for(const metric of list(section.data?.metrics)){
            if(object(metric)&&typeof metric.label==='string'&&Number.isFinite(metric.count)){
              counts.append(el('div','',metric.label+' · '+metric.count));
              if(typeof metric.count_note==='string')counts.append(el('small','',metric.count_note));
            }
          }
          for(const [key,value]of Object.entries(section.data||{})){
            const label=client.dataFieldLabel(key);if(label&&typeof value==='number')counts.append(el('div','',label+' · '+value));
          }
          if(typeof section.data?.qualification_label==='string')counts.append(el('div','',section.data.qualification_label));
          card.append(counts);
        }
        grid.append(card);
      }
      const activity=el('div','mechanism-activity');
      activity.append(el('h3','','机制是否留下运行记录'),el('p','limit-note','以下是本世界已记录的触发流程，不是功能开关或模型调用次数。终结可能是完成、无操作或技术失败；零条不能单独证明未启用。'));
      const rows=current.sections?.operations?.state==='unavailable'?[]:list(current.sections?.operations?.data?.mechanisms);
      if(!rows.length)activity.append(el('p','','当前服务未提供分机制统计，不能按零次处理。'));
      const table=el('div','coverage-grid');
      for(const row of rows){
        if(!object(row)||typeof row.label!=='string'||!['recorded_count','open_count','claimed_count','terminal_count','attempt_count'].every(k=>Number.isInteger(row[k])&&row[k]>=0))continue;
        const card=el('div','coverage-card');card.append(el('strong','',row.label),el('div','',row.recorded_count?'已记录 '+row.recorded_count+' 次流程':'暂无此类账本流程'),el('div','','待处理 '+row.open_count+' · 已认领 '+row.claimed_count+' · 已终结 '+row.terminal_count),el('div','','受理尝试 '+row.attempt_count+' 次'));if(typeof row.scope_note==='string')card.append(el('small','',row.scope_note));table.append(card);
      }
      activity.append(table);
      $('evidenceCoverage').replaceChildren(activity,grid);
      const ribbon=$('runtimeRibbon');ribbon.replaceChildren(el('span','runtime-title','技术运行状态'));
      const runtime=current.sections?.runtime_operations;
      if(!runtime||runtime.state==='unavailable')ribbon.append(el('span','runtime-chip','暂不可用 · 不能据此判断角色沉默'));
      else{
        for(const signal of list(runtime.data?.signals)){
          const chip=el('span','runtime-chip',signal.label+' · '+client.stateLabel(signal.state));chip.dataset.state=signal.state;ribbon.append(chip);
        }
        for(const value of [runtime.data?.expression_episode?.mode_label,runtime.data?.semantic_recall?.semantic_embedding_label])if(value)ribbon.append(el('span','runtime-chip',value));
      }
    }
    for(const id of ['evidenceNode','evidenceKind','evidenceTime'])$(id).addEventListener('change',()=>{page=0;renderRecords();});
    $('evidenceSearch').addEventListener('input',()=>{page=0;renderRecords();});
    $('resetEvidence').addEventListener('click',()=>{$('evidenceNode').value='all';$('evidenceKind').value='all';$('evidenceTime').value='all';$('evidenceSearch').value='';page=0;renderRecords();});
    $('previousEvidence').addEventListener('click',()=>{page=Math.max(0,page-1);renderRecords();});
    $('nextEvidence').addEventListener('click',()=>{page++;renderRecords();});
    return {update(snapshot){
      current=snapshot;items=records(snapshot,client);
      const prior=$('evidenceKind').value;$('evidenceKind').replaceChildren();const all=el('option','','全部类型');all.value='all';$('evidenceKind').append(all);
      const kinds=new Map(items.map(i=>[i.kind,i.kindLabel]));for(const [kind,label]of kinds){const option=el('option','',label);option.value=kind;$('evidenceKind').append(option);}$('evidenceKind').value=kinds.has(prior)?prior:'all';
      renderNode();renderRecords();renderCoverage();
    }};
  }
  return {nodes,records,recordBody,inNode,filterRecords,nodeState,mount};
})();
DashboardHomeClient.explorer=DashboardExplorer;
"""
