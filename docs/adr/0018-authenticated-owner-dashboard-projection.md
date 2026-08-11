# ADR-0018: 认证的 owner Dashboard 使用独立只读投影

- 状态：已采纳；代码与隔离假数据验证完成，真实部署资格待证据
- 日期：2026-08-12
- 取代范围：ADR-0007 对默认 `/dashboard` 必须消费 public DTO 的要求

## 背景

ADR-0007 将默认 Dashboard 设计成公开、最小、只读的 renderer。后续 World v2 已拥有更完整
的 ledger projection、effect-free typed terminal 与多组 process-only runtime evidence；本机 owner
需要查看这些机制是否存在、是否为空、是否降级，而 public DTO 有意不承载这些信息。把浏览器
重新接回 `/health`、legacy Engine 或另一个临时 world 会制造双权威，也会绕过隐私与 replay
边界。

## 决策

保留两个不同 audience，不能互相拼接：

1. `GET /world-v2/dashboard` 继续返回 ADR-0007 的 public/redacted tooling DTO。
2. 默认 `/dashboard` 是本机认证的 owner/operator 只读页面，只读取同源
   `GET /world-v2/dashboard/home`。daemon 通过固定 typed Adapter 从 QQ owner 的固定 endpoint
   取得 `world-v2-dashboard-home.1`；QQ owner 从一次完整 `ProjectionCursor` 的
   `LedgerProjection` 编译它。

浏览器不能提交 world、viewer、permission、cursor 或 redaction policy。owner capability 只由
composition 安装，并同时要求 loopback host、同源 POST、独立
`WORLD_V2_DASHBOARD_OPERATOR_TOKEN` 与 HttpOnly/SameSite session；delivery token 不具备读取
权限。认证只定义 audience，不授权任意原始数据透传。

## Owner 字段策略

每个 `LedgerProjection` 字段必须在版本化 policy 中恰好选择一种处理方式：

- `metadata`：固定 owner/cursor/schema 坐标；
- `typed_summary`：服务端白名单编译的状态、时间、有限 label 与数值；
- `count_only`：内容可能包含 proposal、audit、角色私密判断或自由诊断文本；
- `intentionally_withheld`：semantic/evidence/authority hash 等内部完整性材料。

允许 owner 页面显示事实、记忆、生活、关系、行动、媒体与 runtime 的安全 typed summary 或
准确计数；这是一张运维读模型，不是角色对用户作出的表达。以下边界仍不可放宽：

- `PrivateImpression.reflection_summary` 永远不展示，只保留 aggregate count；
- `privacy_class=withhold` 的自由内容、数值与 title 不展示；
- proposal/audit JSON、prompt、provider payload、路径、URL、开放异常文本不展示；
- semantic/evidence/authority hash、terminal event/proposal/change ref 与各类内部 entity ID 不展示；
- 开放 failure text 必须归入闭合安全分类，未知值只显示通用类别；
- room 只得到服务端给出的 `scene_id/action_id/availability`，不能从 location/activity 推断；
- process-only evidence 先归一化为固定 runtime signal；busy、degraded、unavailable 与 empty/zero
  必须保持不同语义。

Typed terminal 的明细可以有界，但 pinned projection 内 rejected/stale/unsettled 总数必须准确，
并且明细不能暴露其内部确定性 identity。snapshot 的可见 payload hash 仅用于 ETag；内部
semantic hash 只参与服务端缓存完整性判断。

## 不写入与故障语义

所有 Dashboard GET 都是 effect-free：不能 bootstrap world、tick、drain、dispatch、修改记忆或
关系，也不能调用模型/provider。owner 不可用、鉴权失败、schema/hash 不一致或 route 未安装时
fail closed；浏览器冻结最后一个已验证快照或显示 unavailable，绝不请求 `/health`、
`/world-v2/life-state` 或 archive/legacy 数据兜底。

## 证据边界

单元、HTTP、browser contract、privacy canary、replay/cursor 与假数据视觉检查只能证明机械
契约。它们不证明真实 QQ owner 已部署、生产数据库迁移成功、真实 provider 可用、长期数据
布局良好或人工视觉验收通过；这些结论必须另行取证。
