# 连贯前史进入普通 Recall

2026-09-29。实现于隔离工作树，基线`b641109f`。本次把已接受前史中的显式关联接入普通角色Recall检索，不更改旧档案、Production SQLite或发送消息。当日阶段状态仍需完成自动小说导入、真实独立审阅和实际角色模型因果对照；后续进展见本文日期附注。

## 问题与修改

`PrehistoryMemoryReading`已经读取权威档案里的`related_record_refs`，但Recall索引只把单条记忆分窗检索。角色命中一条后，关联的争执、后续披露等即使也是她已保留的记忆，也没有自动进入这一轮的回忆上下文。

Recall索引现在使用`world-v2-recall-index.hybrid.12`。保留普通语义/词项排名结果后，如果命中角色前史，则最多再补两条与命中记录显式相关的场景，整个结果仍不超过Recall trace六条上限和原字节预算。关系只表示阅读上下文关联，不声明因果、动机或情绪。关系命中的场景有自己独立的来源绑定，并以结构化关联信号进入结果。

扩大关联前，候选必须已经存在于当前角色的活跃Memory检索材料，并且通过同一`RecallQuery`的角色、subject、状态、隐私和发生时间过滤。目标记录的完整前文只由既有Prehistory Memory读侧在来源闭包验证后建立窗口；遗忘或缺席的候选不会因邻接记录重新出现。关系不扩展到别的actor，也不经过NPC私密解释。

这次没有建立第二个记忆库，也没有增加模型调用。前史Recall仍沿CharacterInterior现有同快照流程使用。

## 验证

- 新增`tests/world_v2/test_prehistory_story_recall.py`：建立两条经档案/来源闭包导入并活跃保留的链接场景；检索只击中第一条，检查角色pull仍得到第二条并由既有Recall增强函数组进`active_memory_candidates`；角色隔离、窄时间窗、只含当前可读候选都能限制扩展。
- 相关Recall/Memory窗口/权限回归共**114 passed**；最新直接协调器路径和Recall index子集**32 passed**。
- 完整离线冻结场景120项通过；见[`.106机制基线证据`](scenario-baseline-106-2026-09-29.json)。120项中只有`npc_world_impact.01`的派生replay_hash改变，输出hash、状态、调用数、动作结果、全部断言和冷回放均保持通过。
- `ruff`与`git diff --check`通过。

## 尚未证明

当前集成测试使用人工构造的两个隔离前史记录和显式保留候选。它证明运行Recall返回链路会读扩关联记录，不证明模型会如何解释或采纳这段故事。自动文件导入线尚未完成真实请求→候选产物→独立语义复审→CharacterInterior自主保留；完成后还需普通聊天及新选择的有/无故事对照。不能把这项检索联动本身报告为真人理解故事、记忆因果影响或整体编造率下降。

## 自动转换第一次真实试跑

V4.1 `deepseek-flash` 真实转换被隔离到`output/private-audits/luna-auto-ingest-20260929-run-01`和`run-02`。run-01尝试两次，两次均报告`finish_reason=length`并用完各自8,192输出token，产生截断JSON；两次token usage齐全，代码版估算合计CNY0.09371311，未核对供应商真实账单。发现旧状态没有在总manifest聚合两次费用，并会把已知长度截断再当格式失败重试。修改后输出上限为32,768，检测`finish_reason=length`就停止并标`output_truncated`，manifest写入usage和本地估算；同一captured响应不会再次计费。

run-02首次响应完整停止于13,377输出token，后续一次修正也完整返回，但仍有15条`LifeRecollection`的精度不匹配；修正请求在HTTP发送前有一个本地`TypeError`，usage账本已经完整记录第一次请求；修复错误清理和安全续跑后，以**原捕获响应且不增加模型调用**完成解析。时间元数据做了两种受审计的保守处理：不改任何时间端点，仅将不能包住完整区间的`day/month/year`标签降低为`interval`；类似“从某年以后”的单侧`occurred`日期不新增结束时间，清空有歧义的结构化区间而保留原引用与正文时间短语。`known_until==original_world_started_at`作为可知范围上界；编译入旧档案时只把范围端点映射到World启动前1微秒，并在正文说明启动时间是截断边界、不表示新事件。改动均留下normalization audit，无法作此结构化表达的部分仍有原文。run-02导出得到16条未审record，**不是已审核或已保留记忆**。

run-03曾遇到一次HTTP ReadTimeout（provider request文件已落盘、响应/usage未捕获），这笔billing维持unknown，不重发同一run。为来源长输出，预史runner专用请求读取超时改为180秒；通用聊天超时和重试策略未改变。run-03没有形成档案候选。截至run-03结束，当时尚无前史独立审核、导入或角色自主保留的结果；run-02的16条仅为待审捕获产物。


## 后续集成验证（2026-09-30）

本节更新上面的阶段性结论。run-10完成了一批新生前记忆的原文转换、独立审阅、隔离导入和角色自主保留；档案总数与角色实际保留数量不同，逐条回执才是保留范围的依据。相关依据见[内容审计](celia-life-materials-content-2026-09-29.md)、[关系审计](prehistory-npc-linkage-2026-09-29.md)及[独立审阅预览](celia-prebirth-memory-preview-2026-09-29.md)的状态注记。档案导入不自动授权角色记得或对用户讲述，后续表达仍走普通 Memory/Recall 路径。

实现修复了两个会直接截断这条链的问题：紧凑 inbound 的 `result_code` 传输别名现在严格归一化到内部分支名；前史引用在角色实际看到已保留摘录后，可把同一 pinned cursor 下的档案和记录双重来源证明交给最后的表达物化器。模型所见内容、角色能引用的来源和已验证的不可变证据现在共用同一回合边界。持续 Affect 的固定维度也进入紧凑 Gate 提示，以减少格式重选而保留角色选择是否留下情绪的权利。

真实 DeepSeek V4.1 测试都运行在 SQLite 副本上，发送器为 CaptureDelivery，没有真实 QQ 投递或生产库写入：

- v25 的事实回忆测试在预热 BGE-M3 缓存后，用一个作者请求回答“后来她没先告诉你就用了那张照片时，你回了她什么？”。角色回答了“那你早说啊”，并补了一段当时挑表情又没发出的细节；档案有对应原文。provider usage 为 CNY 0.0345，完整 ingress 6.36 秒。此测试证明这条已知细节能经普通角色输入说出，不证明总体编造率或冷启动表现。
- v27 在已有对话副本上询问嘉禾再次想拿活动照宣传时角色会如何回应。它生成了三条边界表达；provider usage 为 CNY 0.0354，模型调用约 4.84 秒，完整 ingress 5.52 秒。但首轮自动 Recall 没把“两个多月断联”和“后来使用照片”的场景送进 provider 输入，不能把该回答当作完整故事因果证明。
- v28 的自然追问“你刚说让她直接来找你，是以前遇到过类似的事吗？”没有透露那段旧事细节。普通自动 Recall 将五段相连前史送进 provider 输入；角色回应“拍了一下午，回去人家连声谢谢都没有，还嫌我说得不好听”，随后坚持让嘉禾自己来沟通。最终提案引用已呈现的前史事件/档案来源，接受后产生三条 CaptureDelivery 文本回执。该例是单样本联动，不是有/无前史的严格因果对照；v27 与 v28 的问题文本和缓存状态都不同，不能据此断言冷缓存是唯一原因。

v27、v28 都记录了 450ms 首轮等待超时；v28 的完整链在回合准备阶段稍后才进入模型输入。代码随后增加了从当前已选前史记忆出发的优先结构化链接，并由真实 ContextResolver + FeatureHash 的隔离测试证明本地回退可以带出五段完整故事；修改后的真实BGE冷缓存首问尚未重复。两轮各约 CNY 0.0354；只按每月3,000轮文本计算约 CNY 106.20，尚未计背景生活、其它审核或多媒体，未达到每月 CNY 100 的目标资格。完整 ingress 分别约5.52秒和5.44秒，未达到5秒目标。BGE-M3 只走本机回环服务。

## 同期来源权限与真实失败边界

v28 同回合还记录到一个被拒绝的提案候选引用了冻结 Capsule 未能绑定的两条前史来源；隔离副本中的另一个提案被接受并完成本地 CaptureDelivery。需要继续把被拒绝候选与通过候选的调用、Capsule 项和回执逐项关联，不能因为最终有文本就忽略来源通路的技术失败，也不能因此放宽来源校验。

远端 CI 基线为 `b641109f`：机制、Scenario 和平台边界验证通过；pytest 报告 9,994 passed、19 skipped、2 xfailed、17 failed。失败集中在 launchd 隔离环境缺 `zsh`、六个 watchdog 用例当时找不到已跟踪脚本，以及浅克隆缺少三个历史 reducer SHA；checkout 清理还报告缺 `.gitmodules`。workflow 已补充 `zsh` 安装和历史 SHA 获取，`.gitmodules` 已补齐，本机 15 个 installer/watchdog 用例通过。修复尚未推送并等待新的远端 CI 结果，因此不能说 CI 已修复完成。


## 角色已注意到的前史作为 Recall 锚点（2026-09-30）

在 v27 的离线重放中，自动 Recall 查询有1,024字符、16个 link refs，BGE-M3 与本地词项回退的头四项都没有命中照片争执链；query 尾部被近期对话和解读摘要占用。先前模型输入中，角色已看见工作室拍照记忆，但关联争执与后来使用照片的场景没有完整进入同一回合。

自动 Recall 现在从当前 actor-scoped、active、privacy-readable 的 MemoryRetrieval 候选中，筛出与**当前原话**有词项重叠的前史，再按现有 Memory 读分选择一个结构化 link ref。该 ID 已是每条前史 RecallDocument 的 source link；命中后再沿档案中显式的 `related_record_refs` 扩展至同一 actor 且仍活跃、可读、通过隐私/时间过滤的独立记录。若存在这个单一故事锚点，本次有界检索专注于它，避免把一个优先锚点稀释进其他可选 link refs。这个处理只选择当前回合可检索的候选，不替角色判断是否回想、如何理解、说什么或是否联系；它不添加模型调用，也不改变记忆的接受或遗忘状态。

回归测试覆盖：当前原话相关度最高的前史 ID优先于普通 link selectors；实际 LedgerProjectionContextResolver 排除他 actor、`withhold`和与当前原话无词项相关的材料；真实 RecallCoordinator 的本地预取能从不含故事关键词的问句经一个 cue-matched scene anchor 找回五段相连故事，且每段保留独立来源闭包。另有一个四条无关高分记忆挤占直接 top-k 的反例，验证显式来源锚点仍能保留故事闭包。受影响权限/冷回放/故事链测试通过；真实BGE克隆的最终代码复测仍未做同问题 A/B。

截至此处的 resolver 与 index 验证已由后续真实冷缓存角色试跑补充；该试跑仍是隔离副本单样本，不能代替生产冷启动、P95、真实QQ或严格有/无故事因果对照。


## 冻结机制基线更新（2026-09-30）

完整120-case固定模型场景在新链接锚点下全部通过，冷回放通过，所有120个`output_hash`和可见行为断言与对照基线保持一致；变化仅在120个派生`replay_hash`，因此将机制版本从`.106`提升为`.107`，不放宽场景门槛。新manifest为`c49e8516d9f8e548196fb2d71edf934df891886b14b5f5379b2427b4d5fd8727`，详见[.107逐场景比较](scenario-baseline-107-2026-09-30.json)。这个离线机制证明不等于真实模型/QQ/成本或限量演示版发布资格。


## 冷缓存首轮真实角色试跑（v35 → v40）

使用同一份 v23 隔离World副本、同一个用户问题和本机 BGE-M3，清空 Recall 向量缓存后先跑了协议修复前的 v35，再跑当前 v40。v35 首次模型输出半写了盼头与关系增量字段，系统用了3个provider请求后仍 deferred、没有可见文本；这复现了紧凑 `payload_json` 字符串schema没有约束内层可选字段的问题。compact提示现列出 `claim_text/scope/source_refs`、盼头四字段与关系三字段契约；唯一明确的 `text`→`claim_text` 别名只在规范字段缺席时做无损归一化。

v40 首轮返回 `action_authorized`，一个 DeepSeek V4.1 inbound请求、三个CaptureDelivery文本Beat；完整ingress为4.758秒，author约4.160秒，provider usage估算CNY 0.0347。BGE冷缓存由424向量清到0，再增56,424 tokens、约CNY 0.008125、两次本地embedding请求。模型实际输入的自动Recall包含五段关联前史：`jiahe-used-photos`、`two-months-silence`、`jiahe-moving`、`photos-argument`、`workshop-photos`。她决定不替嘉禾点头，要求对方直接沟通，并提到“上次那事我还没跟她算清”。该请求没有再出现450ms超时日志。与 FeatureHash resolver 测试合看，当前链条能在冷缓存时把被注意到的活跃前史扩为有来源闭包的故事材料，不必等角色先做一轮关于事实的提问。

输出措辞中的“那张活动照是我拍的”在输入前史中有对应拍照记录，但本次角色返回 `world_claims=[]`，且CaptureDelivery试验把后台单位设为0，没有执行非阻塞抽样语义来源复核。因此来源闭包测试证明故事材料进入Context，不证明这一句已经被独立来源审核；普通事实审查和真实送达仍是发布门槛。DeepSeek文本费0.0347元/轮简单乘3000约104.10元/月，未计后台生活和其它任务，月费目标仍未达资格。4.758秒只是单样本，不能声称P95已达标。


## 最终锚点排序与完整世界本地复核（2026-09-30）

代码复核发现，候选选择误用了 `max(9_900, _rank(...))`：这是“已被注意”的 Context 项所用的 rank floor，会把相关度不同的前史拉成平手。现在只考虑存在正文且对当前原话 `memory_relevance_bp > 0` 的 actor-owned、active、privacy-readable 前史，并按现有 Memory 读分、词项相关度和稳定 ID 排序取一个。无匹配时不传故事锚点；角色仍自主决定是否用 Recall 内容。

在 run-10 的完整隔离 World 克隆 v42 上，用当前代码执行真实 `LedgerProjectionContextResolver` 与 RecallCoordinator，但本地使用 FeatureHash 回退、没有调用 LLM 或 QQ。实际触发消息产生一个前史 link，返回五个关联故事记录，每条均保留两项来源绑定；当前 ledger semantic hash 与 sequence 前后不变。Context resolve约581毫秒，结果确认之前克隆中“候选已选、故事链未进入 top-k”的故障已由显式锚点保留修复。另一个合成反例放入四条分数更高、主题无关的直接候选，仍能返回完整五段故事。该证据证明完整世界的本地候选/闭包路径，不证明模型一定注意、理解或复述故事；它也不是最终代码下的 BGE 冷缓存或真实 provider A/B。
