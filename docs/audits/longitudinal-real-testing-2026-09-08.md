# 2026-09-08 真实模型短旅程：反例、修复与费用

用户授权自行测试并迭代；本批从 `de8491ea` 开始，在 `codex/living-continuity` 隔离分支进行。
使用真正的 QQ host、世界与记忆链，替换外部 QQ 为本地捕获器。没有操作生产数据库、QQ、运行中的
服务或远程分支。本批最多两次付费试跑，每次预约上限 ¥0.5，总预约不超过 ¥1；测试包含在实例费用中。

## 实验身份与边界

两天输入夹具 `longitudinal_two_days.json` 包含六条消息、未回复间隔、计划更正与一次重启点。
其 SHA-256 为 `62cb26b43b73811d7cb28aa7082f9ab5ea9dc0cd2de2f0fe6c170d3cf77c96fc`。
预算不足保存部分旅程；请求两天不代表完成两天。相同输入下模型仍会生成不同世界，两次运行不是配对实验。

真实模型使用已有配置的 `deepseek-v4-flash`，关闭 thinking。仅从已有配置读取必要环境项到子进程，
未复制或输出密钥。当前 debug key 与生产 key 相同：账本隔离，供应商账户并未隔离。
媒体、外界实时源、语义 embedding、文本端点与 Hermes 私密提示路径关闭。
Life general source closure 为确定性检查；novel-origin 使用已有操作员明确配置的 World Author
self-review，不代表独立 reviewer 合格，也不代表自然语言与引用语义相符已得到验证。

## 第一批：真实请求暴露出的反例

实跑代码 **`b5eecf03`，tracked clean**；真实耗时 58.802 秒，虚拟 87 分钟，消费 1/6 输入。
共 16 次 HTTP 模型请求、176 个 WorldEvent、2 个捕获文本 beat、0 次真实 QQ 发送。
全部请求字节 hash、全部事件 payload hash 与 manifest 的三个文件 hash 已核对；回放 hash 相同。
按供应商 usage 结算 **¥0.3055111**，pending / unknown / unresolved / unpriced 均为零。
下一次请求需保守预约 ¥0.278304，超过余额 ¥0.1944889，因此发送前被拒；不是已经花满 ¥0.5。

1. **完整经历过早成为过去。** 09:12 开始的活动窗口截止 12:00，09:27 已结算候选“整个上午阅读，
   中午收拾去午饭”为 Experience。`event:000157` 结算、`event:000162` 提交 Experience，实际第 16 次
   请求（原始 `model-inputs.jsonl` 第 31 行）又将整段内容标作 `verified_experience_text` 提供给记忆模型；
   `event:000164` 角色选择 retain、`event:000170` 激活记忆。来源链可重放仍然可能承载错误时间。
2. **NPC 隐私约束直到最终提交才发现。** 首轮世界草案、审查、角色选择全部合法返回，随后 plan reducer
   报 `plan cannot weaken participant NPC privacy`，`event:000094` 记录 `life-ecology:failed_safe`。
   最终硬边界正确阻止提交，但前面三次调用已花 ¥0.068742，约占本批 22.5%；十分钟后重做整个生成链。
3. **输入重复增加费用。** 199,352 prompt tokens、6,555 output tokens，cache hit 15,872。
   实际输入费用 ¥0.2760136，占 90.3%。两次 world-stimulus 的 no_change / transition 能力 schema
   除 status 外完全重复，单个 parameters 达 25,103 字节。
4. **报告掩盖了提交失败。** 调度步骤没有抛出顶层错误，模型结果也没有 failure_code，原报告把第 4 步
   的 `failed_safe` 折叠为安静时段。现在读取生产定义的技术终态并显式展示，原始实验 manifest 未重写。

另有两个未关闭的语义问题：候选正文写图书馆三楼，typed location 却是校园小路；首条回复新增“家里书店
常泡这个味儿”的习惯，输入只分别支持桂花乌龙偏好与家庭书店背景。后者进入 fallible PrivateImpression，
未发现被提交为 canonical Fact。不能用正则找“图书馆”或删去某句固定台词作为机制修复。

六维评审单独保存在 `trial-01/assessment.json`，不覆写原始评审包。记忆维度为 assessed，含义是发现上述
可追踪反例，绝非合格；人格、话题重复、事件后的变化、丰富度和选择多样性仍为 insufficient。

## 本批修改

- 实际 HTTP transport 请求取证：保存完整模型 JSON 输入与 SHA-256、捕获健康度和部分覆盖状态。
  不记录鉴权头、请求地址或响应正文，不缓冲流。客户端发送证据不等于模型关注或严格 pinned-turn 归因。
- 预算准入移到完整 provider payload 构造之后，包含 tools/schema 和实际输出上限，避免按简短摘要低估。
  已有 Hermes OpenRouter 路由补计价和 provider price ceiling，未知价格仍拒绝发请求；没有验证真实 Hermes
  可用性。价目与路由限制依据 [OpenRouter 模型页](https://openrouter.ai/nousresearch/hermes-4-70b)及
  [max-price 文档](https://openrouter.ai/docs/guides/routing/provider-selection#max-price)。
- 合并两个完全等价的 world-stimulus schema 分支，parameters 25,103 → 12,817 字节；两份实际旧请求仅
  替换 schema 均减少 12,286 字节。保留全部能力、状态、输出上限和召回分支，不预设角色选择。
- 完整 occurrence outcome 等到已接受窗口结束才可选择与结算；同一边界进入 production declared-due。
  提前结束/放弃 Activity 仍可发生，但不能借此提前兑现冻结的整段故事；其它 plan 的终态不能替代证据。
  部分结果、临时中断后实际发生了什么仍需要独立语义设计，当前保守时界不能证明故事持续时间合理。
- 在 pinned capability 中公开 NPC 隐私底线，世界草案解析时即验证，失败返回同一 World Author 的既有一次
  受约束重选；不擅自把隐私改成 private，不移除最终 reducer 边界。旧 manifest 字节身份保持兼容。
- 报告显示模型返回合法之后的 Life ecology 技术失败；关闭流程等待 host 后台安静后再关闭全部模型客户端。

## 第二批：修正路径得到真实覆盖，长期效果仍未证明

实跑代码 **`bbd38773`，tracked clean**，同一输入夹具、相同模型与配置。实际 56.043 秒推进 4.5 小时，
消费 3/6 输入，17 次 HTTP 调用，5 个捕获文本 beat，276 个 WorldEvent；回放相同。
实际费用 **¥0.2845022**，pending / unknown / unresolved / unpriced 均为零。下一笔 World Author
预约 ¥0.295251 超过余额 ¥0.2154978，发送前停止。两批实际合计 **¥0.5900133**，总预约上限 ¥1。

第 5 次请求（`model-inputs.jsonl` 第 9 行）明确包含 `npc_privacy_weakened` 与 NPC 的 personal 底线。
同一 World Author 自行把提案隐私从 shareable 改为 personal，随后只经过一轮审查、角色选择，
`event:000073` 成功接纳计划。这证明前移校验与原模型重选路径在真实请求中被使用。
第三条用户输入的一次角色 JSON 格式修正还多消耗了一次 inbound 调用；不能把所有重选费用都说成已消除。

本批计划安排在 **9 月 12 日周末旧书市场**，并未启动活动或结算经历。因此它没有实测新的 outcome
时间守门，时间修复只获得针对生产 runtime 的离线回归证据。无 lifecycle / outcome / experience 三笔费用
也不是生活效率提升；第一批这三类费用合计 ¥0.0529956。两批世界不同，缓存还从 7.96% 上升至 29.80%，
首个完全相同请求已命中前批缓存，不能把较低费用或较长推进时间全部归因于 schema 压缩。

新的事实边界反例出现在 10:00 的回复：“在图书馆坐着，翻了几页书”。第 9 次实际请求（第 17 行）的
`day_sheet` 与 `lived_moment` 明确把固定作息写成此刻图书馆阅读；同时 `situation.activity_slices`
只包含周末未来计划，`recent_self_experiences` 不可用。同一请求 system 又明确限定作息不是当前地点的
证明，current_world 的 S2–S7 都是传记坐标。**输入中存在事实权威冲突，实际回答没有守住该边界**；
不能仅凭没有 occurrence 判定无来源编造，也不能证明模型具体采用了哪段材料。
还应核对 `situation.social_environment` 是否把未来计划的参加者呈现成当前一起的人。
这条输入证据与第一批记忆时间污染一起，说明修源头材料比只修措辞更关键。

第二批六维评审全部保持 insufficient：即时接受安排更正并不证明跨日记忆更新；一次未来计划不能证明
生活丰富度，4.5 小时不能验证一周重复。原始文件与补充评审分别保存，当前仍为 `manual_only`。

## 最终回归与仍开放的问题

首遍整库为 **6334 passed / 19 skipped / 3 failed**，446.18 秒，保留原日志。三个失败分别为：
冻结请求身份变化；QQ 生产组合测试在 occurrence 结束前 30 秒期待 Experience；30ms 首次能力特化
时延断言观测到 165.51ms。后者单独复测通过，未修改阈值；尚不能据此认定它在并发负载下稳定达标。
QQ 组合测试保留原链路断言，新增原时点“Plan 已完成但 Experience 仍为空”，再到 occurrence 窗口末端
验证完整链。最终代码 **`78744936`** 在不并行启动其他重负载检查的条件下完整回归：
**6337 passed / 19 skipped / 0 failed**，439.43 秒。只有现有 Starlette/httpx 弃用警告。
保留首遍失败，不将单次通过当作生产时延资格。

冻结场景独立比较：全部 120 个业务断言通过，只有 `npc_world_impact.01.replay_hash` 改变，
另 119 条及该条其他 manifest 字段相同。两份账本均 81 事件，首差在 #19 的模型 request_hash；
仅在独立进程恢复旧 world-stimulus schema，全部 manifest 字段恢复旧值。后续引用身份会传播到
部分原始 response hash，不能泛称所有原始响应字节都未变；可见消息材料与 output_hash 未变。
证明保存在 `diagnose-120-bbd38773/diagnostic-summary.json` 与单变量恢复结果。
据此建立 **`.94`** 机制基准，完整 CLI 验证 120 项通过，hash 为
`df9dc32773b742730dfc0ab77156a04c225d9d6f5e3f163722f13d2dd76fb88a`。
旧 `.93` 与旧产物不改写，没有为时间、隐私或业务断言重写预期。

26 项机制目录（schema 2）、平台架构检查与本批 27 个 Python 文件的 Ruff check 通过。
整文件格式检查有 14 个文件不通过，已逐一与 `de8491ea` 对照：这些文件在该基线均已不通过；
未做无关整文件重排，也未把整文件格式检查声称为通过。最终 git diff whitespace 检查通过。
NPC 隐私、时间边界、请求捕获、预算与报告均经非实现者复核；独立 Standards / Spec 没有新增可行动发现，
未将已经列出的来源语义、中断结果和成本覆盖缺口判为关闭。

全部改动保留在 `codex/living-continuity`；原工作区 tracked diff 仍为空，未部署、未推送。
本批五个已完成且干净的临时实现工作区已清理，提交分支与集成目录中的原始证据保留。

此时不能声称真实两天、真实一周或 ¥100/月已经达标。
全实例总账仍缺跨生产库与多个试验库的可靠统一汇总；独立实验目录不是新预算。当前 debug usage 镜像还
存在跨库 reservation ID 无法匹配的问题，因此本批使用本地权威 usage 加两次预约上限的 campaign 账本。

下一步应优先分清固定作息、未来计划、当前活动与已经发生的经历，修来源语义与地点冲突、明确中途活动
结果，再改进保存点分叉续跑以复用已付费前史。
具体入口已经定位：`day_skeleton.compile_day_sheet` 按小时把 seed 作息标成“现在”，
`character_interior.contracts._lived_moment` 又把该窗口改写为“这会儿是”；
`present_prompt` 同时声明这些材料不能证明当前活动。
`situation_compiler` 的 social_environment 使用含未来计划在内的集合汇总参加者，需区分“将与某人一起”
和“正在与某人一起”。下一批应从这些投影的事实语义及其生产者/消费者一起修，不能增加一条固定拒答。
反思唤醒频率和重复的静态能力材料也值得单独实测，但不能用降低生活活跃度或固定不反思规则换取低费用。
本地私人原始产物位于 `output/longitudinal-real-2026-09-07/`；持续使用原目录日期以保留第一批身份。
