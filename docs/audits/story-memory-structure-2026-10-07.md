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
  同次查询中合法但被预算省略的关联场景保留坐标，可用record_ref/start=0继续；不暴露被遗忘、越权或尚未知情节点。
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

专项回归：最终结构/前史来源/故事读取/语义审核/时间/角色prompt等7个文件 **99 passed**；
Ruff与`git diff --check`通过。未配置独立静态类型检查器；使用现有Pydantic接口验证和Python编译检查，
不把编译检查称为静态类型检查。完整测试单独记录在下节。
本轮无付费模型调用，没有新增供应商费用；无生产 DB/配置/进程改动。

这不是P2–P4完成，也不证明独立抽取稳定、模型理解深度或数周生活连续性。P1已打通首个基础闭环；
跨记录故事覆盖规划、NPC私有前史接入、全局事件归并及实时前瞻仍按原设计后续推进。


## 真实捕获材料的离线对照

复用先前run-10的捕获文件，未再次调用供应商：17条正式候选记录中的68个被选来源块、4条类型关系
可重导出。原材料总块数69，其中1块不在这些记录的选用集合内。旧`.1`重导出hash仍为
`fdb142cadf04e99c7c3aef7c410e6a12457cc4263ed7da9c52cb7fe9421de9bd`。
新`.2`候选hash为`9da665422f6f5bc2e1d98e4f8e44dc91350a2c9de5ca147caf23c1be35ec1ea5`；
完整新审核请求148402字节，在既有256000预算内；旧审批拒绝绑定新结构。该新候选未审核、未导入生产。

**发现并保留的成本/覆盖取舍：** 同源、同问题、同明确照片记忆锚点、FeatureHash本地检索：
旧`.1`单次五节点10574字节；富结构在原预算下只能首批两节点。没有扩大预算或把部分读取称为完整故事。
补齐合法跨记录续读后，首批两节点8857字节，通过3次显式后续读取可达其余三节点；每个返回继续绑定
独立的记录+档案两项证明。这3次是控制试验指令，不是模型自主选择，也不是新增模型调用。
当前一次Recall/turn的角色调用约束未改变，实际角色是否会跨轮有效续读、增加多少LLM成本仍待测。
照片记录首批仍是480/492字符窗口，12字符尾段可另按`story_window.next`读取；五节点可达不等于
五段原文已一次全部呈现。这些限制列为后续P1读取效率/真实模型资格，不能用机制测试代替。

私有证据：`output/private-audits/story-memory-20261007/` 下
`historical-reexport-result.json`、`anchored-budget-comparison.json`、
`historical-continuation-result.json`及隔离fixture SQLite。fixture审阅/retention不冒充真实模型审阅或选择。

## Standards

独立Standards轴：无可操作文档标准违反或heuristic smell。角色决定权、旧哈希/回放、来源/隐私/
遗忘边界保留；续读候选只取消定位selector，保留其余资格，未呈现正文不获得事实权限。

## Spec

独立Spec轴：初审首轮闭环无缺陷。续读入口补丁复核发现1个P2：旧`.1`合法关联ID被提示为可续读，
但窗口筛选要求新结构而返回空。新增公共回归先RED，再允许无`knowledge_as_of`时读旧前史窗口；
历史视角仍拒绝缺少知情证明的旧档案。审查者复核确认闭合，无新增发现。

Standards：0项；Spec：1项P2已修复，剩余0项。P2–P4和真实叙事质量不在这个通过结论内。

## 完整回归

全量回归基于实现提交`783caed8`启动，原始输出保存于私有目录的`full-pytest.log`与`full-result.json`。
最终续读资格补丁另有上述99项专项验证。全量终态：**10187 passed、11 failed、19 skipped、2 xfailed**，1183.22秒。
11项失败集中在旧full-envelope工具的冻结schema/identity和当前native prompt版本转换，均由本轮新字段引入，
不是外部环境失败。新增字段被strict转换器变成旧工具的required nullable sibling，破坏历史字节契约；
另native转换仍只匹配旧`.6-slim`前缀。

修复：仅在旧完整envelope的schema生成入口移除两个新只读字段；旧冻结字节/hash断言原封不动。
当前slim/native仍通过JSON载体和版本化提示提供它们。native提示保留`.6-slim→.7-core`历史转换，
增加`.7-slim→.8-core`当前转换。相应7个文件 **136 passed**（20.17秒），原11项全部覆盖。
新增当前slim/native载体的公共测试验证两个新参数实际可传递；故事结构文件最终 **12 passed**。
两个独立审查者复核协议修复均无新增发现。

保留失败全量日志与`compatibility-rerun.log`，另存`verification-summary.json`。
这是“完整回归发现问题并由对应回归关闭”，不是最终提交单次全库全绿；没有为省时间删测试、更新旧hash期望或隐藏失败。
