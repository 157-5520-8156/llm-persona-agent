# 长期理解读取的后期耗时定位

沿用原基线运行；不改变记忆频率、角色选择、来源、隐私或生产配置。

当前基线后期的 45 条完整聊天均同步出现 action_authorized，首条捕获中位约 7.593 秒，仅一条 <5 秒。它们是 Capture，不是 QQ；发出也不证明回答充分或事实均有依据。后续新增轮次仍要等完整终态重新统计。

怀疑长期理解回执随运行积累拖慢前景 Context，本轮从实际运行库做一致备份，禁用任何模型调用，直接测 `MemoryUnderstandingReader.read`：两次约 0.3325/0.1142 秒，3 项 active 理解、49 项待处理。一次 cProfile 约 0.161 秒，最大子段是实际来源读取约 0.054 秒、Pydantic JSON 验证约 0.045 秒、JSON 编码约 0.034 秒，Content JSON 读取约 0.016 秒。子段嵌套不能相加当总耗时。

这个阶段不是目前多个秒的主要解释，不增加跨权限缓存或又一层历史裁剪。来源与理解确认仍完整执行。最初诊断脚本把 suppressions 误叫 failures，导致脚本 AttributeError，已修正并保留；不是生产角色失败，也没有 Provider 账单。

证据：`output/private-audits/understanding-read-profile-20261004/`。另一个完整日进程在同机运行，测量只用于定位，不作独占环境延迟资格。

原基线 exec 54594 与依赖对照 exec 20019 均在本轮多次用真实 handle 验证仍运行；观察超时没有触发重启。后续 World Author 稳定前缀对照继续等待这一具体基线的 completed 终态。<5s、1.5 元/日、回答质量与生产资格仍未关闭。
