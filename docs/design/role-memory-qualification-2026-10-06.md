# 角色协议、记忆读取与隔离验收

基线：`f2d9949e`。用户本轮明确先推进后台角色协议失败、记忆使用质量和候选整体验收，QQ 仅为上线通道，本轮不修复或部署 QQ。

## 范围与验收

- 沿现有 CharacterInterior 工具调用、同角色一次纠正和 typed acceptance 修复已保存失败；区分结构错误、来源审阅拒绝、权限错误和合法沉默。
- 沿现有 RecallCoordinator → 角色实际输入 → 捕获发送回执验证完整记忆；保留原排序、权限、遗忘、时间和预算，不用补写内容掩盖检索遗漏。
- 候选冻结后执行完整离线回归、场景/历史契约、冷恢复与回放；复用专用驱动器、隔离 SQLite 和 CaptureDelivery 做真实模型验证。
- 所有实际调用含失败、纠正和未知费用都记账。不同生活量、不同缓存或未完成运行不能直接证明成本和质量收益。
- 不改生产配置、数据库或线上进程，不发 QQ 测试消息。目标速度、日成本和长期质量未通过时明确保留未达标项。

## 已选测试边界

1. StructuredCharacterRoleFaculty 公共 experience/consider 和实际 provider tool schema，含精确拒绝原因、同作者纠正以及旧契约身份。
2. RecallCoordinator 的结果/来源与同回合作者实际呈现，含完整父记录、窗口边界、不同 actor、撤回/遗忘和字节上限。
3. 公共测试驱动器的接受、捕获投递、恢复和冷回放；离线测试使用临时资源，真实调用使用 debug 账户。

## 首次复现

保存失败包里被旧记录归为关注来源超过32条的25次输出，实际只有5–27条；扩展wire解析均通过。最小公开experience调用复现：当 life_responses 是必需数组，角色在外层写 no_change，内层选择 no_change 并显式返回 response_text=null，工具schema允许，宿主expected_status却是transition；宿主只返回泛化schema错误，又无条件追加来源数量指导。

候选方案：优先在新版本工具中约束可确定的结构状态、精确报告对应字段失败；不由宿主截断关注来源或替换角色选择。历史工具保持原值。DeepSeek官方工具文档确认strict支持enum/anyOf，运行接受仍保留canonical校验：<https://api-docs.deepseek.com/guides/tool_calls/>。
