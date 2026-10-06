# 修复后台接线后的完整日基线

> 2026-10-06 勘误：下文“25 个 attended_source_refs 超过 32 项”的诊断不成立。逐条解析保存输出实际为 5–27 项，其中 18 条是外层 no_change 与必需 life_responses 回执状态冲突；旧纠正包无条件附加来源上限说明，误导了最初归因。原运行计数和费用不受此勘误影响。当前根因、修复与候选验证见[后续审计](role-memory-qualification-2026-10-06.md)。

原 exec session 54594 已明确 exit 0。实际 `result.json` 为 completed=true、error=null，24 个小时目标、50 个固定聊天输入、全部运行时声明到期任务已执行到终态。未重启或删后台任务；V4.1/BGE-M3、CaptureDelivery，未发送真实 QQ。

## 结果

435 次模型调用。保存的四舍五入账本合计 9.4303 元，按每笔实际 token/时段重定价约 9.43198444 元，均不是供应商银行扣款核对。预算拒绝 0，未知账单 0。

49 条输入最终有 Capture Delivered，首条中位 7.633 秒，按排序样本位置计算的 p95 9.456 秒，仅一条 <5 秒。第 50 条“晚安，你按自己的节奏过就好”没有回复，原始工具输出明确 messages=[]，模型结果 winner/proposal_validated 并接受 Appraisal：这是角色选择沉默，不能计为技术失声，也不能当作发出成功。它的私下时间理解仍需单独质量判断。后续同 correlation 的后台 primary_invalid 不反向把这个自主选择改为技术失败。

其他回复是否充分或忠于来源仍未全部验证，49/50 发出不等于质量合格。已发现的生日归属/截断问题依然保留在质量清单；hybrid.14 父阅读尚未默认启用。

后台失败：invalid_role_result_after_correction 27、source_review_exception 3、main_invalid_output 23、corrective_invalid 9、primary_invalid 3、life_source_review_uncertain 6、life_source_review_unavailable 3。来源判拒与技术错误须结合原候选及具体原因判断，不能全称为 hallucination 或角色 no_op。

生活实际触发：19 个 WorldOccurrenceCommitted、18 个 Settled；3 个 ActivityStarted、1 个 Completed、2 个 Abandoned；119 个 MemoryCandidateReviewed、11 个 Forgotten；16 个 ExperienceMemoryDecisionRecorded、61 个 AppraisalAccepted、4 个 RelationshipSignalAccepted、1 个 NpcStateChanged。单次虚拟日不能替代长期稳定或 QQ 资格。

## 费用分布

| 用途 | 次数 | 重定价 CNY |
|---|---:|---:|
| 生活作者 | 52 | 2.1684 |
| 角色生活来源审阅 | 47 | 1.6101 |
| 用户对话 | 52 | 1.3802 |
| 世界刺激评估 | 89 | 1.3115 |
| novel-origin 审阅 | 23 | 0.8176 |
| 抽样 shadow 评估 | 8 | 0.6066 |
| 记忆整理 | 33 | 0.4277 |
| general 来源闭包 | 37 | 0.3203 |
| 来源重写 | 12 | 0.1910 |

其余费用保留在完整 usage 与目的分解中，没有把实验监测、重试或生活机制删去后声称达标。

完整相同 435 调用/输出量假设全部输入都缓存，理论仍约 1.61516438 元；仅输出约 1.375192 元。**仅优化缓存不足以让这一完整执行链达到 1.5 元。**还要减少可避免的技术失败和不必要的输出/重复输入，保持角色语义内容、来源验证与原机会频率。不会用输出截断或减少生活获得低费。

从实际纠正请求定位的一个明确问题：world_stimulus_appraisal 多次 attended_source_refs 超过 32 项。30 个提取到的 memory/stimulus 纠正包中，25 个属于这个格式上限；另有全批选择、遗忘同批引用和非逐字引文错误。尚未修复这些新基线错误；保留原请求而不截断模型选择。

## 串行候选

依赖 exec session 20019 已确认 baseline completed、50 inputs、无 unknown/denial 后启动当前集成候选完整日。目录 `output/private-audits/daily-workload-20261004-world-prefix/`，provenance 明确 world_author_cache_stable_prefix=true；同一个 frozen.sqlite SHA256、World、24 目标、50 输入与到期机制，未与基线同时执行或改父阅读默认。

当前候选仍运行，早期金额不能作完整日节省；第一次作者输入已有预热状态，费用比较还需检查 cache 命中和质量、生活量变化。候选代码包含之前已验证的纯本地优化，所以结果是集成候选验收，不宣称前缀单因果效果。

完整原始证据：`daily-workload-20261004-corrected-geoff/`；分解：`daily-cost-sensitivity-20261004/complete-baseline-breakdown.json`；纠正：`day-correction-localization-20261004/packets.json`。目标与发布资格未达成，生产配置不变。
