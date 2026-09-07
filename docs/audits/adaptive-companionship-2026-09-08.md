# 2026-09-08 自主迭代与逐回合试聊

本阶段由用户明确授权创建 Goal 并持续迭代。仍在 `codex/living-continuity` 隔离分支工作，
生产数据库、配置与实际 QQ 不在写入范围。阶段新增真实模型试验总预约上限 ¥2，每个 fresh world
上限 ¥1；此前三次试验的 ¥0.8820866 持续累计，不能用新数据库抹去测试成本。

## 已确认问题与机制修复

- compact `reply_only` 与 `full_turn` 曾无条件把角色的 `world_claims` 清空。现在保留其原文、
  scope 与 source refs，缺省/null 仍兼容空声明。角色陈述外部事实应明确提供声明，但结构校验
  **不能证明自然语言正文没有漏报**，是否改善必须继续核对真实请求与交付。
- expression materialization 曾删掉非法引用、保留对应正文。生产开关与无人调用的 helper 已移除。
  实际 CLI → host → HTTP adapter 的本地伪造来源反例现在不能产生 `ActionAuthorized`；
  允许既有显式服务故障通知，不把它当成角色回复或角色选择沉默。
  实际 HTTP 本地反例另核对每次尝试恰好原始＋一次校正：两者 snapshot ID 相同，校正请求包含
  `role_result_correction` 与精确的时态/来源失败原因。adapter 单次失败不代表最外层没有重选。
- Deliberation 曾对 Capsule 未绑定的 evidence 再做一次相同剥离。现保留原候选并报告精确失败引用，
  公共回归覆盖无恢复、已有同角色恢复成功、第二次仍非法；正常来源保留并成功。七项旧反向测试
  曾把伪造来源通过当成成功，现恢复拒绝；另两项正向修正 fixture 的 speaker/scope，继续要求通过。
- 已有 World Author novel-origin reviewer 的 `.2` 请求曾完全遗漏 premise。`.3` 在同一次请求中
  包含完整 premise 与权限边界；审阅者用原文片段标出未来源化的既往经历及越权内心。
  片段不存在、用 outcome 片段代替 premise、判 supported 却同时给出违规坐标都会失败。
  不增加审核调用次数；完整 premise 会增加该次请求 token，已有预算预约使用实际新请求。
- 保持当前 one-shot 组装，不复活已退役的泛化 LLM reviewer，不使用关键词拦话、模板补答、
  固定社交规则或擅自生成当前生活事实。

## 可复用的试聊入口

`scripts/run_world_v2_longitudinal_audit.py --interactive` 复用安装的 QQ host 与长程运行器。
输入仍是外部用户消息；角色选择由正常模型链产生。命令使用 stdin JSON lines：

```json
{"id":"hello","at_minutes":0,"text":"今天怎么样？"}
{"wait_until_minutes":20}
{"id":"return","at_minutes":20,"text":"刚忙完，回来了。"}
null
```

每次输出上次观察以来的交付、输入、终态与技术失败，操作者可读完再决定下一条。
等待只推进已登记的时钟与调度，不制造用户消息或替角色选择沉默。命令不能写角色动作、
越过旅程边界或回拨时钟。stdin 可取消，无阻塞读取线程；退出仍关闭 host 与其资源。
`operator-commands.jsonl` 纳入产物 hash，timeline 保留实际消息。`null` 或 EOF 记录
`operator_stopped`、`completed=false`，不能把主动结束试聊当成完整旅程通过。

首轮独立审查发现操作者等待后的账期复查缺失、终点未显示尾段交付；分别补充公共 runner
回归，在任何新模型工作前重查真实账期，并以只读最终观察显示尚未读过的尾段。

本地回归使用实际 host、fixture 模型与本地 HTTP 对端；只能证明机制，不评价人格与真人感。
真实试聊证据将保存在 `output/adaptive-companionship-2026-09-08/`。当前阶段尚未完成，
长期六维、真实 QQ 与每月约 ¥100 均未验收。

## 第一轮真实对话与继续修复

trial-01 在 `c49e8996` tracked clean 上运行。输入“嗨，今天过得怎么样？”后，实际捕获交付为
“还行吧，早上在图书馆看了会儿书，你呢”。请求中 current/past authority 均为空，没有 active
activity 或已结算 Experience；ledger 15 接受的表达仍为 `world_claims=[]`。这再次反证
“保留声明即可防止正文虚构”。当时只保存请求字节及归一化结果，不能判断模型原 wire 是省略、
null 还是显式空数组，也不能把已归一化 proposal 的 response_hash 当成原始 HTTP 响应 hash。

随后实际 PTY 输出触发 `BlockingIOError`，本轮记 `technical_failure:BlockingIOError`，
1 次输入、2 次模型调用（聊天和互动事实草稿）、¥0.0250003；所有预约已 settled，
连同此前三轮为 ¥0.9070869。这次工具故障不是角色沉默，也不是供应商故障；原试验目录不重用。

后续修复：

- 撤掉 world_claims 属于行为任选字段的说法，统一“是否陈述由角色决定、选择陈述后来源不能省略”。
  给真实 compact slim 示例及其说明 schema 补上已有字段，并加入仅展示来源映射的占位格式；
  不提供可照抄为经历的具体生活样例。替换重复文字，实际请求仍通过原 46,000 字节门禁。
  描述性 slim 字段由 25 增到 26，实际 provider carrier 仍为两个字段，未扩大 provider 工具限制。
- 真实本地 PTY 重现 dup(stdin) 共享 open-file-description 导致 stdout/stderr 一同进入非阻塞。
  审计入口改用独立终端描述符与可取消的就绪读写；262KB 观察在消费者延迟读取时保持完整，
  不阻塞事件循环、不改继承的 fd flags、不裁剪输出或吞掉错误。最终 CLI 摘要使用同一路径。

提示、示例与字段说明一致只是改善模型可用性，不是语义完整性证明。继续用真实逐回合对话复测。
