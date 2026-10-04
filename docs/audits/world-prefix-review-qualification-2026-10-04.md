# World Author 前缀候选的独立审阅补验

本轮没有开启生产候选、没有新调用 World Author、没有发送 QQ 消息。沿用此前保存的三份 SYSTEM 尾部指导候选；角色行为、世界频率、权限与所有来源验收保持。

## 为什么不继续微型压缩

三份真实 `life-source-review.21` 请求的 USER 内容分别约 83792、87626、80187 字符。作者快照展示和合格 source_readings 占大部分。

复用 shared-string 表示联合压缩这两块，原阈值只能减少总 USER 字符 4.2–4.9%；私有降低长字符串阈值到 32 字节，收益约 7.5–8.7%。私有同构对象列表转 columns/rows 的表格表示约减少 9–10%。全部逐值还原原输入，candidate、text_fields、permission_choices、current_authorship_authority 保持原值，但字符下降不等于模型 token、费用或语义判断改善。没有为它们付费测模型，没有修改 shared_string_view 或加入生产 codec。

不能直接删掉 author_snapshot_display：现有 source_readings 明确 `complete_source_coverage=False`，既有审核需要区分“没有依据”和“作者看到的相关来源没有合格 reader”。仅保留阅读表不能证明保留了这一判断能力。后续要做完整的合格来源投影，不能将这种删除当作无损压缩。

私有证据：`output/private-audits/life-review-input-size-20261004/`。

## 三份已保存候选补验

此前的 World Author 前缀候选把请求专属示例/执行绑定指导移到后续 SYSTEM，保留指令优先级；所有 USER 值与前版相等。三份新草稿已经通过原完整草稿与执行绑定验证，但未完成独立语义审阅。

本轮逐份重新计算展示 manifest 的 hash 与 location capability_ref，检查它与旧真实 novel-origin 请求的完整 pinned_cursor、manifest_hash 相等，并验证原执行 authority 与原作者请求完全相等。只替换本次新草稿的 reviewed_surface、坐标目录和它们的 evidence_packet_binding；focused 权限、来源、主体与已保存前史保持原值。所有前置检查在调用 Provider 前完成。

随后用实际 V4.1、原独立来源审核工具与完整解析器验证：

| 草稿 | novel-origin 审阅 | general 来源闭包 |
|---|---|---|
| 0 | supported / 完整解析通过 | supported / 完整解析通过 |
| 1 | supported / 完整解析通过 | supported / 完整解析通过 |
| 2 | supported / 完整解析通过 | supported / 完整解析通过 |

general 请求重新由现有编译器生成，使用同一原作者 pinned_world_context、同一 manifest、原执行权威；三份草稿均无 existing_world claim，所以 cited_events 空集合是其精确声明闭包，不是过滤掉已有声明。原 general 权限/主体/坐标检查仍执行。未改字段分类、reason 长度上限或纠错次数。

六次新增调用：focused 三次约 0.11101796 元，general 三次约 0.01820908 元，合计约 0.12922704 元。均保留实际账本和原始输出。它们是保存草稿的语义补验，不是新的缓存费用 A/B；不能拿这些审阅费用推算此前作者的降本比例。

限制：三个正例，没有新的否定留出集；没有创建 Runtime 接受回执、Plan、NPC 或 QQ Action，也没有完整生活品质比较。此结果只支持继续对既有候选做完整副本对照，不能支持生产默认启用或宣称达标。

证据：`output/private-audits/world-prefix-focused-qualification-20261004/` 与 `output/private-audits/world-prefix-general-qualification-20261004/`。原作者材料保留在 `output/private-audits/local-critical-path-20261003/world-system-tail/`。

## 下一次完整对照入口

`benchmark_daily_cost.py --world-author-cache-stable-prefix` 通过 `open_session` 显式传给已有宿主入口，默认仍 false；非 boolean 在打开会话前拒绝。provenance 记录实际选择，不再使用实验脚本替换 Runtime 构造函数而遗漏这一参数。新的对照必须与原 fixed snapshot、24 目标、50 输入、全部 runtime dues、V4.1/BGE、预算护栏完全一致，包含所有失败、未回答与完整原生延迟快照。

先让 exec session 54594 的原完整副本到达终态，再开始候选完整日对照。不得重启仍存活的基线，也不能用本次三个正例宣称完整质量与日费过关。

## 当前完整副本证据

截至本轮查看，已完成 16 条聊天，最近完整命令总费约 1.8864 元，原运行仍继续。这已经超过目标，不以启动积压或后台机制计费为理由宣称达标。

生日显式问题在当前连续运行里已出现完整记忆回答，随后的打火机追问也回答了具体内容，但首条分别约 8.05、7.99 秒；不能用“我想想”当作完成回忆，也不能仅凭有回答就断言没有扩写。下一轮仍需回到具体源记录检查。有一条普通回复约 4.986 秒，也不足以证明常规 <5 秒。整体成本、事实/回答质量、速度和生产资格仍未通过。
