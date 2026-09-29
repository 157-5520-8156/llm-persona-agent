# 历史重放修复与普通文本生成切换

2026-09-29。**已切换生产8787到新包，普通文本使用sampled模式。** 保留V4.1、结构/来源引用校验、事实与记忆写入校验、媒体审核、发送授权、回执和effect-once。当前服务可访问，但后台反思仍有格式失败，健康状态为degraded；不代表整体可发布、低于5%编造率或全部低于5秒。

## 重放失败的准确原因

失败点是生产序号22158的`InteractionFactDecisionRecorded`，时间2026-09-25T19:12:32.308734Z。原`fact_context_hash`与当前保存快照中30条事实的上下文哈希完全一致；不是随机坏哈希。

逐字段对照发现，19条由epoch genesis带入的事实在重放后变了`assertion_binding`及随之计算的`semantic_fingerprint`：运行快照保留原`observed_message`来源，重放将其改成`operator_observation`、指向WorldStarted。事实值、隐私、实体版本、时间等没有变。

Git记录定位到2026-08-20的`76e1fc0e`：该改动新增上述来源改绑，却继续使用`epoch-continuity.1`。旧运行快照与新重放实现从此采用不同表示。已有局部兼容允许部分Fact before-image继续处理，但没有让整个genesis表示一致，直到后续完整Fact上下文哈希检查才失败。

## 修复与限制

- 新建迁移快照使用明确的`epoch-continuity.2`，保留当前“绑定genesis”的规则。
- `.1`历史格式有两种实现。读取原始WorldStarted及按账本顺序保存的Fact更正/撤回前态，比较由原genesis推导出的两种完整Fact表示。第一个能够精确匹配的完整前态用于识别原表示；不是只比较事实ID或值，也不从可变头缓存猜测。
- 无匹配见证时保留此前重放行为；`.2`不能因此被降为旧表示。
- 原始事件、payload hash、事实内容全部未修改。见证事件的原始字节哈希仍校验，随后所有原有事件、转换、Fact上下文、回执和重放校验继续执行。
- SQLite和内存账本使用同一选择逻辑。处理旧schema事件时先用原有upcaster解析，避免新兼容入口绕开既有旧事件支持。

这遵循[Microsoft Event Sourcing文档](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)关于保留事件、显式版本及兼容读取的做法。由完整历史前态识别曾被复用的格式版本，是本项目针对已确认缺陷的兼容处理，不是通用的“忽略哈希错误”。

## 验证

- 生产只读副本24374条历史事件完整重放通过，耗时87.63秒；**所有投影字段均与原保存快照一致**。
- epoch格式、来源表示选择、不可匹配前态、旧schema upcast、SQLite/内存账本、普通文本凭据、宿主配置和可见来源等组合：151 passed。
- 使用冻结发布包代码，在真实生产副本连续进行两轮Capture聊天，均成功发出，整轮约7.05、6.41秒。未向真实QQ发送测试消息。
- 两轮之后再用该发布包完整冷重放，90.90秒，hash一致，ReplayEvaluator无finding。
- 普通文本新模式前一轮隔离对照为6/6发出、首条中位4.10秒；上述真实生产规模结果更慢，不能用小账本结果承诺线上全部低于5秒。

## 切换记录

- 旧生产包：`~/Library/Application Support/Girl-Agent/releases/c9429779`。
- 新生产包：`~/Library/Application Support/Girl-Agent/releases/sampled-text-20260929-55677eea857f`。
- 活跃启动器：`~/Library/Application Support/Girl-Agent/run-production-napcat.sh`。
- 活跃模式配置：`~/Library/Application Support/Girl-Agent/production-text-policy.env`。
- 生产账本仍为原`data/companion.epoch2.sqlite`；没有换World、清空历史或重写事件。
- 先卸载旧launchd作业，再对停止写入的账本做一致性备份。切换时备份头为主World序号24385。
- 第一份包`619f4c152bb0`遗漏`prototypes/pixel-home`静态资源，服务启动报错。已停止重启循环、补齐资源构建第二份包后启动；没有用该静态页面添加角色移动功能。
- 新进程检查时PID97271，主模型`deepseek-flash`、expression episode为off；模式文件明确为sampled，生产`companion.epoch2.text-observation.sqlite`已建立。生产新模式尚无人工QQ测试消息，因此不声称已量出真实QQ交付时延。
- `/dashboard`和`/openapi.json`返回200；`/health`可返回，调度器running并有已完成轮次。

生产启用的配置：

```dotenv
WORLD_V2_VISIBLE_EXPRESSION_PROFILE=grounded_review_v25
WORLD_V2_EXPRESSION_EPISODE_MODE=off
WORLD_V2_ORDINARY_TEXT_REVIEW_MODE=sampled
WORLD_V2_TEXT_REVIEW_SAMPLE_EVERY=10
DEEPSEEK_MODEL=deepseek-flash
WORLD_V2_VISIBLE_SOURCE_REVIEW_MODEL=deepseek-flash
```

项目`.env`没有修改；模式覆盖只作用于NapCat生产启动器。普通文本后台约1/10抽样，最多12次观察/UTC日；观察失败不会卡住回复，相关记录不是新的角色事实。

## 当前残留与回退

健康状态仍为degraded。启动后的后台`world_stimulus_appraisal`出现`attended_source_refs`超过8项等格式失败；旧失败身份也有不能重建的记录。不能将这些技术失败当成角色沉默或视为所有生活机制恢复。主要剩余工作是后台格式/恢复处理及生产规模本地耗时；生成仍会编造未记载经历。

回退生成模式时，先把独立模式文件的`WORLD_V2_ORDINARY_TEXT_REVIEW_MODE`改回blocking，再重启同一新包；这样保留已经写入的新模式凭据和消息。旧包、旧启动器及切换前数据库保存在本机备份中用于进一步恢复，**不自动用旧库覆盖切换后新收到的消息**。

## 证据路径

`output/private-audits/replay-compat-20260929/`：

- `head-facts.json`、`replayed-facts.json`、`genesis.json`：只在本机保留的来源对照。
- `full-replay.json`、`staged-cold.json`、`release-tests.log`：完整重放与回归。
- `release-manifest.json`：冻结包文件哈希及原Git head；明确包含既有未提交工作，不伪称一个已提交release commit。
- `staged/result.json`：冻结代码在真实生产副本的两轮聊天。
- `cutover-plan.json`、`cutover-backup/`：实际启动器/配置、一致性账本备份。
- `activation-result.json`、`live-health.json`、`live-operator-snapshot.json`：上线检查与仍存在的降级状态。
