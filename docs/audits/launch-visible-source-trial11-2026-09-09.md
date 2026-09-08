# 第 11 批真实对照：首稿返回，格式纠正超时

隔离集成版本 `43da429c06210b8a4efc743b578a5c2d9317fd7f`，完整正文审核 required、
atomic v2、同一 DeepSeek flash 配置与原 11 秒作者阶段期限。一次实际用户输入后结束，
没有 QQ 投递、生产库写入或虚拟生活推进。**本次仍未完成真实对话验收。**

## 实际发生的链

首请求 116,510 UTF-8 bytes，返回 HTTP 200 和完整 3,494-byte body，用时 6,679 ms。
因此本次不能再归因于首个作者请求一直没有返回。原工具调用确实采用 v2 的外层
`result`，但内层缺少两个 required-null 字段：`private_turn_state`、`recall_request`。
实际发送的 JSON Schema 与本地主机都要求这些未选 Recall 分支的占位字段；没有证据
表明主机拒绝了一份符合该实际工具 Schema 的结果。

本地 `unwrap` 抛出 `DeepSeek strict transport envelope is incomplete`，同一角色模型
获得一次重选。原纠正请求只带这句笼统原因，没有缺失字段路径。第二次请求已经收到
HTTP 200 headers，但在剩余 4,146 ms 内未取得 body，随轮次期限取消；随后入站记录
`primary_timeout / main_timeout / deferred`。完整正文审核没有被调用，交付为零。
外层超时日志掩盖了首稿已经返回但被拒绝的阶段，必须结合逐调用捕获判断。

首稿未获授权的第三条候选正文仍声称当前在图书馆；该原输入没有提供当前位置或活动的
可用来源，候选 `world_claims=[]`。这继续说明为何需要检查完整正文，而不能以 claim
数组为空跳过审核。本稿先在结构阶段失败，不能声称完整审核已经检出或修正该事实。

另外两次实际后台调用完成了用户事实提取与角色记忆保留。它们与一次失败的聊天同在
该批账本，不能因为没有发出回复就排除后台成本，也不能算对话或生活连续性通过。

## 费用与观察范围

- 4 次物理调用：3 次用量已知，1 次 `caller_cancelled / billing_unknown`。
- 精确已结算 0.0410602 元；未知请求预留 0.391299 元，不按零收费处理。
- 因账单未闭合，原启动器保留整批 0.60 元，释放为零；所有旧未知分配保留。
- 此批在执行前单独声明 0.60 元上限；继承旧批完整上限 4.0134232 元，累计保守上限
  为 4.6134232 元。旧批次、历史凭证和上限均未改写，也未拿旧剩余额度重复分配。
- 整个试验 wall 33.056 秒，只输入一条用户消息及终止指令；虚拟观察 0 分钟，无重启。

v2 请求小于第 10 批的 168,336 bytes，且本次首稿实际返回；两次供应商状态与输出不同，
不足以将差异因果归于 v2，更不能证明延迟、费用或供应商 strict 遵循已通过资格。
不再追加相同付费重试；先用冻结响应复现格式拒绝，修正准确的纠正信息并检查实际
能力和协议提示的一致性。既有事实、角色决定权、审查与截止时间继续保留。

## 可核对证据

私有目录：`output/private-audits/compact-conversation-20260908/`。原输入、响应、账本
及 terminal receipt 保留在本地；本报告不提交私人上下文或将候选文本标为已交付。

| 对象 | SHA-256 |
| --- | --- |
| `trial-11/manifest.json` | `834d2b7f25b876ecd51763dd7dee358e64c9753f44368f6ea66ba378cca8b137` |
| `trial-11/model-inputs.jsonl` | `eaed7d496ac29b92d031ae3f2372e1339743cb884bb09a0e8d218014bf0e8c4a` |
| `trial-11/provider-usage.json` | `06c7b67afa92698450be70e40c3599fe3d7258380b7eb58a75c3ca1eb1b3628f` |
| `trial-11-launch-state/closure-evidence.json` | `f24c4b96e741859d92e1bca4c32e23c6055b1cc625aac617390777aabf546141` |
| `batch.json` | `1fcd84841046916ae351bb473c0894d821fd467359478351a0f3fc32689ff707` |

这是一条可继续诊断的真实失败证据，不是来源审核、加速生活、真人感或月费验收。

## 后续合入：准确纠正信息与 Recall 可用性

`434589b7` 保持 v1/v2 的工具、Schema、身份和成功输出字节，格式拒绝改为提供 `$` 或
`$.result`、缺失字段与多余字段。未知字段名做转义及数量/长度限制，不回显字段值，
不填补角色输出，也不改变两个版本原有的重复键政策。实际首稿在离线回放中继续被拒，
但现在准确指出 `private_turn_state` 与 `recall_request` 缺失；仅在内存添加两个 null
的反事实检查符合原 Schema，不是把修补稿提交给世界或作为实际模型纠正。

`44fbab38` 修复另一处确认矛盾：Core 已接管 Recall，工具允许检索，但旧 expression
adapter 只检查自己的本地 coordinator，导致 occasion 告诉模型 `recall_available=false`。
现在 occasion 与同一次工具调用使用作者已有的同一可用性值。没有增加检索授权或
次数；无能力仍为 false，Core 检索后及之后的一次纠正均为 false，原 pin 和来源不变。

两项合入后的六文件共同调用链 **96 passed / 22.62s**，包括实际 MockTransport
捕获、v1/v2、直接回答/Recall、同模型一次纠正、完整审核前零 Action 与审核失败零交付。
两独立分支另完成 166 项和 65 项相关回归；不把重叠数量相加充当新覆盖。
工具诊断的 12 组历史字节指纹保持，真实试验的所有旧凭证保持。尚未进行修复后的
新付费试验，所以这些改动不能被表述为已经解决真实超时或通过正文审核语义。

额外核对了 [DeepSeek 官方工具文档](https://api-docs.deepseek.com/guides/tool_calls/)：
strict 要求 Beta 路径。当前 `DeepSeekChatModel._completion_base_url` 已根据工具的
`strict: true` 自动加 `/beta`，所以仅见配置 base URL 不含 `/beta` 不能判定端点用错。
原捕获未保存 URL，当前结论是代码路径核对，不补写原试验的网络路由证据。

## 并行复核：第 8 批为何没有生活推进

当前同一集成版本的 `parse_world_author_draft` 与 `_world_author_draft` 消费原始两份
World Author 返回，精确重现首稿缺 `visual_evidence` → 一次纠正 → 合法 `no_op`。
seq194/195 的原请求和返回 hash 全部一致，旧证据前后 hash 不变，零 HTTP。
`main_invalid_recovered` 只表示纠正后的结构合法；首稿未到完整生活来源审核，没有
因此生成生活机会、活动或经历。这次复核不把一次 no_op 当作长期丰富度的判决。

复核入口保存在 `output/private-audits/trial08-life-recheck-20260909.py`，SHA-256
`0d4a2487a9b57f393fc25360d1bafcbb0d813d3a571644878514ed86b01861ab`。注入边界是
冻结初始 messages 与返回，不是重新接受旧首稿，也不是完整新生活链的资格测试。

现有规则将有地点、普通隐私的生活候选与视觉附件绑定；附件内的来源、地点和隐私仍是
硬边界。该绑定已有 H26 设计记录，暂不为此次失败删除。未来若解耦，应一起修正
`inspect_present_moment` 对 `open_life` 的可拍提示，并验证媒体消费者的缺附件路径，
不能单改生活校验后仍向角色宣称所有进行中的活动都可拍。
