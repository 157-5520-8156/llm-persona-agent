# 梧桐短稿前后的二十二天

先读 [episode-reading.md](episode-reading.md)。这段新增人生发生在2024年1月12日至2月2日：16个场景、32条主角可读记忆，另有1段不授予主角的作者世界层材料。校刊改稿、与许桥约时间、习题、书店夹子和饭桌问话共同构成一段连续生活。

它引用前二十年源稿已有的梧桐短稿3条记录，不把同一次创作或交稿再存成新事件。两份阶段视图分别覆盖整个交稿期和同期与许桥的相处，共享发生而不复制事实。许桥仍是既有高中同学，未改成恋爱或前任关系。

- `source.json`：原生源协议2，含有序阶段成员、发生时间、知情方式、明确关联与未决事项。
- `authoring-context.json`：本次写作绑定的旧历史与生平；新具体日期由本次创作提供，旧记录日期精度不变。
- `compiled-package.json`：编译器2可重复生成的导入包；没有伪造独立审核结果。
- `coverage.json`：结构范围；计数不是语义正确率。
- `qualification.json`：隔离导入、保留、费用和读取资格。

编译不调用模型：

```sh
PYTHONPATH=src python scripts/compile_native_story.py compile \
  --source configs/prehistory/native-schoolmag-episode-20261008/source.json \
  --brief configs/prehistory/native-schoolmag-episode-20261008/authoring-context.json \
  --archive-id prehistory-archive:celia-schoolmag-dense-20261008 \
  --out-dir output/schoolmag-episode-compile
```

包绑定已经导入前二十年材料的原历史基线。切换World或历史发生变化时，应从目标库重新生成brief并核对新增内容；旧包不能直接覆盖另一份历史。`compile_native_story.py import` 现在默认同时建立可读初始记忆，0次模型保留调用。需要只存档时显式指定 `--memory-mode archive_only`；底层 `import_compiled` 仍只接纳档案。

应用启动与 `initialize_prehistory_once()` 默认采用direct，不再让角色决定初始人生是否曾经发生或是否有资格被检索。显式配置 `WORLD_V2_PREHISTORY_INITIALIZATION_MODE=character_select` 才启用旧保留实验；该模式的 `consider_calls` 是逻辑调用数，供应商次数和费用仍以传输账本为准。既有已遗忘/拒绝的记忆不会因重新安装而恢复。

普通聊天、QQ capture、HTTP与驱动器共同读取 `WORLD_V2_RECALL_STORY_CONTEXT`，默认true，对应hybrid.17；显式false保留旧策略。普通检索命中会给出opaque阶段导航；CharacterRecallRequest可复制到 `episode_window`，选择 `overview` 或 `detail`，并用 `after_record_ref` 续读。概览提供部分原文来源，详细读取遵守原字节预算；缺失、遗忘和当时尚未知的成员不会由标题或摘要泄漏。

此前32条新记忆已在隔离世界通过旧模式实际保留，旧190条历史不变；35份阶段成员可完整分页读取。实际问答中，收稿与刊出状态得到了区分，但另一题未覆盖后来与许桥改约的经过，不能宣称自动整段复述已完全合格。详见[验收记录](../../../docs/audits/dense-life-episodes-2026-10-08.md)。

创作由本轮Codex直接完成，没有新增源作者远程调用；Codex会话费用不计入供应商实验账本。代码复查是工程检查，未增加日常生成后的独立模型审核步骤。生产与真实QQ未采用此包。

默认直接植入与普通聊天配置对齐的后续证据见[2026-10-08后续记录](../../../docs/audits/direct-memory-and-chat-parity-2026-10-08.md)。私下完整材料理解探针始终只算诊断，不能代替用户入口验收。
