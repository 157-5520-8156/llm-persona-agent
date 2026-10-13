# 历史检索输入与在用链路收敛（2026-10-14）

基线 `989e0bbd`，分支 `codex/role-memory-qualification`。
用户要求：先解决历史检索输入，再收敛聊天和生活链路的重复与耦合；不能机械拆文件、增加层，
不能丢掉现有功能和角色自主性。本文记录本批实施与证据，不另立架构计划。

## 1. 查询输入的根因与修复

`WorldOutlookRuntime._history_material` 在没有既有故事线时，把整个 Context 序列化再取前
1024 字符。既有冻结请求的这一段全是字段名、来源 ID、哈希和 Character Core 元数据，
中文字符数为 0，尚未进入生活正文。材料并非缺失，而是检索输入选错了层级。

区分三个假设：元数据占满查询、首条长材料挤掉其他材料、索引/权限导致召回遗漏。
先保持索引与权限不变，只修改查询；不把生成故事偏浅直接归因于单一查询问题。

考虑过三种实现：递归采集任意字符串仍会混入元数据/不该读的字段；增加查询改写模型会增加调用
及新的语义作者；本批选择从已有可信 Context 的明确正文槽位取原文，不解释语义或创造关键词。

- 已有作者故事线仍使用原 `situation + open_question`，保持顺序和原 1024 字符界限。
- 初始查询读取已提供的 world_life 环境正文、活动前提、角色已接受的意图，以及对话原文和 Fact
  source_excerpt。只读已知字段，不遍历任意 `text`、ID、权威元数据或 Character Core。
- 跳过 unavailable 和各级 withhold，去除完全相同的正文；按条目均分有限查询空间，避免首条独占。
- 查询只是查找线索；意图和说过的话不会因参与检索而成为发生过的事实。
- 索引、作者/时间/隐私筛选、anchor catalog、history offer、来源绑定和请求大小门不变。
  正式世界上下文未被删减或改写，最终故事关联仍由 World Author 决定。
- 无正文时使用既有中性检索词“生活”，不把空元数据当问题。

回归用例先红：8 failed；修复后新的8例通过。六个固定主题、同一词法索引中，旧查询没有命中，
新查询均把对应旧事排到第一；另覆盖长条目不能淹没其他条目、隐藏/元数据不进入查询、旧故事线
保持原文和空输入。这是局部可重复反例，不是通用语义召回质量评测。

一个历史连续性测试原先用自造的根字段 `current_source`，现改为实际 Context 的正文槽位；
未为这个测试增加生产兼容分支，原执行后才产生 story link/普通 Recall 的断言保留。

## 2. 聊天：把预取接续归还给现有 Recall 实现

两个正在使用的聊天作者载体都自行处理：接纳已经完成的预取、判断是否需要 join、再执行角色
主动 Recall。现在直接调用已存在的 `perform_character_recall_with_prefetch`，由它统一处理
这些生命周期细节，接受已经展示过的预取作为可选输入。没有新增 coordinator 或包装类。

调用方继续拥有角色是否请求 Recall、一次次级调用额度、原 accessibility seed 和剩余时间。
共享函数不决定角色要回忆什么，不增加模型调用；两个调用方仍分别保留自己的上下文呈现和
结果校验。删除 paired author 中一个恒不成立的 `prefetch_trace is not None` 分支——紧前面
该局部变量固定赋值为 None，没有其他赋值来源。

## 3. 生活：两套审核重试循环合为一套

将 `_source_closure_review` / `_novel_origin_review` 合为已有 Life Runtime 内的
`_review_world_source(kind=...)`，两个实际调用点直接调用它。保留两种真实用途，不创建
reviewer registry、策略类或新 Module；也不保留只转发的旧方法。

保留各自的 provider、model ID、model_call_scope、action ID、strict tool schema、parser、
纠正文字、请求 hash、失败/用量记录以及一次完整纠正的限制。删去旧方法未使用的 cited_events
和 manifest 参数，调用者不再为无用接口拼参数。非法结果、超时或取消仍是原来的技术终态；
不会被改成角色沉默、no_change 或固定回复。

一处 HTTP fixture 原先直接调用旧私有方法，现改为合并后的同一入口；工具名、strict schema、
完整纠正消息和只调用一次/两次的原断言保留。

## 4. 冻结对照

证据目录沿用任务开始时的日期：`output/private-audits/active-path-simplification-20261013/`。

- `query-red.log` / `query-green.log`：新查询用例先红；初次扩展回归发现上述旧 context fixture，
  修正为正式正文槽位后通过。旧失败原日志保留。
- `reviewer-equivalence.json`：从 `989e0bbd` 加载两套原始审核方法，对两种用途 × strict 开/关 ×
  七种结果作 **28 组差分**。首次合法、非法后合法、两次非法、首次超时、纠正超时、取消、程序错误
  的请求内容/参数、返回审计结构、异常类型/文字、请求次数均逐项一致。0 外部请求。
- `frozen-recall-result.json`：只读备份既有 v7 历史库，重放到原 Context 的精确坐标
  world=19475 / deliberation=20421 / sequence=39896，而非直接用更晚的索引头。
  1259 条前史保留；从该时点的 Memory 与 World-Life 编译 **1282 份固定文档**，仅用本地
  FeatureHash/词法/已有图导航搜索。前后唯一变量是 query，文档集 hash 固定。
- 真实冻结查询：旧 1024 字符元数据 → 新 463 字符正文。返回 hit 数 4→3，按原始
  World Author anchor 绑定筛选后 1→2。新命中含已显示的近期环境材料；没有独立相关性 gold，
  **不能把 hit 数或可引用数当召回质量分数，更不能据此宣布故事深度提高**。
- 原历史库 SHA-256 始终为 `4f0511736920b2eafea6ff66c6bedbb9c21057ad589d843d90ba61669b241ff8`。
  只操作副本，未重新调用模型、改原始事件或消耗旧专项预算。

## 5. 验证与范围

- 初次跨链路定向：402 passed / 3 failed；三项均是已移除私有方法的旧测试调用。修改调用位置后
  查询/连续性/严格审核20项通过；180项扩展通过；108项作者/世界闭环通过。存在重叠，不累加为总数。
- `reviewer-equivalence.log`：28组全相等；Ruff/diff check通过。
- 独立 Standards/Spec 审查及最终冻结全量回归：待收尾补充。

本批没有拆文件、增加生产 Module、注册层或额外作者，也没有关闭事实/权限/来源/回执检查。
大的 inbound wire、Life Runtime、装配/health 混杂问题没有全部解决；本次删除的是已证实重复的
两组实际流程，保留其他复杂性，避免一次重构同时改变多种角色行为。
完整故事质量、跨日生活与真实 QQ/生产 soak 仍未由本批验收。生产配置和进程未变。
