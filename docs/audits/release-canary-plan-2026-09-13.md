# 修复后真实对话试验：执行前计划

当前状态：**首批已执行，因真实回复失败而结束；未取得发布资格**。用户随后提供专用
测试凭据，首批在 `1eb6d1c5` 上完成准入并执行。第 3 条用户输入遇到 primary_timeout、
零可见消息，代理发送 `null` 正常关闭，未重新启动试验。结果见
[首批实测](release-real-canary-01-2026-09-13.json)，后续修复和试验必须另作明确记录。

以下保留首批执行前的范围与配置。修复候选 `067a3fdf` 已通过完整离线门：7876 passed、19 个
原有 skipped、1 warning，详见 [离线检查记录](release-offline-gate-2026-09-13.json)。
执行时仍须绑定最终 clean HEAD，并确认与这份已测试产品代码之间的差异。
不启动旧 Trial17/18 启动器，不改生产数据库、不发真实 QQ、不部署。

## 单批费用边界

费用基线为 [继承快照](release-cost-inheritance-2026-09-13.json)：已知累计约
40.9166433 元、保守占用约 63.3838533 元。旧 unknown 与整批未释放分配全部保留。
拟新增唯一试验上限 **2 元**，计划保守口径不超过 65.3838533 元；这是捕获范围的
预算占用，不是供应商账户实付或月费预测。执行时记录基线文件 hash、最终 clean HEAD、
唯一输出目录、实际配置与开始状态。未执行前不把这 2 元记成新增实付。

`--max-cost-cny 2` 固定 CLI 的月、日、soft-day 上限；背景默认仍为 1.5 元，真实启动须
另设 `WORLD_V2_BACKGROUND_DAILY_BUDGET_CNY=2`。两者共用同一新 SQLite 的逐请求
预算。完整可见审查的 `source_review` purpose 也计入背景，不能把背景额
解释成独立生活额度。HTTP 前按最终请求和输出上限预留；pending/unknown 保留占用。
预算不足即保存部分结果，不提高额度、不重开目录续跑、不用本地话术补成功。

## 固定测试配置

- 使用 real-provider、CaptureDelivery、专用 `DEEPSEEK_DEBUG_API_KEY`；不读取生产 `.env`。
- 显式要求完整可见来源审核，**作者 v3 / 审核 v4**。两者仍未取得真实语义资格；
  不回落 v1 来绕开当前作者结构和审核证据卡路径。
- Life 开启来源审核和显式 self-review；general 与 focused 都用模型。
  当前 CLI 不注入独立 Life reviewer，因此准确标为自审，语义准确率未验证。
- 普通作者 12 秒、hedge 6.5 秒且关闭；保持原完整审查和同角色纠正时限，
  不把这些相加后的完整回合宣称为 12 秒，更不能称 3 秒首气泡已达标。
- 总墙钟 1200 秒、heartbeat 900 秒、max steps 1000、drain passes 8、background units 4。
  墙钟限制新工作与执行；最终退出仍等待资源关闭、费用结算和证据保存。
- 图片、外部实时源、语义 embedding、文本端点评估保持该隔离 profile 的关闭状态；
  本批不能验收这些能力或全功能月费用。实际路由固定到所选 DeepSeek 配置，
  不承接未使用的独立审核 URL 配置。

待执行 CLI 选项（场景与输出路径须在执行声明中绑定，以下不是启动记录）：

```text
--interactive --model-mode real-provider --allow-real-provider
--require-visible-source-review
--visible-author-tool-version 3 --visible-source-review-version 4
--max-cost-cny 2.0 --max-wall-seconds 1200
--heartbeat-seconds 900 --max-steps 1000 --drain-passes 8 --background-units 4
```

专用凭据单独注入，不写入场景、命令记录或日志。该次进程的非秘密配置显式固定为：

```text
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_CHARACTER_THINKING_ENABLED=false
WORLD_V2_SELECTIVE_SOURCE_REVIEW_ENABLED=true
WORLD_V2_SELECTIVE_SOURCE_REVIEW_MODEL=deepseek-v4-flash
WORLD_V2_LIFE_SOURCE_REVIEW_ENABLED=true
WORLD_V2_LIFE_SELF_REVIEW_ALLOWED=true
WORLD_V2_BACKGROUND_DAILY_BUDGET_CNY=2
WORLD_V2_INTERACTIVE_TURN_BUDGET_SECONDS=12
WORLD_V2_INTERACTIVE_HEDGE_AFTER_SECONDS=6.5
WORLD_V2_INTERACTIVE_HEDGE_ENABLED=false
COMPANION_DISABLE_DEBUG_USAGE_LEDGER=1
```

移除该次子进程的 `DSH_INTERACTIVE_TURN_BUDGET_SECONDS`，避免旧变量覆盖普通期限；
不继承旧 reply/expressive/deep-appraisal/local-appraisal 配置或未使用的独立审核凭据、
URL。完整可见 reviewer 由此 CLI 显式注入，使用同一所选模型与专用测试凭据。
上述配置在最初记录时尚未执行，当时不代表已有模型调用或试验额度；实际执行状态见下文。

执行后补注：首批已按上述配置运行。官方模型页现说明旧 `deepseek-v4-flash` 名称
转到 V4.1-Flash，实际响应模型名为 `deepseek-flash`；新公开 Flash 价格低于本候选的
8 月 17 日价格表。本批保持原请求别名和较保守的代码预算，单独记录新公开价格估算，
不把代码估算称为供应商实付，也不按新价重写历史用量。

## 运行中的费用观察

交互 stdout 的 `operator_observation` 没有费用字段；`manifest.usage` 和
`provider-usage.json` 在终局才导出。每个阶段检查点只读同一 `world.sqlite`，
不能依靠最终文件或新建数据库来决定能否继续探索。

已有 `read_provider_usage_evidence(Path(db))` 使用只读事务取得逐模型调用和预留记录。
不要为查询而构造 `WorldV2UsageStore` 或调用 `usage_store_for_settings`，其初始化会
建表和迁移。以纯函数 `price_usage_row(row).cny` 重计价，不能直接把四位舍入的
`SUM(cost_cny)` 当精确实付。

探索停点可使用“所见调用的重计价金额 + pending/billing_unknown 预留全额”的
`conservative_guard_upper`。它可能重复包含 unknown 的部分金额，只作为提前停止
探索的保守上界，不能当作正式 committed、余额或实付。未能计价、记录不全，或发现
本 profile 之外的外部/图片/embedding 账务时停止新探索并保存缺口；不得默默漏算。
现成 reader 只读取模型两张表，额外账务的范围核对须在同一个只读事务中完成。
终局以正式 `manifest.usage`、完整逐调用与预留证据回写继承账本，保留 pending/unknown。

## 按证据调整顺序

旧 r3-proactive / r3-proactive-2 分别花费 2.1002086 / 2.3570784 元，只有一条用户输入，
尚未采用当前 general 语义审核和完整可见审核。首条经历分别出现在第 49 / 124 分钟，
首条记忆保留分别为第 64 / 126 分钟。不能承诺 2 元跑完七轮聊天加十三小时无人回复；
因此把生活后的聊天放在长段观察前面，不修改生产生活频率或机会来节省试验费用。

1. 先发一至两条自然消息，例如“早，今天你打算干嘛”，确认真实作者、完整审核、
   Action 和捕获交付链成立。若出现技术失败，先保存准确失败输入与费用；不连续
   重试来寻找一次看起来成功的输出。
2. 推进短生活窗口，在自然观察点核对实际结算、CharacterLifeResponse、Experience
   和记忆处理。首次探索最多约 180 虚拟分钟；阶段费用上界达到 1 元时停止继续探索，
   这个控制点不保证余款够完成聊天。角色不回应或不保留记忆都允许，记录为对应证据。
3. 一旦形成可核对的链，立即问“你今天后来怎么样”。下一句只引用她刚在聊天中
   实际披露的内容，询问感受或确认是已发生还是计划；不把隐藏世界草稿提供给她当
   用户知识。如果没披露，正常追问并保留“生活尚未进入聊天”的缺口。
4. 在最多七条总输入内观察一项用户安排的更正与后续回忆，以及一个未提供信息的
   未知边界。不要求固定答案或语气，按真实上下文区分支持、矛盾和不足。
5. 固定场景保留一次同进程宿主关闭并冷重建，核对 checkpoint hash、构造交付增量
   和后续实际调用/交付；自动报告的 duplicate recovery 未验证不得改绿。
   这不是 OS 崩溃或生产服务重启验收。
6. 长时间无人回复的自主选择放在最后，按剩余预算决定是否开始观察。若本批无法
   覆盖，保持该原 Goal 子项未验证；不因较短核心链通过便声称一周真人感通过。

## 必须读取的实际产物

- 原始 HTTP 请求/有界响应、finish 和逐物理调用 usage，核对 pinned Context 与来源视图。
- general/focused 语义结果、Plan、settlement、CharacterLifeResponse、Experience 的
  精确来源关联；环境结算本身不能替角色形成内心或经历。
- 记忆决定、候选/保留与事后聊天请求实际包含的材料。答对不单独证明用了长期检索。
- 首稿拒绝、同角色纠正、技术失败、角色沉默、授权延后和实际捕获分别记录。
- manifest、timeline、evidence、provider-usage 及 replay，最后回写累计账本，保留未知。

阶段通过只表示这批有证据的行为。人格长期稳定、生活丰富度、长期未回复、真实 QQ
回执、完整月费用和邀请部署仍需各自证据；缺项继续留在原 Goal，不缩小完成标准。

## 已完成的零费用工具预演

在 `b003fda4` 上使用旧拟定长场景跑了 fixture：7 条输入、870 虚拟分钟、minute 770
宿主重建，12.54 秒完成，零 usage/reservation；前后语义 hash 相等、构造交付增量为零，
replay hash 一致。输出位于本工作树
`output/private-audits/release-canary-preflight-20260913/fixture/`。
这验证了交互输入、推进、重建与导出的工具链；不验证 v3/v4 真实协议、费用、真人感或
新分阶段场景。

新分阶段场景 `release-life-chat-canary.2` 已在 `067a3fdf` 上完成零费用预演：7 条输入、
240 虚拟分钟、minute 190 宿主重建，8.98 秒完成；前后 hash 相同、构造交付增量为零，
replay hash 一致，零 usage/reservation。证据位于
`output/private-audits/release-canary-staged-preflight-20260913/`，哈希已列入离线检查记录。
其中两条开场输入后每 30 分钟观察，minute 180 接着聊天，184 更正用户安排，187 问未知
信息，192 在重建后回忆安排，240 结束。真实交互一旦产生可核对的生活链就提前聊天；
不能把 fixture 的整批 stdin 重定向给真实模型。固定 minute 190 若预算内未能到达，
保留恢复缺项，不追加配额。这个短窗口也不验收长期无人回复后的行为。

fixture CLI 不接受完整可见审核或作者 v3 / 审核 v4 参数，且使用默认背景上限 1.5 元；
它不证明真实配置或语义质量。仍须人工核对真实输入、实际选择和计费，不能将
manual_only / human_likeness unassessed 改成已验收。

## 首批实际执行与停止点

实际执行没有照抄 fixture 输入文件：前两条输入后，观察到 minute 147:01 的首条结算、
角色 Life Response 和 Experience；minute 148 即询问“你上午后来怎么样”，未等到 180。
该回合作者确实看见了这段生活材料，但 v4 审核缺少对应来源，原稿被拒后纠正请求
在共享普通回合期限内被取消。无可见消息，不能当作角色沉默或记忆未保留。
对主稿中角色行为与环境事实的混引仍须单独判断，不能因运输缺口而放行全部主稿。

首批共 3 条输入、148 虚拟分钟、38 次模型请求，517.74 秒后以 operator_stopped 结束；
7 输入、重建、更正后回忆、未知边界及长时间未回复等未执行部分仍未验证。已形成的
生活链和前两轮捕获回复不抵消第 3 轮失败。原始证据与诊断均保存在唯一试验目录，
不重开该启动器。最新费用继承为
[首批后的保守快照](release-cost-inheritance-2026-09-13-canary-01.json)：37 笔已知用量按
仓库保守价估算约 0.6946112 元，1 笔未知账单保留 0.493629 元；完整 2 元分配仍保留，
没有自动追加试验额度或释放旧 unknown。
