# 第 14 批：启动器在首个物理发送前中断

固定版本 `1d87a9e17c14de2bd15c510014ba270438fee8b2`，独立数据库、v3 和完整正文审核。
本批退出码为 1，未重启。没有模型响应、已接受计划、来源审核、交付或 host 重建，
不能作为 v3 供应商资格或角色沉默的证据。

## 直接原因与实际边界

新增的 `scenario_limits()` 在每次发送和收尾校验时，重新使用包含输出目录新鲜性检查
的 CLI parser。准入后真实输出目录已经创建，parser 因此抛出 `SystemExit(2)`。
这是测试启动器错误；此前只执行 prepare 的离线检查没有覆盖这个运行中状态。

原 SQLite 已接受推进到第 5 分钟的 `ClockAdvanced`，但对应 operator checkpoint 未完成。
首个 day-open 请求进入冻结的 `BeforeSend.campaign.verify()`，在 send 计数增加和内部
transport 调用之前失败。捕获到一份本地请求及 `failed / SystemExit`，没有响应文件。
这条技术失败不能被解释为角色决定不生活、不说话或来源审核拒绝。

后续新试验需分别执行一次性的 fresh-output 准入检查与可重复的纯边界检查，并离线覆盖
prepare → admit → output 创建 → 多次发送 → finish 的实际顺序。旧批原件不修改，
不能通过新 runner 伪造旧批完成或重新执行同一 admission。

## 费用和原始证据

主费用记录为空，一笔 `activity_lifecycle_choice` 预留仍为 pending，金额 `0.089226` 元。
外层 batch 及 durable receipt 仍为 admitted，没有 manifest、closure 或 finished。
整批 `0.60` 元继续保留，释放为 0；未发送的诊断不替代账单核销。

已知历史原始金额仍为 `3.059951699999999952` 元，累计保守占用 `6.4134232` 元，
不超过本批声明的 `6.42` 元。独立只读检查核对 115 份历史文件、13 份本批终端产物、
请求内容哈希、admission→batch 和旧递归费用闭包，未改历史预算或数据库。

原件位于 `output/private-audits/life-plan-v3-conversation-20260909/`：

| 对象 | SHA-256 |
| --- | --- |
| `runner-trial14.py` | `d6b4707fe4a090a23fd29d6602da962f403d4303eb28c19d49d6842d45572d69` |
| `operator-incident-trial14.json` | `dedf80ce109f2f2881b6cefa0eef4732e521e957fc2db460c3fbb776d490de9f` |
| `batch.json` | `817f4e9add1b436e0647d3a96b68e52f8e13e20d44654f0386a9767067a8f0fe` |
| `trial-14/model-inputs.jsonl` | `e06f6e70d183937fcdac5619205de8d430ad8e65dd292c88fae84e33dc152987` |

本批仍为未完成准入，后续费用基线必须明确承接这一状态及完整占用，不要求不存在的
正常 closure，也不把独立事故报告升级成 closure。
