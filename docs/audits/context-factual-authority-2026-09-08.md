# 2026-09-08 继续迭代：惯常作息、未来计划与当前事实

本批接续 `5a6f3d12`，在 `codex/living-continuity` 集成，原工作区、生产数据库与 QQ 不在写入范围内。
用户授权继续自主修改测试；本批新增真实模型试跑总预约上限 ¥0.5，计入实例总费用。
上一批两次真实试跑已按 usage 记账 ¥0.5900133，不能因为新建实验目录忽略先前消费。

## 已有反例与修复边界

上一批 trial-02 的第 9 次实际请求同时包含：

- system 声明 day_sheet 与传记习惯不能证明当前活动。
- day_sheet 声称图书馆看书“（现在）”，lived_moment 又写“这会儿是图书馆看书”。
- situation.activity_slices 只有 9 月 12 日未来计划；social_environment 却写 with_others。

这是输入内部的事实权威冲突。不能断言模型具体关注了哪段材料，也不能只因没有 occurrence 就说它没有
读到任何依据。新测试通过公共 `compile_inner_life_snapshot(...).model_view()` 重现：只有时钟与空 slices，
也会得到当前图书馆活动。另经生产 CLI → QQ host → 角色编译器 → DeepSeek adapter → 本地 MockTransport，
复现同样的“（现在）”进入实际 HTTP JSON 字节；本地 HTTP 夹具只验证输入链，不证明真人感。

系统可提供习惯、日程背景、未来计划、正在发生的活动和已结算经历，但不得把一种证据升级成另一种。
角色仍自行决定生活、表达与沉默，本批不规定回答内容，不新增行为频率或情绪映射。

## 本批修改

1. day_sheet 保留惯常时间、地点与周安排，明确属于背景。删除按小时标注正在发生及按日期散列生成天气。
   天气需要有来源的 World 事实；本批不增加天气请求或模型调用。
2. lived_moment 不再从日程字符串反向推断当前活动。有来源的经历、appraisal、impression 仍可见。
3. Situation 的当前社交环境只读取已接受且不晚于逻辑时间的 active 活动；未来、到时尚未开始、paused、
   completed、abandoned 计划均不证明当前有人陪同。没有参加者引用不证明独处，保持 unavailable。
   未来与暂停计划继续在 activity_slices/plan_relation 可见；active 超过原定窗口不被代码擅自判结束。
4. 新 Snapshot compiler 身份为 `.18`；Situation policy 为 `.16.1`。历史事件不改写，viewer 隐私边界不变。
5. 新两小时三输入夹具分别询问现在、以后和刚才，只定义用户输入，不包含角色标准答案。

## 验证记录

定向回归、真实短旅程、独立审查与冻结场景差异在执行完成后记录。完整长期真人感、真实 QQ、
中断后部分经历、地点正文语义审查与每月 ¥100 仍不因本批局部修复而自动获得资格。
