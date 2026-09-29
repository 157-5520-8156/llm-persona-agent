# 自传体记忆小实验：V4.1直接提示对照

2026-09-29。仅用明确标记的合成角色和材料；没有覆盖、导入或修改生产角色前史，没有写生产World，没有连接或重启8787，也没有给QQ发送消息。

## 实验与材料

合成材料有3个人生时期、12个日常片段和3个详细片段，包含平常、愉快、不快、关系与物件，也把事件、当时理解、后来理解和未知细节分开。它不是已审阅的前史包或World事实，不能进入任何角色账本。材料在[synthetic_luna_autobiographical_memory_pilot.json](../../fixtures/world_v2/synthetic_luna_autobiographical_memory_pilot.json)。

通过项目的DeepSeek适配器直接调用官方`deepseek-flash`（V4.1），非思考模式、temperature 0.7。共10个真实API调用：两道记忆回忆题各测flat和layered；两个当前选择题各测no-memory、flat和layered。选择题是含糊约定和温和纠正，提示明确允许角色采用任何自然反应，不要求焦虑、追问、拒绝或认错。请求、原始响应、用量、错误和哈希保存在本机私有目录`output/private-audits/luna-memory-pilot-20260929/`，目录权限700，文件权限600。

这次flat/layered对照有一个重要混杂：flat保留了内部期段ID，但没有人可读的期段名称、记忆ID、深度和类别；layered提供了这些元数据。事件和理解文字相同，但表示条件并未完全匹配。因而本次不能把措辞、细节或输出差异归因于分层本身，也不能把flat条件返回的记忆ID视为绑定过的来源。已执行请求的脚本哈希为`46339ae5fd8fb9994cbc72bd2f7b30c56d5c923944077d559df40c6a35469138`，请求和响应各自哈希见私有`integrity.json`。保留此次真实结果；后续若重测，应把全部元数据在两组中对齐，只改变呈现结构。

## 观察结果

两道回忆题的flat和layered响应都找到了目标事件、主要行动结果及后来的重新理解。学校等待那题的flat响应用了来源未给出的女性复数代词，按本项目宽松中文语义记为小的身份细节偏差，不作为实质编造；layered响应没有这一处。布展题的flat响应比来源多了一点“被接住”的感受性表达，layered响应更贴近原材料。样本仅两题，而且对照有上述元数据混杂，不能得出分层更忠实的结论。

在含糊约定题中，两个有记忆条件都自报使用十五岁等待记忆，并把“约定含糊”纳入当下解释；它们都选择询问更具体的见面时间。no-memory条件也表达了周末有空，只把时间留待之后再定。因此本轮显示了一条提示级的“旧经验进入解释并轻微收紧当前选择”迹象，但无法排除不同元数据的影响，也没有真实CharacterInterior Appraisal或行动提交作为证据。

温和纠正题中，三个条件都没有提取记忆ID，也都选择核对营业时间。旧记忆没有被强制带入，情绪和回应也没有被固定成防御或认错。结构化JSON内的salience、appraisal与choice字段是模型自述，不能当作被系统接受的持久状态。

## 系统读取边界

另做了不写账本、不伪造审阅的最短离线边界复现：一段649字符的合成前史按当前默认480字符前缀读取时，中段和末尾的两个识别细节都不可见，记录见私有`source_read_boundary.json`。当前[MemoryRetrievalCompiler](../../src/companion_daemon/world_v2/memory_retrieval.py:370)把前史正文切为`statement[:max_excerpt_characters]`；[RecallCorpusCompiler](../../src/companion_daemon/world_v2/recall_corpus.py:376)随后以该excerpt正文形成回忆文档，检索文本至多附加已授权的人物/地点标签。离线复现只证明字面截断造成的可见性限制，没有运行真实来源闭包、候选选择、向量检索或最终作者，也没有证明中后部细节会进入或不能进入生产作者输入。

本轮没有改`src/`、没有创建隔离World或持久Appraisal，没有做CharacterInterior初始化/保留、来源审阅、聊天投递、重启或冷回放。它是直接提示实验与只读代码边界复现，不是实际系统接线验证。

## 调用与费用

10次均成功，没有技术失败。provider回报9,456输入token、1,632输出token，合计调用延迟16.128秒。请求时间为北京时间13:40左右，处于官方所列闲时段。按官方当前闲时非缓存输入¥1/百万token、输出¥4/百万token估算，上限约¥0.015984；API适配器暴露的用量没有缓存命中/未命中拆分，因此实际账户扣费未知，不能写成已核实账单。[DeepSeek官方模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)。

运行入口是[run_luna_autobiographical_memory_pilot.py](../../scripts/run_luna_autobiographical_memory_pilot.py)，源边界复现为[probe_luna_memory_excerpt_boundary.py](../../scripts/probe_luna_memory_excerpt_boundary.py)。两者通过`.venv/bin/python -m py_compile`语法检查；没有跑测试套件。本次没有调用BGE-M3，因为实际试验只比较直接提示，没有进入召回索引。

## 结论与下一步

小样本支持继续研究记忆如何参与选择：含糊约定下旧经验影响了模型自述的当前解释和措辞；温和纠正下角色没有采用相关旧记忆，仍选择核对。当前结果不能证明分层表示优于扁平表示，也不能证明生产记忆链路已联动。中后部细节的生产读取问题仍未闭合。下一次先做同元数据、只变结构的无付费离线提示校验，再决定是否值得一次真实V4.1复测；扩大生成或接入生产前，需独立验证有来源记忆的保留、检索和作者输入。
