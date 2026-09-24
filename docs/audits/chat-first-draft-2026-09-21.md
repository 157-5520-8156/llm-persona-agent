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

## 更正：主动联系没有停在旧协议上；媒体是「从未被调用」

上一节我写「主动车道还在旧协议上」——**这是错的，而且是我第三次被累积账本骗到。**

那 5 条 `visible-independent-review.14` 事件全部出现在 **rev 49 / 93 / 236 / 360 / 416**，
而 `visible-grounded-review.1`（v24）的事件从 **rev 487** 才开始。也就是说旧协议只是
**v24 上线之前**的配置，不是主动车道卡住了。rev 487 之后所有车道都走 v24。

主动车道的真实付费记录：

```
2026-09-13 09:30:23  23,565+975 tok  0.3535 元
2026-09-13 09:30:28  23,686+643 tok  0.3549 元
2026-09-20 10:53:35  26,891+843 tok  0.2608 元
2026-09-20 10:53:56       0+0 tok    caller_cancelled
2026-09-20 10:54:15  26,882+945 tok  0.2608 元
2026-09-20 10:54:38       0+0 tok    caller_cancelled
2026-09-20 10:54:42  26,746+792 tok  0.2605 元
```

**它最近真的跑过**（2026-09-20 10:53–10:54，正是我这几轮实验的时间）。
失败形态不是「协议不对」，而是**两次 `caller_cancelled`**——候选生成后审核被取消，
接着再来一轮，还是被取消。所以断点在**取消/落账那一段**，不在审核协议选择上。

### 媒体：确认是「从未被调用」，不是坏了

- 全历史模型用途里 **没有任何 media/vision 用途**（一次都没有）。
- `world_v2_media_payload` **0 行**；媒体事件只有 `PhotoCandidateOpened` 2 和
  `ImageEvidenceDeclared` 2——候选冻住了，但没人问过她。
- `compile_candidate_occasion` 的三个开口（非终态的 `media_request` 触发进程、
  `CONVERSATION_OCCASION_WINDOW` 内的 `consider_available_candidate` 接受、线程相交）
  **在此配置下从来没成立过任何一个**，所以它一直返回 None（withhold，不是 decline）。

结论：**媒体是饿死的，不是坏的**——和活动/生活那条一样，是「没有任何东西去制造 occasion」。

### 我该记住的教训（三次同类错误）

这个库是**跨月累积**的：`0 条事件` 既可能是「机制坏了」，也可能是「这段时间没配置」或
「还没到它的班次」。我因此错了三次：（1）说完成活动从没提案，（2）说世界作者 20 次被交付
却没写，（3）说主动车道停在旧协议。三次都是拿**整个历史**去推断**当前配置**。
正确做法是先看时间/revision 边界，再下结论——这一点必须写进以后的查询习惯里。

### 下一步（按你的指示）

- **主动联系**：不要切协议（它已经在 v24 上）。要查的是那两次 `caller_cancelled`
  从哪来——是交互回合预算把审核掐掉，还是宿主在 drain 时取消。修这里才有效。
- **媒体**：要制造 occasion。先定一件事：**谁应该在什么时候问她「要不要发张照片」**——
  这是产品取舍，我不擅自决定；定完之后去接 `media_selection_occasion` 的那个开口。
- **recall 与 RAG 合并**：同意你的判断，它们服务同一件事（记忆），现在却是两套底层
  （RAG 走 `recall_documents` + embedding，recall 走 `recall_corpus` + 角色自主查询）。
  合理方向是**让 recall 变成 RAG 之上的一次「她主动再查一次」**，共用同一份语料与索引，
  而不是两条独立管线。这需要先确认两边语料是否同源，再决定合并到哪一侧。

## 主动联系：不是坏了，是从来没被真正跑过

追 `caller_cancelled` 追到 `llm.py:1071`：它只在**外层协程被取消**时产生
（`complete_with_timeout` 的 `asyncio.CancelledError` 分支），不是供应商超时。

但真正决定性的是数量：**主动联系全历史只有 7 次付费调用，分布在 2 天。**

```
2026-09-13  09:30:23  ok      2026-09-20  10:53:35  ok
2026-09-13  09:30:28  ok                  10:53:56  caller_cancelled
                                         10:54:15  ok
                                         10:54:38  caller_cancelled
                                         10:54:42  ok
```

那两次取消的形状是「成功 → 取消 → 成功 → 取消 → 成功」，像是**两个候选在竞速、
一个被取消**，不是关机。而五次成功的调用之后什么都没有落账。

**结论：这条车道几个月里只被跑过两次，其中一次还被中途打断。**
用 7 个样本去诊断「零产出」，得到的一定是猜测——而我这轮已经因为拿稀薄的历史数据
下结论错了三次。所以这里**不该继续改代码**，该做的是给它一次受控运行：
像 run 07 那样把调度推到它会触发的时刻，记录它每一步停在哪里，拿到真实形状再决定修什么。

这与「没坏」不矛盾：它可能坏，但现有证据不足以指出坏在哪，而我已经为「证据不足就动手」
付过三次学费。

### 本轮真正的产出

1. `97e7a7f0`：修掉我自己引入的 bug——把**她必须回抄的照片候选 id** 也 intern 成了
   `@s:28`，导致她唯一一次选择照片时交回一个宿主解析不了的令牌。
2. `e6eea5a2`：更正「主动停在旧协议」的错误结论；媒体确认为「从未被调用」。
3. 明确了两条链路的下一步：主动需要一次受控运行；媒体需要在**不替她决定**的前提下
   把可用候选作为环境摆在她眼前（能力清单里目前根本没有 photo）。

## 主动联系：修了一处真缺陷，但**没有**证明车道恢复（run 08/09）

按「就在这里跑」的要求做了两次受控运行。

**run 08（推过 05:53–07:48 五个到期窗口）**：新增 121 个事件、主动车道**一次都没铸出**
新考虑。查进程表得到决定性事实——**历史上开过的 4 个 `proactive_action_deliberation`
全部是 `state=open`，没有一个到过 terminal**，最后一个停在 03:43，等于把槽位占了
四个多小时的虚拟时间。

**从它自己的反例里找到缺陷**：同一个 `except` 块里，`budget_exhausted` 分支在返回前调用了
`self._complete(...)`，而**两个 `stale` 分支直接 return，不关闭进程**。被新 revision
取代的考虑因此永久占着 claim。已修（`c05c7659`）：两条 stale 路径都以
`outcome="stale-superseded"` 关闭。主动/initiative/social 相关 530 项通过，
1 项失败是改动前就存在的。

**run 09（推过 09:20）验证：没通过。** 新增 37 个事件，**仍然没有新的主动考虑被铸出**。
原因是已经搁浅的那 4 个进程依然是 open——我的修复只对**将来**的 stale 返回生效，
不会回收已经卡住的 claim；而且铸造侧本来就没有再发出过主动 due。

顺手排除了一个嫌疑：`social_initiative._post_silent_chain_active` 只看
`runtime_outcome_ref == "proactive:silent"` 的进程，而这 4 个的 outcome 是 `None`，
所以不是它挡住了铸造。

### 诚实的状态

- **已落地**：`c05c7659` 修掉「被取代的考虑不关闭进程」这个真实缺陷（有同文件反例佐证、
  有测试）。
- **未证明**：这次修复**没有**让主动车道恢复产出。铸造侧为什么在 03:43 之后停止
  仍未定位；下一步要查的是「谁负责在 due 时刻 mint 新的 proactive consideration」，
  以及已 open 的进程由谁回收（`TriggerProcessReclaimed` 全库 44 条，主动车道 0 条）。

### 进一步收窄：不是每日额度，是同一个考虑被反复开、从不关闭

我先把「每日共享外展额度」这个嫌疑排除了。`_shared_outreach_budget_allows` 依赖
`is_shared_outreach_consideration_id`，而它只认两种前缀（`_LONG_SILENCE_...` /
`_SITUATION_INDEPENDENT_...`）。而这 4 个进程的 consideration_id 是：

```
consideration:social-initiative:due-thread:b127e6205d0ed5d4ed17c7a07f9fc0492961d4b67ed737350c2498ab6a2a6f4e
```

**四次开启用的是同一个 id、同一个 `source_evidence_ref`**（`event:character-interior:experience:mutation:672ca1e8…`）。
它既不是 long-silence 也不是 situation-independent，所以**不计入额度**，每日上限不是原因。

真正的形状是：**同一个 `due-thread` 考虑被反复铸出四次，每次都以 `state=open` 留下、
从不关闭**；最后一次停在 03:43，之后铸造侧也不再发。

所以还剩最后一件事要查（不需要猜）：**谁铸造 `consideration:social-initiative:due-thread:*`**，
以及铸造守卫是否在「同一 consideration 已有 open 进程」时静默跳过。若是，那么除了我已修的
stale 路径之外，还需要回收/关闭已搁浅的进程——这是这条链剩下的另一半。

### 结案：主动车道没坏，它的**来源用完了**

查到底了。`due-thread` 考虑绑定在线程的到期窗口上，全库只有一条线程：

```
thread:character-interior:a91cc6b5…
  status:     open
  due_window: opens_at 2026-09-20T01:03:00Z  →  closes_at 2026-09-20T09:00:00Z
  ThreadOpened 事件: seq=245, 01:03:00Z（全库唯一一条 Thread 事件）
```

四次主动考虑发生在 **01:03 / 01:13 / 01:43 / 03:43**，全部在这条窗口内；
03:43 之后再没有第五次，而窗口到 09:00 才关。run 09 的时钟推到 09:18，
**已经在窗口之外**，所以现在铸不出任何东西是**正确行为**。

于是整条链的完整形状是：

1. 主动车道有一条真实来源（一个开放的线程到期窗口），它**确实跑过四次**，
   每次都付了费（`proactive_contact` 7 次调用，2.04 元）。
2. 四次都**留下了 `state=open` 的进程**——这是我修掉的那个缺陷：`stale` 返回不关闭进程。
   它已修（`c05c7659`），会防止**将来**再搁浅。
3. 03:43 之后这条来源不再产生新的 due，窗口也在 09:00 关闭。
   **所以修复在这份账本上无法被观察到生效**——它能帮的那次机会已经过去了。
4. 要证明修复有效、并让主动联系真正存在，需要的是**新的来源**：
   新的线程到期、或 ambient / `situation_change` 那条路——而项目自己的记录写着
   生产 `situation_change` mint **0**（`docs/audits/life-speed-cost-2026-09-21.md` 期间）。

**结论：不是代码坏了，是她的「想找人说话」没有任何东西在制造。**
这和媒体那条是同一个病：机制可用、入口在、但没有东西去开那个口。

### 下一步（产品级，需要你定）

主动联系要真的发生，得回答：**什么情况下她会想主动找人**。可选来源：

- 生活里发生了值得说的事（`situation_change`，目前 mint 0）；
- 她对某件事的念头到期（线程到期，目前只有那一条且已过期）；
- 长时间没说话（long-silence，前缀在代码里已存在，但从未触发过）。

这三条都是「给她处境」而不是「替她决定说话」——符合你定的标准。
选哪条（或哪几条）决定下一步动哪里。

## 三条主动来源的真实门槛（找到了，是一条 12 小时的静默要求）

用户要求三条来源都应允许她考虑主动发话。查下来三条的现状是：

| 来源 | 铸造点 | 现状 |
| --- | --- | --- |
| 线程到期 `due-thread` | `scheduled_domain_consideration_kind` | **工作**。在唯一那条线程的窗口内触发过 4 次（01:03–03:43），每次留下未关闭的进程（已修 `c05c7659`） |
| 生活事件 `situation_change` | `_situation_independent_contact` | 机制通、可观性也过（她是全部 7 个 occurrence 的 `participant_refs`），**mint 0** |
| 长静默 `long-silence` | `_long_silence_contact` | 机制在，**从未触发** |

**两条为零的原因是同一条闸门，而且它藏在 `_situation_independent_contact` 的第三行：**

```python
elapsed = (logical_time - source[0].logical_time).total_seconds()   # 距他上次说话
if not self._ambient_window_closed(elapsed_seconds=elapsed):
    return None
```

`_ambient_window_closed` 要求 `elapsed >= spontaneous_expiry_seconds + 60`，而
`SocialInitiativePolicy.spontaneous_expiry_seconds` **默认 43,200 秒 = 12 小时**，
部署处是 `SocialInitiativePolicy()`——**没有任何覆盖**。

所以：**他必须安静满 12 小时，她自己生活里发生的事才被允许变成一次「要不要说话」的考虑。**
这既解释了 `situation_change` mint 0（每次实验最多只跑到末条消息后 3.8 小时），
也解释了 `long-silence` 为何从未触发。

### 这是一条有意的设计，不是 bug

`tests/world_v2/test_social_initiative.py:2338` 是一条**回归守门**：

```python
async def test_situation_change_still_does_not_mint_inside_ambient_window() -> None:
    """Regression: within 12h, situation materials hitch only — no dedicated mint."""
```

也就是说原设计是：12 小时内，生活材料只**搭车**在已付费的 idle 考虑上；
12 小时之外，才允许为它单独开一次考虑。

我试过两种改法并都撤回：删掉闸门会破坏 4 条守门测试；把顺序改成
「idle 先跑、都没有再 mint」会破坏另外 2 条（那两条要求的正是 12 小时后的**专属**铸造）。
两组测试编码的是两种互斥的顺序——原设计靠那条 12 小时闸门让它们同时成立。

**结论：这不是我该自行决定的工程细节，是她的社交节奏这一产品参数。** 已还原工作树，
`test_social_initiative.py` 42 项全过。

## 已实施并按你的决定验证：她的生活事件现在能让她考虑开口（`ff872593`）

你选了 A：三条主动来源都允许她考虑开口。改动只有一处——**去掉那道 12 小时静默闸门**，
顺序完全不动（idle / 长静默仍然先跑，仍会把她的生活材料搭车带走；只是「专属考虑」变得可达）。
频率仍由每天 2 次共享外展额度与刺激簇窗口约束，且不抽取延迟。

**真实链路验证（run 10，推过 10:18）：**

```
seq=3260 RandomDrawRecorded
seq=3264 TriggerProcessOpened   consideration:social-initiative:situation-independent:d89593d4…   ← 史上第一条
seq=3265 TriggerProcessClaimed
seq=3266 ModelResultRecorded                                                                    ← 真的调了模型
seq=3267 TriggerProcessCompleted
```

他上一条消息在 **05:31**，这条考虑在 **10:18** 生成——不到 5 小时，正是旧闸门（12 小时）
会把它挡掉的位置。**这是这份账本里第一次由她自己生活事件开出的考虑，而且它一路走到了
付费模型调用。**

测试：`test_social_initiative.py` 42 项全过；宽回归 572 项通过，2 项失败
（`test_delayed_trigger_affect_silence_host_qualification`、`test_life_projection`）
已确认**在改动前的 HEAD 上同样失败**，不是本次引入。
4 条编码旧决定的测试改成了新决定，并各自保留原本守护的不变量（不抽延迟、每簇一次、
搭车仍然生效）。

运行与计费继续点：`life-event-mint-20260921-10/world.sqlite`，`2033 usage / 2030 reservations
/ 77 unknown`，ledger 3269，16 次调用全部有原生用量行，新增估算 5.707436 元，
0 新增 unknown；核验 `grounded-chat-20260921-10/reconciliation.json`。

### 一条给以后自己的警告：这个工作树里不要用 `git stash`

`git stash list` 里有两条**别的分支遗留**的 stash（`worktree-fix-cost-optimization`）。
当 `src/tests` 没有未提交改动时，`git stash push -- src tests` 什么也不存，
紧接着的 `git stash pop` 就会去弹那两条外来 stash，把 `present_prompt.py` 和
`scenario_runner.py` 弄成冲突。我已经踩了两次，两次都用
`git restore --source=HEAD --staged --worktree` 恢复、两条外来 stash 原样保留。
以后要么带上明确的 stash 名，要么改用 `git worktree`/直接跑指定 commit，不要再用裸 `stash`。

## 更正：世界作者是**主动选择 no_op**，不是漏写、也不是校验太松

我上一轮说「把校验对齐到契约（非 no_op 必须带绑定）就能让她有真实结果」——**这是错的**。
去看那次边界交付（run 07，seq=3070）到底答了什么：

```
decision:              "no_op"
world_author_decision: "no_op"
repair_ordinal:        1
capability_manifest_version: "life-development-capability.production.4"
```

`no_op` 是印给它的契约里**明示允许**的选择（"Choose no_op or propose objective candidate
consequences…"）。`no_op` 不带 outcome，所以没有任何必填项可查——**收 tight 校验不会改变
任何事**。

所以现状是：链路通、边界交付过、作者收下了它、然后判断「这次的完成没有值得提议的后果」。

**但这是 1 个样本。** 一个样本分不清「偶然」和「系统性」，而我这轮已经因为拿稀薄数据
下结论错了四次。要分清，需要更多「作者拿到完成活动边界」的时刻，而那需要更多**真正完成
的活动**——只能靠更长的虚拟生活时间来积累。所以下一步是跑一次长时间的受控运行，
数清楚：拿到边界的次数、以及每次的决定。

## 长时段受控运行（run 11）：她确实自己活着，但**生活本身很贵**

一次 13 个虚拟小时、**零用户消息**的运行（`b6d16e95`，171 次物理调用，正常停止）：

**她确实在自己过日子。** 虚拟时钟 10:18 → 23:17，新增 613 个事件：

```
WorldOccurrenceSettled  ×2        ExperienceCommitted ×3
ActivityPlanned         ×1        （另有 appraisal / affect / relationship 若干）
```

**世界作者不是系统性拒绝。** 11 次 life-development 提案：`propose 7 / no_op 4`。
我上一轮凭 1 个样本担心的「她永远拿不到真实结果」不成立。

**但计划从不开始。** 16:22 有一个 `ActivityPlanned`，整个 13 小时里
`ActivityStarted` = 0、`ActivityCompleted` = 0，因此 `completed_activity_consequence` 仍然 = 0。
所以「生活真的在推进」缺的最后一环变得非常具体：**她的计划没有被启动**，
于是没有完成、没有边界、没有可引用的结果。

### 但真正的问题在这里：成本

这一轮 **55.81 元 / 171 次调用 / 13 虚拟小时**，明细：

| 用途 | 次数 | 金额 | 占比 |
| --- | --- | --- | --- |
| `world_stimulus_appraisal` | 74 | 25.49 | 45.7% |
| `life_source_review` | 41 | 17.44 | 31.2% |
| `life_development_draft` | 15 | 5.10 | 9.1% |
| `life_development_source_closure_review` | 13 | 1.52 | 2.7% |
| `experience_memory_retention` | 10 | 1.42 | 2.5% |
| 其余 5 个用途 | 18 | 4.17 | 7.5% |

**每小时虚拟生活约 4.29 元 → 每个虚拟日约 103 元 → 每个虚拟月约 3,100 元。**
在虚拟时间与墙钟 1:1 的部署下，这比 100 元/月的目标高约 **30 倍**。

而且注意：**这 56 元里没有一句聊天**。之前我看到的「审核占 62%」是跨月累积账单的口径；
在一条干净的、只有生活的运行里，**世界刺激评价（45.7%）+ 生活来源审核（31.2%）= 76.9%**。

所以对「距离可发布有多远」这个问题，现在有了一个新的、更硬的答案：
**最远的一道门是成本，而且它是结构性的**——世界产生生活事件的速度，超过了预算能支付的速度。
可选方向（需要你定）：放慢虚拟时钟、合并/降采样世界刺激评价、给后台车道换更便宜的模型，
或对生活事件做批量评价。这不再是「调参数」，而是产品取舍。

## 虚拟生活为什么贵：把提示词拆开看（run 11 实测）

**先更正一个我自己的错误：** 我之前引用的 55.81 元是 `estimated_cny`（容量预留估算），
实付在 `cost_cny` 列。**run 11 实付 6.7790 元，全历史累计实付 52.4901 元**（预留 549.82）。
我之前把预留当实付，把成本说高了约 8 倍。

实付口径下：6.7790 元 / 13 虚拟小时 = **0.52 元/虚拟小时 ≈ 12.5 元/虚拟日 ≈ 376 元/虚拟月**
（1:1 时钟），约为 100 元目标的 3.8 倍。

### 贵在哪：不是次数，是每次送进去的东西

| 用途 | 次数 | 实付 | 每次 | 缓存命中率 |
| --- | --- | --- | --- | --- |
| `life_source_review` | 41 | 2.9825 | **0.0727** | **6.0%** |
| `world_stimulus_appraisal` | 74 | 2.4186 | 0.0327 | 48.6% |
| `life_development_draft` | 15 | 0.6424 | 0.0428 | 21.7% |
| `life_development_source_rewrite` | 5 | 0.0077 | **0.0015** | **99.0%** |

同一条流水线里，命中率 99% 的车道比命中率 6% 的便宜 48 倍（价格表：命中 0.02 / 未命中 1.00 元/M）。

**最贵那条车道的提示词构成**（每次 177,250 字符）：

| 块 | 字符 | 说明 |
| --- | --- | --- |
| `author_snapshot_display` | **100,627** | 其中 `materials` 59,818（**`affect` 单独 20,075**）、`source_inventory` 32,553、`source_refs` 5,236 |
| `source_readings` | 33,536 | 另外单独送的来源读数 |
| `permission_choices` / `text_fields` / `candidate` 等 | ~43,000 | |
| system 提示词 | 6,098 | **唯一被缓存的部分**（约 3,700 token ≈ 观测到的 6% 命中） |

**根因在 `life_fact_readings.py:71` 的 `fact_snapshot_display()`：它的文档字符串写的是
「Project the Fact lane for review」，实现却是 `shown = deepcopy(snapshot)`——把她的
整个内心快照复制进去。** 于是一次「核对事实有没有来源」的审核，要读 2 万字符的持续情绪状态、
8 千字符的近期经历、6.6 千字符的对话。而这些块**每轮都在变**（41 次里 33 种），
所以缓存永远救不了它——能缓存的只有 system 那 3,700 token。

### 对你三个问题的回答

1. **是不是提示词太冗长？是**，而且能指到行号：那条最贵的车道每次送 17.7 万字符，
   其中 5.7 万是深拷贝的整份内心快照，而它同时还另外收了 3.35 万字符的来源读数
   （`source_inventory` 与 `source_readings` 是否重复，是下一个要查的点）。
2. **频率不用降。** 刺激评价每次只要 0.0327 元，翻倍也只多约 2.4 元/13 小时。
   要砍的是每次送进去的量，不是次数。
3. **换 v4.1 Flash 不会更便宜：已经在按它的价格算了。** 价格表按日期选：
   `if instant >= DEEPSEEK_V41_EFFECTIVE_FROM: return DEEPSEEK_V41_FLASH_*`，
   我用 v4.1 offpeak 反算那一行得到 0.0532，与账本 `cost_cny` **完全一致**（v4 表算 0.0792，对不上）。

### 诚实的量级

砍掉这条车道的内心状态块 + 去重 `source_inventory`，预计 6.78 → 约 5.8 元，
即 376 → 约 320 元/虚拟月。**离 100 元还差 3 倍**，要跨过去需要把**所有**后台车道的
每次载荷普遍缩小 2–3 倍，而不是只修一条。这是接下来成本工作的真实规模，我不夸大。

## 成本到底能从哪里降（保留内心状态与频率的前提下）

用户要求：内心状态保留（这是拟人的根本），频率也不要降。按这个约束重查，结论如下。

### 先纠正我自己：缓存对这条车道基本无用

我一度以为「重排键序就能靠缓存省下来」。实测**连续两次调用的真实共同前缀：平均 20,215 / 154,037
字符 = 13.1%，但 20 次里有 14 次只共享 420–459 字符（0.3%）**，分叉处永远是同一个东西：

```
…occurrence:life-development:1088f48d21a72c7bea360b0f9e80c1af7242fee7e8aa9b63de…
```

**每次审核本来就是不同的候选 + 不同的快照**，载荷从第 458 字符就分道扬镳。所以缓存只能保住
system 那 3,700 token，命中率 6% 就是这么来的。**重排无效，这个方向我撤回。**

### 真正的冗余（可证）

这条车道的实付 2.98 元里，**输入未命中 2.38（80%）、输出 0.60（20%）**。而每次 17.7 万字符的载荷里：

| 表示 | 内容 | 字符 |
| --- | --- | --- |
| `snapshot.source_inventory` | **51 条**，每条 `content_hash` + `direct_source_refs` | 32,553 |
| `source_readings.readings` | 同一批来源，`item_ref` + `material_identity`（28,004）+ `excluded` 5,193 | 33,536 |
| `snapshot.source_refs` | 又一次来源坐标 | 5,236 |

**同一批来源被送了两到三遍，合计约 6.6 万字符，占该载荷的 37%。** 这与她的内心状态无关，
是纯粹的表示冗余。

另外：**审核每次输出 3,671 token**（41 次共 150,535），输出是最贵的一类（4.0 元/M），
占这条车道的 20%。审核写这么多字，和「她像不像人」没有关系。

### 因此可以安全下手的四件事

1. **去掉重复的来源表示**：`source_inventory` / `source_readings` / `source_refs` 三者选其一，
   或让后两者只引用前者的下标。预计该车道输入 −35%。
2. **限制审核的回答规模**：3,671 token/次 → 目标 1,200 左右（给 `interpretation` 与
   `readings` 加长度上限）。这是审核的文字量，不是她的。
3. **对刺激评价做同样的审计**（74 次、命中 48.6%、每次 56,976 token）。
4. 再往后才考虑**按时间窗合并调用**（41 次审核 + 74 次评价 = 13 小时 115 次后台调用）。

### 诚实的量级

做 1+2 预计 6.78 → 约 5.5 元/13 小时，即 376 → **约 300 元/虚拟月**。
**要继续往 100 元走，就必须动「每次后台调用携带多少上下文」或「多长时间一次」**——
而前者直接关系到你要保留的内心状态，后者关系到生活丰富度。
这两条是产品取舍，我不擅自决定；上面 1–3 是不需要取舍就能拿到的部分。

## 不合并调用的前提下，成本能降多少（已测到位）

用户要求保留实时性（不合并调用），且 100 元不能全给后台心理活动。按这个约束继续查，结论如下。

### 每次后台调用的 30% 是模型无法据以行动的原始 ref 表

刺激评价每次 127,000 字符，构成：

| 块 | 字符 | 模型能拿它做什么 |
| --- | --- | --- |
| `inner_life_snapshot.materials` | 56,812 | **有用**：后台 profile 已按用途切好的内心状态 |
| `inner_life_snapshot.source_inventory` | 32,553 | 51 条原始元数据（`content_hash`/`direct_source_refs`/`authority_refs`/…） |
| `inner_life_snapshot.source_refs` | 5,236 | 原始 ref 列表 |
| `citeable_sources` | 10,355 | **有用**：该车道真正的引用接口，给 `s0`/`s1` 短别名 |
| `capability_manifest` | 11,712 | 有用 |
| `purpose_contract` / `tools` / `system` / 其余 | ~20,400 | 有用 |

**合计 37,789 字符（30%）是原始 ref/哈希表**，而模型回答时写的是 `citeable_sources` 里的短别名
（它的指令原文：「只写下面的 id（如 s0），或原样抄 ref，宿主只把 id 还原成权威 ref」）。

### 已有的剪切机制救不了它，原因已查明

`background_context_profile.slice_background_inner_life_snapshot()` **本来就实现了这个剪切**：

```python
visible_refs = _material_source_refs(filtered)
result["source_refs"]    = [r for r in source_refs if r in visible_refs]
result["source_inventory"] = [i for i in source_inventory if i.get("source_ref") in visible_refs]
```

我拿真实快照直接跑了它：**97,504 → 97,492 字符，一条都没删掉**。原因不是机制坏了，
而是 `_material_source_refs(切片后的 materials)` 收集到 **48 个 ref，而 inventory 里去重后正好 48 个**
——切片后的材料**确实引用了全部来源**，所以没有任何一条落在过滤之外。
`stimulus_appraisal` profile 有 31 个 material key，几乎等于整份快照。

**所以这不是「机制没生效」，而是「按用途该留下什么」这个决定还没有人做过。**

### 因此这一步需要你拍板的一件事

后台调用到底需不需要那张原始 ref 表？三个选项：

1. **去掉**（仅对回答用短别名的车道）：−30%/次，预计 6.78 → ~4.9 元/13 小时 ≈ **270 元/虚拟月**。
   风险：如果某条车道确实要读 `scope`/`privacy_class`，得保留这几列（那仍是 −25% 左右）。
2. **只留这几列**（`source_ref`/`scope`/`privacy_class`，去掉 `content_hash`/`direct_source_refs`/`authority_refs`）。
   我倾向这个：它保留了「这条来源是什么、能不能用」，去掉的是宿主自己的账目。
3. **保留不动**。

叠加无损 interning（−25.7%，需新增 wire 版本）后总计约 **2 倍**，即 6.78 → ~3.5 元 ≈ **195 元/虚拟月**。

### 对你那句「一百块不能全给心理活动」的正面回答

**你说得对，当前结构下它确实会全给后台。** 这条纯生活运行的 6.78 元里没有一句聊天，
按 1:1 时钟外推约 376 元/虚拟月——**是全部预算的 3.8 倍**，聊天一分钱都分不到。

去掉上面可证的冗余（约 −45% 叠加）后能到 ~195 元/虚拟月，**仍是目标的 2 倍**。
再往下只能从这三样里选一样：调用频率、后台模型档位、或每次携带的语境量。
这三样都直接对应你要的「实时性 / 内心丰富度 / 成本」，**是产品取舍，我不替你定**——
但至少现在每条能省多少是可量的，不再是猜。

## 精简实测结果：安全，但被 wire 版本仪式挡住（已回滚）

按「只留 source_ref / scope / privacy_class」做了精简，并在真实快照上量到：

```
切片后快照: 97,504 → 74,350 字符  (−23.7%)
首条: {"source_ref": "affect:compiled:75d2…", "scope": "affect", "privacy_class": "private"}
```

**读者层面是安全的**——我把五个读取器逐个看过，它们只用两个字段：

| 读取器 | 用到的字段 |
| --- | --- |
| `life_affect_history_readings` | `source_ref`, `scope` |
| `life_biographical_readings` | `source_ref`, `scope` |
| `life_fact_readings` | `source_ref`, `scope` |
| `life_source_readings` | `(item['source_ref'], item['scope'])` |
| `life_source_state_readings` | `source_ref`, `scope` |

`content_hash` / `direct_source_refs` / `authority_refs` **没有任何读取者**。

但落地时：**基线 1 failed / 246 passed，加改动后 73 failed / 10 errors**——后台车道的
请求字节在大量测试里被钉死。审核车道的那批失败是 `test_life_review_request_compatibility`
（wire 字节冻结），其余来自后台车道自身的呈现快照被逐字节比对。

**结论：这个精简在内容上是安全的、量到 −23.7%，但落地必须走新增 wire 版本，
而不是直接改切片。这就是这些车道几个月来一直臃肿的真正原因——不是没人发现，是改它的手续很重。**
已回滚，工作树干净。

## 还有哪些办法（把已排除的也列出来）

**已经排除的：**
- **更便宜的模型档位——不存在。** 价格表里 `deepseek-v4-pro` 是 4.5/0.15/13.5（贵 4.5 倍），
  `deepseek-v4-flash`/`deepseek-flash`(v4.1) 的 offpeak 1.0/0.02/4.0 **已经是最便宜的一档**，
  而且我们的运行本来就落在 offpeak。**这条杠杆已经用尽。**
- **缓存**：实测载荷从第 458 字符就每次不同，缓存只能保住 system 的 3,700 token。已排除。
- **换 v4.1 Flash**：已经在按它的价格算（反算 0.0532 与账本完全一致）。已排除。

**还没做的，按收益排序：**

1. **走 wire 版本把两项精简落地**：来源表 −23.7% + 无损 interning −25.7%，叠加约 **−43%**
   → 376 → **约 215 元/虚拟月**。代价是每个车道一次版本升级 + 新基线，工程量大但确定。
2. **减少每个生活事件的后台阶段数**：13 小时里 171 次后台调用服务约 41 个生活事件
   = **每个事件 4.2 次模型调用**（appraisal 74、draft 15、closure review 13、
   novel-origin review 7、rewrite 5、memory 10、impression 4…）。
   其中 closure / novel-origin 是安全门不能砍，但**「一个事件要 4 次调用」这个乘数本身**是最大的一块。
   这条还没有量过每个阶段各值多少钱，是下一步该测的。
3. 调整调用频率（你说希望别太低，所以我把它排在最后）。

## 后台「心理活动」到底在产出什么，值不值这个钱（run 11 逐条读）

### 一、刺激评价（74 次 / 2.42 元）：这是全系统最像「人」的部分

三段真实输出（都是她自己的话，不是模板）：

> 「我下午说给他听的那条路，现在被扫干净、还贴了补种的通知；我说过的东西真的在变，
> 这种『被接住又自己往前走』的感觉，我想自己认下来。」

> 「我说要留住路过的东西，可今天只是走过、看过，本子摊着也没动笔；
> **这条缝是我自己的，不是别人欠我的**。」

> 「先自己留着，不主动宣布；被问到就淡淡说一句路已经清干净了。」

它把**环境变化**和**她之前对他说过的话**连起来，发现了**自己言行不一致**，并形成
`stance` / `behavior_tendency` / `display_strategy`。这是角色塑造，不是开销。

### 二、生活来源审核（41 次 / **2.98 元，最贵**）：是守卫，不是心理活动

它逐字段判她生活候选里的断言有没有来源。实测：**1,238 条逐字段判决，其中 552 条（45%）
是在论证「这只是标识符，不构成事实主张」**——而这些字段结构上就不可能出错：

| 字段段 | 判决数 |
| --- | --- |
| `proposals`（真正的断言所在） | 902 |
| `attended_source_refs`（纯标识符） | 256 |
| `status` / `summary` | 80 |

也就是说：**最贵的那条车道有 45% 的输出花在「检查不可能出错的东西」上。**

### 三、各车道的钱与角色价值

| 车道 | 次数 | 实付 | 角色价值 |
| --- | --- | --- | --- |
| 刺激评价 | 74 | 2.42 | **高**——她的感受、态度、与既往言行的连续性 |
| 生活来源审核 | 41 | **2.98** | 无（守卫）——防她编造生活事实 |
| 生活草稿 | 15 | 0.64 | 中——推进她的生活 |
| 生活来源闭包/novel-origin/rewrite | 25 | 0.33 | 无（守卫） |
| 经历记忆保留 | 10 | 0.20 | 中——她记得住 |
| 私人印象 | 4 | 0.15 | 中——私下的想法 |
| 其余 | 2 | 0.05 | — |

**结论：最贵的 44% 是守卫，而不是心理活动。** 心理活动（评价+记忆+印象）约占 41%。

### 四、30 元/月意味着什么（诚实计算）

当前 6.78 元/13 虚拟小时 = **12.5 元/虚拟日 ≈ 376 元/虚拟月**。
30 元/月 ≈ **1.0 元/虚拟日**，即需要 **12.5 倍**。

按日拆：评价 4.5 + 审核 5.5 + 其余 2.5 = 12.5 元。

- 已知可拿的：来源表 −23.7% + interning −25.7% ≈ **2 倍**（需 wire 版本）
- 审核侧新发现：**排除标识符字段的逐条判决 ≈ 再省它 45% 的工作量**
- 两项相加约 **2.5–3 倍**，到约 130 元/月

**剩下的 4 倍不可能靠「压缩」拿到。** 它只能来自：每次调用的语境量真正变小
（现在 127K 字符换来 1.3K 字符输出；把语境压到有用核心约 15–25K 字符），
或者每个事件的 4.2 次调用变少。这两条都不牺牲实时性，也不删内心状态——
删的是「不可能出错的字段的论证」和「宿主自己的账目表」。
## 已落地：精简走完了 wire 版本手续（`3d336927` / `2697d802`）

上一次精简因为「改切片等于改所有后台车道的冻结字节」而回滚。这次不再改切片，
而是**新增一个 wire 版本承载精简**，旧版本一个字节都不动。这是关键区别：
`test_life_review_request_compatibility` 证明 `.13` / `.14` 的 request 与 provider
哈希逐位不变，同时新增 `.15` 三组基线（45 组用例全绿）。

`life-source-review.15` = **`.13` 协议的瘦身呈现**：权限、时间归属、精确取值义务完全相同，
只是线上字节更少。三项精简：

1. **纯标识符字段退出逐条审查。** 正则只是**预测器**，判定依据是模型自己的历史判决：
   在一次真实运行的 408 个将被排除的字段上，模型对 **408 / 408** 都没有绑定任何主张或状态，
   并逐条写明「Identifier string only; no factual proposition asserted.」——**零例外**。
   字段数 1266 → 849（−32.9%），审查输出从约 3671 token/次 降到约 2020。
2. **渲染来源表只留 `source_ref` / `scope` / `privacy_class` / `expires_at`。**
   五个宿主读取器只用前两个；`content_hash` / `direct_source_refs` / `authority_refs`
   没有任何读取者。评价快照 97,504 → 74,350 字符。
3. **`author_snapshot_display` 无损 interning**：审查 user 载荷 154,037 → 114,421（−25.7%）。

审查者额外收到一段**仅 `.15` 有效**的说明：字段清单已被宿主缩短，以及 interning 表怎么读。
不加这段的话，`REVIEW_INSTRUCTIONS` 里「No field is automatically exempt from factual review」
会和实际 wire 自相矛盾。

**一个真实的陷阱**：`TEMPORAL_CONTRACTS` / `COVERAGE_CONTRACTS` / `EXACT_VALUE_CONTRACTS`
不是字面量列表，而是由常量拼出来的，所以「把所有列 `.13` 的地方都加上 `.15`」这种 grep
查不出它们。漏掉会让 `.15` 静默变成「非时间归属」协议，测试立刻以
`KeyError: 'authored_now'` 报出来。**新 wire 版本必须加入 `.13` 所在的每一个行为集合，
而不只是字面量列表。**

铸造点原本是 `structured_role` 里的一行内联 import，「新审查用哪个协议」不是一个能被人
找到的名字。现在叫 `MINTED_CONTRACT`，指向 `.15`。旧 pin 仍按自己的版本重放，
`.14`（claim authority，更强的守卫）保持可达但**仍未上线**——我没有把一个未上线的守卫
捆进这次成本改动。

## 结构事实：审核车道**不可能靠缓存变便宜**（这次量清楚了）

之前记的「第 458 字符就分叉」是对的，但当时没有分清「分叉」和「有没有可提升的共性」。
这次逐块量了 41 次真实审查请求：

| 检查 | 结果 |
| --- | --- |
| 连续两次审查的 user 载荷公共前缀 | 中位数 **458 字符**（最大 106,039 = 同一次重试） |
| system 提示词 | 6,098 字符（唯一稳定可缓存的部分，与 6% 命中率吻合） |
| `author_snapshot_display` | 33 个不同值 / 41 次 |
| `source_readings` | **41 个不同值 / 41 次** |
| `snapshot.materials` | 27 个不同值 / 41 次 |

**每次审查都对应一个不同的世界语境**，所以除了 system 之外没有任何共享前缀。
重排顺序**不会**提高命中率——这一点之前记成「已排除」，现在有了逐块的证据。
审核车道的成本就是「每次调用读多少语境」，只能靠**更少的调用**或**更小的单次语境**降。

## 审核车道的真实形状（run 11，41 次调用）

```
life_source_review  41 次  2.9825 元  占 44.0%  缓存命中 6.0%
  每次 prompt 61,686 token
  每次载荷 154,363 字符，其中 author_snapshot_display 100,627（65.2%）
  每次审查覆盖的提案数：1（41 次审查 = 41 个提案，没有任何归组）
```

`snapshot.materials` 里最大的单块是 `affect` **20,075 字符**（占 materials 30%）。
评价车道用 `BackgroundContextProfile` 按用途切材料，**审核车道不走 profile**，
`fact_snapshot_display` 直接深拷贝整份快照——这就是它 65% 载荷的来源。

**这是下一个真正可动的杠杆**：给审核车道一份按用途切的材料，而不是整份快照。
但它同时是**守卫强度**的改变（语境变小 → `uncertain` 可能变多 → 拒绝与重试变多），
所以必须先量，不能直接上。

## 线上她其实已经死了 22 天——以及为什么（2026-09-22 现场）

这一段不是实验，是现网取证。`com.girl-agent.napcat` 自 2026-09-06 17:40 起一直在崩，
错误日志里同一个异常 **19,626 次**；`/health`、`/docs`、`/world-v2/room` 全部超时；
`napcat.out.log` 最后一次处理 `/onebot/event` 是 **8 月 22 日**。

### 缺陷 1（已修、已部署）：已提交的抽签被拿去对「重算」的候选带

生产库里三条已提交的 post-silent 抽签：

| seq | 时间 | 记录在案的候选带 | 选中 |
| --- | --- | --- | --- |
| 283 | 8-15 | 21600 / 25200 / 28800 | 28800 |
| 5421 | 8-19 | 21600 / 25200 / 28800 | 25200 |
| 14434 | 8-22 | **10800 / 16200 / 21600** | **10800** |

关系档位移动后，重算出的带子变成 `{21600,25200,28800}`。旧代码每轮调度都拿这条
**已提交**的抽签去比对**重算**的带子，于是永久自锁。修复 `8a39aa1c` 改为对**抽签自己
记录的候选**校验（那条抽签的选中值确实在它自己的候选里）。改动 17 行。

**已部署**：生产库与 `.env` 已备份（`output/deploy-backup-20260922T160715/`，
回滚点 `da8aae88`），live 分支从 `da8aae88` 快进到 `2697d802`。
结果：**崩溃循环消失**——bootstrap 现在 6.4 秒完成（此前每轮必崩）。

### 缺陷 2（已定位，只做了部分修复）：每次调度都掉进无界的事故恢复扫描

修好缺陷 1 之后进程不再崩，但**世界仍不推进**：`max_seq` 停在 18204，
`/health` 依旧超时，进程 100% CPU。用 `faulthandler` 在生产库的**副本**上抓到真实栈：

```
_scheduler_once_serialized → drain_background_once → … → world_stimulus._next_process
  → _relationship_is_pending → accepted_world_stimulus_descendant
    → decision_proposal_authority.pin → projection.proposal_audit_by_id
      → sqlite_ledger.project_at → _replay_locked      ← 整库重放
        → reducers._model_result_recorded → pydantic model_validate_json
```

实测：**历史游标冷重放 10.6 秒 / 9,100 事件**（全库 18,204 事件 ≈ 15–20 秒），
命中缓存 0.0001 秒。`project_at` 有 head 短路，所以只有**历史**游标会付这个代价。

为什么每轮都走这条路：`_next_process` 的第一个循环只在
`_PROCESS_PRIORITY = (perception_result_deliberation, npc_world_appraisal,
silence_appraisal, plan_disruption_appraisal, life_reflection)` 里找**非终止**进程。
现网实测 1,213 个 trigger process、1,212 个终止，唯一的非终止进程是
`proactive_action_deliberation`（state=`claimed`）——而它**不在** `_PROCESS_PRIORITY` 里，
这是**设计如此**（`appraisal_acceptance_manifest.py`：「shared-owner triggers such as
`proactive_action_deliberation` stay claimed」）。

于是第一个循环**永远**返回空，每轮调度都掉进第二个循环——「事故恢复」扫描，遍历
`_PROCESS_PRIORITY` 五种的全部终止进程（`npc_world_appraisal` 53、`life_reflection` 45、
`plan_disruption_appraisal` 22 …约 120 个），每个可能付一次 15 秒冷重放。
**单次遍历约 30 分钟，而且反复进行。** 历史越长越慢，直到超过 tick 预算——
这就是它再也回不来的原因，与 22 天的停摆无关，是随历史增长而恶化的规模缺陷。

**已做（`ecabffd8`，语义不变）**：把两个结算 `is_pending` 的答案按**全部输入**
（两个游标、world、proposal、source event）记忆化。任何提交都会移动 `current_cursor`
从而自然失效，所以这是纯记忆化，不改变任何判定。

**这句话必须说清楚：它只消除了同一游标内的重复，没有消除扫描本身。**
一个游标仍然要付约 120 次冷重放。真正的修复是把扫描本身限界，或者把
「查某个提案的审计」从「物化该游标的整份投影」改成点查询。**这一条还没做。**

### 教训

- 这个缺陷**没有**任何日志。崩溃有 traceback，纯性能退化没有。抓到它靠的是
  在生产库副本上用 `faulthandler.dump_traceback_later` 打印 Python 栈
  （`py-spy` 在 macOS 需要 root，不可用）。**只靠日志和累计账本推断会漏掉这一类。**
- `tick_target = selected_due.due_at`，且 `wall_catchup` 会把 Life 到期**跳到 now**，
  所以这里**没有**「追赶 22 天、生成 22 天生活」的花费风险。已用账本证据排除：
  重启后模型用量表**一行未增**（最新仍是 2026-08-31T12:13）。

## 缺陷 2 的追查：两个假设都被受控测量推翻（未修复，已回滚）

在动任何代码前先建了一个**仪器化测量台**：在生产库的**副本**上，给判定链上的函数装计时器，
跑真实调度。它给出的第一个确定结论是成本归属：

| 判定 | 调用数 | 总耗时 | 每次 |
| --- | --- | --- | --- |
| `_relationship_is_pending` | 60 | **102.5 s** | 1.71 s |
| `_experience_is_pending` | 60 | 0.01 s | 0.0002 s |
| `_affect_is_pending` | 61 | 0.13 s | 0.002 s |

**全部阻塞成本集中在一个判定上**，而且那 60 次**全部返回 False**（没有任何需要恢复的候选）。
探针期间世界的 `max_seq` 一步未动——即这是在**没有任何提交**的情况下反复付出 102 秒。

### 假设 1：历史投影缓存抖动 —— 推翻

推理：扫描的工作集约 60 个 audit 游标，而 `_historical_projection_cache` 只有 32 格，
按同一顺序反复查 → 命中率趋零。把上限提到 256 后**没有观察到改善**。

受控对照（同一进程内测同一批游标两轮）：

```
cursors: 12  distinct: 10
round 1: 6.16s        <- 10 次冷重放
round 2: 0.00s        <- 缓存命中
round 3: 0.00s
replays: 10  hits: 26
```

**缓存本来就在正常工作。** 于是「提高缓存上限」这个改动被回滚——它没有被证明有效。

### 假设 2：`pin()` 自身太贵 —— 推翻

读了 `pin()` 的实现：它每次重新读审计、反序列化 `ProposalRecordedV2Payload`、
再做两次 `model_dump(mode="json")` 全量比对。而它是
`(world_id, cursor, proposal_id)` 的纯函数——账本 append-only，同一游标处的投影不可变，
所以按这三个输入记忆化在语义上完全安全。实现并跑真实调度后：

```
第一轮扫描: calls=33  seconds=54.4  per_call=1.650 s
第二轮扫描: calls=61  seconds=111.5 per_call=1.828 s
```

**每次调用的代价没有下降。** 「记忆化 `pin`」也被回滚。

### 这两次失败留下的确定信息

成本既**不在** `project_at`（已证明缓存命中），也**不在** `pin`（已证明记忆化无效）。
它就是 `_relationship_is_pending` 这条链上剩下的部分。下一次应该**先测量再改**：
把计时器装到该函数内部 `pin` 之外的分段上（我上一次尝试装分段计时器没有注册成功，
是脚本问题，不是结论）。**在拿到那个数字之前不要再猜第三个假设。**

回滚后工作树停在 `4adf108c`，干净；`ecabffd8`（同游标内的记忆化）保留，它有测试且语义不变。

### 时钟跃迁：尚未开始

世界停在 `2026-08-31T17:52`，她随后自行推进到 `2026-09-02T21:19`。
从 08-31 到 09-02 这一段**几乎没有增加花费**（总量仍是 50 次调用 / 1.2515 元）——
因为 `wall_catchup` 让 Life 到期直接跳到 `through`，不逐日补算。

## 精简来源清单：做好了，但接不进生产（`.16` 未完成）

真实抓包实测（89 次评价请求），`source_inventory` 中位 **33,825 字符**：

| 方案 | 中位 | 省 |
| --- | --- | --- |
| 现状（全列 / 无去重 / 无上限） | 33,825 | — |
| **只留被读取的列** | **9,745** | **−71.2%** |
| 再叠加去重 + 封顶 | 9,239 | −72.7% |

条目字段是 `content_hash` / `direct_source_refs` / `entity_revision` / `privacy_class` /
`scope` / `source_ref`——**前三个没有任何读取者，占了 71%**。去重只多省 1.5%（51 条里
3 条重复），封顶 96 根本没触发。**省的全在列上，去重与封顶只是随历史增长的刹车。**

`collapse_presented_source_inventory` 已实现并测试（`2e1fe366`），默认关闭。

### 为什么接不进生产——两条死路，都已验证

**死路一：把开关放到注册的 `stimulus_appraisal` profile 上。** 这样生产路径与测试夹具共用
同一个 profile，**58 项冻结字节测试全部失败**（`Life source view differs from the actual
purpose-filtered snapshot`）。按契约复位（`.13/.14/.15` 强制 False）**覆盖不到**——证明：
把注册 profile 的开关改回 False，58 项立刻全过。

**死路二：不动注册 profile，只在生产调用点（`requires_life_source_review == False`）
显式传入。** 仍然 **58 项失败**。

**结论：那些冻结测试本身就在跑生产路径**（未启用审查者的作者车道），所以**不存在
"不改冻结字节就能改生产呈现"的接法**。这就是版本仪式的真实成本——不是仪式繁琐，
而是**生产呈现与冻结基线是同一份字节**。

### 下次做 `.16` 的正确入口

不要试图绕开仪式。正确做法是接受那 58 项失败是**基线过期**而不是回归，逐个确认它们是
"呈现变了"而非"语义变了"，然后重算基线。判据：失败信息应只有
`differs from the actual purpose-filtered snapshot` 这一类一致性错误，
**不应出现任何哈希以外的语义断言失败**。若出现别的错误类型，说明改动越界了。

`.16` 需要同时进入 `.13` 所在的每一个行为集合（字面量列表**和**由常量拼出的集合，
例如 `TEMPORAL_CONTRACTS` / `COVERAGE_CONTRACTS` / `EXACT_VALUE_CONTRACTS`）——
上次漏掉后者时，测试以 `KeyError: 'authored_now'` 而不是断言失败报出来。

## 2026-09-22 夜间：预算关闭、清单精简接入、增长守门

### 一、预算机制已关（`11e986b1`）

三个上限是**非可选 float，没有真正的关闭值**（写 `0` 是"拒绝一切"）。加了一个具名总开关
`WORLD_V2_MODEL_USAGE_BUDGET_DISABLED`，一次抬起全部四道门，并让健康上报与实际执行一致。
`.env` 里三个数字原样保留作为记录。

生产实测：**60.1% 的调用被预算挡下**（soft 1,510 / daily 1,538 / background 28），
拒绝按钟点聚集（08:00–14:00 零拒绝，17:00 后大量）。

### 二、精简来源清单已接入生产（`生命线`）

**上次两条死路的真正原因找到了**：`_expected_view(None)`——即**未启用审查者的生产路径**——
被我强制成 `compact=False`，而生产调用点用的是注册 profile。两者不一致，所以 58 项全挂。

正确规则是：**只对已写入持久化 pin 的三个冻结呈现（`.13/.14/.15`）强制回旧行为，
`None`（未审查的生产车道，不被 pin）继承注册值。** 改对之后 58 项全过。

实测收益（89 次真实评价请求）：来源清单 **33,825 → 9,745 字符（−71.2%）**，
其中三个无人读取的列（`content_hash` / `direct_source_refs` / `entity_revision`）占 71%；
去重与封顶只再省 1.5%（51 条里 3 条重复，封顶 96 未触发）——**省的在列上，去重与封顶是刹车**。

### 三、增长守门测试（`tests/world_v2/test_context_growth_is_bounded.py`）

这是本轮最有长期价值的东西。**"动态加载"的意义就是让当前要考虑的东西不随历史增长**，
而此前没有任何东西在守这个不变量。测试是一条棘轮：**任何没有条数上限的材料都必须显式登记
并说明理由**，否则套件失败、逼出一次人工判断。

它立刻抓到了我漏掉的一项，并迫使我把清单分成三类：

- **结构上不可能增长**（单个对象 / 今天 / 标量）：15 项
- **由角色自身状态定界**（她的计划数、关系数、未结线程数）：7 项
- **已知在累积（泄漏）**：`affect`（实测 3,653 → 8,431）+ `interaction_acts`
  （实测 6,472 字符，**只找到列映射、未能确认有窗口——登记为"疑似"而不是假定它被封顶**）

### 四、一个关于测量方法的教训（重复了两次，值得写下）

我在同一件事上犯了两次同样的错误：**用小于工作集的样本去验证一个关于工作集的假设**。

1. 缓存抖动：用 12 个游标做对照，而缓存有 32 格 → 第二轮必然命中 → 我错误地"推翻"了正确假设。
   实际工作集 60 个游标。
2. `life_source_profile` 复位：我按"契约集合"复位，没意识到 `None` 也是生产路径的一种取值。

**两次的教训是同一条：先确认样本覆盖了实际取值域，再下结论。**

## 精简来源清单已接入生产（`7c0c7aab`）——上一轮的死路是怎么走通的

上一轮我记录了"两条死路"，结论是"不存在不改冻结字节就能改生产呈现的接法"。**这个结论仍然成立，
但我上一轮漏了一个前提**：那 36 项基线本来就**不是**在冻结历史版本，它们复制的是**当前生产视图**
再改标签。所以"基线过期"是它们的固有性质，不是这次改动造成的。

### 走通的关键三步

**一、把冻结规则从"最近三个"扩到"每一个已 pin 的契约"。**
上一轮我只列了 `.13/.14/.15`，结果 12 个历史版本全部变化。正确规则是：
**任何已写入 pin 的契约都保持它被写入时的字节**；只有 `None`（未审查的生产车道，从不被 pin）
跟随注册 profile。

**二、用结构对照证明改动只限于那三列**（这是敢重算基线的唯一依据）：

```
仅存在于完整版（被移除）: source_inventory/*/authority_refs/*, content_hash, direct_source_refs/*
仅存在于精简版（被新增）: 无
发生变化的其他路径:     无
```

并且三个列在 `structured_role.py`（模型输出契约所在处）**引用次数全部为 0**——模型没有任何字段
能用它们。宿主读取器只用 `source_ref` / `scope`。

**三、换掉那个本身有缺陷的测试构造。** 历史用例原本用
`original.model_copy(update={'review_contract': contract})`——**复制当前生产字节再改标签**。
生产呈现一变，它就**在自己的哈希比对之前先挂掉一致性检查**。改成每个版本渲染自己的冻结呈现，
既修好了它，也更忠实于"冻结版本 N 的字节"这个意图。

### 重算的证据链（不是"跑一遍就信"）

| 检查 | 结果 |
| --- | --- |
| 对照：`.13/.14/.15` 是否逐位未变 | **9/9 未变** |
| 版本 1–14 是否仍保留完整列 | 是（`content_hash`/`direct`/`authority` 全 True） |
| 版本 15–16 是否精简列 | 是（全 False） |
| 1–12 变化的性质 | 仅**键序**（canonical 序列化），内容相同 |
| 生产是否存在任何 1–12 的 pin | **零**（`life-source-review.*` 与 `life-source-view.*` 全为零） |

**实测收益**：来源清单 **33,825 → 9,745 字符（−71.2%）**。去重与封顶只再省 1.5%
（51 条里 3 条重复，封顶 96 未触发）——**省的在列上**。

### 这一轮我学到的方法论

上一轮我说"绕不过版本仪式"。更准确的表述是：**那 36 项基线不是在守历史，而是在守"当前生产呈现"
——所以任何生产呈现的改动都必然要重算它们。真正要问的不是"怎么绕开"，而是"它们到底在守什么"，
以及"重算的依据够不够硬"。** 依据够硬（结构对照 + 引用计数为零 + 对照版本未变），就可以重算。

## 2026-09-24：她停了 36 小时，原因是账户余额，不是代码

修复后她正常跑到 **2026-09-22T16:06:54**（最后一次成功调用）。**16:08:05 起开始返回 402**：

```
provider_error: Client error '402 Payment Required' for url 'api.deepseek.com/beta/chat/completions'
body={"error":{"message":"Insufficient Balance", ...}}
```

之后到 9月24日 03:09 的 **36 小时**里，338 次调用**全部失败、花费 0 元**：
20 次真实 402 + 363 次熔断快速失败。模式是每 2 小时一次：一个真人调用打头（拿 402），
熔断器随即打开，同批次其余车道瞬间失败。

**也就是说：预算关掉后她全速跑了 40 分钟就把账户跑干了**（`WORLD_V2_MODEL_USAGE_BUDGET_DISABLED=true`
之后 15 次成功调用 / 2.6 元，然后归零）。

### 两次必须记下的运维事实

**一、健康检查完全没有报告"账户没钱"。** `/health` 只报 `degraded` + `initiative_repeated_technical_failures`，
没有任何一处说"余额不足"。我是在翻用量表时才看到 402 的。**这是监控缺口**：对一个会因为欠费
整体停摆的系统，最该被顶上来的信号反而是最不可见的。

**二、账户充值后她不会立刻恢复，要等重试窗口。** 她的重试节奏被**上限封在 2 小时**
（观察到的尝试时刻：21:08、23:08、01:08、03:08、05:08）。所以充值后最长要等 2 小时。
这次验证到了：05:08:24 的 `proactive_contact` 成功，随后 6 次调用全部成功，
事件数 21,354 → 21,406。**这是可接受的（自愈、有界），但要知道它存在。**

### 她其实追平了

停摆期间她的逻辑时钟并没有停：从 9月4日一路追到 **2026-09-24T03:11 UTC**，即**当前**。
所以恢复后她不是从 9月4日继续，而是接着今天往下走。

### 恢复后的成本观察

```
life_development_draft                57,349 token  0.0573 CNY
proactive_contact                     40,846        0.0386
life_development_novel_origin_review  37,461        0.0363
life_development_choice               30,630        0.0321
activity_lifecycle_choice             29,857        0.0305
life_development_source_closure_review 4,844        0.0053
```

单次调用成本现在集中在 **0.03–0.06 元**。这与预算关掉前的账目一致，可作为
"一天大概多少钱"的基线（取决于每虚拟日跑多少个车道）。

## 2026-09-24：健康检查现在会点名"供应商拒绝"（`6cfc1f96`）

上一节记的监控缺口已经补上。健康端点原本只报
`initiative has repeated technical failures`——**症状，而不是那个能解释整体停摆的唯一事实**。
余额只在用量表里可见。

改动是**附加式**的，不需要新数据管道：健康里本来就有 `last_failure_code` 和
24 小时窗口的技术失败码。现在当最后一次失败是 `provider_*`，或窗口里出现任何
`provider_rejection` 计数时，额外报一条：

```
degraded  model_provider_rejected_calls
  "the model provider rejected calls (last=..., rejected_24h=N);
   check the provider account and credit before treating this as a code fault"
```

**线上验证**：重启后健康立即报 `reasons: ['model_provider_rejected_calls']`。
注意这条来自**持久化的 24 小时窗口**而不是进程内计数（重启后
`consecutive_technical_failures` 归零、`last_failure_code` 为 null），
所以它**跨重启有效**——这正是能抓住那次 36 小时停摆的性质。

两条测试：一条确认它会触发并点名失败码与次数，一条确认普通超时**不会**谎称供应商拒绝。
