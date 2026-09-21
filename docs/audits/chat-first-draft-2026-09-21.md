# 普通聊天首稿交付复测与可体验版本（2026-09-21）

结论：修复了一个会整轮吞掉回复的审核技术故障后，同一批输入从「首稿交付 0/2、一轮无任何交付」
变为「两轮都交付」。连续 14 轮真实输入里 10 轮完成实际交付。仍然没有解决的是角色自己编造
生活经历（第 3、7、9 轮就是因此被审核正确拒绝，纠正稿也仍然不合法），以及单轮成本。
本次**不是**发布验收：没有真实 QQ 外发、没有生产库写入、没有部署。

## 改了什么

### 1. 审核返回不可用时不再吞掉整轮（`8cce5843`）

上一轮真实聊天第 2 条的失败码是 `source_review_exception`，细节只有
`visible_grounded_review.receipt.ValueError`，无法定位。用本次保留下来的原始响应复现后确认：
Flash 审核在同一条 fact 对象里**重复输出了一个 JSON 成员**（`text`/`proposition`/`claim_scope`
等各出现两次）。`visible_grounded_review.inspect_response` 里的重复成员检查抛出裸 `ValueError`，
一路变成 `source_review_exception`：**该轮既没有交付，也没有进入角色纠正**，用户侧完全无回应。

`GroundedReviewWireFailure` 现在把「审核没给出可用答案」和「审核给出了判断」分开：

- 前者在**已经打开的** validation 阶段内重问同一个审核一次，**不**重问角色；
- 后者（通过、明确拒绝、inconclusive）与 provider 超时一律**不重问**；
- 没有合法的完整回执，任何内容都不会被放行。

失败细节现在保留审核自己的原因文本。新增端到端用例
`test_unusable_reviewer_answer_is_reasked_once_without_losing_the_turn` 用真实缺陷形状
（重复成员）验证：角色不被重问、审核只重问一次、最终仍然交付、回执与重放一致。
`test_grounded_rejection_returns_original_expression_to_same_author_once` 增加断言
「明确拒绝只审一次」。

定向验证：审核/回执相关 288 项通过，Ruff 通过。

### 2. 可实际聊天的入口

`output/private-audits/grounded-chat-20260921-03/chat.py`：隔离 full host、真实 provider、
CaptureDelivery、自适应 stdin/stdout。默认是零 provider 预检。

```bash
cd /Users/geoff/Projects/Girl-Agent-release-repair
COMPANION_DISABLE_DEBUG_USAGE_LEDGER=1 PYTHONPATH=src \
  /Users/geoff/Projects/Girl-Agent/.venv/bin/python \
  output/private-audits/grounded-chat-20260921-03/chat.py --code-head 8cce5843        # 预检
# 去掉 --code-head 换成 --execute 即真实开聊
```

真实运行时：每个回合的观察写入 `conversation.jsonl`（她实际发出的气泡在里面），
下一句用户输入追加到 `turns.jsonl`，`{"stop": true}` 正常收尾。运行时冻结 HEAD，
物理调用与墙钟都有上限。

## 同一批输入的前后对照

两个输入与上一轮完全相同，同一身份、同一累计状态、同一 Flash 作者 + Flash 审核、隔离 full host。

| | 修改前 `072a25e7` | 修改后 `8cce5843` |
| --- | --- | --- |
| 输入 1「我先不折腾了……」 | 首稿被拒，一次纠正后交付 4 条，step 48.85s | 交付 4 条，step **17.67s**，该 step 无失败记录 |
| 输入 2「宿舍，坐在桌边……」 | **未交付**，`source_review_exception` | 交付 4 条，step **29.08s**（一次 `primary_invalid` 重选） |
| 首稿直接交付 | 0/2 | 1/2 |
| 实际交付 | 1/2 | 2/2 |
| 物理调用 / 已知估算 | 9 次 / 1.763804 元 | 11 次 / 2.345120 元 |

成本口径：两行的 `estimated_cny` 都取自同一张仓库价格表（`deepseek-2026-09-10-peak`）。
交接文档里的「0.36410712 元」不是同一口径，不能与本次并列比较。

**必须说明的限度：** 本次运行里审核没有再次吐出重复成员（`answer_retry` 出现 0 次），
所以「输入 2 这次交付了」与修复**一致但不是被它隔离证明的**。修复的隔离证据是上面那条
用真实缺陷形状端到端跑通、并且证明角色不被重问的用例。首稿严格通过只有 1/2：输入 2 的
首稿是 `primary_invalid`（作者自己的输出不合契约），纠正后才交付。

## 连续 14 轮真实交付记录

逻辑时间 2026-09-20 05:11–05:13Z，同一次运行，用户按她实际发出的气泡临场接话。

| 轮 | 用户输入 | 结果 | step 秒 | 她实际发出的 |
| --- | --- | --- | --- | --- |
| 1 | 我先不折腾了，就在这儿坐一会儿。你想聊点什么？ | 交付 | 17.7 | 行，那就坐着 / 我这边也没什么正经事，就顺路走了一段 / 路边叶子被扫成一堆一堆的，旁边停了辆装花盆的小车，空的 / 你要是懒得说话也行，我随便说点，你听着就好 |
| 2 | 宿舍，坐在桌边。画倒是画完了，就是手上那点蓝色还赖着不走。 | 交付 | 29.1 | 那就让它再赖一会儿 / 蓝色挺好看的，蹭手上就当留个记号 / 我这边也没正经事，就顺路走了一段，叶子被扫成一堆一堆的，旁边停了辆装花盆的小车 / 你不想说话也行，就这么坐着 |
| 3 | 你还有闲心数叶子。那车是空的，花盆搬哪儿去了？ | **未交付** | 20.6 | 审核拒绝：她把用户的话改写成自己的经历（「我数叶子」「你数花盆」），纠正稿仍不合法 |
| 4 | 睡着了？不回我就当你默认了啊 | 交付 | 16.9 | 默认什么了 / 我又没走，就是看了会儿路边 / 你坐你的，我坐我的 |
| 5 | 行，那咱俩谁也别理谁，看谁先憋不住 | 交付 | 13.1 | 行啊，那你先 / 别到时候又冒出来一句「默认了啊」 |
| 6 | 我憋着呢。就是那点蓝的擦不掉，越擦越像长在手上了 | **未交付** | 13.4 | 作者调用超时（`primary_timeout`），未形成可见交付 |
| 7 | 又没声了。你今天是打算一直晾着我？ | **未交付** | 22.2 | 审核拒绝 + 纠正不合法 |
| 8 | 算了不逗你了。我这边灯有点暗，画本摊着也懒得收 | 交付 | 17.7 | 嗯，不逗就不逗 / 灯暗就别盯着看了，眼睛累 / 画本摊着就摊着吧，又不急着收 |
| 9 | 嗯，那我眯一会儿。你要是有事就去忙你的，不用陪我 | **未交付** | 20.2 | 审核拒绝 + 纠正不合法 |
| 10 | 那我眯十分钟，醒了再跟你说 | 交付 | 13.2 | 好，睡你的 / 醒了想说再说，不急 |
| 11 | 醒了。你还在吗 | 交付 | 14.8 | 在呢 / 十分钟掐得还挺准 |
| 12 | 掐得准吧。没睡踏实，做梦都在擦那只手 | 交付 | 17.0 | 十分钟睡出个擦手的梦，你这觉睡得挺忙 / 现在呢，那点蓝还在不在 |
| 13 | 还在，淡了一点。你倒是记得清楚 | 交付 | 17.7 | 当然记着，你都念叨两回了 / 淡了就好，别真擦成手的一部分 |
| 14 | 行，那我不擦了。你今天话倒是挺多 | 交付 | 18.8 | 嫌我话多？/ 那你可以不接啊 / 反正你也没少说 |

14 轮里 2 轮 1 步就交付、0 轮撞到物理上限。交付轮 step 耗时 13.1–29.1 秒。
跨轮连续性是真实的：第 5 轮她主动回引了第 4 轮用户说的「默认了啊」，第 13 轮她回引了
「你都念叨两回了」。她的沉默是待处理状态（`deferred`），不是伪造的送达。

## 费用与继续点

- 66 次物理调用全部有对应原生用量行；已知估算 **13.772318 元**，另有 **2 条 unknown 预留**
  （容量预留，不是已确认收费）。平均每次调用约 0.209 元。
- 汇总：`1930 usage / 1927 reservations / 77 unknown`，ledger 高水位 2587。
- 运行与计费继续点（两者已合一）：
  `output/private-audits/source-model-cost-20260921-01/chat-first-draft-20260921-03/world.sqlite`。
- 核验：`output/private-audits/grounded-chat-20260921-03/reconciliation.json`
  （冻结 HEAD、干净工作树、前缀继承、66 次调用与 66 行用量一一对应）。
- 进程正常停止、客户端已关闭、无生产库改写、无真实 QQ 外发。

成本口径警示：本次全部落在 DeepSeek 高峰时段（`deepseek-2026-09-10-peak`）。按当前价格表
单轮约 0.98 元（14 轮 13.77 元），与约 100 元/月的产品目标差距约 3 倍以上；这是**未达标的
实测数字**，不是可以外推的月费结论。真实回复延迟也不是 README 里 2–3 秒的目标。

## 追加：精简作者出口已实现并实测（`64d277a5` + `afed85a9`）

按上一节列的「下一步只做一件事」实现完毕。做法是复用而不是新增协议模型：
atomic 作者工具换成 stream 路径上已经在跑的 compact carrier（`result_kind` + 一个
`payload_json` 字符串），payload 走现成的 `compile_slim_consider_payload` 编译成同一份
canonical `appraisal_draft` / `expression_draft`，下游审核、接受、Action、回执全部不变。
她的决定面一项没少：理解、感受、affect、时机、气泡、沉默、媒体、world_claims、recall
都还是她选；宿主只补 envelope 常量、beat modality、有界 rationale 副本和必为空的兄弟字段。

版本号用 `"slim"` 而不是 `"4"`：审核协议命名空间已经占用了 4，仓库里还有一条
「审核版本不得顺带开启作者 envelope」的守门测试，改号会让那条测试名字变成假的。

同一段钉住输入（run 03 的第一条，两轮真实调用都从同一身份状态出发）：

| 组成 | 严格双草稿 v3 | slim carrier |
| --- | --- | --- |
| provider tool schema | 26,974 字符 | **1,007 字符**（−96%） |
| system 提示词 | 8,334 字符 | 15,265 字符（+83%） |
| 她的输出 | 1,562 token | **275 token**（−82%） |
| 该次 prompt token | 34,578 | 45,050 |
| 单次作者调用估算 | 0.265854 元 | 0.304930 元 |

**结论必须分开读：输出侧的协议负担确实被解决了，成本没有。**

- 输出从 1,562 降到 275 token：不再写 26,974 字符 schema 要求的几十个必填 null 兄弟、
  内部 hex component/episode id、重复的 stance/rationale。这是这一阶段的目标，达成。
- schema + system 合计从 35,308 降到 16,272 字符（−54%）。
- **但单轮成本没有下降。** 该次 prompt 反而从 34,578 涨到 45,050 token：一部分是比较期间
  真实对话又长了 14 轮（user 材料 64,605 → 89,571 字符，属对照污染，不是 carrier 造成），
  另一部分是**我自己把 system 从 8,334 写到了 15,265 字符**——`slim_consider_instruction()`
  有 8,536 字符。也就是说：接口层省下的输入，被更长的说明和更大的语境吃掉了。

单轮成本仍约 1.16 元（3 轮 3.489328 元，16 次调用），与 v3 的约 0.98 元同一量级。
**100 元/月目标依旧不达标，而且瓶颈已经不在协议外壳，而在 system 说明与 user 材料。**

真实链路同时验证了两件事：

- carrier 在完整 host 里跑通：第 1 条输入交付 4 条气泡，5 次物理调用；3 轮里 1 轮交付，
  另 2 轮是已知的「审核拒绝 + 纠正不合法」和作者自己的 `primary_invalid`。
- **上一节的修复在生产链路里生效了。** 第 3 轮出现 `source_review_exception`，现在细节是
  `visible_grounded_review.receipt.GroundedReviewWireFailure:unknown or duplicate grounded source reading`
  ——不再是无法定位的裸 `ValueError`；账本里能看到同一请求字节在 4 秒后被重问了一次
  （26,616 prompt，第二次 cache_hit 26,368），即那条有界重问确实按设计触发，且只触发一次。

运行与计费继续点：`output/private-audits/source-model-cost-20260921-01/slim-chat-20260921-04/world.sqlite`，
`1946 usage / 1943 reservations / 77 unknown`，ledger 2827，16 次调用全部有原生用量行，
新增估算 3.489328 元，0 新增 unknown。核验：`grounded-chat-20260921-04/reconciliation.json`。
进程正常停止、客户端关闭、无真实 QQ 外发、无生产库写入。

### 因此下一阶段仍然是同一件事的下一半

不是再改协议，而是**把上下文本身变小**：先砍 system（`slim_consider_instruction` 8,536 字符
能否只保留她真正会选错的字段），再看 user 材料里哪些是每轮都送、她从不使用的部分。
在此之前不新增审核层、不改生活链。

## 追加：把上下文本身变小（`20ee1c11`）

上一节把工作指向了上下文。先量了它到底由什么组成。slim 运行的一次真实作者请求，
user 材料 89,571 字符里 `inner_life_snapshot` 占 77,205（86%），其中 `materials` 占
67,190（整体的 75%）。`materials` 内部最大的三块是 `affect` 19,501、`recent_dialogue`
9,084、`appraisals` 8,536；`affect` 里单个 `volatile_last_entry` 就有 16,091 字符，
内容是同一个 83 字符句柄在组件与 appraisal 表之间反复出现。

`pack_shared_strings` 就是为这件事存在的，四个审核模块已经在用。它只把「出现 ≥2 次且
≥72 字节」的字符串换成 `@s:N`，原始树仍是权威，并且每次调用都自证
`unpack(pack(v)) == v`。改动只在**面向 provider 的那份副本**上包一层，且只在确实有东西
可复用时才包：`role_result_correction`、`source_refs`、以及她必须照抄的
`appraisal_affect_hard_boundaries` 里的 id 都保持明文原地可读，历史请求字节不重写。

同一条继续点上的两轮真实对照（run 04 → run 05，run 05 从 run 04 结束态续跑）：

| | run 04 未打包 | run 05 打包后 |
| --- | --- | --- |
| `materials` | 67,190 字符 | **53,326 字符**（−20.6%） |
| user 材料 | 89,571 字符 | **75,356 字符**（−15.9%） |
| 该次 prompt token | 45,050 | **38,403**（−14.8%） |
| 她的输出 token | 275 | 173 |
| 单次作者调用估算 | 0.304930 元 | **0.275744 元**（−9.6%） |

两轮都真实交付。这一层是**隔离测量**的：system 与工具都没变，只有材料表示变了。

**但单轮总成本仍然没有下降**：run 05 两轮共 11 次调用、2.374112 元（约 1.19 元/轮），
与 run 04 的约 1.16 元/轮同量级。原因是单轮总成本由**重试次数**主导，而不是由单次请求
大小主导——这一轮第 1 条输入就用了 8 次调用。所以 100 元/月依旧不达标，而且现在的瓶颈
已经清楚地分成两个：请求本身还太大（system 15,265 + user 75,356 字符），以及一轮里会
发生多次重试。

运行与计费继续点：`output/private-audits/source-model-cost-20260921-01/packed-chat-20260921-05/world.sqlite`，
`1957 usage / 1954 reservations / 77 unknown`，ledger 2944，11 次调用全部有原生用量行，
新增估算 2.374112 元，0 新增 unknown；核验 `grounded-chat-20260921-05/reconciliation.json`。

### 下一步

不再动协议表示。按收益排序只剩两件，且都还没有做：

1. **减少一轮内的重试次数**（本轮 11 次调用只换来 2 条输入）。这直接决定每轮成本，
   也直接决定用户等待时间。
2. 继续缩 system（15,265 字符，其中 slim 说明 8,536）与 user 材料里她从不使用的部分。

## 追加：重试到底花在哪里（`cc583f3a`，run 06）

先看捕获到的原始回答，两次真实运行里第一稿反复犯同一个错：**把已经发生过的自己的经历
写成 `current_world`**（run 04 的「我下午在校园路上走…」、run 05 的同类），run 05 里还有把
自己的打算当成世界事实来声明。宿主会正确拒绝这种 scope，代价是每轮多一次作者调用。

`slim` 说明里因此补上了 scope 的判定：scope 按事情本身的时间选，不是按你现在正在说它——
已经发生过的经历属于 `shared_history` / `past_world`，`current_world` 只给此刻确实成立的世界
状态；自己的打算不是世界事实，不要为它写 claim，真想开始做就用 `life_intent`。
这不是新增行为脚本：边界本来就由 `expression_hard_boundaries` 强制，说明只是把它的
scope 名字讲清楚。同时补了一条测试，保证这份精简说明仍然点出编译器接受的每一个字段
（防止下一次精简悄悄删掉她的决定项）。

run 06 用一条真实输入「你今天都干嘛了」验证，并把前几轮被 `logging.disable` 吞掉的拒绝原因
记进 `warnings.log`。结果**分两半，两半都要看**：

**scope 那一半修好了。** 第一稿这次写的是 `past_world`（run 04/05 是 `current_world`）。

**重试没有减少。** 这一轮仍然是 5 次调用、1.076252 元：

| 顺序 | 调用 | prompt | completion |
| --- | --- | --- | --- |
| 1 | 作者（第一稿） | 39,759 | 246 |
| 2 | 审核（拒绝） | 26,642 | 704 |
| 3 | 作者（纠正稿） | 40,809 | 233 |
| 4 | 审核（通过） | 26,635 | 581 |
| 5 | 交互事实 | 2,869 | 6 |

**所以「一轮内重试」的真正结构是：作者 → 审核拒绝 → 角色纠正 → 审核通过。**
不是协议失败，也不是 schema 失败。而这一次审核拒绝的理由和 scope 无关：

- 「看路边有人把画摊在地上给人翻」——环境记录里没有这个场景，编的；
- 「看得挺久」——活动生命周期只证明结束和结束时间，不证明时长，编的；
- 「什么也没画」——completed 不证明「没做」，无来源的否定命题，编的。

也就是说：**每一次多余的作者调用，是被第一稿里编造的生活细节换来的。** 这正是第 7 节记录的
根因——库里没有真实执行结果，她就用合理的故事补——不是提示词或协议能解决的。

还有一个必须记下的反向发现：**纠正稿把 scope 从 `past_world` 退回了 `current_world`，
而审核这次放行了。** 同一批规则前一轮拒绝、后一轮通过，说明审核在 scope 上的判定不自洽。
这不影响「纠正稿发出去了」这个事实，但说明纠正路径不能当作质量保证。

运行与计费继续点：`output/private-audits/source-model-cost-20260921-01/diagnosed-chat-20260921-06/world.sqlite`，
`1962 usage / 1959 reservations / 77 unknown`，ledger 3023，5 次调用全部有原生用量行，
新增估算 1.076252 元，0 新增 unknown；核验 `grounded-chat-20260921-06/reconciliation.json`。

### 下一步（修正后的判断）

重试次数不是靠改提示词或协议能降下来的：它由第一稿里编造的生活细节决定。
所以下一步就是**生活结果链**——让「做过的事」在库里真的留下结果，她才有东西可引用，
而不是靠故事补。在此之前不继续调协议、不继续调提示词。

## 生活结果链：已定位到的机制与还差的那一步

按上一节的判断转去查生活结果链。**目前只有机制和证据，没有可交付的修复**，
所以这里不写「已修」，也不往链里塞任何补丁。

### 确实存在的部分（不是「消费者不存在」）

链路在代码里是完整接好的：

1. `life_development_runtime.pending_completed_activity_ref`（约 1861 行）在投影里找
   `status == "completed"` 且 `authority_origin is not None` 的计划，取
   `origin.accepted_event_ref`，要求 `read_completed_activity_consequence(...)` 返回非 None，
   再用 `_completed_activity_proposal_id` 去查这次完成是否已经提过案。
2. `life_ecology_runtime.py:484` 调它，`:532` 调 `advance_completed_activity_once`。
3. `life_development_runtime.py:2945` 在拿到完成事件时构造
   `completed_activity_consequence`、把它和 anchor 一起写进 capability manifest。
4. `world_consequence_authoring_context.py:67` 校验它，`derive_world_consequence_authority`
   把配对的 `ActivityStarted`/`ActivityResumed` 变成 `ActivityExecutionBinding`。
5. 世界作者据此可以写 `WorldConsequenceV2.authorized_attempt_result`。

### 账本里的实测（run 06 数据库，继承全部历史）

- `ActivityPlanned 3 / ActivityStarted 3 / ActivityResumed 0 / ActivityCompleted 1 /
  ActivityAbandoned 1 / WorldOccurrenceSettled 3 / ExperienceCommitted 2`。
- 唯一那条完成是配对的：`ActivityStarted seq=660` 与 `ActivityCompleted seq=865` 是**同一个**
  `plan:world-life-intent:f95168eac30d2607fda7be3`。所以第 4 步的「配对」条件本身是满足的。
- 3 条 `WorldOccurrenceSettled` 全部是 `occurrence:life-development:<hash>`（生活发展车道），
  不是完成活动产生的后果。
- 全库检索 `authorized_attempt_result`：29 处命中，**全部出现在提示词/契约文本里**
  （`world_v2_life_content.text` 的世界作者指令、事件里的 proposal 契约描述），
  **没有任何一处是真正被写出来的结果值**。所以第 5 步从来没有产出。

### 还差的那一步（三个假设已被直接执行证伪，问题被夹窄）

我没有停在猜测上，把三个候选断点都拿真实账本跑了一遍：

1. **「`authority_origin.accepted_event_ref` 指错了事件」——证伪。**
   从 `world_v2_heads` 重建投影后，唯一那条 completed 计划
   （`plan:world-life-intent:f95168eac30d2607fda7be3…`）的
   `authority_origin.accepted_event_ref` **正好等于** `ActivityCompleted` 的
   `event:activity-lifecycle-effect:db09418f…`。ref 语义是对的。
2. **「`read_completed_activity_consequence` 返回 None」——证伪。**
   用 `SQLiteWorldLedger` 打开同一个库直接调用它，它**成功返回了后果**，
   `execution_binding.source_event_ref = event:activity-lifecycle-effect:e3569d70…`。
   读取器和配对逻辑都是好的。
3. **「这条完成其实进过提案」——证伪。**
   `pending_completed_activity_ref` 会为这次完成生成
   `proposal:life-development:b2f461a70f2d66e02e05642b146da86189…`；
   全库检索这个 id：**0 条事件**。也就是说这条完成**从来没有变成过一次
   生活发展提案**。

于是断点被夹到很窄的一段：**`pending_completed_activity_ref` 返回 ref 之后、到一次提案被提交之前**
没有发生任何事。剩下两个可能，二选一，而且都不需要再猜：

- 该函数自己返回了 None——它的守门条件是
  `after_world_revision is not None and origin.accepted_world_revision <= after_world_revision`
  时 `continue`，以及「这个完成已经提过案」时 `return None`。（已确认提案不存在，
  所以不是后者。）
- 或者 `life_ecology_runtime.py:484` 拿到了 ref，但 `:532` 那条
  `advance_completed_activity_once` 在这次调度里没跑到。

下一次开工就从这两条里选一条读代码确认，然后才动代码。

### 第四个假设也被证伪——是项目自己的测试拦下的

我把上一节剩下的两条都读了代码。`life_ecology_runtime.py:480-535` 的门是：

```python
is_clock_wake = 当前 wake 是 ClockAdvanced
if callable(pending_completion) and is_clock_wake:
    completion_ref = pending_completion(
        after_world_revision=None if development_due else projection.world_revision,
    )
```

`pending_completed_activity_ref` 的守门是
`after_world_revision is not None and origin.accepted_world_revision <= after_world_revision → continue`。
实测这条完成计划的 `authority_origin.accepted_world_revision = 349`（就是那次 ActivityCompleted），
当前投影 `world_revision = 1390`；而 `ClockAdvanced` 的实测位置是 **seq 862 rev=347（完成之前）
和 seq 883 rev=355（完成之后）**。所以：不在 due 的 wake 上，`after = 355`，349 ≤ 355 → 被跳过。

我据此写了「把水位从当前 revision 改成上一次 wake 的 revision」的修复，并加了测试。**它被
项目自己的测试否决了**：`test_completed_activity_consequence_runtime.
test_completed_attempt_scheduler_settlement_and_character_response_survive_restart`
明确断言——在**不到期**的 wake 上，完成活动车道必须**不**启动
（`early_result.life_development_followup_status is None and untouched.calls == 0`），
只有 `_due_wake` 才允许。我的改动让不到期的 wake 也启动了，测试直接失败。

也就是说：**「不到期就跳过」是既定契约，不是 bug。** 我按规矩撤回了改动
（`git checkout` 还原 `life_ecology_runtime.py`，删掉新测试），确认
`test_completed_activity_consequence_runtime.py` 3 项通过、工作树干净。

这条完成理应在 **349 之后的第一个到期 wake** 上被捡起来（那时 `development_due` 为真、
`after=None`、`read_completed_activity_consequence` 已被证明可用、提案 id 也确认不存在）。
所以现在真正剩下的问题只剩一个，而且范围明确：

**349 之后到底有没有出现过一次到期的 wake，那次 wake 上这条车道有没有真的被跑到。**
要查的是 `life_ecology_schedule.next_consideration_at` 的节奏与 349 之后那 80 多次
`ClockAdvanced` 的关系，以及车道在那几次 wake 上是否被别的分支（aftermath / NPC /
activity）占住而没走到 `:531`。这是纯账本读取，不需要改代码。

### 真正的根因：链路是通的，是**世界作者从来不写**那个结果

继续往下查，把前面所有猜测都推翻了，包括我自己「提案从没发生」的说法。

**第一，完成活动每一次都被送到了世界作者面前。** 全库检索
`completed_activity_source_refs`：**20 次命中**，全部在 `ModelResultRecorded` 里，
rev 从 360 一直到 1295——也就是说 349 那次完成之后，**此后每一次生活发展运行都把这次
完成的执行绑定重新交给了世界作者**，revision 一路涨、ref 别名一路换（S37→S38→…→S62）。
所以我上一节说的「全库 0 条事件提到那个提案 id」是被我自己算错的 id 误导了：提案是按
`proposal:life-development:model-output:world_author:…` 记的，我查的是
`pending_completed_activity_ref` 用来判重的那一个 id。

**第二，这 20 次作者调用全部成功。** 逐条看审计：`outcome=winner`、
`failure_code=None`、`status=proposal_validated`，输出 153–1581 token。没有一次被拒。

**第三，`authorized_attempt_result` 从来没有被写出来过。** 全库只有 1 条事件提到它，
而且那一条是**评论文字**在描述它的缺席——「All three outcomes are environment_text only,
with no authorized_attempt_result」。不是被写出来的值。

而 `world_consequence_prompt.py:210` 对这份请求的指示是明确的：
「Each outcome must include authorized_attempt_result with the exact offered binding.」

**所以真正发生的是：世界作者被反复告知「每个 outcome 必须带上这个绑定」，20 次都给出了
通过的提案，却一次都没有写它，而校验放行了这些提案。** 这不是断线，也不是读取器坏了，
是**契约要求与实际校验强度不一致**，加上世界作者稳定地选择只写环境。

这正好解释了聊天的现象：账本里只有生命周期的开始与结束，没有「尝试的结果」，
所以她要说「我走了一段」时手上没有任何可引用的东西，只能补故事。

### 该修的地方（下一次开工的第一件事，仍未改代码）

三个候选，从最小到最大：

1. **把校验对齐到已经印出来的契约**：当请求是
   `capability_manifest.completed_activity_consequence` 这一种时，任何**非 `no_op`** 的
   outcome 都必须带 `authorized_attempt_result` 且绑定必须等于请求里给出的那一个；
   缺了就按现有的有界重选让作者重答，仍不行就是技术失败。`no_op` 继续允许。
   这不伪造任何东西：它只是拒绝不完整的回答，而且拒绝之后走的是既有路径。
   **这条是我认为该做的**，但它需要改校验、补测试，并且要一次真实运行才能证明
   世界作者会因此真的写出来——我的预算不允许我在没验证的情况下把它落进事实权威层。
2. 如果 1 之后作者仍然稳定给 `no_op`，那问题就从「校验太松」变成「这份请求对她没有约束力」，
   要动的是提示词强度或请求条件，不是校验。
3. 只有在 1 和 2 都试过之后，才轮到考虑让宿主从生命周期自身派生一个「尝试结果」——
   而那恰恰是交接文档禁止的（不能把 Completed 当执行成功、不能补写假经历）。

### 结论：没有代码 bug——是实验从来没跑到那个到期时刻（run 07 实测）

我原本又写错了一处归因，一并更正：那 20 条提到 `completed_activity_source_refs` 的事件，
`route.reason_code` 全是 **`ordinary_compute`**，也就是**角色**车道；那个键位于
`expression_hard_boundaries.companion_life_authority_availability` 里，是**给她**引用的完成活动来源，
不是世界作者的边界。全库检索 `completed_activity_consequence`：**0 条**。
15 次世界作者调用，没有一次带完成活动或活动中的边界。

于是我去读了排期本身：

```
world_v2_life_ecology_schedule_overlay:
  next_consideration_at = 2026-09-20T05:38:01Z
  last_completed_at     = 2026-09-20T05:08:01Z
  （那次完成发生在 2026-09-20T04:28:00Z）
```

而 run 03–06 的虚拟时钟都停在 **05:28–05:31** 之间。也就是说：**这条完成理应在 05:38
那个到期时刻被处理，但所有实验都在它到来之前就结束了。**

run 07 只做了一件事：`{"wait_until_minutes": 285}`，把虚拟时钟推过 05:38。结果：

| | run 06 结束 | run 07 推过 05:38 之后 |
| --- | --- | --- |
| `completed_activity_consequence` 出现次数 | 0 | **1**（seq 3070，rev 1413） |
| `last_completed_at` | 05:08:01 | **05:38:01**（正好是到期的那个时刻） |
| 下一次到期 | 05:38:01 | 05:53:01 |

**链路完全正常，没有任何东西需要修。** 生命周期记录在、读取器可用、配对正确、
排期到点就把边界交给了世界作者——它只是从来没等到自己那一班。第 7 节当初记的
「结果无法结算」这个前提，本身是不成立的。

run 07 在边界交付后 9 个事件就被我停了（6 次物理调用），所以**这一轮世界作者有没有真的写下
`authorized_attempt_result`，还没有观察到**。这正是下一步唯一要做的事：再跑一个到期周期、
让它走完并落账，然后看结果里有没有尝试结果、以及她的聊天引用是不是终于有了依据。
**这是观察，不是改代码。**

### 我的判断错在哪里（记录下来）

我连续提了四个「断点」假设，全部证伪，其中三次是拿真实账本直接执行排除的，
一次是被项目自己的契约测试拦下的。真正的答案不在代码里，而在我从来没有先问的那个问题：
**这条链路最后一次被给到机会是什么时候。** 一句排期查询就能回答，我却先写了四处代码。
这是我的方法错误：在确认「机制根本没被触发」之前就去查「机制哪里坏了」。

### 为什么停在这里

用户明确要求不把 Completed 自动当成执行成功，也不补写假经历。在没有确认「哪一步返回 None」
之前改这条链，等于把猜测写进事实权威层。所以这一段的产出是**可复查的定位**，不是补丁：

- 已确认：配对存在、消费者接线存在、`authorized_attempt_result` 从未被写出。
- 未确认：断点在 `pending_completed_activity_ref` 的 ref 语义，还是在
  `read_completed_plan`（三个 life-intent 读取器）那一层。
- 下一步第一件事就是这次比对（一条只读查询），然后才动代码。

## 还没解决、不要掩盖

1. **角色仍然编造自己的生活经过。** 第 3、7、9 轮的拒绝理由是审核正确指出的：她把用户
   的玩笑话升级成自己数叶子、把用户说成在数花盆、把环境写成自己到达过的地方。第 1、2 轮
   「顺着校园路走了一段」也是同一类问题被放行。这与第 7 节记录的根因一致：库里没有真实的
   执行结果（`occurrence_result` 只有 `environment_text`），也没有一条「已走过」的记录可以补给她。
2. **纠正仍然经常不合法。** 14 轮里 4 轮未交付，其中 3 轮是「审核拒绝 + 纠正不合法」，
   1 轮是作者超时。纠正后的稿子还是会被同一批规则拒掉。
3. **协议负担只是被测到，还没有被改掉。** 作者请求仍然是 34,578 输入 token，其中 provider
   tool schema 26,974 字符；她为此输出 3,847 字符协议（内部 hex id、必然为 null 的兄弟字段、
   重复的 stance/rationale），只为说 61 个字符的话——约 63:1。同一能力的 compact carrier
   实测只有 2,236 字符，且 slim→canonical 的编译器（`compile_slim_consider_payload`）和
   物化器已经存在并在 stream 路径上跑着。
4. **成本可观察但不可控。** 单轮约 0.98 元，后台调用（`interaction_fact_draft`、
   `fact_memory_retention`）已在账内，但预算门没有在这次运行里拦住任何东西。

## 下一步只做一件事

把普通聊天的作者出口换成精简结构：以现成的 compact carrier 作为 atomic 作者工具，
payload 走现成的 `compile_slim_consider_payload` 物化成同一份 canonical appraisal/expression，
下游审核、接受、Action、回执一律不变。目标是把 provider schema 从 26,974 字符降到约 2,200，
把她的协议输出从 3,847 字符降到几百，并据此再测首稿质量、首交付时间与单轮成本。
这不是新增一个「协议模型」，也不删她的任何决定项。

## 全链路体检：哪些在跑、哪些只是开了口（run 07 账本，3079 事件 / 66 类）

问「其他链路是否正常」，最可靠的答案不是读代码，是数账本。以下是同一个真实库里的实测。

### 健康：每一轮都在产出

| 链路 | 账本产出 |
| --- | --- |
| 对话主链 | `ActionAuthorized→Scheduled→Claimed→DispatchStarted→ProviderAccepted→Delivered` **各 63 次，一条不差**；`ExpressionBeatSettled`/`BudgetSettled` 各 63 |
| 事实 | `InteractionFactDecisionRecorded` 38、`FactCommittedV2` 10 |
| 评价 / 情绪 / 关系 | `AppraisalAccepted` 28、`AffectEpisodeUpdated` 21 + `Opened` 2、`RelationshipSignalAccepted` 12 + `SlowVariableAdjusted` 12 |
| 记忆 | `MemoryCandidateOpened/Accepted` 各 16、`FactMemoryDecisionRecorded` 10、`ExperienceMemoryDecisionRecorded` 2 |
| 私人印象 | `PrivateImpressionAccepted` 4 |
| 身世 | `CharacterPrehistoryRecordImported` 24 |

**对话主链 63/63 全部送达、零丢单**，这是全场最健康的一条。

### 慢，但确实会跑

活动/生活：`ActivityPlanned 3 / Started 3 / Completed 1 / Abandoned 2`、
`WorldOccurrenceSettled 3`、`LifeContentRecorded 5`，`life_ecology` 进程开了 16 次。
就是我刚查的那条——**能跑，节奏约 30 分钟一次到期**。

### 不正常：开了口、花了钱，账本里没有产出

| 链路 | 进程开了 | 花了 | 账本产出 |
| --- | --- | --- | --- |
| 主动联系 Proactive | `proactive_action_deliberation` 4 次 | `proactive_contact` 7 次调用 / 2.04 元 | **0 条事件** |
| 召回 Recall | — | — | **0 条事件** |
| 媒体 | `PhotoCandidateOpened` 2 | — | 只有 2 个候选 + `ImageEvidenceDeclared` 2，**没有任何生成或投递** |
| NPC 生态 | `npc_world_appraisal` 3 次 | `npc_actor_decision` 1 次 / 0.05 元 | 只有 `NpcRegistered` 2，**没有 NPC 计划或发生** |
| 外部世界感知 | — | — | **0 条事件**（完全没跑） |

这五条里有两条（Recall 命中 0、Proactive `situation_change` mint 0）在 CLAUDE.md 里早已记为
「进行中待验证」，账本确认**至今仍然是 0**；媒体和 NPC 是「有入口没产出」；
外部感知是「完全没跑」。

### 成本结构（这条最重要，总计 472.15 元）

| 用途 | 次数 | 金额 | 占比 |
| --- | --- | --- | --- |
| `source_review` | 1033 | 226.65 | 48.0% |
| `inbound_turn`（角色作者） | 279 | 109.21 | 23.1% |
| `contextual_source_review` | 153 | 40.69 | 8.6% |
| `inbound_source_review` | 105 | 25.55 | 5.4% |
| `interaction_fact_draft` | 113 | 7.48 | 1.6% |
| 其余 16 个用途 | — | ~62.6 | 13.3% |

**审核类（`source_review` + `contextual_source_review` + `inbound_source_review`）合计约
292.89 元，占总成本 62%。** 角色生成只占 23%。

这解释了为什么我前面在作者协议上做的两轮优化（schema 26,974→1,007 字符、输出 1,562→275 token）
没有让单轮成本下降：**我优化的是那 23%，而账单的大头在审核那 62%。**
下一轮成本工作的正确目标是审核调用次数与审核请求大小，不是作者。

## 主动联系与媒体为什么零产出（run 07 账本）

**主动联系：走的是旧的独立审核，而不是聊天在用的 v24。**

账本里一共只出现过两种审核协议：

```
visible-grounded-review.1      18   ← v24 单次语境审核（聊天在用，我刚修过的那条）
visible-independent-review.14   5   ← 旧的独立审核
```

rev 164 那批事件把主动车道的下场写得很清楚：

| seq | status | failure | route / detail |
| --- | --- | --- | --- |
| 314 | `main_exception` | `source_review_exception` | `visible_independent_review.source_read.RuntimeError` |
| 315 | `candidate_returned` | — | `author_candidate.proactive_visible_candidate.validation_unresolved` |
| 316/317 | `proposal_validated` | — | `validation.source_review` |
| 318 | `main_exception` | `source_review_exception` | `validation.source_review` |

也就是说：**主动提候选 → 旧审核在 `source_read` 阶段抛未捕获的 `RuntimeError` → 整个回合变成
技术失败**，另外还有两次「审核拒绝 + 纠正」，最后什么都没落账。这和我这轮在聊天里修的
`visible_grounded_review.receipt.ValueError` 是**同一类病**：审核运行时里一个未捕获异常
吃掉整条链路，只不过聊天那条已经修好、主动这条还在旧协议上。

`proactive_action_deliberation` 开过 4 次进程、`proactive_contact` 花了 7 次调用
（2.04 元），全部消耗在这上面。

### 下一步（按顺序）

1. 把主动车道的可见审核从 `visible-independent-review.14` 切到聊天已经在用的
   `visible-grounded-review.1`，或者给旧路径的 `source_read` 加上与聊天同款的
   「没给出可用答案就重问一次、角色不被重问」保护。**先确认 `source_read` 那句
   `RuntimeError` 到底从哪里抛出来**（`visible_independent_review_runtime.py:168` 只是
   设置 stage 的地方，真正的抛出点在它调用的 provider 或 preparation 里）。
2. 媒体：`PhotoCandidateOpened` 2、`ImageEvidenceDeclared` 2，没有任何生成或投递事件。
   还没开始查，闸门在 `media_selection_occasion.py` 的「是否问」判定和授权链上。

### 召回 vs RAG：不是替代关系，是两条并存的机制

- **RAG（宿主自动检索）在工作**：`world_v2_recall_documents` 有 **53 行**、索引头 1 行；
  真实作者请求材料里 `automatic_prefetch` 约 2,516 字符、`remembered_material` 约 1,859 字符
  ——**每一轮都在把检索结果喂给她**，所以「记忆进上下文」这件事是通的。
- **角色的自主 recall（她自己选择去查一次）在生产里 0 命中**：账本里 38 轮对话、
  279 次 `inbound_turn`，没有一次产生 recall 事件。
- 所以两者不是替代：RAG 负责「**宿主把可能相关的记忆放进她眼前**」，recall 负责
  「**她自己决定要不要再查一次**」。现在第一条在工作，第二条从未触发——
  这就是 CLAUDE.md 里「生产自主 recall 命中仍 0」的现状，账本确认至今没变。

保留 recall 是有意义的（它是「她主动想起来」这条产品目标的唯一入口），但**在 RAG 已经在
供料的前提下，它的价值需要重新评估**：如果她从来不需要额外检索就够用，那这条车道的成本
和复杂度就该降级；只有在她面对「我记得有这么件事但想不起来」的场景时它才不可替代。
这属于产品取舍，需要你定，我不擅自删。
