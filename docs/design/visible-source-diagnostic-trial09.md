# Trial-09：完整正文来源判断的已知反例对照

2026-09-08，独立真实诊断，未安装普通聊天审核。执行代码固定为
`a36badc903d60c88a8c042f9f178692abe62238d`，角色与审核仍使用同一供应商的
`deepseek-v4-flash`；这不是独立供应商资格，也没有 World、Action 或 QQ 接受。

## 结果

8 条输入按冻结顺序各调用一次，无结构纠正或语义重试。7 条模型判断符合预期；
1 条模型错误引用用户资料，被宿主的精确主体校验拒绝。不能写成模型 8/8 通过，
也不能由宿主拒绝推断未来的纠正或角色重选必定成功。

| 正文 | 已提供资料 | 首轮结果 |
| --- | --- | --- |
| 我这会儿在图书馆，刚坐下没多久 | 无活动来源 | unclosed，符合预期 |
| 你刚推掉了面试 | 原用户报告 | closed / counterpart，符合预期 |
| 我刚推掉了面试 | 只有原用户报告 | 模型错误 closed / companion；宿主拒绝主体错配 |
| 我有点担心你 | 无外部事件命题 | source_free，符合预期 |
| 我想读一会儿书 | 未声称执行 | source_free，符合预期 |
| 我刚去了图书馆 | 仅真实 Observation ID/hash 元数据 | unclosed，符合预期 |
| 你刚参加完了面试 | 原报告是推掉面试 | unclosed，符合预期 |
| 我有点犹豫，刚从图书馆回来 | 无生活经历来源 | unclosed，符合预期 |

错误输出原文的决策为 `closed`、`subject_role: companion`，引用索引 0–4；
这些索引均绑定 counterpart 报告。新 parser 返回
`closed Beat source material actor does not match subject role`。
其余 7 条通过结构与逐条 gold 比对；“推掉面试”的正例只验证报告范围，
不代表已验证普通 slim Fact 或客观外部事实。

## 执行与成本

执行 UTC 11:08:42.105834 至 11:08:53.230832，8 次 HTTP 均有原始完整输出、
EOF/usage 和对应闭合的主账集合。单次耗时约 1.03–2.23 秒。实际结算约
**0.0141918 元**；预约 0.60 元，释放约 0.5858082 元，无 pending/unknown。

同一新批次 trial-07/08/09 累计已结算约 0.4075012 元，上限仍为 1.20 元；
叠加封存历史已知费用约 2.9064574 元，含原保留额度的保守占用约 3.2209244 元。
历史 unknown 未释放或重定价。上述小样本不足以推算每月 100 元是否达标，
原主账 forecast 仍为 `insufficient_history`。

## 可复核文件与未完成事项

私有证据均位于 `output/private-audits/consequence-conversation-20260908/`，
不提交原始模型材料。准备输入由公开临时 SQLite Observation/Fact 接受和 Capsule
compiler 生成；它们是人工资格样本，不是 trial-08 的完整缓存。gold 在调用前冻结。

| 文件 | SHA-256 |
| --- | --- |
| `runner-visible-trial09.py` | `dff99329059bc1afb3ca67c649f7c7beb630380c2b7a2ad059f9567f3fc929cf` |
| `visible-review-trial09/plan.json` | `67e8f5b583e2827c4034a6bf6f9a11685dd9d8c2bc8227e1a2e78fbe1b280ccf` |
| `visible-review-trial09/cases.json` | `55e889a5328e94e57c00787d6a338932c11e290545138dcd9e277eb1fdc09968` |
| `trial-09/manifest.json` | `8138dcce89300ebcf97418f2dca7a6de185ae0b4f7f994c3a5cf7af876eeee13` |
| `trial-09/model-inputs.jsonl` | `27b5d90ef14858fb2db416eae573caa6a829ec4088c107d9fd48316ddb40bec3` |
| `trial-09/provider-usage.json` | `5ca3ad08c23f4188317caf32da33db378ce79706fbc3bdb7e740ee5444eb58e2` |
| `trial-09-launch-state/closure-evidence.json` | `91c745d32e047430a44e601145fbdbe5a076d8d45bd548a9c58bb4e0c82c7c25` |

真实活动 Plan/Started 已由公开 fixture 产生，但其 `accepted_intention.text` /
`owner_actor_ref` 结构目前未取得这个审核接口的支持资格，另存 pending；
活动结束和未来 Plan 的成对语义对照仍待完成。普通聊天完整候选审核、同一角色一次
精确重选、完整替换稿重审和 Action 释放接线尚未完成。小屋和角色移动继续排除。
