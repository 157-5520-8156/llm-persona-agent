# 小说导入与跨故事连续性审计

日期：2026-10-07。源码基线：`759b59b5dc76fd0ffb033588c357b4d2df08cd0b`，工作树 `/Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent`。

本次只读源码和既有试验产物，执行离线测试及内存中的结构对照；没有调用付费模型，没有导入生产，也没有修改实现。研究问题是：小说式材料能否拆分、保留结构、重组为长故事，并与后续故事及实时生活保持一致；不是片段数量是否足够。

## 结论

1. **已有可工作的离线小说抽取、审核、档案导入、角色自主保留、普通 Recall 路径。** run-10 的历史产物与最终克隆账本支持这条路径确实走通过。它还不是任意长篇小说一键进入角色的稳定系统：需要绑定 brief、角色、原始 World 边界、专用 run；run-10 是多轮修订后的短篇样本，链接生成失败后使用了已审核关系的恢复路径。
2. **中间材料比正式档案丰富，关键结构在导出时被压平。** `event_ref`、block 身份和逐块出处没有成为正式记录字段；typed story relation 转为双向无类型 `related_record_refs`；`known_from/known_until` 被映射到正式档案的 `occurred_from/occurred_until`，真正事件时间只能部分留在正文中。运行读取沿这个简化档案继续传播。
3. **可重放与语义稳定不是同一能力。** 同一捕获响应续跑、相同审核档案重复导入有机制和测试证据；同一小说在冻结模型、提示、既有档案和评估下重新抽取，是否仍产生等价事件、视角与关系，没有充分验证。
4. **已有的“重组”是单个锚点附近、有来源约束的有限图遍历。** 最终真实 provider 输入确实有一条五节点照片故事链，但其中一个节点只是 480 字符窗口。没有证明整部长故事、多条交织故事、跨阶段转折与长期影响的完整重建，更没有严格有/无故事因果对照。
5. **有跨档案的同 ID 防改写边界，没有发现自动增量 canonical merge。** 正式账本中的历史实体和记录具有全局精确身份检查；语义冲突与重复靠 brief 中显式提交的 `accepted_archives` 和独立审阅。不存在已证实的全局事件身份归并、异名同人、同事件不同 ID 去重、版本化矛盾消解和持续故事合并流程。

## 证据范围

历史产物根目录：`/Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/`。本文使用以下简称：

- **R**：`luna-auto-ingest-20260930-run-10/`。
- **F**：`luna-final-recall-20260930-r3/`。

`R` 是目录名，档案实际 ID 仍为 `prehistory-archive:celia-lifeline-auto-20260930-run-07`。不能由路径名推断它是一次独立初始抽取，或把 run-07 与 run-10 当成两个正式故事。`R/provider-run/manifest.json` 明示 `mode=faithful_source_revision`、`attempts=2`。

本次直接解析并核对了 [R 的捕获 manifest][E1]、[原始与导出材料][E2]、[最终审阅请求][E3]、[最终审核包][E4]、[F 的真实模型输入][E5]、[第二轮模型输入][E6]，以及 [F 的 SQLite 账本][E7]。后者仅以 SQLite `mode=ro&immutable=1` 打开；检查的是已经落盘的静态历史克隆。F 属于 2026-09-30 的历史试验，不是对当前 HEAD 重新进行的真实模型验收。

## 1. 裸小说自动导入做到哪里

`prepare_life_materials_prehistory.py:39–54,77–116` 已有 `--narrative --ingest-narrative --brief --actor-ref --archive-id --run-dir`。抽取与自由创作分开，原文作为数据；CLI 继续执行独立 story linking，导出未审档案，并可另做 semantic review。相同模型身份的 review 可以保存结果，但不会经此 CLI 自动打成可导入 package（`:320–324`）。这仍是离线编排，不会在此脚本里执行角色保留或写运行档案。[S1]

入口将原文按非空行做稳定 source segments，保留全文 hash 和字符范围；**这只是出处分段，不是成熟的长篇章节编排器**。模型输出带逐字 `source_quote` 与 locator；结构校验检查引用确实是对应源段的子串、原始 World/角色绑定、视角授权和最多 8 blocks/回忆等边界。注释明确：这些检查不宣称语义蕴含。[S2:302–384,556–614][S2]

现有限制包括：原文最多 120,000 UTF-8 字节、512 segments；普通抽取 packet 先检查 140,000 字节后仍进入 `_bounded_packet` 的默认 96,000 字节限制；修订 packet 320,000 字节；语义审阅最终包 256,000 字节；story-link packet 120,000 字节、最多 24 条边；模型输出上限 32,768 tokens。超限会要求拆分，没有在所审路径中看到自动跨分片收集与合并协议。因此不能把函数接受 `narrative_text` 等同于长篇规模支持。[S2:296–299,381–384,477][S2][S3:20–27,313][S3][S4:24,187–189][S4][S5:30][S5]

run-10 的真实规模与状态：

| 对象 | 本次从产物直接复核的值 |
| --- | --- |
| 最终审阅请求中的原文 | 4,582 字符，13,112 UTF-8 字节；短篇材料规模 |
| 原文 SHA-256 | `21fc89ed4c6199a2919e43496aef8a871afaf470dcb1376d7d10d0decf86c528` |
| 修订后 materials | 1 actor、4 historical entities、69 blocks、17 recollections；69 个不同 `event_ref` |
| 新 story-linking 调用 | 两次，最终 `validation_rejected`；错误为一端缺少证据 |
| 恢复路径 | `approved_review_carry_forward`；保留先前关系索引 `[9,10,11,12]` 共 4 条边，12 条先前被拒关系不携带 |
| 最终 bound review | 17/17 records、4/4 links approve，`cross_record_findings=[]`，reviewer_ref 为 `model:gpt-6-luna:independent-prehistory-semantic-review.1` |
| F 静态账本 | seq 24720 接受该档案；17 次 `CharacterPrehistoryRecordImported`；17 次 `PrehistoryMemoryDecisionRecorded`，本样本都为 retain |

carry-forward 不是本地补造语义关系：它只复用先前被独立 approve、且端点与所有引用 block 字节不变的边，并且要求同一原文来源；最终审阅仍重新逐边判断。[S4:269–309,344–379][S4] 但是它也意味着这个成功样本不能被描述为“一次裸小说输入，全链稳定自动完成”。本次核对的 review 目录保存了请求、响应、bound review 与 package；reviewer_ref 本身不是供应商调用真实性证明，绑定函数也明确把请求/响应配对和 reviewer metadata 视为调用方提供的信任输入。[S3:316–325][S3]

离线内存重建结果：从 `R/provider-run/linked-materials.json` 使用当前代码重新 `export_actor_archive`，与 `R/candidate-unreviewed.json` 及最终 package.document 完全一致；canonical digest 为 `fdb142cadf04e99c7c3aef7c410e6a12457cc4263ed7da9c52cb7fe9421de9bd`。这证明产物链的当前可重建性，不证明原文覆盖完整或抽取判断全部正确。

## 2. typed 关系、event identity、known 时间在哪里丢失

| 层 | 确实保留 | 压平或未进入下一层 |
| --- | --- | --- |
| `LifeMaterialBlock` | block_id、event_ref、kind、subject_actor_ref、allowed_actor_refs、source_quote/locator、可选事件时段 | event_ref 只是非空字符串；未见 canonical event registry 或同事件归并约束 |
| `LifeRecollection` | actor、known_from/until、block_ids、participants、privacy、related refs | 关系仍是 ID 列表，typed 边另存 story-link artifact |
| `PrehistoryStoryRelation` | 四种关系类型、left/right、两端证据、bridge quote、rationale | `apply_story_links` 将每条边双向加入 related refs；类型、方向、证据不随记录直接下传 |
| `PrehistoryRecord` | statement、participants、privacy、无类型 related refs、单组 occurred 时间 | 无 event_ref、block_ids、known_*、typed relation、逐块出处字段；statement 拼接保留部分语义提示 |
| `PrehistoryMemoryReading` / Recall | 正式记录的时间、participants、related refs、档案与记录权威绑定 | 不重新加载 rich materials / typed link artifact，不能恢复被导出丢掉的类型与时间维度 |

对应源码：`LifeMaterialBlock` 与 `LifeRecollection` 在 [S2:33–66][S2]；关系 schema 与双向应用在 [S4:18–41,244–265][S4]；正式档案字段在 [S6:51–61][S6]；运行 reading 在 [S7:28–43,138–151][S7]。

时间尤其需要区分：`export_actor_archive` 将 `r.known_from` 写入 `record.occurred_from`，将 `r.known_until` 写入 `occurred_until`；如果 known_until 等于 World 启动，就减 1 微秒并改为 interval，同时在正文解释“启动只是截断边界”。block 的真实事件时间仅在非空时拼成正文标记。[S2:190–229][S2] 本次 run-10 重建中 17/17 条都符合这一路径，69 个 block 只有 7 个有非空结构化 occurrence。

这不是说故事的日期完全消失：文本与 hash 仍在，知道与发生的区别也有正文说明。**但确定性时间查询和邻接排序拿到的是一组被命名为 occurred 的字段，无法同时按真实事件时段与 actor 获知时段查询。** Recall 时段过滤用这些字段；邻接顺序也用这些字段的中点距离。[S8:1069–1073,1152–1161,1226–1230][S8] 对延期披露、回忆过往、他人后来告知等情形，不能声称运行层拥有完整双时间模型。

身份也要分开：历史人物 `history:person:*` 和 record ID 保留到了档案；block 的 `event_ref` 没有。正式 ledger 里的 `CharacterPrehistoryRecordImported` event ID 是档案导入的不可变事件身份，不能代替小说内“同一个发生事件”的身份。[S6:191–215][S6][S9:68–80][S9]

## 3. resume、idempotence、重新转换稳定性

| 问题 | 当前保证和证据 | 不能推出 |
| --- | --- | --- |
| 同一 run 续跑 | manifest 绑定原文 hash、brief、model、mode、actor、archive；completed 读取落盘 materials；捕获了响应可重新解析；请求已发送但无响应时阻止自动重发；截断不自动重试。[S5:287–325,376–441][S5] | 全新模型请求会生成等价分段、ID 和含义 |
| 同一 story-link run 重放 | request hash 与材料绑定；completed 重新应用捕获 draft。测试重复调用 completion 次数仍为 1。[S5:548–605][S5][T1:179–209][T1] | 独立重新 link 会得到同一语义图 |
| 同一 reviewed archive 重复导入 | 同 archive/record identity 必须相同；事务追加缺失记录，重复无新事件；重启后的幂等与 cold rebuild 有测试。[S9:54–87][S9][T2:50–72][T2] | 同一事件改用新 ID、不同说法或新 archive 后仍被自动归并 |
| 同一输入重新转换 | 没有找到冻结全部条件并评价语义等价的重复样本组 | 不能用目录同名、source hash 相同、一次成功或两次 hash 相同证明稳定 |

定向检查 run-01…run-10 的 manifests：原文 hash、brief hash 相同；成功的初次抽取 run-02/run-05/run-07 分别产出 16/13/18 条 recollections；run-08/09/10 是 revision，分别 18/18/17。run-08 与 run-09 materials hash 相同，其余不同。这些过程中提示、校验、修订材料与审阅反馈变化，**不是可归因于纯模型随机性的重复实验，也不能当作稳定性验收**。它们只能说明当前成功包经过演进和反馈，不是一个被冻结算法的稳定性样本。

现有修订保护有价值：已批准记录和所引用 block、非目标 actor 等必须保持字节不变；被拒记录必须修改或移除。[S2:482–553][S2] 这是一个候选内的受约束修订协议，不能扩大解释为跨原文、跨故事的 canonical merge。

## 4. 真实故事重组证据及边界

当前 Recall 从 actor 的 active memory 候选构建 corpus；每个前史文档必须携带 `RecordImported` 与 `ArchiveAccepted` 两项源证明。正文一般以 480 字符窗口、416 stride 进入索引；可选 parent reading 仅对全文不超过 1,024 字符的记录建立完整 context-only 文档，不参与向量/词项排名。[S10:389–452][S10][S8:973–1013][S8]

`_add_linked_prehistory_context` 使用一个选中的故事锚点，把 `related_record_refs` 作为无向关联做有界遍历。目标必须已在同 actor 活跃索引中并通过时间、privacy、status 筛选；遗忘和不可读记录不会因为有边而复活。硬上限为 6 hits，故事 reading/receipt 字节预算分别 11,000/22,000；无独立命中的目标默认取第一窗口。它不会生成故事摘要、恢复 typed causal edge 或保证整个连通分量被读取。[S8:39–45,1015–1190][S8]

F 的首轮真实 provider 输入（解析 `model_content_json` → user JSON → `inner_life_snapshot.materials.value.automatic_prefetch.items`）如下：

| source_ref 后缀 | model-facing 正文字数 |
| --- | ---: |
| `celia-workshop-photos:window:0` | 480 |
| `celia-photos-argument` | 321 |
| `celia-two-months-silence` | 253 |
| `celia-jiahe-moving` | 324 |
| `celia-jiahe-used-photos` | 171 |

这直接证明五个相连记录的材料进入了真实模型输入；第一条仍是窗口。输入没有 typed `relation_kind` 或 `known_from` 字段，与导出/读取链路一致。[E5] 首轮 result 记载一次 captured model request、`action_authorized` 和三条本地可见文本；第二轮为一次请求和四条文本。[E8][E9] 这些是动作与回复证据，不能替代理解评估或严格因果证明。

第二轮自动 prefetch 的前史只有 `celia-jiahe-mother-argument`，此外是解读、对话和实时经历条目。其 wire 的 `text="@s:0"` 是字符串表引用，展开 `materials.strings["@s:0"]` 后为 **378 字符**，不能把 4 字符压缩标记当正文长度。本结论仅指第二轮 prefetch 未再次取回五节点链；不推断上轮对话、其他材料或角色推理完全没有带入相关故事。[E6]

尚缺的证据至少有：

- 数十或更多事件、多个相互交织故事下，问题所需的完整前因后果是否都能重取；以及 6-hit/字节预算下怎样分步读取未完部分。
- 延期披露、误会纠正、事件身份重复、角色间不同知识版本是否稳定保留和被正确使用。
- 保留/遗忘后仍能表达“我只记得这一部分”，而不把缺失连接自行补成事实的评价。
- 新旧故事冲突的全局查找与增量合并，而非一次 review packet 内的局部检查。
- 冻结输入、同一上下文、反复样本、有/无或不同故事条件下的决策差异；长期真实经历之后，历史怎样被重解读并影响后续行动。现有对话与来源进入证据不能承担这些结论。

## 5. 多故事一致性与 registry/merge

已有的两层机制应保留准确表述：

**结构边界。** `PrehistoryAuthoringBrief.accepted_archives` 最多 32 个，并要求同 World/actor。`validate_draft` 禁止覆盖已有 archive/record ID、禁止同 entity_ref 改 kind/label，并检查 related refs 存在。实际 importer/reducer 还会从整个已接受档案集合检查相同历史实体 ID 不被改写，且 record ID 不被别的档案复用。因此不能说“完全没有全局身份约束”。[S3:61–114][S3][S9:54–67][S9][S11:35–40][S11]

**语义审阅。** review prompt 要求对照 profile 和 accepted_archives 检查年龄、学年、地点、身份、时序、重复和矛盾；逐记录、逐关系判断；`cross_record_findings` 有阻塞问题就不能 approved。schema 与绑定函数检查 verdict 完整和相互一致，但不会自己判断这些语义事实。[S3:42–57,200–225,345–357][S3]

然而，accepted_archives 是调用方显式提供的有限快照，不是 reviewer 自动从全局 story registry 查询的结果。run-10 最终 review 请求里只有 **1 个旧档案/12 records**。这足以说明该样本审阅范围，不能证明未来所有并存故事都会在每次增量审阅时完整出现。[E3]

本次最小离线对照：将 run-10 已接受的一条记录正文与时间保持不变，换一个合法新 record ID 与 archive ID，brief 的 accepted_archives 包含原档案。当前 `validate_draft` **通过**，没有调用模型，也没有真正导入。这个结果与该函数“Structural coverage only. No inference about narrative consistency.”的边界一致：同内容不同 ID 的重复必须由语义 review 阻止，结构层没有事件级 canonical 去重。[S3:92–114][S3]

审阅路径中未发现下列已实现协议：按同一事件合并多篇原文、实体别名归一、保留相互冲突来源并附可信/时间版本、增量关系更新至旧档案、已接受记录的 canonical revision。档案不可改写是事实边界；`carry_forward_approved_story_links` 还明确不能跨 original narrative sources，它不是跨故事 merge。[S4:281–309][S4]

实时生活也不能靠后补小说填空：importer 强制所有记录早于最初 WorldStarted，`prehistory cannot cover runtime life`；正式实时经历需要自己的接受行动/结果来源。[S9:36–43][S9] 本审计没有审完实时生活全栈，因此不对其完整连续性作否定结论；这里确认的是“小说前史图”尚未以富结构形式成为可持续增量更新的统一故事模型。

## 6. 旧审计哪些已经过时

[prehistory-story-recall-2026-09-29.md][D1] 是分期追加的历史记录，不能只读开头：

- 行 24 的“自动文件导入线尚未完成”已被该文行 35–45 和真实 run-10 产物超越。继续用这句话描述当前能力会漏报已完成工作。
- 行 9 的“最多再补两条”是早期实现；当前代码是最多 6 hits 的有界图遍历，不再只是两邻居扩展。
- 行 78、87 中“五段完整故事”须收窄为“一个五节点故事链的有界材料”；最终首轮输入第一条确为 480 字符窗口，不能称逐条全文完整。
- 行 87 提到的 v42 本地复核，本次能找到的 `R/isolated-prefetch-large-world-v42/resolver-link-diagnostic.json` 实际 `hits` 为四项，只有 `celia-jiahe-baking` 一条前史，不能据这个文件重证“五段返回”。它可能是修复前捕获或另一个阶段；本文不猜测是哪一种。最终五节点 provider 输入有独立直接证据 F，不受这个中间文件限制。
- 行 90–98 的最终双轮确有对应 F 输入和 result，但结果不能提升为整故事理解、因果影响、长篇容量或多故事一致性。原文里“不等于严格因果/生产/P95”等限制仍然有效。

## 7. 本次验证

从当前工作树执行，显式 `PYTHONPATH=src`；已用模块 `__file__` 确認加载 `/Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/prehistory_life_materials.py`：

```text
PYTHONPATH=src /Users/geoff/Projects/Girl-Agent/.venv/bin/python -m pytest -q \
  tests/world_v2/test_prehistory_life_materials_ingestion.py \
  tests/world_v2/test_prehistory_story_links.py \
  tests/world_v2/test_prehistory_story_recall.py \
  tests/world_v2/test_character_prehistory.py \
  tests/world_v2/test_prehistory_semantic_review.py
79 passed in 2.98s
```

另完成三个无 provider 的检查：run-10 material → archive 字节绑定重建；F 静态账本的 17 imported/17 retain event 计数；同文同时间换 ID 的结构校验对照。它们分别支持可重建性、已执行链路、结构去重边界，不支持语义准确率或长期故事效果。只新增本 Markdown。

## 源码与产物索引

[S1]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/scripts/prepare_life_materials_prehistory.py:39
[S2]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/prehistory_life_materials.py:33
[S3]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/prehistory_authoring.py:20
[S4]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/prehistory_story_links.py:18
[S5]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/prehistory_life_materials_runner.py:253
[S6]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/character_prehistory.py:51
[S7]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/prehistory_memory_source.py:28
[S8]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/recall_index.py:1015
[S9]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/character_prehistory_runtime.py:31
[S10]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/recall_corpus.py:389
[S11]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/src/companion_daemon/world_v2/character_prehistory_reducers.py:35
[T1]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/tests/world_v2/test_prehistory_story_links.py:179
[T2]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/tests/world_v2/test_character_prehistory.py:50
[D1]: /Users/geoff/.codex/worktrees/role-memory-qualification/Girl-Agent/docs/audits/prehistory-story-recall-2026-09-29.md:24
[E1]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-auto-ingest-20260930-run-10/provider-run/manifest.json
[E2]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-auto-ingest-20260930-run-10/provider-run/linked-materials.json
[E3]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-auto-ingest-20260930-run-10/provider-run/independent-review-final/submitted-request.json
[E4]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-auto-ingest-20260930-run-10/provider-run/independent-review-final/reviewed-package.json
[E5]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-final-recall-20260930-r3/model-inputs.jsonl
[E6]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-final-recall-20260930-r3/followup-model-inputs.jsonl
[E7]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-final-recall-20260930-r3/world.sqlite
[E8]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-final-recall-20260930-r3/result.json
[E9]: /Users/geoff/.codex/worktrees/publish-integrated-results/Girl-Agent/output/private-audits/luna-final-recall-20260930-r3/followup-result.json
