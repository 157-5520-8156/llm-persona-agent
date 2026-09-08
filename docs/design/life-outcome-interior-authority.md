# World Author outcome 的角色内心权限

本片修正 World Author 与已有 focused novel-origin critic 的明确权限矛盾。
在 803f15a5 中，作者主提示、能力表及 `LifeDevelopmentOutcomeDraft` 的 schema
描述都允许候选文字包含她的想法；critic 主提示、维度说明及格式纠错消息亦允许
候选 feelings。仅禁止 premise 内心并未覆盖 outcome。

trial-06 的真实候选包含“心里觉得安静又踏实”，focused critic 返回 supported。
该 trial 停止时尚无 settlement 或 Experience，不能声称这句话已经成为经历。
但已有 `life_aftermath_runtime` 会将被选 outcome 的文字写入 occurrence result 和
experience summary；只选择一个作者提供的 token，不等于角色自行创作了内心反应。

## 权限及既有调用链

World Author 可以提出客观动作候选、NPC 对话、环境及客观后果；她是否参与、如何
理解、产生何种感受或动机，仍由 Character Model 决定。新感受、想法、动机、意图、
主观反应即使写成条件句，或放在 character_choice 分支，也不获得作者权限。
已存在的内心材料只可按精确来源作为历史背景引用，不能改成此候选引发的新反应。

本片沿用现有 focused critic，不新增模型、reviewer 或角色回合。一般来源检查仍
不拥有 outcome 的负向坐标，只修正其对 focused 权限的描述。focused 复用
`unsupported_outcome_prerequisites`：`prose_path=outcomes.N.text`，
`violation_kinds=[character_interior_authorship]`，以及该条 outcome 的逐字片段。
这个 kind 仅新增于 outcome finding，不向 NPC、地点或 claim 坐标扩权。

模型作语义判断；本地只检查坐标、逐字片段和 supported/unsupported 一致性。有效
unsupported 直接结束本次 admission，记录技术拒绝，不进入角色选择、不创 Plan 或
Occurrence、不让 World Author 重写。错误坐标才走已有一次同 critic 格式纠错；
仍错则记录 invalid-contract 技术失败。后续技术重试仍由原 Life 调度负责。

## 身份与历史

新 focused output contract 为 `life-development-novel-origin-review.5`，
evidence packet 为 `.6`，subject 为 `.5`。消息全文继续进入 request hash，
原 World Author raw hash、manifest、cursor、wake 和 review request hashes 共同
绑定 subject；新 Proposal 明确记录 packet 6。健康摘要同步报告新 output contract。

缺少 packet marker 永远采用历史 packet 4 / subject 3；显式 packet 5 保留
subject 4；不会因为当前常量升级而改变历史默认。新提案删标、改为 packet 4/5，
甚至重算 Proposal 内的 review 副本，仍须与原 ModelResult 精确审计匹配，不能
通过重启或冷回放降级。没有重写历史事件，也不声称旧 supported 获得了新覆盖。

`tests/world_v2/fixtures/life_origin_packet5_replay.json` 在修改生产代码前通过
803f15a5 的公开 Life runtime 和本地模型 stub 生成，共 10 事件、9 sidecar。
保留一段按旧合同允许的候选内心，用于冷回放、reader 和原始事件字节不变的检查。
它是历史机械兼容证据，不是真实模型准确性或已完成经历的证据。已有 packet 4
fixture 同时保留。未修改 frozen baseline；集成后由根任务独立逐字段比较。

## 验证及开销边界

公共 MockTransport 通过实际 DeepSeek JSON 请求与 Life runtime 检查：两个因果
模式下的有效拒绝都仅一次 critic 响应；角色无调用、Plan/Occurrence 无效果；客观
候选可以成功提交；非法片段获得一次原 critic 纠错；历史冷回放与 fresh 防降级。
HTTP fixture 显式禁用 debug usage ledger，使用纯本地响应及内存 World。

相同固定 location-bound fixture 的紧凑 JSON messages 字节数：作者
36,169 → 36,799（+630），focused 16,558 → 17,346（+788）。这是消息字节测量，
不是 token、供应商账单或月费估计。正常模型调用数增量为 0，未执行真实 HTTP。

这些测试证明权限可见、有效拒绝可被消费和来源身份闭合；并不证明模型会找全
语义越权。trial-03/05 的既往经历误判、聊天空声明捏造当前事实等问题，不因本片
通过而被宣称解决。真实语义能力继续保持 unqualified。
