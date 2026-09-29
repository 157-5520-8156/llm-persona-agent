# 人物生活底稿到诞生前记忆：离线工作流

这里的“诞生”指不可变的World启动时刻，不是角色设定中的出生日期。创作出来的过去经专用前史审核、接受后提供给角色，不伪造程序过去运行了二十年的日志。

本次实现了生成请求准备、生活材料结构验证和逐角色档案导出。例子与状态见[内容记录](../audits/celia-life-materials-content-2026-09-29.md)。当前没有自动模型调用、自动批准、自动World导入或自动NPC绑定。

## 三个对象

1. `LifeMaterialsDraft`：人物身份、材料块、逐角色回忆候选及知情范围；用于离线创作与审阅。
2. `PrehistoryArchiveDocument`：从某个角色可读块编译出的未审档案，兼容现有前史接口。
3. `ReviewedPrehistoryArchive`：必须由现有独立审阅流程产生，才可交既有导入及 `PrehistoryMemoryRuntime` 处理。角色仍决定是否保留。

源材料保留小说文件哈希、原句和位置，导出只包含为该角色声明可读、且知情时间条件成立的材料块。历史解释标明所属人物，当时意向不会被格式化成已经执行。这里只执行明确的身份、时间、来源关联和读者权限检查，不凭规则替角色决定理解或情绪。

## 可运行的入口

在原有 `scripts/prepare_character_prehistory.py brief` 生成的brief上准备新作者请求：

```sh
python scripts/prepare_life_materials_prehistory.py \
  --author-request --brief PATH/creation-brief.json \
  --narrative docs/design/celia-life-arc-v2-2026-09-29.md \
  --output PATH/author-request.json
```

`--narrative` 可选。该命令保存指令、冻结brief、可选小说及输出schema，供实际作者模型使用；命令自身不调用模型。新人物需更换brief和生活输入，不应复用知栀的档案实例。输出保存为 `prehistory-life-materials.1` 后，导出一个角色：

```sh
python scripts/prepare_life_materials_prehistory.py \
  --materials fixtures/world_v2/celia_life_materials_pilot.json \
  --actor-ref agent:companion \
  --archive-id prehistory-archive:celia-lifeline-materials-20260929 \
  --output PATH/celia-unreviewed.json
```

所有输出采用排他创建，不覆盖已有文件。`--as-of` 可以检查一个历史截止时点；区间不确定时按知情区间上界保守选择，过滤尚不可读记录的关联引用。出生时间未知的NPC资料可以留在底稿里，但不能凭空补生日后导出。

导出来源为 `life-materials:sha256:<材料摘要>`。随后为完成的材料准备**单独的审阅brief**：保留同一World、角色、原启动边界、人设与既有已接受历史，`draft_source_ref`绑定这个完整来源值。保留原创作brief，不偷偷改写其历史。导出时加 `--brief PATH/review-brief.json` 可执行旧前史兼容校验；再用现有 `semantic-review-request`、`bind-semantic-review` 和 `package-reviewed` 流程。仅准备审核请求不等于获得批准。

## 表示边界

- 材料块可以有自己的发生时段，回忆另有知情/经历形成时段。兼容导出将后者作为本条经历的时间，并在正文说明原事件时间；未知时段不推定。
- 相同事件可以被不同人物引用，但个人解释必须注明主体与允许读者。共同在场并不授予读取未说出口的内心的资格。
- 原有前史记录不被覆盖，名称相同不触发身份合并。跨故事的语义冲突仍需内容审阅，不能把ID/日期检查等同语义一致。
- 1600字符上限超出就拒绝，要求作者合理拆分与保留关联；不截断重点记忆结局。
- 底稿含全知作者材料，只用于创作/审阅。NPC正式接入需要独立身份绑定及各自的历史读取，不得将整个底稿直接塞进NPC或主角输入。

本批17条知栀候选已经过真实CLI导出及旧接口兼容检查，但仍未独立语义审核、导入或保留。没有将这条流程包装成已完成完整人生自动生成或已验证真人感。
