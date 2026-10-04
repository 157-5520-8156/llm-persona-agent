# 对话恢复读取与延迟计时

本轮沿用发布集成工作树，未部署或发送真实 QQ 消息。全天副本仍使用既有进程 exec session 54594，没有重启或减少到期机会。

## 保留的修改

1. `_AuditRecoveryLedger` 增加已有按 Action 查找回执接口的只读转发。先检查完整请求 cursor 不超过原审计 prefix，再交给 SQLite 原方法逐事件验证 owning commit、World 与 cursor；旧适配器没有此接口时返回 None，保留 RecentDialogueCompiler 的普通验证读取。不暴露写接口或当前 World 头。
2. 纯文字、没有 reaction/sticker refs 的 `compile_inbound_surfaces` 直接返回原来的空元组，不解析它根本不会使用的 QQ 表情 YAML。存在任何表情/贴纸 ref 时仍加载、验证原目录并遵循原 unmatched 规则。
3. PinnedTurn 的 Context/model/record 日志分别记录实际阶段持续时间，另列 elapsed_since_start_ms。原 value 使用同一个起点，原来 model_ms/record_ms 不能相加，也不能将 record_ms 当作数据库写入单独耗时。修改仅涉及计时和日志；没有调整执行、等待或超时。
4. 全天测试按完成命令保存 `latency-NNN.json`，来自宿主的原生单调时钟 `latency_samples()`，保留上下文准备、供应商、来源闭包、candidate 验收与可见结果的阶段证据。虚拟生活时钟不能替代真实延迟。现有正在运行的进程不会自动加载这一新输出。

## 对照与回归

同一冻结数据库，每个测量重新打开 Ledger，避免旧路径预热新路径：审计恢复对话读取 old/new 两组分别 255/154ms、194/160ms。253 条对话及 121 条回应水位完全相等，包含正文、来源、回执状态与顺序。它主要改善未预热的恢复读取；共用已热 Ledger 的另一次对照仅 20/18ms，不能外推为热聊天有 100ms 收益。

84 项 PinnedTurn、原生 latency、表情目录、无文字对话、回应水位、审计 Context 恢复与 Ledger Context 测试通过。恢复测试覆盖原 cursor、未来 cursor 拒绝、旧适配器 fallback、不可见后来预算与模型审计、原 Context 字节相等。表情测试验证纯文本不依赖目录，而真实表情仍在目录不可用时失败。Ruff/diff 检查通过。

私有对照：`output/private-audits/audited-dialogue-reads-20261004/`。

## 没有采用的细小优化与失败诊断

timezone validator 叶节点过滤仅在 20 次投影验证中约 138→112ms，约每次 1.3ms；没有修改生产校验器或添加额外缓存。证据在 `output/private-audits/timezone-validation-20261004/`。

一次真实带剖析聊天用于更新旧热点定位。嵌套 cProfile 在来源回执验收阶段报 Another profiling tool is already active，故此试验没有完成发出，不能作为 <5s/质量/成功率验收。保留一笔 V4.1 调用约 0.0093644 元及全部输入、原输出、阶段数据和脚本错误。费用失败不被记为零，脚本失败也不冒充角色沉默。证据在 `output/private-audits/inbound-stage-trace-20261004/`。

该剖析中记录的 Context 2.48s、model 5.97s、record 6.27s 均是累积值，结果记录增量约 0.30s；不能认定仅写入花了 6 秒。剖析本身还会放大本地阶段耗时，并与另一完整副本并行运行，因此只用于定位，不能作为普通运行延迟。

## 剩余

现有完整日副本尚未结束；截至本轮已记录的八条聊天首条捕获都 >5s，结构/API 成功不能证明事实内容或回答充分。实际新增开销包括已经接通的记忆、生活评估、生活来源审阅与主动联系，不以“旧错误基线没有调用”为由删除它们。仍需完成日费和质量验收，并在统一时钟与数据条件下利用原生段计时确认主要瓶颈。未宣称 1.5 元/天、5 秒或生产资格达标。
