# 生活输出约束与实际传输修复

范围：用户同意继续优先生活连续性与速度/成本，近况语义仍暂缓。基于上轮保存的真实
失败请求修复确定性接口，不更换角色、不补造生活事件、不放宽来源或本地校验。

## World 环境字段

实际 `e54ac…` 输出为合法 JSON，且通过原 tool.2 schema，但环境四字段全 null，
本地模型要求至少一项非空；这是两端结构约束不一致。

`d3f1bdce` 的新 tool.3 从 canonical 环境字段构造完整 `anyOf`，字符串用 `pattern`
表达非空。环境整体仍可为 null，没有强制补天气或光照。旧 tool.1/tool.2 原 schema
及哈希保持，历史纠正恢复其原工具。新的紧凑工具定义为18,066字节，原 tool.2 为16,400。

82项相关回归通过；独立检查实际坏输出为 old2接受/new3拒绝，环境整体null以及各
字段分别为非空或单独换行均合法。固定旧工具forced/auto字节保持。数组数量、对象
local_ref唯一、来源子集及位置值匹配仍由本地校验，不能用native schema代替事实权限。

此方案使用[DeepSeek 官方工具文档](https://api-docs.deepseek.com/guides/tool_calls/)
支持的严格对象、anyOf、pattern；其不支持数组minItems/maxItems及字符串minLength/
maxLength。JSON Schema的组合语义参见[标准说明](https://json-schema.org/understanding-json-schema/reference/combining)。

## Appraisal 传输

实际初稿786b…及纠正d5c…都未带strict，因而此前不能把未转义引号归因于“严格模式
仍不保证JSON”。纠正携带完整且hash绑定的原输出，未发现其证据丢失，不改纠正布局。

新DeepSeek请求复用现有严格schema投影和beta路由，采用显式v2工具/result封装；
standard v1原字节保持。240项相关回归通过，含真实适配器的beta URL、一次纠正、
原始错误字节保留、SQLite恢复及禁止重编旧工具的v1恢复。新封装仅用严格JSON解码，
不修补引号或打捞片段。已保存的
LifeSourceView仍按原provider_controls_json恢复，不重新编译为新工具。

同一真实manifest及48个source tokens的工具定义：v1为12,719字节，v2为8,460字节。
后续真实400证明v2投影不合格且漏了affect分支，该字节差不能当作有效压缩或token、
延迟、费用改善。现成引用压缩仅能再省
157字节，未为此增加额外表示。

## 真实复验结果

冻结`fff6a8d3`，从`after-source-fix/`的1027/413及1793/1790/74完整账目继续。
真实12次调用均known，新增估算0.29235522元；零新增unknown、零初始化、无真实QQ或部署。
该阶段运行及计费点为`output/private-audits/source-model-cost-20260921-01/after-contract-fix/world.sqlite`：
1141/revision468，ledger05:08:01Z（operator05:11Z），1805 usage/1802 reservations/74既有unknown。
冷重放hash为`ab002de218364933dadd85691a9636bbfcc9ebb57300ca0ddb0dae09acde57c4`。
原4份v22回执字节不变，新1份也通过冷重建；独立对账与原生用量匹配。

新模拟入站“我想试试用蓝色画树叶，你觉得呢？”正常替代旧待回复机会，没有手改队列或
跳过退避。角色交付3条，正文与作者输出相同，均有捕获终态回执：
“可以啊，蓝叶子挺好看的”；“秋天本来就该有点不像叶子的颜色”；“你画就是了，画完给我看”。
4次聊天关键调用估算0.05818958元，加交付后interaction fact判定共0.05953650元。
整轮scheduler step为17.7522秒，包含前后调度；本次没有独立首交付墙钟，不能称首条
消息恰好耗时17.75秒。作者5.049秒、两位meaning reader并行4.739/1.389秒、source
审核3.670秒，不能直接相加作为用户等待。

仅作算术情景：若900轮都与这单轮相同，聊天加fact约53.58元，100元目标剩46.42元
供后台、初始化和重试；这不是月费预测或生产默认成本。7次后台调用本轮共0.23281872元。

生活新增ActivityStarted(seq1125)，没有新WorldOccurrence、Experience或Memory。
World tool3确实发出了4次新请求，均strict且实际admissions记录beta路径；环境全null错误
未再发生，但仍有不同阻塞：

- seq1127草稿通过本地与native；来源审核第一次因reason长2540超过2000而需要纠正，
  第二次判unsupported。这里的main_invalid_recovered只是审核格式恢复，不是生活接受。
- seq1132同作者来源纠正的原生arguments末尾多一个右括号，完整HTTP200返回且非截断。
- seq1137新初稿JSON/native均合法，但NPC标签缺少narrative:前缀。
- seq1138纠正修好了标签，却把视觉地点写成services，和proposal的campus-path不一致。

结构纠正已有错误规则及路径，但缺少原稿和具体坏值；来源纠正则有完整原稿。不能说
错误类型反馈丢失，也不能把上述故障都归因于schema。Appraisal v2本次根本没有触发，
其真实provider验收仍待完成。

operator在一次长wait结束才收到结果，因此04:58失败后仍自然执行了05:08重试；不把
它描述为每次调用一失败就停止。进程最终主动停止、客户端关闭，对账和冷回放完成。
证据为同目录`after-contract-fix-reconciliation.json`及`after-contract-fix-inspection.json`。

## 后续最小对照

`cdd57a20`为捕获工具增加与虚拟生活时钟分离的真实时间记录：每次入站接收、返回、
drain完成以及每条捕获消息均可关联。记录只在评估输出，不进入World或输送回执；
41项时钟、运行边界和续跑检查通过。这只能测capture适配器，不能冒充真实QQ延迟。

四次World请求都携带约16KB canonical输出契约和约18KB native schema，实际输入
约5万token。现有JSON-object通道可保留完整canonical契约，省去native schema；后续
仅对新请求显式选择该已有通道，旧工具恢复不变。一次有限对照后再判断，不默认切换。
完整原稿纠正和显式JSON入口已实现；默认auto不变，没有增加重试或配置层。
原稿、hash、错误和原pin存储后再请求；旧tool1/2与未标记JSON仍按原载体，切换当前
配置也不改变已保存请求。明确JSON请求若adapter或原供应商不支持complete_json则
报错，不能退回普通文本。纠正及request audit等72项通过，双向切换的6项冷恢复再过，
CLI相关74项通过。真实JSON对照仍待执行。

依据[官方JSON Output说明](https://api-docs.deepseek.com/guides/json_mode/)选用已有
response_format=json_object；仍保留JSON提示与示例、输出上限和完整本地校验。
官方提示可能空content或截断，因此不把该出口视为永不失败的保证。


## JSON入口续跑：聊天误拒与Appraisal供应商拒绝

冻结`c89efca9`从上述完整1805前缀继续，05:11模拟输入
“好，我想先画一片偏靛蓝的。你会选什么颜色？”。16次物理/240秒上限、0初始化，
每个虚拟分钟检查新失败；实际10次调用后停止，无新交付。

9次known、1次供应商400明确not_billed，新增估算0.19990700元，零新增unknown。
该阶段运行及计费点为`output/private-audits/source-model-cost-20260921-01/after-json-transport/world.sqlite`：
1815 usage/1812 reservations/74既有unknown，ledger1163/revision471，logical05:11Z。
冷重放hash为`8e0ac08c1d3acf5029d41ff60b9dd1fa4f0ec4effe164c044a3fdfe876f76490`；
原5份v22回执字节不变且冷重编通过，新增0份。

此轮没有到达World JSON生成请求，不能把它算作通道对照通过或失败。
新的首交付计时记录已生效，但没有角色交付，因此没有可报告的首条响应延迟。

两个独立阻塞：

1. 可见纠正稿“我会挑橙红／靛蓝配橙红，摆一块儿应该挺热闹／画完记得给我看”，
   v22把最后一句未来邀请误当成需要既有记录证明正在或将要画画的断言而拒绝。
   这是已确认的语义误拒；并非角色不想回复，也不是超时。本轮不再扩近况语义调试。
2. Appraisal v2新请求`c00ecfa0…`实际到达beta，供应商400明确拒绝
   `An object with no properties is not allowed.`。原参数的`properties.result`是
   `type:object`加`anyOf`、无`properties`，与拒绝直接对应。同一转换还把
   affect_transition的oneOf删除、留下discriminator，experience来源数组保留了
   不在文档支持子集里的contains。此前离线mock只验证路由，未能证明真实schema被接受。

第二项已用新Appraisal v3严格载体修复：保留完整操作判别分支、去掉冗余object类型和
不支持的关键词，本地仍守来源及角色权限；旧v1/v2原schema及hash不变，已保存请求
仍读原provider controls。8项重点检查含原400、四种情绪操作和错误结构拒绝，相关
5个文件202项通过；下一步仅单次真实格式探针，不再用完整聊天间接测供应商schema。
本段证据为`after-json-transport-reconciliation.json`与`after-json-transport-inspection.json`；完整原请求及供应商body均保留。


## Appraisal格式探针与联合类型展平

冻结`8741d56a`只发送一次新的v3格式请求，保留原失败c00…的角色材料、Flash、
温度0.8及4096输出上限，仅更换新编译工具与其身份。供应商433毫秒返回400：
`field anyOf: field anyOf: missing field type`。这是嵌套纯联合类型
`anyOf:[{anyOf:[完整操作对象…]},null]`的供应商限制；未产生模型结果或World变化。
原生请求、400响应及账目均保留于`appraisal-v3-probe/`，不把该格式算作已验收。

随后仅对v3中的纯联合分支做等价展平；有其他约束的分支不动，完整操作对象和null
都保留。9项重点检查通过，包含真实失败结构、全部Affect操作、每个anyOf分支直接
携带类型、旧v2哈希与旧保存请求恢复。v3此前只有未获接受的独立格式探针；原请求
字节不改，新编译摘要区分修复前后，不增加另一套角色协议。

独立检查也确认本轮Appraisal 400没有作者checkpoint。到期后的新技术attempt重新
编译当前工具，不会永久陷在旧v2，不需要改写历史LifeSourceView或迁移角色状态。


## 展平后的单次真实验证

冻结`3349f716`，继承上一probe全部1816/1813/74账目，使用新prepared2及新请求摘要；
原角色材料、模型、温度与输出上限不变，工具差异只含上述等价展平。
实际1次HTTP200、4.812秒，返回1份transition提案；原生schema、严格JSON解码及
canonical typed shape均通过，没有本地JSON修补。

这次只验供应商格式接纳与类型，不做来源审核、能力授权、Appraisal物化、Experience
生成或QQ交付，World所有非计费表保持原样。不能拿它替代完整生活链验收，也不能
把4.812秒当作聊天首条响应时间。没有再次运行World JSON或新的整轮聊天。
完整调用证据位于`output/private-audits/source-model-cost-20260921-01/appraisal-v3-probe2/`。

本轮工程修复和有限实测到此收口，状态仍为manual_only / qualification_incomplete。
剩余实证阻塞是普通未来邀请的语义误拒、World候选与纠正尚未形成新经历，完整聊天
速度和每月100元负载尚未通过；下一次工作不得从旧的低计费前缀重新开始。


最终独立对账通过（`appraisal-v3-probe2-reconciliation.json`）：probe2新增1笔known，
仓库精确估算0.09089224元（账本显示四舍五入0.0909），43097输入/650输出，缓存
命中256、未命中42841；无新增unknown。probe1为not_billed。两者所有非计费表逐值
不变，5份旧回执保留。

**后续唯一接续组合**：运行从`after-json-transport/world.sqlite`恢复1163/471、05:11Z；
先合并`appraisal-v3-probe2/world.sqlite`的完整**1817 usage/1814 reservations/74既有unknown**。
不能只用运行源里的1815前缀。两轮旅程加两次格式探针共24次物理调用，22known、
2not_billed，新增仓库估算合计0.58315446元；不把测试累计量外推月费。
