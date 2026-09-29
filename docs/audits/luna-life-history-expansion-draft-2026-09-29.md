# Celia 人生记忆扩展候选：草稿质量清单

状态：隔离创作候选，未独立审核、未接受、未导入World、未做记忆联动验证。内容不能当作既有或实际发生的事实。

- 档案：`fixtures/world_v2/luna_life_history_expansion_draft.json`；契约 `luna-life-history-expansion-draft.1`。
- 规模：六个人生时期、十组重复经历、32条片段；多数短而具体，重点长度有差异。016为一条超过480字符的较长回忆，其他条目没有为了长度扩写。
- 时期是宽泛目录。“中学后期到离校前”不为2020–2024每年指定年级；001以家人转述的幼年夹子记忆为材料，日期范围为2007–2010，没有硬定年龄。
- 记录分开保存事件、当时感受和后来回看；后来回看允许留空。人物体验有开心、好笑、烦躁、嫉妒、无聊等不同状态，不要求每段都产生长期结论。
- 历史人物使用 `history:person:*` 命名；父母没有绑定当前NPC，匿名同伴没有映射林晚、乔宁等当前NPC。
- 既有12条前史未改。没有修改源码、配置或生产档案；未调用外部API、导入World、连接QQ或接触8787端口。

本地结构检查通过：JSON有效，32个ID唯一，六期、十组目录及人物/记录关联引用完整，时间落在所属时期且不晚于2026-08-13，单条正文不超过1600字符。检查不代替语义审查。

待完成：独立内容审核。如后续要接入现有 `PrehistoryArchiveDocument`，还需导出兼容格式并做契约校验；该步骤尚未执行。此草稿未导入，也没有证明真实记忆检索或选择联动。

## 兼容导出

已新增确定性导出脚本 `scripts/export_luna_life_history_draft.py`，输出为 `fixtures/world_v2/luna_life_history_expansion_unreviewed.json`（`character-prehistory-archive.1`，仅PrehistoryArchiveDocument，不是Reviewed包）。事件、当时感受和启动前回看使用分栏标签合并为statement；空栏省略。源创作JSON的SHA256绑定在 `source_artifact_ref` 中。导出将月精度扩展为该月首日/末日上海时区区间，将幼年范围写作interval；所有记录均早于World启动边界。

验证命令：`.venv/bin/python scripts/export_luna_life_history_draft.py`。脚本通过Pydantic解析和manifest生成校验；连续运行两次结果一致：32 records、11 entities、manifest SHA256 `f143dc9631e8a242497d4d88d74bd121041355379dd862b42bf0d645094c1c97`，输出SHA256 `b2608f89147c935d755e4566fcefe6f116e967640a94e54830ff90af798041ad`。目前仍待独立语义审核和明确导入；没有写入World。
