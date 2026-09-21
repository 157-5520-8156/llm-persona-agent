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
这只是schema字节差，不能宣称等比例token、延迟或费用下降。现成引用压缩仅能再省
157字节，未为此增加额外表示。

## 真实复验结果

冻结`fff6a8d3`，从`after-source-fix/`的1027/413及1793/1790/74完整账目继续。
真实12次调用均known，新增估算0.29235522元；零新增unknown、零初始化、无真实QQ或部署。
唯一运行及计费继续点为`output/private-audits/source-model-cost-20260921-01/after-contract-fix/world.sqlite`：
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
完整原稿纠正及新JSON出口接线正在处理，尚不算已通过真实复验。
