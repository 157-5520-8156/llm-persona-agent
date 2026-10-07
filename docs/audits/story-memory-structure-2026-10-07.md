# P0/P1 首个故事记忆闭环

实施基线：`186684a7`；用户同意继续后，在隔离候选分支推进。范围是长期故事的
结构保留、正式导入、角色可读材料和分段继续读取；QQ、生产导入、实时命运系统不在本轮。

## 已实现

- 冻结既有小说样稿、材料文件的原始字节哈希，以及友情/照片、工作/旅行、写作/自我解释三条交织线的事件集合、未知事实与知情时点反例。
  基准：`fixtures/world_v2/long_story_oracle_v1.json`。这是工程验收材料，不声明该样稿已成为生产历史。
- `.2` 档案保留 actor、原始源标识、逐块事件身份/种类/来源位置/原文引句、事件时间和独立的知情区间，以及有方向、有原文证据的类型关系。
  `.1` 序列化与哈希不变，既有审核不会自动授权新结构。
- 正式 ledger 导入、retention fixture、MemoryRetrieval、RecallCoordinator、审计与 CharacterInterior 读取共用现有来源闭包。
  角色只看经过权限筛选的文本与结构坐标，不读取全知原文引句、关系桥接引句或审阅者理由。
- `knowledge_as_of` 按整个记录获知区间的上界保守筛选；晚获知内容及其链接不会污染早期读取。
  当前遗忘/隐私/撤回边界仍优先。旧格式与实时材料缺少此知情证明时不混入该模式。
- `occurred_from/to` 对新结构按逐块实际发生时间检索，未知时间保持未知，不以晚获知时间冒充。
- 超出一个窗口的记录给出 `story_window.next` 坐标，角色可用现有 Recall 请求继续读取。
  坐标不提供额外权限，忘掉记录后继续读取也失效。预算不增加，覆盖仅声明“当前记录材料”，不宣称拿到完整故事。

## 可复验入口

离线导出，默认写未审材料，不直接导入：

```sh
PYTHONPATH=src python scripts/prepare_life_materials_prehistory.py \
  --materials <captured-linked-materials.json> --actor-ref <actor> \
  --archive-id <new-archive-id> --output <new-unreviewed-output.json> \
  --preserve-story-structure --story-links <captured-story-links.json>
```

新叙事抽取可在既有 `--ingest-narrative --semantic-review` 命令上加
`--preserve-story-structure`，新结构在独立关联之后、正式语义审阅之前生成。
原始材料没有类型关系 artifact 时，导出保留原始普通关联，不能把它们假装成因果。

自动 `.2` 审阅失败后的修订/恢复尚未实现；已有 `.1` 修订流程与捕获仍保持原状。
旧档案补建新结构需要新候选和新审核，不可覆写已接受记录。

## 验证范围与尚未证明的事

新公共接口测试经历 RED→GREEN：结构贯穿导入/冷重放/真实 Recall 读包；2018视角不读2020披露；
事件年份检索不误用获知年份；遗忘和跨角色/隐私边界；运行期事件伪装前史被原子拒绝；
旧序列化稳定；离线 CLI；长记录精确续读及遗忘后的拒读。审核用显式测试夹具，未冒称真实模型判断。

旧前史、语义审核、Recall 与 prompt 回归及完整测试结果在本节完成后记录。
本轮无付费模型调用，没有新增供应商费用；无生产 DB/配置/进程改动。

这不是P2–P4完成，也不证明独立抽取稳定、模型理解深度或数周生活连续性。P1已打通首个基础闭环；
跨记录故事覆盖规划、NPC私有前史接入、全局事件归并及实时前瞻仍按原设计后续推进。
