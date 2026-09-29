# 人生记忆联动验证（2026-09-29）

范围：GPT-6 Luna 执行实验，主 agent 复核代码与落盘证据。实验使用隔离 World 副本；没有修改生产数据库、重启生产服务或发送 QQ 消息。

## 内心决策是否读取记忆

当前 CharacterInterior 的 `experience` 与 `consider` 共用角色执行路径。初始快照可携带活跃记忆，满足条件时进行自动预取，角色还可提交 Recall 请求，再用补充后的同一快照继续判断。因此记忆并非仅供最终聊天检索。

但接口存在、相关材料进入输入、角色实际采用、经历对选择产生因果影响，是四种不同强度的证据。本轮不能证明所有内心决定都读取记忆，也不能把措辞变化或角色自述当成因果验证。

## 修复与实际证据

发现前史正文先被截为480字符，检索索引也仅收到这段前缀：后续细节根本没有进入候选。修复在已校验的活跃记忆来源上，将完整记录用于本地索引，形成最多480字符、相邻重叠64字符的窗口。完整原文不通过普通摘录序列化进入模型；保留记录哈希、档案接受与记录导入的双重来源绑定、角色及隐私边界。

主复核证据：`output/private-audits/luna-memory-integration-20260929/windowed-recall-result.json`。

- 一条992字符的隔离前史完成导入，真实V4.1调用自主保留，Memory状态为active。
- 索引生成三个窗口，起点为0、416、832；普通模型可见前缀仍为480字符。
- 实际使用BGE-M3，命中窗口416与0。目标细节不在最初480字符内，但在命中窗口416内，并成功经 Recall 上下文组装进入 `active_memory_candidates`。
- 最后832窗口未命中；不能把本结果报告成末窗命中或全档案覆盖成功。
- 上下文每条摘录仍为480字符，没有整条992字符原文注入。来源绑定随结果保留。
- 冷重放前后语义哈希及ledger sequence一致。
- 这是BGE检索与真实上下文组装验证；组装后未再调用角色完成新选择，因此不证明她实际采用或受其影响。

执行agent报告的针对性回归为32 passed，覆盖长记录窗口、旧前史来源与Memory读取：

```sh
PYTHONPATH=src:scripts:tests/world_v2:tests/support .venv/bin/python -m pytest -q tests/world_v2/test_luna_prehistory_recall_windows.py tests/world_v2/test_prehistory_memory_source.py tests/world_v2/test_memory_retrieval.py
```

另一次真实入站副本记录在 `real-inbound.json` 与 `model-inputs.jsonl`：走过角色生成、接受的Appraisal及三条捕获运输回执，输入存在与活跃Memory关联的旧Experience。自动预取当轮超时使用FeatureHash备用检索，不能称为BGE端到端成功，也不是有无经历的因果对照。捕获回执不是实际QQ送达。

## 评审与失败记录

长记录是检索边界夹具，不是人生质量样例。DeepSeek创作审阅拒绝记录保留；没有将拒绝改成批准。后由独立GPT-6 Luna依据实际年龄、日期、已有背景和World边界做operator一致性评审，限定批准隔离夹具用途，并以报告哈希绑定包。评审见 `luna-long-record-independent-assessment-2026-09-29.md`。

实验曾在角色保留后因审计脚本误用ProjectionCursor而失败；之后复用已有副本进行检索检查，没有因此重复保留调用。一轮早期审阅的原始用量未成功保存，费用未知，不能将其计为零。其余usage见捕获文件；本轮没有得到完整供应商账单，不报告完整实验总价。

已知用量：三次语义审核合计11,228输入/1,297输出tokens；聊天主调用38,751输入/229输出tokens；单次保留调用捕获为18,617输入/404输出tokens（含512缓存输入）。BGE使用记录为三次成功请求、31,253估算tokens、估算¥0.004500432；这是记录口径，不是本轮所有调用的完整新增账单。备用聊天候选和丢失用量请求等仍须区别于这些已知计数。

## 规模扩展与剩余范围

新增32条创作候选，覆盖六个人生时期、十组重复经历目录，含11个历史人物实体。已修正日期/年级冲突、重复故事和行动顺序，删去混入人物感受的审查口吻；普通片段允许没有后来感悟。

`fixtures/world_v2/luna_life_history_expansion_unreviewed.json` 已通过现有PrehistoryArchiveDocument及manifest契约校验，确定性导出脚本为 `scripts/export_luna_life_history_draft.py`。它仍未完成独立内容审核、World导入或角色保留，不能与现有生产12条相加宣称已有44条运行记忆。

尚缺：扩大候选档案的独立审核与导入；有/无特定经历、其余条件一致的真实内心决策对照；在更大混合语料下的检索覆盖率及成本测量。当前单条夹具没有证明长期人格连续性、遗忘压缩质量、聊天编造率或发布资格。
