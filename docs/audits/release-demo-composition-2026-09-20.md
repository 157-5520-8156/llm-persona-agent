# 同一角色的演示入口

最新状态与运行/累计费用继续点统一见 [当前发布状态](release-status-2026-09-13-current.md)。

longitudinal CLI 现支持 `--primary-user-id`、`--reviewed-prehistory`、
`--initialize-prehistory-steps`、`--dashboard-port` 与 `--dashboard-token-file`。
沿用正常 QQ host、角色模型、记忆和原有面板；投递仍捕获到本地，不发送 QQ。

人生档案必须已经审核且精确绑定演示 World/角色。导入不等于记住，显式初始化
一次最多处理一个片段，由原角色选择保留。重启只恢复已有决定，不重开模型调用额度。
面板只监听 loopback，要求独立 ASCII 口令，通过现有登录 cookie 访问；不提供聊天、
写入或会启动第二个 World 的健康检查路由。口令不写入 URL、manifest 或 timeline。
面板关闭时会等在途快照读取结束，再允许 owner 关闭数据库，浏览器取消请求也一样。

接线的离线验证：99 项相关检查通过（包含 5 项新集成/边界检查）。正常宿主使用离线 HTTP
provider 完成审核档案导入、一次角色保留、认证面板读取、缓存、关闭以及冷重放；
确认只有一个 World、没有面板触发模型调用，关闭后端口停止监听。
这些证明组合接线，不证明真实聊天质量、QQ 终态回执、月费或发布资格。

历史接线阶段的两项来源呈现修复：

- `a04e464c` 明确同一 plan 的 Started/Resumed 与 Completed 是不同转换，来源审核
  获得原 pin 重验的身份、状态和时间。没有授予意图成功、地点或内心权限；42 项回归通过。
- `17d2974b` 新 Fact 卡仅展示精确接受值及限定信息，完整 Observation 仍保留于审计。
  独立发言来源不变；188 项回归及 33 张旧回执冷验证通过。原错误响应仍拒绝。

随后真实干净身份旅程在 `aa8c8cce` 完成 24 条前史审核、角色自主保留 4 条及两轮
聊天交付 5 条文字；同 owner 认证面板 HTTP 通过。
随后续跑确认 WorldOccurrence settled、ActivityStarted；63 分钟时 Life `.11` 两次真实
审核均完整返回，紧凑行旧错不再复现，但时间类别和 Fact 引文格式仍阻止接受。没有新
CharacterLifeResponse、Experience 或角色交付。v23 可见来源对照亦未合格。

该 63 分钟阶段的历史运行与累计费用继续点：
`output/private-audits/release-clean-continuation-20260920-03/run`，
ledger **405** / revision **193**，**1553 usage /1550 reservations /66 unknown holds**。
冷重放与旧回执通过，所有付费进程已关闭；历史目录不能直接作为低费用继续点。
详见[来源接口与生活续跑](release-review-interface-validation-2026-09-20.md)。
后续 04 续跑已完成面板登录后的桌面、手机与录制模式视觉验收，无 JS 错误或横向溢出。
05 随后接通并实际展示已结算环境正文，录制模式标注节选；只读浏览不新增模型调用。
QQ 终态回执、持续运行及约百元月费仍未通过；最新运行/费用点以当前发布状态为准。
