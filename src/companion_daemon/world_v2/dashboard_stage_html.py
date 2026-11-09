"""Static shell for the owner Dashboard "stage" presentation.

The shell carries no World data.  Every dynamic field is filled by the
browser script through ``textContent``/DOM nodes from the authorized owner
snapshot, so the markup itself can be cached or previewed safely.
"""

GARDENIA_MARK = (
    '<svg viewBox="0 0 40 40" aria-hidden="true" class="gardenia">'
    '<g transform="translate(20 20)">'
    + "".join(
        f'<path d="M0 -3 C 6 -6 7 -15 0 -18 C -7 -15 -6 -6 0 -3Z" transform="rotate({angle})"/>'
        for angle in (0, 60, 120, 180, 240, 300)
    )
    + '<circle r="3.4"/></g></svg>'
)

PETALS = "".join(f'<i style="--i:{i}"></i>' for i in range(9))


def _scene_head(index: str, eyebrow: str, title: str, lede: str, tools: str = "") -> str:
    return (
        '<header class="scene-head">'
        f'<div class="scene-title"><p class="eyebrow"><span class="idx">{index}</span>{eyebrow}</p>'
        f"<h2>{title}</h2><p class=\"lede\">{lede}</p></div>"
        f'<div class="scene-tools">{tools}</div></header>'
    )


STAGE_BODY_HTML = (
    '<div class="backdrop" aria-hidden="true"><div class="aurora a1"></div><div class="aurora a2"></div>'
    '<div class="aurora a3"></div><div class="grain"></div></div>'
    '<header class="topbar" id="topbar">'
    f'<a class="brand" href="#now">{GARDENIA_MARK}<span class="brand-text"><strong>沈知栀</strong>'
    "<small>A LIFE, CONTINUING</small></span></a>"
    '<nav class="nav" id="sectionNav" aria-label="页面章节">'
    '<a href="#now" data-nav="now">此刻</a><a href="#inner" data-nav="inner">心声</a>'
    '<a href="#fate" data-nav="fate">命运线</a><a href="#week" data-nav="week">一周</a>'
    '<a href="#bonds" data-nav="bonds">羁绊</a><a href="#mind" data-nav="mind">记忆</a>'
    '<a href="#seen" data-nav="seen">所见</a><a href="#station" data-nav="station">寄送</a>'
    '<a href="#engine" data-nav="engine" class="operator-only">引擎</a>'
    '<span class="nav-ink" id="navInk" aria-hidden="true"></span></nav>'
    '<div class="top-tools">'
    '<div class="sync" id="syncPill" data-state="loading"><i class="sync-dot" aria-hidden="true"></i>'
    '<span id="captureState" class="capture-state" role="status">正在读取</span>'
    '<span id="syncAgo" class="sync-ago"></span></div>'
    '<span id="headerClock" class="top-clock"></span>'
    '<button id="pauseDisplay" class="tool" type="button" aria-pressed="false">暂停画面</button>'
    '<button id="presentToggle" class="tool primary" type="button" aria-pressed="false">演示模式</button>'
    '<form class="logout operator-only" method="post" action="/world-v2/dashboard/logout">'
    '<button type="submit" class="tool ghost">退出登录</button></form>'
    "</div></header>"
    '<p id="captureNotice" class="capture-notice" role="status" hidden></p>'
    '<p id="pauseLabel" class="pause-label" hidden>画面已暂停 · 不暂停角色进程</p>'
    '<main class="wrap">'
    # 01 · Hero ---------------------------------------------------------------
    '<section id="now" class="scene hero reveal" data-scene="now" aria-labelledby="nowTitle">'
    f'<div class="sky" aria-hidden="true"><div class="sky-stars"></div><div class="sky-sun"></div>'
    f'<div class="sky-haze"></div><div class="petals">{PETALS}</div></div>'
    '<div class="hero-grid"><div class="hero-copy">'
    '<p class="eyebrow"><span class="idx">01</span>THE PRESENT · <span id="heroDate">世界时间读取中</span></p>'
    '<h1 id="nowTitle" class="hero-name"><span>沈知栀</span><em>她的生活，从不因你离线而暂停。</em></h1>'
    '<p class="hero-kicker" id="heroKicker">活动与计划</p>'
    '<p class="hero-activity" id="heroActivity">正在读取她的生活…</p>'
    '<div class="hero-meta" id="heroMeta"></div>'
    '<div class="hero-context" id="heroContext"></div>'
    '</div><div class="hero-dial"><div id="dayDial" class="day-dial"></div></div></div>'
    '<div class="odometer" id="heroStats"></div>'
    "</section>"
    # 02 · Inner voice ----------------------------------------------------------
    '<section id="inner" class="scene reveal" data-scene="inner">'
    + _scene_head(
        "02",
        "INNER VOICE",
        "嘴上说的，和心里想的",
        "同一个回合里，她发给你的话与她当时真实的内心。说多少、怎么说、要不要开口，都由她自己决定。",
        '<div id="innerLive" class="live-pills"></div>',
    )
    + '<div id="innerDramaStudio" class="inner-grid">'
    '<div class="phone card"><div class="phone-head"><span class="phone-avatar" aria-hidden="true">栀</span>'
    '<div><strong>沈知栀</strong><small id="phoneStatus">正在同步回合…</small></div>'
    '<span class="phone-hint">点一段对话，看她当时在想什么</span></div>'
    '<div class="phone-body" id="innerChat" role="list"></div></div>'
    '<article class="thought card" id="innerThought" aria-live="polite"></article>'
    "</div>"
    '<div class="inner-lower"><section class="card mini-panel"><h3>她怎么理解眼下 <small>处境评估</small></h3>'
    '<div id="innerAppraisals" class="appraisal-list"></div></section>'
    '<section class="card mini-panel"><h3>写给自己的手记 <small>私人印象</small></h3>'
    '<div id="innerImpressions" class="impression-list"></div></section></div>'
    "</section>"
    # 03 · Fate route -------------------------------------------------------------
    '<section id="fate" class="scene fate reveal" data-scene="fate">'
    + _scene_head(
        "03",
        "FATE ROUTE",
        "命运线",
        "世界为她铺开的故事线与未来机缘。机缘只是可能——走不走进去，仍然是她的选择。",
        '<div class="seg" role="group" aria-label="缩放">'
        '<button type="button" class="seg-btn" data-route-zoom="fit" aria-pressed="true">全览</button>'
        '<button type="button" class="seg-btn" data-route-zoom="near" aria-pressed="false">细看</button></div>',
    )
    + '<div id="chapterRibbon" class="chapters"></div>'
    '<div class="route-wrap card"><div class="route-gutter" id="routeGutter"></div>'
    '<div class="route-viewport" id="routeViewport" tabindex="0" aria-label="故事分支图，可拖动横向浏览">'
    '<div id="routeMap" class="route-map"></div></div></div>'
    '<div class="galbox" id="routeBox" role="region" aria-label="故事阅读框">'
    '<div class="galbox-name" id="galName">命运线</div>'
    '<p class="galbox-text" id="galText">选择一个节点，读一读这条故事线。</p>'
    '<div class="galbox-meta" id="galMeta"></div>'
    '<div class="galbox-nav"><button type="button" id="galPrev" aria-label="上一个节点">◀</button>'
    '<span id="galCount"></span><button type="button" id="galNext" aria-label="下一个节点">▶</button></div>'
    '<span class="galbox-caret" aria-hidden="true">▼</span></div>'
    '<div id="routeLegend" class="route-legend"></div>'
    "</section>"
    # 04 · Week -------------------------------------------------------------------
    '<section id="week" class="scene reveal" data-scene="week">'
    + _scene_head(
        "04",
        "THE WEEK",
        "这一周的生活",
        "课表、她自己的计划、亲身经历与世界事件，按北京时间铺成七条生活带。点开任意一段读原文。",
        '<div id="weekNav" class="week-nav"></div>',
    )
    + '<div id="weekPhases" class="week-phases"></div>'
    '<div id="weekLegend" class="legend-chips" role="group" aria-label="按类型显示"></div>'
    '<div class="card week-card"><div id="weekGrid" class="week-grid"></div></div>'
    '<div id="weekFoot" class="week-foot"></div>'
    "</section>"
    # 05 + 06 · Bonds & Mind -------------------------------------------------------
    '<div class="duo">'
    '<section id="bonds" class="scene reveal" data-scene="bonds">'
    + _scene_head("05", "BONDS", "羁绊", "她身边的人，以及她和你之间已经记录下来的关系。")
    + '<div class="bonds-grid"><div class="card constellation" id="bondsMap"></div>'
    '<div class="card bond-you" id="bondsYou"></div></div>'
    "</section>"
    '<section id="mind" class="scene reveal" data-scene="mind">'
    + _scene_head("06", "MIND &amp; MEMORY", "心绪与记忆", "此刻的情绪余温、被保留下来的记忆，以及塑造她的人生坐标。")
    + '<div class="mind-grid"><div class="card mood" id="moodOrb"></div>'
    '<div class="card memory" id="memoryField"></div>'
    '<div class="card biography" id="biography"></div></div>'
    "</section></div>"
    # 07 · Seen ---------------------------------------------------------------------
    '<section id="seen" class="scene reveal" data-scene="seen">'
    + _scene_head("07", "WHAT SHE SAW", "她看见的世界", "她自己刷到的视频与热点、随手拍下的照片，和发给你的语音。")
    + '<div id="signalTicker" class="ticker" aria-label="外部热点"></div>'
    '<div class="seen-grid"><div id="perceptionFeed" class="feed"></div>'
    '<div class="seen-side"><div class="card"><h3 class="card-title">留住的瞬间</h3><div id="photoWall" class="photo-wall"></div></div>'
    '<div class="card"><h3 class="card-title">她的声音</h3><div id="voiceList" class="voice-list"></div></div></div></div>'
    "</section>"
    # 08 · Interaction station -----------------------------------------------------------
    '<section id="station" class="scene reveal" data-scene="station">'
    + _scene_head(
        "08",
        "ACROSS THE DISTANCE",
        "跨过距离，寄点什么",
        "给她点一份外卖、寄一个包裹，或签收她寄给你的东西。每一次交互都作为世界事件写进她的生活。",
        '<div id="interactionSummaryBadges" class="ix-badges"></div>',
    )
    + '<div id="interactionStationPanel" class="station-grid">'
    '<div id="interactionInboxList" class="ix-list"></div>'
    '<form id="interactionDispatchForm" class="card ix-form" autocomplete="off">'
    '<h3 class="card-title">发起一次交互</h3>'
    '<div id="interactionPresetBar" class="ix-presets"></div>'
    '<label>交互类型<select id="ixKindSelect"><option value="instant_delivery">即时外卖 / 饮品 / 跑腿</option>'
    '<option value="counterpart_parcel">跨城快递 / 礼物包裹</option></select></label>'
    '<label>名称<input id="ixTitleInput" type="text" placeholder="例如：把子肉盖饭 + 青菜豆腐热汤" required></label>'
    '<label>内容细节<input id="ixDetailInput" type="text" placeholder="例如：配卤蛋与热汤，送到宿舍楼下"></label>'
    '<label>给她的附言（可选）<input id="ixNoteInput" type="text" placeholder="例如：说了请你就请你，趁热吃"></label>'
    '<div class="ix-row"><label>送达地点<input id="ixPlaceInput" type="text" value="华东师大宿舍楼下"></label>'
    '<label>送达时间<select id="ixEtaSelect"><option value="0">立即送达</option><option value="2">2 分钟后</option>'
    '<option value="15">15 分钟后</option><option value="30">30 分钟后</option></select></label></div>'
    '<button id="ixSubmitBtn" type="submit" class="tool primary wide">写入世界账本</button>'
    '<p id="ixFeedbackMsg" class="ix-feedback" hidden></p></form>'
    "</div></section>"
    # 09 · Engine (operator) -------------------------------------------------------------
    '<section id="engine" class="scene reveal operator-only" data-scene="engine">'
    + _scene_head(
        "09",
        "THE ENGINE",
        "她是怎样运行的",
        "角色作判断，系统只提供事实并守住边界。下面是运行机制、账本证据与成本——机制示意，非逐条因果轨迹。",
    )
    + '<div class="engine-top"><div class="card mech"><div id="mechanismMap" class="mech-map"></div></div>'
    '<aside class="card mech-detail" id="mechanismDetail" aria-live="polite"></aside></div>'
    '<div class="engine-grid">'
    '<div class="card"><h3 class="card-title">运行信号</h3><div id="runtimeSignals" class="signals"></div></div>'
    '<div class="card"><h3 class="card-title">认知流程 <small>已记录的持久流程</small></h3><div id="processBars" class="process-bars"></div></div>'
    '<div class="card"><h3 class="card-title">账本 <small>可重放的世界记录</small></h3><div id="ledgerStats" class="ledger"></div></div>'
    "</div>"
    '<div id="costMiniGaugePanel" class="card cost"><div id="costPanel"><p class="muted">成本面板将在滚动到这里时读取。</p></div></div>'
    '<div class="card evidence"><h3 class="card-title">翻看记录 <small>授权摘要检索</small></h3>'
    '<div class="evidence-tools"><input id="evidenceSearch" type="search" placeholder="搜索标题、正文或字段…" aria-label="搜索记录">'
    '<select id="evidenceNode" aria-label="按机制环节筛选"><option value="all">全部环节</option></select>'
    '<select id="evidenceKind" aria-label="按类型筛选"><option value="all">全部类型</option></select></div>'
    '<p id="evidenceCount" class="muted"></p><div id="evidenceRecords" class="evidence-list"></div>'
    '<div class="evidence-pager"><button type="button" class="tool" id="previousEvidence">上一页</button>'
    '<span id="evidencePage"></span><button type="button" class="tool" id="nextEvidence">下一页</button></div>'
    '<div id="techDetails"></div></div>'
    "</section>"
    '<footer class="footer"><span>只展示经过授权的摘要，私密反思与未送达正文不在此呈现。</span>'
    '<span id="footerMeta"></span></footer>'
    "</main>"
    '<div id="presentHud" class="present-hud" hidden><span id="presentIndex"></span>'
    "<span>← → 切换 · Esc 退出</span></div>"
    '<dialog id="detailDrawer" class="detail" aria-labelledby="drawerTitle">'
    '<div class="detail-head"><div><p class="eyebrow" id="drawerEyebrow">LOOK A LITTLE CLOSER</p>'
    '<h2 id="drawerTitle">记录详情</h2></div>'
    '<button type="button" id="closeDrawer" class="tool ghost" aria-label="关闭详情">关闭 ✕</button></div>'
    '<div id="drawerBody" class="detail-body"></div></dialog>'
)
