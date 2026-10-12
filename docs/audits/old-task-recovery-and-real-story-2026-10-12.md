# 旧任务恢复与真实长期故事验收

范围：基线 `988db8fc`，仅 `codex/role-memory-qualification` 隔离工作树及完整前史副本。生产进程、真实 QQ 和生产开关没有改变。

## 结论

旧任务恢复已取得明确改善：相同三项旧反思任务所在步骤由 **122.2235 秒降至 5.6798 秒，减少 95.35%**。返回结果逐项相同，优化前后调用数相同，重放一致，冷重开重复推进新增 effect 为 0。它是一次完整库对照，不能视为聊天 p95 或 2–4 秒 SLA。

真实长期故事验收**不予通过**。三次真实运行均在初次编排阶段终止，尚未形成跨日已执行故事，没有完成原计划的最多 14 个模拟日及五个普通聊天节点。实际 HTTP 成功和草案文字不能代替长期质量通过。后两次运行保留全部失败；没有反复调用到碰巧成功。

## 恢复修复及证据

慢点并非重新生成停机期间的生活，而是旧模型选择的恢复路径反复重建约 3.2 万条历史事件：

1. Experience、Aspiration 及通用 Proposal/ModelResult authority 只需不可变审计，却读取整个历史投影。现在按精确 cursor 读取审计，保留三维 cursor、事件成员、原字节/hash/commit、最终模型结果和 trigger 绑定。SQLite 同一读快照覆盖边界和审计；跨连接刷新仍验证账本，竞争写入拒绝。未知适配器继续原读取路径。
2. Affect 在已接受、可复用或来源已关闭时仍提前构建不用的历史 bounds。现在仅新 mutation 需要时读取，原历史/当前 bounds 校验不变。
3. 已接受 Affect 又 pin 原历史候选并重复走 acceptance。现在在匹配原角色选择后，验证原 acceptance manifest、candidate、mutation 的同一原子批次、类型、hash 和 causation，返回原回执。缺失 mutation 不能当作成功。没有原回执时仍走完整 acceptance。

最小回归先后捕获真实调用路径上的历史读取。完整库 v1/v2/v4 分别仍为约125/126/124秒，说明只改第一处会把冷回放移到下一处；没有用这些版本宣称修好。两个诊断 trace 在将要开始大历史回放时主动抛出异常，以定位 Affect bounds 和 Affect acceptance 的具体栈；这是诊断停止，非生产失败。

最终完整离线链 `offline-full-v6`：1项通过，366.57秒；21次推进、48小时4分钟、三模拟日期。与此前 v7 相同的三项旧反思返回内容完全相同。第12分钟整个步骤仍包含原来就有的一次生活记忆保留替身调用，三项旧反思自身不重新调用角色；该步骤所有调用数与基线一致。前史1259条不变，源历史一致、replay一致、冷重复 effect 为0。整链总耗时包含完整重放校验，且候选运行部分时间与其他CPU工作重叠，不能声称整链总耗时也下降95%。

相关228项测试通过；随后新增缺失 mutation 拒绝反例也通过。最终全库回归结果见本文件末尾。Ruff与diff检查通过。

## 真实运行：到底失败在哪里

使用源 `output/private-audits/incremental-memory-index-20261008/world.sqlite` 的新 SQLite backup，保留1259条前史、旧队列、旧失败及另一 World。采用正常 host、真实 DeepSeek 角色/World Author、配置中的本地BGE、捕获投递，副本开启 World Outlook；无同步聊天审核。虚拟日历遵守 runtime dues，发送节奏由 JourneyClock 模拟，因此不是用户真实响应延迟测试。图片、网页感知及 QQ 不在范围。

| 运行 | 实际请求及结果 | 判定 |
|---|---|---|
| live-v1 | 首条作者请求HTTP400；随后2条原积压后台请求HTTP200，但滚动证据文件的O_EXCL错误让它们被误记技术失败 | 接口与验收脚本缺陷；全部原响应保留 |
| live-v2 | 2条作者请求HTTP200；首份NPC statement带了不允许的participants，condition多了source_kind；一次纠正后故事锚点仍使用请求字典别名 | 草案未提交，不是两日故事 |
| live-v3 | 2条作者请求HTTP200；首份对空outlook选择keep；一次纠正后3条故事、0个机会，又在故事锚点清单校验处失败 | 未进入故事执行或普通聊天验收 |

已修复的传输问题：

- Outlook JSON mode请求明确包含JSON输出指令，并用真实请求构造器及离线HTTP替身回归。供应商400错误与[DeepSeek官方JSON模式要求](https://api-docs.deepseek.com/guides/json_mode/)一致。
- 增加持久化请求标记 `outlook-source-references.1`：仅指定引用字段可按本次请求字典展开完整别名，未知别名拒绝，叙事正文不变，所有schema/来源/权限校验继续。旧请求没有标记时维持原解释，原失败不追认为成功。
- 验收脚本滚动日志改原子替换，8次离线写入覆盖及live-v2/v3多次响应保存验证通过；每次请求的原始捕获继续独占保存。Outlook失败后立即停止，不再继续花钱跑后台。

### 输入与校验清单的不一致

对live-v3逐项核对，实际提供的 `history_material` 只有2个近期occurrence锚点、0个prehistory锚点，但完整库仍有1259条前史。模型返回的另外2个锚点不在该清单，却**与权威账本catalog完全一致**，其source ref、authority ref和hash均已出现在同一请求的 `world_context`。

因此这两条失败不能记为“模型编造不存在的经历”。更精确的问题是：作者看到了某些有来源的材料，但这些材料没有进入它可以选用的故事锚点集合。当前校验执行了既有契约，编排输入与可选集合却没有统一。不能通过放宽所有来源校验、猜测引用或把整库都授权给作者来解决。

下一步应先统一“实际展示的、权限允许的来源”与“可选故事锚点”，用固定捕获请求验证一致性；再验证初次编排必须新建草案的结构契约，之后重新做跨日运行。无需再添加同步聊天审核或付费评审模型。

### 草案质量的可观察范围

live-v2有承接近期环境事件的迹象，但6个机会分别只属于一条故事，`after_opportunities`均为空；文本变化主要是告示、胶带、水桶、清洁和湿痕。第二份是同次请求纠正，不是跨日发展。live-v3的纠正稿有3条情况描述、没有可执行机会。这些样本尚未展示角色选择改变后果、多故事交织、持续关系发展或普通聊天回忆能力。

本次没有对所有长期故事做出“质量很差”的总体结论；长期连续性、交织、选择后果、自然回忆、长期费用与回复速度均为**未通过/证据不足**。入口缺陷已足以阻止本轮资格通过。

## 成本与完整性

用户明确新增的独立上限为5元，最多64次真实请求。实际7次远程请求，按provider usage记录估算已知费用 **0.138021元**，HTTP400没有可结算usage，继续保留 **0.289870元** 预留；专项可用余额 **4.572109元**。这些是本专项账本口径，不是DeepSeek平台账户扣费对账，不据此解释用户整月费用。两个旧专项账本没有扩大或清除未知预留。

所有真实运行都核对源文件SHA未变。四个最终副本的原39895条主World事件及另一World5条事件均逐字节一致，向量缓存/用量表也均与源一致，见 `integrity.json`。本地BGE与远程DeepSeek文本账本分别记录，不能把本地检索调用当作新增远程模型扣费。

私有证据根：`output/private-audits/old-task-recovery-story-quality-20261012/`。主要文件：`recovery-comparison.json`、`offline-full-v6/result.json`、`live-v1..v3/result.json`及原始provider捕获、`live-v3/anchor-authority-diagnostic.json`、`authorized-story-budget.sqlite`、`integrity.json`。失败日志与诊断停止均保留。

## Standards

复核指出的旧v3载体兼容问题已关闭：窄读复用既有migration-aware字节验证，当前格式的hash检查保持。滚动日志O_EXCL缺陷已修复并验证。最终增量复核无新发现。

## Spec

复核指出的合法旧ModelResult默认字段/null编码被误拒绝已关闭：按对应Payload schema规范化后比较，原始事件/hash仍校验；四项兼容回归通过。Affect旧回执复用及缺mutation拒绝、旧载体和版本化别名边界复核通过。真实草案不因此判为长期质量通过。

## 最终全库回归

最终冻结源码全库：**10540 passed / 19 skipped / 2 xfailed / 1既有Starlette依赖warning**；pytest1248.45秒，封装1250.61秒，退出0。1692个Python文件的SHA在整个运行期间均未改变。终态见 `final-full-result.json`、`final-source-freeze.json` 和 `final-full.log`。无未关闭的独立复核问题；真实故事、生产、QQ和回复SLA仍未获资格通过。
