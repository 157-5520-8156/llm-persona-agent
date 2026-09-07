# 2026-09-07 加速长期旅程测试：实现与证据

本批基线 `a1ded640`，集成于 `codex/living-continuity`。用户希望在较短时间内发现人格、记忆、
重复话题、经历后的变化、生活丰富度和选择多样性的长期问题，而不只依赖单元测试或等待生产一周。
产品意图见总纲 §12.11；方法、命令和限制见[工具说明](../design/longitudinal-testing.md)。

## 已实现

- 使用生产 `build_qq_c2c_host`，新建隔离 SQLite 世界，运行原有机会、角色决定、事实接纳、记忆、
  Action 和回执路径。仅把外部日历与等待接到虚拟时钟，把 QQ 交付接到本地捕获器。
- 生产宿主新增只读 `scheduler_wake_snapshot`；生产调度与实验读取同一份 due 收集逻辑。
  Runner 不另建事件调度类型表，也不替角色决定发言、主动联系或沉默。
- 日历推进到下一条输入、声明到期点、重启点或有界 heartbeat。同刻环境先结算，再处理用户输入。
  展示节奏、后台定时器、真实供应商超时和真实费用账期分别处理。
- 默认七天旅程包含 11 条输入、长时间未回复、用户计划更正和跨日回忆线索；两次重启保存静止检查点。
  场景只提供输入，不定义角色标准回复或必须发生的心情变化。
- 增量导出连续时间线、账本事件、模型失败、捕获交付、费用与重放证据；六维评审包保留证据引用，
  默认 `insufficient`。`assessed` 只表示已经作出有依据的判断，不表示质量合格。
- CLI 默认无网络模型夹具；真实模型需显式开关和独立 debug 凭据。单次实验预算默认 ¥0.5，
  通过已有共享预约门控制准入；预算不足保留部分结果，不伪造完整周。

## 实跑结果

实际整周运行代码为 **`f668f02e`，tracked clean**。公开 CLI 的离线模式在 HTTP 客户端构造被阻断的
条件下执行；本次 heartbeat 为 **900 秒**，不是 CLI 默认的 300 秒。与整库回归同时运行。

| 项目 | 观测 |
| --- | --- |
| 模拟区间 | 2026-09-07 00:00Z 至 2026-09-14 00:00Z，604800 秒 / 7 天 |
| 本机实际耗时 | 340.111 秒，约 5 分 40 秒；仅代表该离线夹具 |
| 输入、交付 | 11/11 输入；11 条捕获文本；11 个唯一捕获 ID 与 ActionDelivered.provider_ref 一一匹配 |
| 推进 | 706 步、876 个 WorldEvent |
| 模型审计 | 98 条 ModelResultRecorded 的 failure_code 均为 null；不是 98 次真实 API 调用 |
| 重启 | 两次前后语义 hash 相同；检查点文件 hash 复核通过 |
| 重放 | replay_hash_matches=true，findings=[] |
| 实际外部调用与费用 | 无真实模型 / QQ / HTTP 调用，¥0；pending、unknown、unresolved 均为 0 |

最后的逻辑时间为 09-13 21:30Z，窗口末尾有 9000 秒 quiet tail。它表示没有窗口内剩余 due，
不是用空事件补齐时钟。最终小修 **`bba133a7`** 之后只读复核同一静止世界：读取前后事件数与
ledger_sequence 均为 876，语义 hash 均为
`c51abe377e4c9df346750263b8b3a76a0346db5b97fa1f45ab7c44d7f582f0c0`；
所有剩余 due 晚于 09-14 00:00Z，最早为 Life ecology 的 09-14 05:17Z。
原运行 manifest 未修改，没有把 f668 实跑重标为 bba 实跑，也没有再调用模型。

本地完整产物在 `output/longitudinal-gates/`：
`fixture-week/report.md`、`fixture-week/manifest.json`、`fixture-week/timeline.jsonl`、
`fixture-week/evidence.jsonl`、`fixture-week/review.json`、两份 checkpoint；
同级 `fixture-week.stdout.log` 与 `fixture-week.final-gate.json` 保存运行及最终只读复核证据。

## 实测与独立复核发现的错误

1. **虚拟入站时间与真实 deadline 混用。** 旧日期的输入会立刻 primary_timeout。实验预算策略现使用
   同源的展示时钟换算 deadline，实际 monotonic 时间、12 秒预算及 HTTP 超时未放宽。
2. **展示等待推动世界日历、同刻输入抢在环境之前。** 分开两种时钟；后台 timer 只等待 Runner 推进；
   同刻先 scheduler/drain，防止用户输入读到尚未结算的世界。
3. **未来租约被当作后台卡死。** 在生产声明的未来唤醒点可确认时允许有界静止，仍保留 waiting 状态，
   不把 not_due 说成队列完全 idle，也不无限循环 drain。
4. **技术故障被折叠掉。** 保留实际终态值与包括已恢复失败在内的模型审计；供应商 TimeoutError 与
   实验墙钟期限使用不同错误类型。没有输出不能反推出角色选择沉默。
5. **完成条件证据来自不同状态。** Standards 复核指出关闭前 due 与关闭后回放可能不一致；Spec 复核
   指出逻辑钟到终点仍可能遗留 overdue。`bba133a7` 要求 due 读取前后及关闭后的事件序列一致，且
   无任何 due 落在窗口终点之前或同刻。新增关闭尾部和 overdue 回归覆盖；Standards 原审查者复核关闭。

## 验证范围

- 主体代码 `f668f02e`：完整测试 **6277 passed / 19 skipped / 0 failed**，482.69 秒。
  一个已存在的 Starlette/httpx 弃用警告。
- 完成条件修正 `bba133a7`：四组旅程、边界、CLI、评审测试 **52 passed**，5.47 秒；该小修之后
  没有重复整库。实际整周的最终状态另有上文只读补证。
- 冻结场景 `.93`：120 项通过，原指纹
  `92ec85bce2396318298ac53bdd5cd19b72a6ddd04e388cbef69358a1471353c3` 不变。
- 机制目录 schema 2、26 项以及平台架构检查通过；formal fixture 结构通过，
  `synthetic=true / report_status=blocked` 保持不变。
- 本批改动 Python 的 Ruff 与 git diff whitespace 检查通过。

## 尚未证明与下一步

本轮证明加速宿主链可运行，**没有证明真人感或每月 ¥100 已达标**。固定回复夹具是已知负对照，
不能评价角色人格、记忆或生活丰富度。六维仍未评审，仍为 `manual_only`。

当前实验 profile 关闭媒体、外界实时源、语义 embedding 和文本端点评估。**后续源码核对更正**：
缺少 Life reviewer 并不关闭生活事实接纳；general closure 为确定性检查，novel-origin 在无 reviewer 时
走 deterministic focused-origin，详见后续真实测试审计。实际发送给模型的完整 Context 字节尚未保证
捕获，不能从事件入库推断模型已经看过。重启 hash 证明状态连续性，异步恢复重发资格仍为 unverified。

后续优先补实际 Context 取证和与目标生产能力一致的生活审查配置，再做小额真实模型短旅程及连续周
评审。保存点已存在，但自动分叉续跑及配对统计尚未实现。每次用有证据的具体反例推动修改，不把重复
词语、固定主动次数或必定生气的规则写进角色。少量真实时间观察继续承担 QQ、并发网络和长期体感验证。

加速节省空等，不节省模型推理本身；¥0.5 是准入预算，不保证完成七天。约 ¥100/月包含测试费用，
总纲暂给 canary 的 ¥6/月信封仍需跨实验目录及生产实例汇总，不能凭新建数据库获得新的总额度。
本批全部提交保留在隔离分支，没有修改原工作区 tracked 文件、生产数据库或运行中的服务，也没有推送。
