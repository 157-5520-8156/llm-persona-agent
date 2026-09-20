# 首版收敛与剩余验收

最新状态与运行／累计费用继续点见 [当前发布状态](release-status-2026-09-13-current.md)。
当前资格仍为 **manual_only / qualification_incomplete**。
首版限定单角色、限量邀请：自然聊天、已有生活和记忆、能看清来源与状态的面板。
小屋和额外能力不进入本次发布；长期人格、遗忘、事件多样性仍属于项目完整目标。

08第一轮正常交付3条文字，第二轮状态追问仍失败，尚未证明稳定连续聊天。
角色自主开始了活动，尝试结果生产入口已触发，但作者格式失败后选择no_op，未形成新结果。
新增Life response与Experience来自已有环境结算，不能替代行动结果或后续准确回忆。
召回权威标签实传；World外层标签的正式类型漏接已用真实producer链修复与覆盖，
新呈现下的真实对话效果仍待验。单次工具探针证明供应商接受严格工具结构，原业务
解析仍拒绝缺失visual_evidence的候选，尚未结算活动结果。

最新运行继续点是
`output/private-audits/release-clean-continuation-20260920-08/run`：ledger758/revision317，
该旅程结束时1678 usage /1675 reservations /72 unknown holds。26次调用全known，估算0.70176968元，
新增unknown为0；已正常关闭、独立对账及冷重放通过，2张旧与1张新v22来源回执冷验。
真实QQ终态和连续运行仍待验。旧probe未知预留已完整继承，不恢复较旧账本。
随后工具探针的最新完整费用在`release-world-author-tool-20260920-01/run/world.sqlite`：
1679 usage /1676 reservations /72 unknown holds。恢复08运行后必须先合并该费用前缀，
再读取水位或调用模型；探针本身不能恢复为旅程。

## 推进顺序

1. **完整聊天交付**：当前主阻断。固定07/08反例区分读法、来源匹配与协议结构问题，
   保留正确的无源行动拒绝。用已有观察与新Experience验证自然认知、回忆、实际交付、
   延迟、费用和冷恢复；相同配置连续失败时停止追加试探，按具体反例处理。
2. **同一身份的生活—记忆—聊天链**：现已有真实Life response和Experience；08确认
   召回权威已实传，仍需准确引用与交付。
   修正纯环境混入recent_self_experiences的外层类型后，正常推进角色自主的新计划，取得进行中
   授权尝试结果的真实提交，以及生命周期／情绪新权限被正确引用的证据。未来计划、
   API200、材料送达或一条Experience均不等于整条链通过；不得强迫行动来制造演示。
   前史沿用该身份真实审核的24条档案与角色选择的4条保留，不复制受污染测试库。
3. **可录制面板**：既有环境正文、同owner认证HTTP、桌面／手机／录制模式已通过。
   用新Life response和Experience核对同一旅程展示；没有读取的正文不能呈现成已证明
   的完整因果。继续使用认证owner DTO，无须界面重写。
4. **冻结候选与恢复**：最终修复合并后执行一次 `scripts/test_fast.py --tier full`、
   Ruff和diff检查，完成独立进程恢复、备份回滚、重复事件不重复发送。已有定向回归、
   120-case机制基线和冷重放不能冒称最终冻结全套通过。
5. **实际运行门槛**：QQ staging终态回执与24小时持续运行分别取证，再按使用量估算
   月费。部署／真实QQ不在当前自动goal执行授权内，先完成可审查操作方案。

## 正式入口与邀请边界

正常QQ composition可显式选择 `WORLD_V2_VISIBLE_EXPRESSION_PROFILE=experimental_independent_v22`；
Life审核由 `WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=true` 安装。来源模型分别配置为
`WORLD_V2_VISIBLE_SOURCE_REVIEW_MODEL` 与 `WORLD_V2_LIFE_CANDIDATE_REVIEW_MODEL`，
不改变角色主模型；当前Life新请求为 `.13`，旧版本恢复保持原编译。默认开关不变，
配置已接线不等于发布资格。v23及人物关系偷换误放行的support-basis实验不推广。

邀请实例须唯一收件人、独立World／DB目录／端口／owner token，显式v2模式；auto在
不支持的配置下可能回兼容路径。现锁按数据库父目录，不等于同一QQ账号多实例自动
分流已通过；扩大allowlist不是已验证的多人隔离方案。

月费须按同一合格旅程／持续运行账本，分别统计初始化、聊天、后台、审核和重试，
以明确使用量估算约100元目标。本轮费用不是月费证明；测试额度解除不取消产品成本要求。
任何旧运行历史恢复前，都须继承最新工具探针完整累计账本，不能恢复更便宜的旧费用状态。
