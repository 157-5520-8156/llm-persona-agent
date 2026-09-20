# 同一身份生活续跑与检索分层定位（2026-09-21）

仍为 **manual_only / qualification_incomplete**。本轮真实运行在冻结代码2978be51，
复用同一角色09历史并先合并全部1702条累计用量，初始化0步。这里只验证隔离capture渠道，
未部署、未发送QQ、未写生产库。随后修复的代码由单独测试验证，不计入本轮真实运行成果。

## 实际生活链

私有目录：`output/private-audits/release-clean-continuation-20260921-10/`。
按原宿主调度，从03:39Z推进04:03Z，再到04:28Z；原Started的no_op未重开。
角色自主选择完成原计划，seq865接受`ActivityCompleted`，新的World请求实际携带
`author_world_consequence_v2`与8192输出上限。首稿与受约束重选仍失败，未接受新的
活动后果候选。旧环境事件另有一次`WorldOccurrenceSettled`，不是新活动后果通过。

另有一次Appraisal／Affect接受、既有Experience的记忆保留及一个PhotoCandidate。
没有新增Experience，也没有新角色聊天回执。唯一新增可见文字是明确标注的系统故障提示，
不能计作角色成功表达或角色选择沉默。旧失败聊天恢复仍包含无来源的未出门／未移动状态。
新生活结果失败后结束试验，没有追加同配置付费追问。

真实World失败分层已复核：call24完整tool2首稿通过schema与原parser；call25来源审核
返回33个未声明字段路径，超过该审核合同32上限，触发`invalid_source_closure_shape`。
call26审核格式纠正后32路径、原parser接受其不支持判定；call27 World作者重写完整返回，
却多一个末尾右花括号，原parser报`invalid_json`。这次不是8192仍截断或超时。
[DeepSeek官方strict工具文档](https://api-docs.deepseek.com/guides/tool_calls/)要求beta路径和
所有工具strict=true。call27原生request唯一tool确为strict=true；原始EOF响应中arguments
13716字符，合法对象结束13715之后多一个右花括号，非adapter拼接或本地截断。捕获没有
保存endpoint，只有冻结代码路由到`/beta/chat/completions`的证据，不能声称实际URL已独立
证明或直接定性供应商缺陷。未本地删括号通关。格式错误与来源语义错误分别记录。

另需保留Life来源审核的语义风险：call10使用`.13`却把`permission:1`等ID写成`1`，
原inspect机械拒绝，表现为`life_source_review_unavailable`，并非HTTP超时。call12同合同
通过并落Appraisal／Affect；但它将“角色在校园路上亲眼看见画桌”对应到permission:6的
环境存在来源，理由没有证明角色亲眼观察。这是尚未解决的误支持风险，不能用accepted
标签声称事实表达正确。精确claim、permission、reading及native／账目核查见
10目录的`final-independent-inspection.json`，无新增Experience或角色回执。

## 检索断点：按四个原cursor复现

对合成30轮fixture的四个实际prefetch请求，用原ledger投影与恢复reader重建corpus，
原返回文档、分数和顺序逐字段一致；没有重跑模型。四次字节预算拒绝均为0。

| 探针 | 源数据与真实断点 |
| --- | --- |
| T13 热饮意译 | 茶偏好已在22条corpus且合格；lexical=0、dense1747低于5500、structured=0，未进候选 |
| T29 姓名与饮品 | 姓名在40条corpus且合格；lexical=0、dense1065低于5500，未进候选；茶已入选 |
| T17 窗外天气 | 天气系统旧Fact仅词面“天气”重合；另外3条感想仅同会话关联入选 |
| T23 电影摄影 | 妹妹资料仅靠“最近”词面重合入选 |

公共conversation关联单独提供structured10000（加权2000），即使没有词面匹配且向量
未达门槛仍能准入。去除这个公共关联的反事实，候选数分别13→1、7→1、1→1、9→3；
**不能救回茶和姓名**，因此噪声与语义候选漏召回必须分开修。现用feature-hash-ngram，
这不是成熟语义embedding的效果上限；暂不靠降低全局门槛或添加话题关键词掩盖漏召回。
另外，当前production composition固定用FeatureHash构造基础index，可选semantic embedding
作为RecallCoordinator的独立通道；自动attention保持本地。不能把“打开semantic配置”
直接宣称已修自动预取，需要分别验证角色主动检索与自动预取两条路径。

证据在`output/private-audits/release-recall-eval-20260921-01/`：
`candidate-replay.json`、`candidate-replay-summary.json`、`reconstruct_candidates.py`。
这些案例的语义正确率仍未评定，不能称真实供应商对话通过。

## 已修复的历史读取错误

`75976daa`将SQL占位符构造移到未缓存locator集合确定之后。旧实现按完整集合生成SQL，
却用过滤后的集合传参；真实600→797 cursor读取在6条已缓存＋1条新来源时抛出
`uses9/bindings3`。原cursor副本修后成功，冷缓存、部分缓存、全部缓存及历史读取／性能
共74项测试通过。没有改来源权限、事实、缓存准入或历史trace；全程0 HTTP。
证据：10目录下`partial-observation-cache/`。

`ec58520d`将可信actor／channel会话身份从新自动检索的相关性线索中排除，共享轻量身份
函数并保持旧hash字节一致；具体event／thread线索、文档来源与主体隔离保留。四个原查询
继续精确重放，新自动查询按上述反事实减少噪声。茶／姓名漏召回仍在，没有修改排序门槛。
97个不同定向测试覆盖通过（68检索／resolver／appraisal与29恢复）；恢复测试原4失败是
fixture依赖被移除的公共会话加分，现仅该组显式提供固定本地embedding，不测试语义，
双呈现、篡改拒绝和跨进程恢复断言均保留。无真实供应商调用或新完整回归。
验证记录：`release-recall-eval-20260921-01/conversation-scope-validation.json`。

## 常规方法与下一步

[Microsoft的混合检索说明](https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking)
将词法、向量、融合和重排分开，并支持查看各通道分数。
[Anthropic的Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
采用词法与语义检索合并、去重、可选重排，并要求用实际评估选择成本与质量的取舍。
本项目的工程推论：先修确证的共同会话噪声和缓存故障，再对现有语义embedding接口做
同一corpus的对照；目标未进候选时，单换重排算法不能补足证据。无需迁移整套框架。

## 账本、停止与恢复

30次真实请求全部原生用量核验，仓库估算新增 **1.0408613元**；无新增未知预留。
operator_stopped、客户端关闭、独立对账、重启checkpoint及冷重放通过。
统一最新运行／费用继续点为上述目录的`run`：**1732 usage／1729 reservations／73 unknown**，
ledger882／revision354，04:28Z。继承3张v22回执全部冷验，新增0张。
冷重放hash：`0527998d2b5713a231729f2c72d6932afd3019d6f28ad807623143d943dc6960`。
独立对账hash：`da6a66d184f5634d4f64cad3387b0918341819c65a93308c9bd7f9684459b026`。

费用只说明该有界实验，不证明100元月费达标。再次运行必须继承全部最新账目及73个
unknown holds，不能恢复09或probe的较便宜费用前缀。完整聊天、QQ终态、24小时运行、
可录制完整旅程仍未通过，工程定向测试不能替代这些门槛。
