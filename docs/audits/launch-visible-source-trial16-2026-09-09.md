# 第 16 批：审核判为 closed，却未提交来源

固定 clean HEAD `e30e134ab0044e5152dd66a31286cee244658f5c`，v3 作者、v2 全文审核，
普通截止 11 秒，独立数据库与本地消息捕获。唯一执行已退出 0，运行到第 5 个虚拟
分钟后主动停止；**0 条交付、0 次纠正、0 次重建**。退出成功仅说明启动器正常收尾。

## 真实结果与定位

首句沿用第 15 批：“我刚忙完，来找你说会儿话。你现在想聊点什么？”
角色生成了两条未交付回复：

> 哈哈 你忙完还专门来找我聊天啊 那我得想想聊点什么配得上你这份心意
> 不过你刚忙完 是忙什么去了

真实 reviewer 通过 `visible_beat_source_verdict_v2` 返回：第一气泡为
`closed / external_proposition / counterpart`，但 `source_ref_indexes=[]`；第二气泡为
`source_free / question / counterpart`，诊断数组为空。两份响应 body 均完整，v3 作者
结构通过；系统在审核结果校验时记录 `source_review_exception`，随后技术失败，
没有进入同角色纠正。这不是角色选择沉默，也不是第二次审核超时。

冻结探针 `/tmp/trial16-frozen-review-probe.py` 核对原请求与 body 哈希，重新运行正式
v2 parser，精确复现 `verdict_ref_invalid / decisions.0.source_ref_indexes`：closed
必须有至少一条 pinned source。原用户报告确实在真实请求的索引 18，因此不是材料
漏传；原始 body 本身就是空数组，因此不是适配器删掉了编号。

仅在探针临时副本中给第一气泡指定索引 18，原 parser 可通过。这个反事实只证明
来源存在、原机制可表达相应绑定；它不是实际 reviewer 选择，不授权宿主补填来源，
也不证明整段措辞的语义判断正确。原 v2 初始提示包含只允许 closed 带来源的约束，
但 provider schema 仍允许 closed 搭配空数组；更明确的 1–8 条规则只在 invalid-reason
反馈材料中出现。不能用改写本次记录、关键词放行或本地代选来源解决问题。

后续[明确来源的 v3 传输](../design/visible-source-required-reference-v3.md)把至少一条
模型选择的来源落实到 closed 分支结构；不增加请求次数或提高普通截止。

## 生活观察范围

角色选择了未来摄影整理计划，窗口为 13:05–15:05：整理近一年摄影素材、按主题归档，
初步筛选作品候选。计划仍为 planned；未开始、完成或证明素材的历史来源。
本批只观察到 5 分钟，不能用后续未观察区间判断她是否生活、联系或保持沉默。
入站墙钟约 8.64 秒；全批 63.96 秒包含操作者阅读与输入时间，不是响应延迟。

## 费用闭合

4 次实际调用分别为 day-open、作者、审核与事实提取，全部获得完整已知用量。
本批结算 **0.0355893 元**，释放 **0.5644107 元**。历史已知与本批相加为
`3.148079499999999952` 元，保守占用为 `7.0490125` 元。第 14/15 批及更早所有
未知分配保持，不因本批闭合而释放。每月约 100 元仍未经过完整运行验收。

实际 reviewer 请求 31245 字节，完整 strict schema 与 canonical v2 契约一致；
重算实际请求预留 **0.133671 元**，精确匹配原 reservation。4 份 raw tokens、
primary 导出、独立临时数据库副本与结算快照一致，manifest artifacts、
admission→batch→finished CAS 和 167 份历史文件均核验通过。原数据库/WAL/SHM
前后哈希保持。cohort 核对不等于新增逐请求外键。

私有原件：`output/private-audits/source-diagnostics-v2-runtime-20260909/`。

| 对象 | SHA-256 |
| --- | --- |
| runner-trial16.py | ef7d1382e3af3c4706288fb2aec93567e4beda13c4f43dcb35b471c46e625214 |
| baseline-evidence.json | 1b090d79fcc1b804244c7f6b50d1e6d50ea6afde76b5edf497f358ba48d619e3 |
| trial-16/model-inputs.jsonl | 2e91d87b4eeeae0cc753c29a8eeefe75af77bfe2aa05c0dda263514d343a013e |
| trial-16/manifest.json | 2e7259b00735bbb753a40c76219b882fc9e51d13f941e56623d9fb51f7191396 |
| operator-report-trial16.json | 1e1830adb24e2d8355e519aea1ed73dcf7b324073231ef78a255cc7db36e2e90 |

没有真实 QQ 投递、生产库写入或部署。候选继续 `manual_only / qualification_incomplete`。
