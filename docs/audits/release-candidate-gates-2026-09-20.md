# 首版收敛与剩余验收

首版范围是单角色、限量邀请：自然聊天、已有生活和记忆、能看清来源与状态的面板。
小屋和额外能力不进入本次发布；长期人格、遗忘、事件多样性仍属于项目完整目标。
当前资格仍为 manual_only / qualification_incomplete。

最新同一干净身份已续跑至 61 分钟：WorldOccurrence settled、ActivityStarted 已发生，
但无新的 CharacterLifeResponse/Experience 或 accepted visible 角色交付，仅系统失败提示。
Life `.11` 在本地准备失败，实际 provider 请求为 0；`e127eaaf` 的紧凑行适配及真实
snapshot 请求组装有 27 项离线检查，不能算实跑通过。主动联系纠正本轮未行使。
唯一最新运行/费用继续点是 `output/private-audits/release-clean-continuation-20260920-01/run`：
ledger 400/revision 192，1543 usage /1540 reservations /64 unknown holds。
本轮 25 physical /25 known、估算 0.60819330 元，重启/冷重放通过；不恢复旧低费用账本。
详见 [干净旅程与续跑证据](clean-demo-validation-2026-09-20.md)。

## 推进顺序

1. **完整聊天交付**：当前主阻断。用已有真实观察测试当前认知与有来源的回忆，核对
   正文、审核结果、实际交付、延迟、费用和冷恢复。相同配置连续失败则停止追加
   试探，保留反例定位具体责任。两轮成功也不能单独证明长期质量。
2. **正式入口接线已实现，待运行验收**：正常 QQ composition 已能显式选择
   `WORLD_V2_VISIBLE_EXPRESSION_PROFILE=experimental_independent_v22`；专用 Life 审核由
   `WORLD_V2_LIFE_CANDIDATE_REVIEW_ENABLED=true` 安装。来源模型分别配置为
   `WORLD_V2_VISIBLE_SOURCE_REVIEW_MODEL` 与 `WORLD_V2_LIFE_CANDIDATE_REVIEW_MODEL`，
   不改变角色主模型。原子表达、客户端/同 DB 审核证据库的生命周期与冲突已离线验证。
   默认保持关闭，选择实验 profile 不赋予发布资格；尚未部署或完成实际 QQ 验收。
3. **同一干净身份的完整旅程**：自主计划、实际后果、角色反应、记忆保留、跨会话
   回忆及聊天；同时覆盖用户离开/回来和旧话题。既有档案须重新绑定演示 World，
   不能复制受污染测试库。入口是 longitudinal audit 的 `--primary-user-id` 与 prepare_character_prehistory；
   正常宿主已完成真实 24 条前史审核、角色保留 4 条及首次两轮聊天交付；续跑已结算
   世界事件并开始活动，仍缺 Life 反应、Experience/生活记忆与回来聊天的完整交付。
   先复测 `.11` 本地准备修复后的真实审核和落地，不把 Fact 记忆或活动开始算作完成。
4. **可录制面板**：主体已经具备，用同一旅程数据验证来源和状态展示，无须界面
   重写。同 owner 认证 HTTP 已通过，登录后的视觉验收待做。Experience 未读取正文
   不能呈现成已证明完整因果。使用认证 owner DTO。
5. **运行验收**：最终配置的独立进程恢复、备份回滚、重复事件不重复发送；实际 QQ
   staging 终态回执与 24 小时持续运行分别取证。本次重启/冷重放已通过，不能代替这些
   未执行项目。外部部署/真实 QQ 不在当前自动
   goal 的执行授权内，先完成本地候选和可审查操作方案。

## 邀请与费用边界

邀请实例须唯一收件人、独立 World/DB 目录/端口/owner token，显式 v2 模式；auto
在不支持的配置下可能回兼容路径。现锁按数据库父目录，不等于同一 QQ 账号多实例
自动分流已通过。扩大 allowlist 不是已验证的多人隔离方案。

月费按同一合格旅程/持续运行账本分别统计初始化、聊天、后台、审核和重试；以明确
使用量估算约 100 元目标。测试额度已解除限制，不代表产品运行成本要求取消。

冻结候选后运行一次 scripts/test_fast.py --tier full、Ruff、diff 检查；重复全库
测试不能代替真实生活、聊天、终端回执与持续运行证据。
