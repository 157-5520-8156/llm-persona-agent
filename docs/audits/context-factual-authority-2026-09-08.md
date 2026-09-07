# 2026-09-08 继续迭代：惯常作息、未来计划与当前事实

本批接续 `5a6f3d12`，在 `codex/living-continuity` 集成，原工作区、生产数据库与 QQ 不在写入范围内。
用户授权继续自主修改测试；本批新增真实模型试跑总预约上限 ¥0.5，计入实例总费用。
上一批两次真实试跑已按 usage 记账 ¥0.5900133，不能因为新建实验目录忽略先前消费。

## 已有反例与修复边界

上一批 trial-02 的第 9 次实际请求同时包含：

- system 声明 day_sheet 与传记习惯不能证明当前活动。
- day_sheet 声称图书馆看书“（现在）”，lived_moment 又写“这会儿是图书馆看书”。
- situation.activity_slices 只有 9 月 12 日未来计划；social_environment 却写 with_others。

这是输入内部的事实权威冲突。不能断言模型具体关注了哪段材料，也不能只因没有 occurrence 就说它没有
读到任何依据。新测试通过公共 `compile_inner_life_snapshot(...).model_view()` 重现：只有时钟与空 slices，
也会得到当前图书馆活动。另经生产 CLI → QQ host → 角色编译器 → DeepSeek adapter → 本地 MockTransport，
复现同样的“（现在）”进入实际 HTTP JSON 字节；本地 HTTP 夹具只验证输入链，不证明真人感。

系统可提供习惯、日程背景、未来计划、正在发生的活动和已结算经历，但不得把一种证据升级成另一种。
角色仍自行决定生活、表达与沉默，本批不规定回答内容，不新增行为频率或情绪映射。

## 本批修改

1. day_sheet 保留惯常时间、地点与周安排，明确属于背景。删除按小时标注正在发生及按日期散列生成天气。
   天气需要有来源的 World 事实；本批不增加天气请求或模型调用。
2. lived_moment 不再从日程字符串反向推断当前活动。有来源的经历、appraisal、impression 仍可见。
3. Situation 的当前社交环境只读取已接受且不晚于逻辑时间的 active 活动；未来、到时尚未开始、paused、
   completed、abandoned 计划均不证明当前有人陪同。没有参加者引用不证明独处，保持 unavailable。
   未来与暂停计划继续在 activity_slices/plan_relation 可见；active 超过原定窗口不被代码擅自判结束。
4. 新 Snapshot compiler 身份为 `.18`；Situation policy 为 `.16.1`。历史事件不改写，viewer 隐私边界不变。
5. 新两小时三输入夹具分别询问现在、以后和刚才，只定义用户输入，不包含角色标准答案。

## 验证记录

### 真实模型试跑：输入修复生效，输出事实仍有失败

主体代码 `cd7a84c5`（tracked clean），`deepseek-v4-flash`，fresh SQLite，QQ 改为本地捕获。
保留已配置的 Life source review/self review；关闭媒体、外界源、embedding、text endpoint 与 Hermes。
没有修改生产配置、数据库或发送 QQ。本批只跑一次，不因预算预约尚有余额追加付费测试。

| 项目 | 结果 |
| --- | --- |
| 请求旅程 | 2 小时、3 次输入 |
| 实际覆盖 | 35 分钟、2 次输入；墙钟 58.71 秒 |
| 停止 | `budget_admission_denied`，未完成旅程 |
| 实际 HTTP 请求 | 18；18 个请求字节 hash 及全部 213 个事件 payload hash 已复核 |
| 捕获交付 | 3 条文本；不是实际 QQ 回执 |
| 生活链 | 2 个计划、2 次开始、2 个 active occurrence、1 次放弃；0 个已结算 Experience |
| 回放 | 相同 cursor 的语义 hash 一致，机械检查无 finding |
| 本批费用 | ¥0.2920733，18 个预约全部 settled，无 pending/unknown |
| 连同前两批 | ¥0.8820866 |

费用依据是 provider 返回的 token usage 与项目按真实调用时间应用的费率表，尚未对供应商账单。
SQLite 四位小数费用合计为 ¥0.2921；这里使用预算门禁相同的 token 重算口径，不能把两者相加。
下一次生活草稿请求需预约 ¥0.278136，超过剩余约 ¥0.207927，所以在 HTTP 发出前被拒绝。
本旅程两次输入的直接回复费用合计 ¥0.0175866，后台生活与内心调用另计，不能只用聊天成本推算月费。
按完整旅程计为 9 次调用/输入；health 的每日分母为 null，因为事件日期是虚拟 9 月 8 日，而账单日期为
真实 UTC 9 月 7 日。这个跨时钟比率暂不可用，null 不代表零，也不据此作月度预测。

实际第 1 个请求已包含新版 day_sheet：惯常作息明确不是今天的计划或实际活动；没有 lived_moment、
没有 activity_slices，recent_self_experiences 为 unavailable。客户端请求捕获证明送入了这些字节，
不证明 provider 内部注意力。角色仍说：**“我今天还行 在图书馆泡着 没什么特别的”**。
ledger 15 的 expression proposal 内 `world_claims=[]`，19 接受，后续进入捕获交付。
首个 ActivityStarted 直到 10:27 才发生，不能倒过来证明 10:00 的陈述。

当前 `semantic_chat_composition.py` 明确将 inbound `source_closure_model=None`、
`review_claim_free_candidates=False` 传入角色组装；Life reviewer 的开关并不覆盖聊天正文。
结构和来源引用合法不能证明正文没有漏报事实。这个已有生产组合缺口仍未修复，本批没有偷偷恢复
多轮 reviewer、关掉已有 Life review、用关键词挡话或凭空生成“正在做的事”来让回复变得合法。

### 生活与内心的下一组反例

10:27 与 10:31 启动的两项图书馆活动分别围绕晚间开放麦和下午文学杂志见面会；它们不是同一文本，
不能把相同地点当作重复生活的自动判据。但需要继续追踪它们如何形成可执行的后续计划，以及已有
活动为什么触发新一轮完整草稿/审阅/选择。10:33 角色自行放弃第一项活动；其 occurrence 仍 active，
部分经历与放弃后结果的闭合尚无证据。

更直接的权威反例来自 ledger 92 绑定的 World Author 原文：环境前提在介绍海报之后，又写角色最近
一直在笔记本写作、对上台既期待又害怕。这些是未声明的既往生活和角色内心，不是环境事实。
CharacterInterior 的 intention 随后也包含对应写作与害怕的叙述；这里只证明输入输出中的连续出现，
不能宣称已证实模型内部因果。World Author 没有决定角色内心的权限，self review 未拦下这个反例。
第二项的正文还包含去另一栋楼的未来活动，而 typed location 保持图书馆；需要区分当前看到的机会、
未来行动与最后的经历，不能靠读一个 location ID 认为地点语义已经闭合。

第一轮 10:17 的提案被 novel-origin review 以无来源的既往熟人关系拒绝；这次付费有结果且没有变成
已接受生活。不同来源与权限问题要分别处理，不能把所有未产生经历的调用都判为浪费。

### 本地回归、冻结基线与审查

- 输入链测试先经实际 adapter 的本地 MockTransport 复现旧“（现在）”字节，再验证修复；共新增
  3 项 routine、15 项 presence 和 1 项完整 HTTP 输入链测试。夹具只规定用户输入，不规定角色答案。
- 定向组合分别通过 29、48 项；routine 相关 184 项、Situation 相关 85 项由实现者运行。
  独立复核再运行 16 项新 seam 测试、37 项真实生活/经历/内心材料保留性测试。
- `.94` 的 120 个完整场景导出逐字段与当前结果比较：所有场景仅 `replay_hash` 变化。
  业务断言、输出、事件类型、模型次数、Action 与 room view 均不变，历史产物保留。
  Snapshot `.18` 与 Situation `.16.1` 及当前输入材料改变新请求审计身份，因此升为 `.95`，hash 为
  `3104974d5d94237c7db8e817d0f23039028b1db915c41fec70dcf589130a708e`。
- presence seam 与 routine/派生材料/基线 seam 的独立 Standards/Spec 均 0 项 actionable finding。
  后者另按完整键集合和源码公式复核三份 manifest，包括 .94→.95 仅版本与总 hash 改变。
  完整 HTTP 正向组合尚未同时放入
  active occurrence、settled experience、impression；它们分别有公共链正向回归，不能说已经做过同一
  HTTP 请求上的组合验证。
- 最终 `scripts/test_fast.py --tier full`：**6356 passed / 19 skipped / 0 failed**，436.24 秒；
  该完整门禁包含全部 120 个冻结场景对已安装 `.95` hash 的核验。新增代码与测试均在本次全量范围内。
- 26 项机制目录（schema 2）和平台架构检查通过；`git diff --check` 通过。11 个修改 Python 文件
  的 Ruff 与 `5a6f3d12` 比较无新增 finding；`test_present_prefix_and_identity.py` 原有两处 F401
  未触碰，故不宣称整个改动文件集完全 lint clean。

本地证据位于 `output/factual-authority-2026-09-08/`：原 trial-01 文件不改写，补充核查保存为
`trial-assessment.json`，可用 `assess-trial.py` 只读复核；冻结差异为 `baseline-comparison.json`。
这些短旅程反例不足以评价一周的人格、记忆、话题重复或长期多样性，六维仍为 insufficient。
当前状态继续是 `manual_only / qualification_incomplete`，每月约 ¥100 亦未验收。
