# 首版收敛与剩余验收

首版范围是单角色、限量邀请：自然聊天、已有生活和记忆、能看清来源与状态的面板。
小屋和额外能力不进入本次发布；长期人格、遗忘、事件多样性仍属于项目完整目标。
当前资格仍为 manual_only / qualification_incomplete。

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
   显式身份已完成零调用 bootstrap 验证；档案导入、角色保留和面板同 owner 组合已接通，
   正常宿主/离线 provider 的集成检查通过，仍待干净身份的真实模型体验验收。
4. **可录制面板**：主体已经具备，用同一旅程数据验证来源和状态展示，无须界面
   重写。Experience 未读取正文不能呈现成已证明完整因果。使用认证 owner DTO。
5. **运行验收**：最终配置的独立进程恢复、备份回滚、重复事件不重复发送；实际 QQ
   staging 终态回执与 24 小时持续运行分别取证。外部部署/真实 QQ 不在当前自动
   goal 的执行授权内，先完成本地候选和可审查操作方案。

## 邀请与费用边界

邀请实例须唯一收件人、独立 World/DB 目录/端口/owner token，显式 v2 模式；auto
在不支持的配置下可能回兼容路径。现锁按数据库父目录，不等于同一 QQ 账号多实例
自动分流已通过。扩大 allowlist 不是已验证的多人隔离方案。

月费按同一合格旅程/持续运行账本分别统计初始化、聊天、后台、审核和重试；以明确
使用量估算约 100 元目标。测试额度已解除限制，不代表产品运行成本要求取消。

冻结候选后运行一次 scripts/test_fast.py --tier full、Ruff、diff 检查；重复全库
测试不能代替真实生活、聊天、终端回执与持续运行证据。
