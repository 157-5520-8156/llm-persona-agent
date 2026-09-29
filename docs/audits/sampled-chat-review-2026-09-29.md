# 一次生成与后台抽样评估：实现和切换验证

2026-09-29。用户已明确授权：容忍不改变事情本质的口语近似，验证普通聊天一次生成、后台抽样评估，并在条件满足后替换生成路径。

**状态：新模式代码和隔离链路已完成；没有切换正在运行的生产包。生产旧账本的完整重放未通过，真实生产副本的延迟也尚未达到5秒。** 默认配置仍是blocking，测试通过显式sampled配置启用；生产`.env`、启动脚本和进程未改动。

## 实现

- 增加`WORLD_V2_ORDINARY_TEXT_REVIEW_MODE=sampled`，依赖v25作为其他候选的原审核路径。纯文本reply/followup以及角色自己的Appraisal/Affect可以一次生成直接进入正常验收/发送；其他动作或事实、经历、记忆等变更仍走原审核。资格由类型和能力决定，不读取关键词、动机，也不把`world_claims=[]`当作无事实证明。
- 新的`visible-source-text-observation-required.1`固定在原始角色能力和来源上下文里。输出留下`semantic_review=not_performed`的来源/作者/候选哈希凭据，不制造LLM已审通过的结论。原审核字段承载政策凭据哈希，不应据其非空推断语义已经通过。旧政策与回执继续保持原含义。
- 保留结构、引用、权限、发送授权、预算、effect-once、回执与重放校验。结构不合法仍允许原来的一次同角色受约束重选；技术失败不解释成角色沉默。
- 后台观察存入独立`.text-observation.sqlite`，只在对应文本获得平台ACK或送达回执后开始。**ACK不等于已读或最终送达**，这个条件仅用于把质量观察移出发出之前的路径。
- 默认约1/10稳定抽样，每个World每天最多12次观察调用（UTC日）。SQLite原子领取避免重复调用和跨实例超额。未抽中、待观察、失败、中断、未观察过期与观察结果分开记录；失败不默认为通过。前台入队锁等待上限50ms，失败只报告观测缺口。
- 后台任务使用空的contextvars Context启动，不继承作者调用ID、截止时间或前台预算作用域。它不重写、不撤回、不重发消息，不产生角色Fact/Experience/记忆。健康信息可区分抽样数及none/minor/major/uncertain结果。
- 观察规则接受日常非精确承诺语境中“一部分→一半”等口语近似，重点标记新增具体事件/人物/共同历史、实质矛盾；没有给角色增加固定拒答话术。

ADR：[0020](../adr/0020-sampled-ordinary-chat-review.md)。新模块为`ordinary_text_observation.py`和`text_shadow_observer.py`，沿既有作者、验收和发送接口接入。

## 真实V4.1对照

保持V4.1 `deepseek-flash`、配置BGE-M3、相同冻结源账本。每题独立克隆，无QQ外发。阻塞组沿用v25；普通文本新模式不调用前台语义审核。

| 范围 | Capture提交文本 | 首条中位数 | 前台调用 | 估算费用 |
|---|---:|---:|---:|---:|
| 新模式12题 | 10/12 | 4.10秒 | 13 | CNY0.2529 |
| 其中与旧模式配对的6题 | 6/6 | 4.10秒 | 6 | CNY0.1172 |
| 旧阻塞模式，同样6题 | 4/6 | 6.76秒 | 17 | CNY0.3863 |

以上费用不包含另行执行的后台观察，不是每日成本。Capture显示发送提交，不冒充真实QQ交付。样本小且只有一个旧世界状态，不是统计上的质量非劣证明。

新模式未发出的两题涉及私人状态来源引用不合法：pet最终为invalid_role_result_after_correction；shared_past发生private_turn_state.unpinned_source。它们属于结构/来源失败，未改成虚构台词或放宽引用校验。

质量仍有问题：新模式的毕业题编出“有人从楼上撒卷子、自己在走廊看”；旧模式同题编出“教室撕纸、抱书等雨、雨没停就走”，也通过了阻塞审核。不能说关闭审核已经把编造率降到5%，也不能把“一半”这种口语表达与整段新增经历混为一类。

## 后台观察实际验证

- 独立回放casual与childhood两份实际发送候选：casual判none，childhood判major，明确指出毕业场景没有来源。角色World数据未修改。
- 另通过实际QQ宿主组合安装的观察器调用了一次，普通聊天判none。角色的Fact、Experience、MemoryCandidate、Appraisal、Affect、私人印象及角色提案均未变。宿主独立完成原有ACK的送达确认，追加18条回执/预算/终结技术事件；没有新增ActionAuthorized或角色提案，不能把这段宿主确认活动算成观察器创造角色经历。
- 独立两份观察估算CNY0.14838；已安装组合那次CNY0.0019，缓存条件不同，不能据后者外推日价。后台观察当前发送完整固定来源，冷输入成本偏高，未来可进一步压缩证明元数据；本轮没有隐瞒这部分费用。
- 隔离新模式候选冷重放一致，零ReplayEvaluator finding，未审核文本的来源凭据重新核验成功。

## 真实生产检查与未切换原因

实际8787进程PID6452运行固定包：
`~/Library/Application Support/Girl-Agent/releases/c9429779`，由
`run-production-napcat.sh`加载。它不是当前工作区的新代码。进程检查时CPU约99%，不能仅改变新工作区配置便宣称线上已切换。

对`data/companion.epoch2.sqlite`做只读备份，生产World为`world:companion-v2:qq-c2c:geoff`，副本头为ledger_sequence 24374。当前代码可以加载其快照，但从事件历史完整重放失败：

- 事件：`InteractionFactDecisionRecorded`
- ID：`event:interaction-fact:decision:229564869e615fd56a930066722fa3a4f18111bee5ef51b470842a1a920aa9bf`
- 时间：2026-09-25T19:12:32.308734Z；位置22158
- 原因：`interaction Fact decision changed its exact Fact source context`
- 该事件为`fact-observation-draft.4`的no_change。原记录和代码预期的Fact上下文哈希不一致。完整根因仍待查，**不能据此断言生产数据库已损坏，也未通过修改历史或略过校验“修好”它**。

还在该真实生产副本跑了一轮新模式：一次作者调用、成功Capture提交，首条8.43秒。其中模型完成约2.33秒，上下文约1.06秒、账本提交累计约1.42秒；分段存在包含关系，不能简单相加。测试时同时有重放负载。这说明小验收账本的4.10秒不能直接作为当前生产延迟。

因此当前不满足安全替换固定生产包的条件。待历史重放兼容问题修复，并完成真实生产副本的启动/交互复验后，再应用已授权的模式切换；无需重新争论是否保留普通聊天阻塞审核。

## 检查与配置

相关最终组合109项通过，覆盖旧来源回执、v25、宿主构建、直接文本凭据篡改、媒体/事实/经历/记忆类型排除、观察失败不阻断、原子观察配额及冷重放。不是项目全量测试通过。

候选切换配置保存在私有实验目录`candidate-activation.env`，**未应用生产**：

```dotenv
WORLD_V2_VISIBLE_EXPRESSION_PROFILE=grounded_review_v25
WORLD_V2_EXPRESSION_EPISODE_MODE=off
WORLD_V2_ORDINARY_TEXT_REVIEW_MODE=sampled
WORLD_V2_TEXT_REVIEW_SAMPLE_EVERY=10
DEEPSEEK_MODEL=deepseek-flash
WORLD_V2_VISIBLE_SOURCE_REVIEW_MODEL=deepseek-flash
```

先部署支持这些契约的新包，再应用配置；不可直接把它用于固定旧包。回退新模式时设`WORLD_V2_ORDINARY_TEXT_REVIEW_MODE=blocking`，旧凭据仍按各自原政策重放。

## 证据

`output/private-audits/sampled-chat-20260929/`：`direct/`、`blocking/`、`summary.json`、`observer-results.json`、`installed-observer/result.json`、`sampled-cold-replay.json`、`production-copy-check.json`、`replay-failure.json`、`live-copy/`、`final-tests.log`。生产备份和聊天内容只保存在本机私有输出目录。
