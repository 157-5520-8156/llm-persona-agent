# 同一角色的演示入口

longitudinal CLI 现支持 `--primary-user-id`、`--reviewed-prehistory`、
`--initialize-prehistory-steps`、`--dashboard-port` 与 `--dashboard-token-file`。
沿用正常 QQ host、角色模型、记忆和原有面板；投递仍捕获到本地，不发送 QQ。

人生档案必须已经审核且精确绑定演示 World/角色。导入不等于记住，显式初始化
一次最多处理一个片段，由原角色选择保留。重启只恢复已有决定，不重开模型调用额度。
面板只监听 loopback，要求独立 ASCII 口令，通过现有登录 cookie 访问；不提供聊天、
写入或会启动第二个 World 的健康检查路由。口令不写入 URL、manifest 或 timeline。
面板关闭时会等在途快照读取结束，再允许 owner 关闭数据库，浏览器取消请求也一样。

验证：99 项相关检查通过（包含 5 项新集成/边界检查）。正常宿主使用离线 HTTP
provider 完成审核档案导入、一次角色保留、认证面板读取、缓存、关闭以及冷重放；
确认只有一个 World、没有面板触发模型调用，关闭后端口停止监听。
这些证明组合接线，不证明真实聊天质量、QQ 终态回执、月费或发布资格。

同期两项来源呈现修复：

- `a04e464c` 明确同一 plan 的 Started/Resumed 与 Completed 是不同转换，来源审核
  获得原 pin 重验的身份、状态和时间。没有授予意图成功、地点或内心权限；42 项回归通过。
- `17d2974b` 新 Fact 卡仅展示精确接受值及限定信息，完整 Observation 仍保留于审计。
  独立发言来源不变；188 项回归及 33 张旧回执冷验证通过。原错误响应仍拒绝。

尚未进行这些修复后的付费复测。累计运行/费用基线仍为
`output/private-audits/release-lifecycle-chat-20260920-01/run`，
1475 usage / 1472 reservations / 60 unknown holds。新身份必须分离运行历史，
同时完整保留累计费用来源，不能用新库清空试验账目。
