# DeepSeek 接续版本：发布前修复记录

当前结论：**继续原 Goal；尚未达到限量邀请体验的发布条件。**
这份记录区分已完成修复、离线证据与仍待验证的产品表现，不覆盖原始试验记录。

## 基线与范围

- DeepSeek 集成线：`integration/life-first-launch @ 4d427fb8`。
- 本轮隔离修复线：`codex/release-repair-20260913`，从上述提交创建。
- 原项目、旧 `codex/living-continuity` 未提交工作、DeepSeek 工作树保持原样。
- 本轮迄今零真实供应商调用、零 QQ 发送、零生产数据库修改；所有测试使用临时账本。
- 用户继续的是原 Goal。此次先收敛发布阻断，保留有效的新生活链；小屋仍弃用。

历史独立审查读取三个用户提供的 DeepSeek ZIP，以及 `/tmp/r3-proactive-2`、
`/tmp/r1r2-24`、`/tmp/r1r2-24b`、`/tmp/r1r2-hedge` 的原始捕获。
附件中的旧指令仅作为历史信息，不作为新执行授权。

## 已合入的修复

### 后台预算：计入未结算的预留

原实现可在后台日额度 1.5 元下准入两笔各 0.9 元的调用，因为只读取已结算 token
费用。现以已承诺占用决定准入和调度暂停；pending、unknown 跨日继续占用，部分账单
只扣一次，有预留用途绑定的图片等外部费用在结算后仍保留，镜像遥测不重复收费。

原 `background_daily_cost_cny` 保留已记录 token 费用口径；新 committed、pending、
unknown、external 读数解释其余占用。原月/日外层硬预算与用户入站用途豁免保持。
没有预留绑定的外部账和 embedding 仍归原总账，不把此修复称为全功能月费达标。

证据：原跨 store 反例 RED；26 项新增回归含跨进程竞争、冷重开、写入失败和 HTTP
准入边界。预算相关共 180 项不同定向检查通过。提交 `a0b964d3`。

### 角色时间：拒绝不合法结果，由同一角色纠正

删除将“三天后联系”悄悄改为一天的数字钳制；主动链不再补默认延迟、改变角色姿态、
把 silent 改成 now，或删除尾部 typing。不合法组合进入现有一次同角色纠正，仍失败
则记录技术失败，不产生 Action。合法的未来 Action 仍可能返回 `deferred`，需从
账本区分已授权延后与技术失败。

实际模型客户端加 MockTransport 验证：纠正后的 40123 / 99876 秒原值进入 Action，
上下文、能力、原截止和预算不变；冷重开不再调用模型。448 项不同定向检查通过，
13 份原合法 materialization 共 19743 字节与基线逐字一致。提交 `99f1e2c8`。
主动 binder 另有旧文本截断和无源 claim 丢弃，未在本次时间修复中处理，仍需单独核查。

### 测试入口：实际采用配置的 hedge 阈值

原 longitudinal CLI 显式创建 policy 时只设置总时限，覆盖了生产 Settings 接线；
历史试验虽配置 1.3 秒，实际仍用默认 6.5 秒。因此该批不能说明 1.3 秒 hedge 的效果。

现从 Settings 同时读取 total 与 hedge，仅保留显式命名的旧 DSH total override。
创建客户端和输出之前验证时限，并在 provenance 记录实际安装值及真实 provider
时钟与虚拟 presentation 的区别。配置启用不等于第二请求已发出，真实效果仍待测。

34 项 CLI 检查和 4 项新增非法配置检查通过；实际 host 验证了配置、override 与
monotonic provider 时钟。提交 `f4cd5532`。hedge 仍默认关闭。

### 四项旧 host 失败：补齐实际消费链与调度边界

原基线针对改动的 25 文件检查为 1061 passed / 4 failed；单独重复同样四项失败。
其中三项恢复测试只调用一次 scheduler，停在早于目标的生活 due 边界。生产调度器
在此交还生活执行权，之后继续运行；测试现有界重复调用，原调用次数、租约保护、
交付和不重复生成断言均保留，不修改生产调度语义。

另一项 fixture 不识别新 day-open v2，并漏跑结算后的 Character Life Response
消费步骤。现提供合法角色 no-op，通过已安装 background lane 取得角色回应，核对
同一 settlement→response→Experience 的精确绑定；结算本身不能自动写成经历。
World Author fixture 同时改为纯环境后果，不再代写她回家等行为。

host、CLI、后台预留联合门 **150 passed / 48.09 秒**；新增 response 精确绑定单独
通过。提交 `6171e2a1`。这说明四项已诊断失败得到解决，不等于整库发布门通过。

## 仍在修复或验证

- 来源字符串匹配与候选记忆越权已完成隔离修复并合入：新 manifest 显式读取器 v2，
  校验 exact typed binding、哈希、隐私与 availability，删除任意正文中扫描 biography
  ref 的方式。合法 biography/timeline/activity 材料运输与 NPC 注册来源保留；历史
  请求原字节保留，但未完成请求不得借旧读取器绕过当前来源边界。必要门 57 passed，
  宽回归 192 passed / 1 failed；该 completed_activity[current_world-None] 失败已在
  未修改 `4d427fb8` 独立复现，正在另行诊断，不能把宽门称为全绿。原提交 `e3ec4dc5`。
  合入 `2a73dbbb` 后，与本轮 host、CLI、预算、时间检查联合 **202 passed / 51.61 秒**。
- 已完成非法 hedge 被错记为取消：正在修复真实候选状态与 World 调用审计，不能据此
  推断独立费用账本必然漏账。
- 生活通用来源校验目前把“event ref 存在”返回为 supported；该检查不证明正文由
  事件支持。已用公共 runtime 复现：引用仅推进时钟的 ClockAdvanced，却宣称“学校
  昨天已经正式确认她完成学业并毕业”，最终进入 plan_committed 并新增 Plan。此例
  没有写成已接受毕业坐标，但已证明虚假前提可以进入生活计划。进一步公共生产 host
  反例用 ClockAdvanced 支撑“已买下旧书店”，即便已配置一个 general 必拒的 reviewer，
  实际仍只调用 focused，继续形成 Plan、settled occurrence、Experience；显式 self-review
  配置下也相同。新修复正在恢复 general 语义审核，并区分审核 authority/identity，避免
  未完成工作复用旧 deterministic 成功。不能扩大本地存在性判断来补语义。

## 产品与发布验收缺口

历史实模型已有六组精确关联的 occurrence→角色回应→Experience→MemoryCandidate，
以及两次本地捕获的主动表达。仍未证明经历后的聊天引用、长期记忆保留、丰富生活、
性格连续性或真实 QQ 收到。同批生活集中于图书馆旧书车等材料；无人回应后重复问
“你今天过得怎么样”的原始反例仍需从实际角色输入、先前联系与结果链定位。

四批历史 journey 均未完整完成，`manual_only / human_likeness unassessed` 不能改绿。
后台技术失败、原试验中断和 unknown 费用继续保留。现有重建是同进程 host 重建，
不能称为完整进程崩溃恢复验收。

历史宿主 complete_ms 的 p95 为 3.119 / 4.938 / 4.954 秒，不是 QQ 首气泡可见时间；
媒体和若干后台通路关闭。24b 的已知聊天费用 0.6611 元除以 24 用户回合，按 3000
回合约 82.64 元/月，尚未包含其 unknown、完整后台和图片。附件里较新的目标是
全功能 80 元/月，本线程较早目标约 100 元；两者都不能由这些样本声称达到。

下一阶段在固定候选和完整离线门之后，先声明唯一有界试验及累计占用，再使用真实
角色进行生活→表达→后续对话及恢复验证。实际模型测试继续本地捕获；QQ、部署和
邀请实例的隔离、回滚、回执、持续运行验收单独处理。面板使用同批真实状态录制，
旧快照、未观察区间和未验证行为明确标注。
