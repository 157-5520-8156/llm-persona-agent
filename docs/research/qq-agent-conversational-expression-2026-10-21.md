# QQ Agent 的表情包、语气与对话活人感：源码研究与迁移边界

日期：2026-10-21。状态：**公开机制已核实；自然度收益是效果假设；Girl-Agent 迁移和真实效果待测。**

## 研究问题与范围

用户反馈 [QQ Agent 人设广场](https://kondius.cn/qq-agent/persona-plaza/)关联项目的 DeepSeek 接 QQ 对话很自然，希望学习表情包、语气等活人感。Girl-Agent 仍以有来源的角色生活、长期选择和后果为方向，不能把目标缩成群聊扮演，也不另造一套 harness。本文研究其表达材料和能力如何供模型使用，而不是移植整个框架。

公开作者仓库为 [K0nd1us/QQ-agent](https://github.com/K0nd1us/QQ-agent)。本次沿用冻结 commit **fdfd325e148d91845a0aeea3dfdff4cf92fc61a1**（提交于2026-10-17，package 0.4.0），固定链接如下。只读源码副本和前轮公开接口原始响应位于 `output/private-audits/qq-agent-research-20261021/`；前轮 `public-project-findings.md` 与 `public-evidence-manifest.json` 保留版本、公开卡和数据 hash。没有启动其应用、安装扩展、调用付费模型、发 QQ 或访问原候选数据库。

用户当时所看演示的版本、完整连续对话、模型型号、参数、插件组合与失败样本仍未知。因此以下代码结论不自动说明演示当时用了这些路径，也不能单凭代码证明自然度提高。

## 已核实：表情是可理解、可记住、可选用的材料

### 1. 候选目录进入角色输入，系统不直接替角色挑最终回复

`StickerManager` 同步 QQ 收藏表情并维护本地认知库，默认一分钟同步缓存；同步失败返回已有缓存，保留错误信息，不因异常响应清空库。表情条目包括稳定 ID、描述、模型本地笔记、标签、用法、发送次数、最近使用时间/语境，以及可发送地址。[管理器](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sticker-manager.js#L52-L126)、[条目字段](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L13-L44)

每轮组装上下文前取得库快照，再由 `buildStickerContext` 输出有限文本目录。默认最多10项；库大于候选数时，约半数为高使用稳定项，余项按小时与 ID 的确定性种子轮换；最多30项。目录主要展示描述（优先于本地笔记）、标签和 ID，不把完整 URL 或不断变化的使用次数塞进提示。[实际入口](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/orchestrator.js#L750-L759)、[候选算法](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L143-L189)、[注入位置](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L789-L796)

这属于**候选曝光**：代码没有把当前情绪映射到必须发送的某张图。模型可以不发、查全库、看图或选另一项。高使用排序也可能让旧常用图继续占位；轮换不等于已经解决语义重复或自然度问题。描述缺失的图会被提示为尚需查看，不能把未看图片的猜测当成视觉证据。[候选文字](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L180-L189)、[策略](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L193-L211)

### 2. 查看、理解、搜索、发送是分开的模型能力

| 能力 | 已核实行为 | 限制 |
| --- | --- | --- |
| `list_stickers` | 查询描述/笔记/用法/ID/标签等文本，返回稳定 ID 与描述/笔记/标签；模型自拟 query | 是字符串检索，不是语义检索；`usage`参与匹配但未出现在返回项中 |
| `get_sticker_image` | 取图片并构成真正的多模态 parts；GIF 可能转视频输入 | 依赖视觉能力；不是把 base64 当文本；没有证明纯文本模型看懂图片 |
| `sticker_note` | 模型写下本地理解、用法和标签，下轮可复用 | 是模型记忆，不是不可错的事实或行为命令 |
| `send_sticker` | 模型选择 ID，验证库项/地址后通过当前会话发送，返回结果 | 未强制存在“已看过该图”的回执；先看再发主要是提示引导 |
| `collect_sticker` | 从当前会话原消息定位图片，模型可决定收藏并备注；本地备份与 QQ 收藏分别记录 | 不能把“已收藏”解释成 QQ 端必定成功；返回 localSaved/qqSaved/warning |

代码依据：[检索](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L121-L140)、[发送/列表/看图工具](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L257-L361)、[笔记/收藏工具](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L365-L434)、[持久化笔记](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L214-L229)。

视觉工具在当前模型可用性下受门控；媒体从工具结果中分离，随后作为 image/video parts 附到 user 消息，避免把图像字节误当文本灌入上下文。这解释“模型拿到了什么”，不证明其理解正确。[视觉可用性](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/orchestrator.js#L756-L772)、[媒体回注](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/orchestrator.js#L1157-L1190)

### 3. 发送结果反过来成为后续上下文

模型发图后，代码仅在 `sendSticker` 成功返回后调用 `markUsed`，保存使用次数、最近时间与有限触发语境；发送器将自己的已发图写入聊天历史，内容是表情描述或本地笔记的文本标识。下轮既能知道自己发过图，也可以按使用记录调整候选。[工具执行](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L299-L306)、[使用记录](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L232-L248)、[发送与历史](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sender.js#L200-L269)

这里的历史主要是文本说明，并没有把上轮完整视觉结果永远保留。`lastContext`不是复杂情境记忆检索；候选摘要也没有展示它。不能说该库自动理解了所有旧梗、关系和表情的全部语用。

### 4. 可用性保障与去重边界

地址临时失效、路径搬迁和本地文件安全确有工程处理：限定本地收藏目录，发送前尝试转存；失败时尝试协议端缓存或 base64 等原素材载体，不生成替代文案。收藏流程保存 QQ ID与本地备份、区分失败。[发送预检](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sticker-manager.js#L127-L196)、[收藏流程](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sticker-manager.js#L224-L280)、[发送回退](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sender.js#L200-L269)

**不要误读“去重”**：本版本文字 `sendTextBatch` 有8秒完全相同文本指纹去重；`sendSticker` 路径没有调用那套指纹。收藏幂等/条目合并、发送次数记忆、软提示避免重复也不等同外部发送 effect-once。[文本去重](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sender.js#L82-L120)、[表情路径](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/sender.js#L200-L269)。技术回退面对“发送超时但实际已送达”时，仍需分别评估交付幂等性，不能据其注释认定 Girl-Agent 可删原回执边界。

## 已核实：语气示例与真实对话共同作用，但也有冲突

核心系统提示赋予“正在群聊的人”这一沟通位置，鼓励选择性回应、少总结/服务/附和、从当前群友的长短和分条习惯学习。默认卡另外给大量正反例和鲜明角色反应。每轮动态上下文则保留真实发言时间、发言人/QQ稳定身份、引用对象、已读/未读、自己的已发消息，以及相关成员印象；模型在同一次请求中同时看到“怎么说”的样本和“此刻在和谁说什么”。[核心风格](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L49-L122)、[角色卡](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/personas.js#L1-L145)、[实际消息格式](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L298-L340)、[历史与触发](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L516-L579)、[动态输入](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L789-L887)

普通 assistant 正文默认不向 QQ 发送，显式 `send_message` 才外发，允许无工具结束；数组元素是模型决定的多条完整消息。图文独立气泡、引用/@、拍一拍等真实聊天能力，让模型不必把所有态度压成一段完整说明。[协议](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L35-L46)、[发送工具](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L160-L180)、[拍一拍](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/tools.js#L440-L446)。其实际发送器会添加等待，属于产品呈现策略，不足以证明随机等待就更自然。

也发现不应当神化的部分：

- 默认卡既有短句/沉默建议，又规定点名必回、指定人设反应和很多口头模板。核心轻量记忆只想记稳定人物印象，而默认卡后段仍建议记录进行中话题/未尽想法，存在指令张力。[核心记忆](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/prompt.js#L125-L130)、[默认卡尾部](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/personas.js#L190-L196)
- 表情“积极度”改变提示文字，包含大约几轮一张等软频率建议；发送器没有按此轮数强制发图。这依然是行为偏好指令，Girl-Agent 不应搬成硬配额。[表情策略](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/stickers.js#L193-L211)
- 人设广场不是一套统一风格；前轮公开样本有碎短默认卡、偏长表达卡和简短开放关系卡。社区卡、安装插件、系统覆盖都可改效果；未知用户当时用哪套。
- 固定角色卡允许脱离世界来源随口描述吃饭/外出等。它可能强化聊天观感，却不能证明长期生活、共同经历或剧情后果真实存在。[角色卡](https://github.com/K0nd1us/QQ-agent/blob/fdfd325e148d91845a0aeea3dfdff4cf92fc61a1/src/personas.js#L14-L100)

## 效果假设：值得验，但尚未证明

| 假设 | 最小可区分对照 | 不应当算作证明 |
| --- | --- | --- |
| 明确表情含义/适用语境，比只有 ID 更贴切 | 同一角色事实、同一对话，只变化候选元数据或真实看图证据 | 发图次数变多、HTTP 200 |
| 当前会话对象/回复指向清楚，能减少乱关心/抢话 | 同一对话分别保留/缺失引用对象和上一轮发言 | 只挑一张漂亮截图 |
| 少量合角色的表达正反例有助于摆脱任务汇报腔 | 同一 pinned 情境与角色动机，单变量增加 advisory 示例 | 把角色变成默认鲸鱼、指定必须顶嘴 |
| 自己刚发过什么可读，能减少重复表情/重复补话 | 同一场景显式保留真实发送回执和对应材料 | 本地直接压掉不同角色意图 |
| 常用项与可探索项同时可见，能兼顾熟悉感与选择空间 | 记录候选曝光与模型自主选择，比较实际语用 | 轮换数/题材数、强迫没用过的图 |

这些对照应复用 Girl-Agent 已有 harness，区分单轮观感、长期角色一致性、事实/隐私闭包、真正外部交付和时延；本文没有进行任何付费 A/B 或自然度盲测。

## 迁移原则：学习材料和能力，保留 Girl-Agent 的目标

1. **提供可用材料，而非决定行为。** 可借鉴稳定素材 ID、来源/可视证据、意义与使用语境、真实最近发送记录；角色决定文字/图片/混合/不回复和具体语气。候选曝光可以有限且可重放，不能把情绪分档后固定映射到表情或话术。
2. **风格材料与事实来源分开。** 正反例只能是可选择的表达参考，不是已发生经历。图片内文字和角色用图暗示的事实同样要守原隐私、同意、事实引用；不能因它是表情而免检，也不能把“文字笑话”当现实行动。
3. **视觉不确定性要诚实。** 精确素材证据可供模型查看/回忆，缺证据可选别的能力或不使用；不能让代码按标签冒充角色已经看懂。看图成本与当前模型模态能力要真实记录。
4. **技术成功与角色决定分别记录。** 真实授权、CAS、effect-once、回执和重放仍由原机制执行。不得用时间窗文本/图片去重替代角色自主性或真实交付身份。
5. **不另造社交规则框架。** 不移植必须回应、每几轮一图、随机决定开口、固定顶嘴/安慰模板。可以缩清材料与能力描述，减少互相矛盾的提示，靠同一角色模型作语义选择。
6. **先定位现有缺口再应用。** Girl-Agent 已有输入/表达/素材/交付和验收路径，应逐项映射证据缺失、能力不可达、提示冲突、模型选择或交付失败；不能只因别项目有模块就复制整个模块。

## Girl-Agent 实现映射与验证状态

本次只读对照 Girl-Agent `6fcc753e31c67d06e8e622ebbc84b42133773c1a`。没有把研究内容安装到正在验收的源或改生产 QQ。

| 方面 | 当前已核实实现 | 迁移方向及边界 |
| --- | --- | --- |
| 角色表达决定 | `inbound_prompt.py`、`inbound_author.py` 已支持分条、延后、沉默、Recall、内心/关系/情绪等选择 | 先检查实际有效请求，不能把已存在能力再做一套。普通对话材料要清楚呈现对象与近轮连续性；风格示例仅作参考，不能强迫某语气 |
| 现有贴纸 | `expression_payload_contract.py` 的 QQ_STICKER_OPTIONS 是9个标准 `qq-face` 候选；`qq_delivery.py` 的 send_sticker 最终发标准 face segment | 这条已核实路径尚不等于任意收藏图素材库。值得补的是有来源的素材、语义/视觉材料、模型自主选择与真实发送记录；其他图片通路是否可复用需进一步查其权限和回执 |
| 既有 Action harness | `expression_action_capabilities.py` 已安装 sticker/reaction/typing 的不可变 payload、Acceptance、transport、receipt_recovery；`qq_c2c_transport.py` 继续校验标准贴纸协议 | 自定义图片若接入，应复用原授权、交付、回执与 effect-once，不能把远程URL直接塞入标准face ID字段或照搬对方发送回退 |
| 已有输入感知 | `qq_face_render_catalog.py`、`qq_attachment_perception.py` 区分平台标签、真实视觉和对方主观意图 | 图片名/标签不证明对方情绪；素材理解笔记也不升级成外部事实。未知、未看过或缺少有效图片时保留不确定性 |
| 提示容量 | `inbound_context_presentation.py` 已有稳定前缀和无损压缩；`inbound_prompt.py` 存在多种原请求版本 | 需测真实编译包，而非用文件总行数/静态总字数代替模型负担；不为自然度移除事实/隐私校验 |

本地代码入口：

- [角色提示](/Users/geoff/.codex/worktrees/reference-contract-repair/Girl-Agent/src/companion_daemon/world_v2/character_interior/inbound_prompt.py)
- [表达载体与标准贴纸](/Users/geoff/.codex/worktrees/reference-contract-repair/Girl-Agent/src/companion_daemon/world_v2/expression_payload_contract.py)
- [可达能力与原交付闭包](/Users/geoff/.codex/worktrees/reference-contract-repair/Girl-Agent/src/companion_daemon/world_v2/expression_action_capabilities.py)
- [QQ 实际发送](/Users/geoff/.codex/worktrees/reference-contract-repair/Girl-Agent/src/companion_daemon/qq_delivery.py)

**应用次序**：先在现有 harness 记录有效输入和实际能力，再单变量补足材料呈现或可选表达样本；素材能力确有缺口时再闭合图片选择和交付。把图片视为角色的表达材料，不再搭建另一个角色/记忆/调度框架。候选目录的筛选只决定曝光，不代替角色决定是否发送。

**效果验收**：固定同一角色、事实、连续对话、模型/参数、能力和原审核；分别比较原呈现与新呈现、无示例与 advisory 示例、纯ID与可靠素材描述/视觉证据。自然性、贴切性、跨轮一致性、重复补话、虚构事实、合理沉默和端到端时延分别记录，失败全部保留。看完整连续对话并隐藏方案标签，不使用发图频率/关键词命中/少几个token代替体验判断。图中文字及其实际语用需纳入证据和隐私边界。

**状态**：本轮完成公开源码研究与现有能力映射，未实施图片素材库改造，未做付费 A/B、真实 QQ 或自然度盲测。长故事无人值守验收与聊天活人感是两个独立效果项；有 harness 使可控验证和执行可复用，不意味着自然度已得到保证。

## 2026-10-21：首次应用的具体范围

公开隔离聊天入口确认：旧 slim 的 messages 只编译文字；完整双草稿格式可以选择已安装表情，说明原 Action/权限链路可复用。新的独立 `slim2` 载体把单字段 `sticker_id`、`reaction_id` 与 `typing:true` 编译为原 Expression Beat，保留原选项权限、typing 后续内容要求、延后仅文字、来源审核与回执；角色也可混合文字或沉默。当前 v25 非 native-core 组合默认使用新载体，显式旧载体与原 native-core 配置保持原行为。

17 个旧工具/提示/编译对照的 151,651 字节完全相同。隔离 QQ transport + CaptureDelivery 覆盖实际发送、同库重开和原已付费旧载体 Action 的恢复，不会重复作者调用或投递；非法选项继续交给同一角色纠正。本次没有增加图片素材库、题材或表情使用频率规则，也没有证明真实连续对话更自然。该表达改动不进入正在观察的 v53 冻结源；生产 QQ 未改动。
