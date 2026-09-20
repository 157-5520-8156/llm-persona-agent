# 本地语义检索对照与Life事实权限连接（2026-09-21）

状态仍为 **manual_only / qualification_incomplete**。本轮完成离线真实向量对照，
以及显式可选的Life审核合同修复。未运行新的付费probe或连续旅程，未切默认检索或审核。
目标工具当前返回paused，已有工作在此收口；不自动继续付费测试。

## 对照的依据与方法

[Anthropic的Contextual Retrieval](https://www.anthropic.com/engineering/contextual-retrieval)
建议结合词法与语义检索，评估重排及上下文组织的质量／延迟取舍；
[BGE-M3模型说明](https://huggingface.co/BAAI/bge-m3)同样建议混合检索与重排，M3查询无需
额外指令前缀。[Google的Sufficient Context研究](https://research.google/blog/deeper-insights-into-retrieval-augmented-generation-the-role-of-sufficient-context/)
则区分证据不足和有证据仍答错。这些提供诊断方法，不保证本项目零虚构。

复用上一轮4个原cursor及完整corpus，资格过滤、文档、5500 dense门槛、融合权重和预算
保持一致。4个查询×3种查询／关联组合×2种embedding，共24组：
原attention／原会话关联、原attention／去除公共关联、当前用户原句／去除公共关联。
不同模型在同一查询版本内比较；缩短查询是单独变量，不把其改善全部归于换模型。

使用本机已有`mlx-community/bge-m3-mlx-8bit`缓存，1024维normalized CLS，固定8192
最大长度；21个不同输入实际最长509 tokens，均未截断。模型加载1.432秒，强制完成计算
与导出后累计3批2.259秒。全过程禁止网络连接，没有新下载或云端调用。该数字只描述
离线批处理，不是HTTP服务、450毫秒首稿预取窗口或端到端聊天的延迟验收。

输入hash：`8d9931c78cd2c9eaa2ae2c61dbe84e1d9e9eb8993f940db4a3de0e57b1889d81`。
向量hash：`dc278d0801c626b4e9674d6b8d3afe704253ffc260283b7a7d1279788cbb60a7`。
私有证据在`output/private-audits/release-semantic-recall-20260921-01/`：
`vectors.json`含模型／包版本和权重hash，`score-results.json`与`score-summary.json`
保存实际RecallCoordinator／InMemoryRecallIndex评分。评分阶段复用固定向量，无额外推理。

## 结果与正确的修复层

| 目标 | M3长attention | M3当前原句 |
| --- | --- | --- |
| T13 茶偏好 | cosine .5569，dense第14；入候选但未选中 | .4828，dense第1；被5500门槛拒绝 |
| T29 姓名 | .5276，dense第13；未入候选 | .4720，dense第2；被门槛拒绝 |
| T29 茶偏好 | .5693，dense第9；最终入选 | .5074，dense第1；仍因词面匹配入选 |

长attention含重复合成感想和状态JSON，干扰了这些原句的排序。只换M3仍会使T23的
负例入选从1条增至3条；原句仍有1条词面负例。全部24组资格过滤一致，无预算淘汰，
也未触发embedding失败后退回本地索引。

因此已定位到两项不同问题：自动查询组织稀释当前问题；固定相似度门槛尚未针对真实
语义模型校准。**没有据这4题直接降低门槛**，也没有把排名改善记成召回成功。
这些输入来自合成30轮fixture，不能推出真实角色长期语义准确率。

代码核对同时修正一条旧结论：`production_turn_application.py`的基础index始终使用
FeatureHash，但`recall_runtime.py`的同步与后台prefetch都会调用已配置的semantic
embedding；生产宿主可接入本地服务或远程端点。09／10审计宿主显式关闭该配置，所以
它们没有测到真实semantic路径。`config.py`的旧“automatic remains local”注释已纠正。

## Life .14：约束命题与证据之间的权限连接

`4423ca04`新增显式合同`life-source-review.14`，默认仍为.13。审核模型先写出每个事实
命题所需的来源范围与主体身份，再选择已有permission；代码检查所选来源是否能提供
该范围／主体的权限。环境存在不能凭source_owner标签变成角色亲身观察。

71项定向／邻近测试通过；最后仅增补断言后，新7项再次通过。覆盖环境事实、个人亲见、
历史情绪与当下感受、真实typed前史导入→保留记忆→Life reader、错误主体与篡改拒绝、
mock HTTP审核拒绝→同角色纠正→冷恢复。观察正例是明确构造的前史fixture，不能当作
10旅程产生了观察记录。旧合同重新生成保持原字节，未升级已发送请求或历史回执。

**限制**：命题分解、主体与时态的语义识别仍由模型完成。模型若把个人亲见错误标成
environment/general，仍可能骗过兼容性检查；测试明确保留这个反例。本修复只使权限
连接可以机械核验，没有证明真实审核的语义准确率已提高。

两臂真实probe仅完成准备：保留10的call12原.13 strict tool v7及模型参数，新.14用v8；
9个candidate／evidence字段hash一致。`release-life-claim-authority-20260921-01/`内
`probe_preparation.py`、`controls.json`、`preparation.json`可复核，尚无供应商结果。

## 恢复后应做的两项有界验证

1. 检索：独立冻结校准与留出题，含中文意译、多目标、主体／偏好更新、未知问题和回指。
   先评估原句查询与模型专属准入，再决定版本化最小改动；角色显式召回仍保留其查询选择。
   不新增框架，不用这4个已知失败题同时选门槛和宣称泛化成功。
2. 来源使用：原.13／新.14使用同一真实候选和证据做两次有界审核，比较具体命题判定。
   有改善再继续原身份旅程；新合同技术可用不能代替受支持回复、生活结果和交付证据。

最新可继续运行和累计账目仍为`release-clean-continuation-20260921-10/run`：
ledger882／revision354，1732 usage／1729 reservations／73 unknown。此轮未产生新用量。
完整聊天、QQ终态、24小时运行、录制旅程和约100元月费资格均未因此通过。
