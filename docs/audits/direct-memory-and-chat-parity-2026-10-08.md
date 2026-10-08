# 默认直接植入与用户聊天路径核验

基线 `e7d2d4fa`，首个实现 `79297b13`，旧决定恢复修复 `76cdfdbd`。工作发生于隔离分支 `codex/role-memory-qualification`，未改生产数据库、配置、进程或真实QQ。规格见[设计记录](../design/direct-memory-initialization-and-chat-parity-2026-10-08.md)。

## 已实现

用户安装入口、原生故事导入CLI、应用启动默认直接建立来源绑定的active初始记忆，无retain/no_change模型调用。底层档案接纳仍可单独使用，`archive_only`与旧`character_select`为显式模式。正常部署加载器支持旧审核包和确定性编译包，不给编译包伪造审核收据。

所有初始来源使用相同的中性检索默认值5000，并显示 `initialization_defaults_not_character_judgment`。这是保证现有Memory检索契约可读的技术初值，不表示角色评估了八种心理意义。单独初始化事件绑定精确源、主体、策略和接受事件；角色的当下看法、详略和后续行为仍由角色模型决定。已遗忘/拒绝不复活，withhold不进入可读记忆，既有active不重写；自己的部分写入可恢复，旧角色流程的pending不被抢占。

## 找到的实际不一致

此前真实自然对话通过 `DriveSession.inbound → QQC2CHost.inbound_text → PlatformHost → WorldV2TurnApplication/CharacterInterior → Action/receipt → CaptureDelivery`，不是直接调用模型补答案。但自测显式开启了故事上下文，而普通专用聊天工具原先未开启，两者有效检索策略可能不同。旧驱动器还默认关闭了配置中的语义embedding。仅声称“调用了同一个函数”不足以证明等价。

已改为普通聊天、QQ capture、HTTP与CLI共同读取Settings中的故事开关，默认true；驱动器也默认使用已配置的embedding。明确的兼容开关仍可使用。聊天记录新增实际检索版本/初始化模式/embedding、模型与配置文件路径；驱动器记录回复覆盖和配置覆盖，不自动宣称语义成功。

普通聊天新增重复 `--env-file` 参数，可显式加载与服务相同的配置层。环境变量仍遵循Settings原有优先级。没有假装worktree中的默认`.env`等价于主仓库`.env`加部署policy文件。

## 证据分类

| 证据 | 实际经过的边界 | 能证明的范围 |
|---|---|---|
| 新CLI离线回归 | 仅替换HTTP供应商边界；正常配置加载、安装、输入、Recall、回复、投递与重放 | 用户只提问即可从Memory自动获得来源，0保留模型调用；回复文本是fixture，不证明真实生成质量 |
| 以前的两题真实自然对话 | 正常应用流程、真实模型、隔离捕获投递，但有显式配置覆盖 | 当时配置下的实际表现；许桥后续遗漏仍未通过 |
| 以前自动6项/完整35项理解探针 | 直接provider精简提示，无World授权投递 | 阅读诊断；不得充当普通对话验收 |
| 本次正常CLI预检 | 实际配置与用户消息，生成正常HTTP请求，外部模型发送前停止 | 当前请求与预算、有效配置；没有真实回答，不证明语义质量 |

新预检使用原隔离保留世界的副本，共222条前史。启动直接补入此前两条没有Memory的来源，已有保留及旧回执不改。使用主仓库`.env`和部署`production-text-policy.env`，无自测故事开关/每日预算覆盖；设置只是日志捕获、隔离数据库、ConsoleDelivery与零后台任务。用户问题沿用此前许桥问题，没有人工加入答案或正确片段。

预检要求预留CNY **0.225634**，旧共享CNY5上限剩余 **0.139907**，因此未发送外部模型请求。已知花费 **4.628753**、旧未知预留 **0.231340**、166次已发请求均未改变。没有压低正常请求预算或清除旧未知费用。另观察到本次语义prefetch未在1000ms内完成，系统转用既有本地fallback、留待角色pull；这一性能边界仍可能影响真实首次选材，本次没有模型回答可用来评估影响。

私有完整证据：`output/private-audits/direct-memory-parity-20261008/`。预检因停止在HTTP边界而记录deferred/技术失败，不计作角色沉默或成功回复。真实自由复述与后续覆盖仍是 `qualification_incomplete`。

## 检查

专项64项、入口95项、新增初始化/普通聊天15项分组通过（与专项有交叠），包括single/batch × 缓存terminal/World决定两种中断位置的4项恢复回归；Ruff与Python编译检查通过。正常CLI的31,592事件重放hash匹配。冻结76cdfdbd后的独立进程冷回放亦通过、hash一致、无findings，耗时137.78秒。全量在运行时代码76cdfdbd冻结状态下完成：**10,321 passed / 2 failed / 19 skipped / 2 xfailed**，耗时1228.96秒，原始退出码1，不把它改写成一次全绿运行。

两项失败都是旧默认假设：`test_character_prehistory`要求启动只存档，现显式指定archive_only；`test_longitudinal_demo`要求真实供应商fixture完成一次retain，现显式指定character_select。原断言保留，新默认由独立direct测试覆盖。修正仅涉及测试配置，运行时代码没有变化。两文件与新增direct测试一起复测 **39 passed**，无失败；此前存档文件单独18项亦通过。不需要重复全部已通过的运行时回归。

原始日志：`output/direct-memory-full-76cdfdbd.log`；最终复测：`output/direct-memory-final-retest.log`；冷回放：`output/private-audits/direct-memory-parity-20261008/cold-replay.json`。两项失败均已定位与复测关闭，但真实生成质量仍不因此获得合格结论。

## Standards

无可操作发现。直接初始化遵循用户授权，默认值明确未经角色判断；事实来源、主体、隐私与效果唯一性边界保留，没有伪造角色或审阅结果。未发现需单列的代码异味。结论只针对代码边界。76cdfdbd的只读sidecar恢复补丁已补充复查，无新增可操作发现。

## Spec

首轮发现1项P2：旧角色已保存retain但未创建MemoryCandidate时，direct可能覆盖其待恢复draft，或与残留proposal身份冲突。已在76cdfdbd修复：检查既有World决定和已校验hash/主体的技术terminal；单条和批量原决定均交回旧流程恢复，返回pending_existing，不调用新模型。批量只有确实含该source_id的choice才阻止直接安装，同批漏答不算完成。旧已记录no_change仍按用户新初始化策略补入。复查确认关闭，无剩余确认阻塞。

技术terminal只用于阻止覆盖，不授予记忆或World权限；实际应用旧决定仍经过原始完整lineage/import cursor校验。

两轴结果：Standards无未解决发现；Spec原1项P2已关闭。
