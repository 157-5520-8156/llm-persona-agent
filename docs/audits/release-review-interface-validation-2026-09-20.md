# 来源审核接口与真实续跑

本页按阶段保存证据；最新发布判断和唯一有效继续点见
[当前发布状态](release-status-2026-09-13-current.md)。

代码 `64f41e06`，发布资格仍为 `manual_only / qualification_incomplete`。
本记录保留失败结果，不以离线测试、API 200 或来源展示改善代替角色交付。

## 可见聊天

v23 在每条来源旁展示已有用途，在每个固定命题旁保留原有 `reading_id → scope`
映射。来源、权限、两个独立读取者、完整原文及遗漏检查不变；239 项针对性检查通过，
35 张历史回执分别在两份临时数据库中冷验证。v22 的冻结编译保持一致。

复用实际双读取者原始返回及完整来源，进行 4 次真实来源审核：

| 控制 | 实际结果 |
| --- | --- |
| 自然承接用户家人送用户出发 | 仍误拒，要求报告事件的独立客观证明 |
| 错写成用户送角色出发 | 正确拒绝 |
| 把计划写成已完成 | 正确拒绝 |
| 把言语或环境变化写成角色行动 | 正确拒绝 |

实际请求与准备文本一致；自然承接说明、用途映射和共享字符串均确实到达模型。
因此不能宣称展示修复已经解决语义问题，v23 不取得发布资格。

随后仅对前两项启用供应商推理，保持相同来源与独立读法。客户端同时移除
`temperature`、加入 `reasoning_effort=high`；这不是完整请求只改变一个字段的对照。
两次均超过原 22 秒期限，没有语义结论。没有扩大期限或据此部署新模型配置。

## 生活链

同一角色从 61 分钟推进到 63 分钟，保留 v22 聊天配置，验证 Life `.11` 紧凑行修复。
4 次真实调用中，两个审核请求均 HTTP 200、完整返回 `review_life_candidate_v6`；
38／30 个字段覆盖完整，所有 source_span 都是对应候选字段的精确子串。
精确 Fact 展示及新的快照适配已经走过真实接口，上一紧凑行错误没有复现。

两项新失败分别是：`created_current_states` 中两条 `time_relation=future` 被严格检查
拒绝；另一次选择已绑定的 Fact 权限却遗漏必填 `quoted_value`。两者均未被本地修补
成成功，没有新的 CharacterLifeResponse、Experience 或角色交付。

后续 `fa1a2ccc` 已实现新调用的 `.12`：当前创作状态的时间字段与实际校验一致；仅在
权限已唯一绑定精确接受值时，由编译器解析该值继续执行原权限／哈希检查。83 项相关
离线检查通过，`.1–.11` 请求和 readings 与旧编译器逐字一致，旧真实 `.10` 回执冷验
通过。两份 `.11` 原始失败仍拒绝，未改作者绑定，未重标旧请求；新协议尚待真实调用。

## 费用与继续点

| 试验 | 实际调用 | 已知估算 | 新未知预留 |
| --- | ---: | ---: | ---: |
| v23 非推理来源对照 | 4 | 0.30638940 元 | 0 |
| 同来源推理配置对照 | 2 | 0 | 1.418922 元 |
| Life `.11` 续跑 | 4 | 0.15491072 元 | 0 |

未知预留不是已确认消费。各阶段均独立对账；旧账按稳定主键保留，推理试验没有改动
World，Life 续跑完整继承最新累计账目。第一次续跑启动的私有目录约束错误发生在
宿主构建前，0 次调用；另建目录后继续，原始失败记录保留。

最新运行及费用统一继续点：
`output/private-audits/release-clean-continuation-20260920-03/run`。
累计 **1553 usage /1550 reservations /66 unknown holds**，ledger **405**、revision **193**。
冷重放一致、机制检查无 finding，旧 2 张 v22 回执原字节继承并冷验证，新增回执 0。
没有存活的付费进程，没有真实 QQ 或部署。旧检查点只能作历史证据，不能直接恢复旧账。

## 回归收敛

全量缺陷扫描在主动审核超时用例处停止，已有 6285 passed、18 failed、2 xfailed；
剩余 3003 项另行运行。这是查找回归的扫描，不能记作全量通过。

- `a983933c` 恢复 atomic v1/v2 原编译字节，8 条原 golden 不变；v3 保持修前字节。
  核心 9 项及更新 fixture 41 项通过，相邻其余 117 项已通过；35 张真实回执冷验通过。
- `3989d9a9` 把纯 reviewer 配置从执行 runtime 分离，保留数据库创建前的配置拒绝；
  修正价格时间和 persona 的旧测试断言，生产计费和角色配置未改。209 项通过。
- `2ee667fc` 修正架构检查的模块边界，避免把 world_v2 误认作 legacy world；25 项通过。

尚在处理的失败包括旧生活/感知测试未准备合法来源，以及主动审核超时后的审计收尾。
这些离线修复不代表正常聊天误拒、生活记忆链或演示上线已验收。

## 后续 ce28d96d 真实续跑和独立反证

Life `.12` 续跑 `release-clean-continuation-20260920-04` 已终止并独立对账。
普通短等待未触发下一次 heartbeat；推进到正常 heartbeat 后实际调用 12 次，另 2 次
在本地物理上限处拒绝。11 笔原生用量已确认，估算 0.24318082 元；3 笔新增未知预留
合计 1.202158 元，其中包含 2 次未转发的保守预留，不是已确认消费。

`.12` 第一次完整返回 31 个字段，精确 Fact 权限和当前状态 wire 校验通过。
审核拒绝 3 项：角色补写了没有来源的“站一会、翻页、离开”；另两项把当前评价里的
“不声张的认真”当作外界事实，这两项是否过度拒绝仍待分析。原稿整体不能接受。
同一角色的修正稿已返回，但第二次审核被物理调用上限阻止，不能称作修正通过或失败。
没有新增 CharacterLifeResponse、Experience 或可见角色交付；主动审核还出现超时。
新回放 ledger422/revision195，哈希 abc94cd7db113d9c1fc6282ad94b7163060f3f4bc7fbc94c106b9914b8605d84，
冷重放和机制检查通过，2 张旧 v22 回执原字节保留。

同次运行完成了真实同 owner 登录后浏览器检查：1440px 桌面、390px 手机和生活聚焦
录制视图均已同步，无 JavaScript 错误或横向溢出。截图及 JSON 在该私有运行目录。
这是显示检查；事件正文尚未读取的卡片仍不能讲清发生了什么，正在接入有权限的环境摘要。

随后在 `2dafb527` 做独立 `report-uptake-support-basis-experiment.1`，仅改变诊断返回
接口，不写 World、不生成可见回执、不修改生产审核协议。正常报告承接通过，但
“counterpart 送 companion”被错误地当作用户自述“家里送我”的忠实承接，也被放过。
因此第二例立即终止，未运行其余两例，**实验不可推广**。这说明显式列出承接类型
不足以保证人物关系匹配；旧 Boolean 接口曾成功，不能宣称 Boolean 本身是根因。
两次均有原生用量，估算 0.1830816 元，新增未知预留 0；全非计费表及旧账逐行不变。

当前运行继续点：`output/private-audits/release-clean-continuation-20260920-04/run`。
当前费用继续点：`output/private-audits/release-report-uptake-basis-20260920-01/run/world.sqlite`，
**1569 usage /1566 reservations /69 unknown holds**。其 World 与前者相同。
下一次运行须复制上述运行历史并继承这一最新累计账目，不能直接使用旧 1567 条账本。
两次试验均已闭合对账；没有存活的付费进程、真实 QQ 或部署。

## 本批离线回归完成范围

- 全量扫描两段分别为 6285 passed/18 failed/2 xfailed（中断）及
  2972 passed/12 failed/19 skipped。发现的失败均已针对性修复并复测；没有冒称这两段
  是一次完整冻结全绿结果。
- 主动审核取消、外部停止、重选及冷恢复共 86 项分批通过；Deliberation 相关 122 项通过。
  重启 fixture 先完成同一批待回执 Action，再断言事件、调用和交付不再变化。
- diagnostic 老断言更新后 93 项通过；合法 Life 来源准备及感知邻接 37 项通过，
  场景因果引用精确匹配 4 项通过。
- 120 个冻结机制场景、断言、replay 均通过。与旧 `.103` 的全部 17 个非 replay-hash
  字段相同，确认是已有版本身份变化后建立 `.104`，正常完整门禁通过。
  旧证据保留，详见 `scenario-baseline-104-2026-09-20.json`。

## Life 作者权限说明对齐

`bfb7040c` 提取审核者原有的 `current-life-authorship.1` 定义，只向显式安装 Life
reviewer 的新作者请求展示。初稿、同角色纠正与审核者收到同一 actor/time 及权限含义；
新增输入标识为 `life-author-current-authorship.1`，不新增 World 来源或可选支持权限。
186 项定向检查通过，实际 mock HTTP 初稿—纠正—审核—冷恢复链经过此接口；权限
说明伪作来源、篡改请求绑定均拒绝。审核 `.1–.12` 的完整 preparation/readings 与
冻结 `2dafb527` 编译器逐字一致，未装 reviewer 的初稿/纠正消息和工具不变，真实旧
`.10` 回执冷验通过。补说明后 120 项机制的 `.104` 完整基线检查也通过。

该补丁没有额外 provider 调用，没有证明两个主观评价误拒已经解决，也没有新增
Life response 或 Experience。离线证据在
`output/private-audits/release-life-author-authority-20260920-01/offline-verification.json`。

## 同一身份 05 续跑与面板实测（b60fa314）

`e7822c02` 接通面板的窄环境正文读取器，复核同一 ledger prefix、实际 settlement、
唯一发布 descriptor、选中结果和正文哈希，再执行 actor、三层 privacy 及用户通道
限制。只把 `environment_text` 放进现有 detail；超过 240 字标注节选，没有角色回应或
模型审计回退。88 项定向检查通过；实际同 owner 认证 HTTP 支持正文、ETag304，完整
账本不变。真实 05 浏览器登录后，1440px 桌面、390px 手机与录制模式均已同步，
环境摘要可见，无 JS 错误或横向溢出。截图在 05 私有目录，所读 cursor 为 ledger422。
环境正文保留原英语；显示通过不等于中文录制内容全部就绪，也不代表 Life 已接受。

05 先继承 04 的运行历史与 support-basis 的完整费用，再取新用量起点 1569；正常
调度推进至 95 分钟，再发一轮 96 分钟的自然回来聊天。调用总上限 24，实际转发 23，
没有本地上限拒绝。作者初稿、纠正实际带上 `life-author-current-authorship.1`，
与同次审核的 actor/time 和权限含义一致，排除了接线遗漏。

5 次 Life `.12` 审核均完整返回 31 字段且结构有效，但均拒绝；无新增角色 Life response
或 Experience。停步、路过、远远看见等动作确实没有角色来源。该 World 的
OutcomeObservation（seq367）绑定 Clock；seq371 的环境仅描述一般路人，既无
authorized_attempt_result，也无角色 Perception 事件。参与者和地点用于筛选、触发，
不能据此创造她到场或看见的事实。未来“晚点他再说话时”被强化为必发生事实，以及
用户报告“回来”被要求另有客观证明，是不同的范围误判问题，不能合并为真实动作支持。

回来聊天没有交付，来源审核为 `source_review_timeout`。该请求 66,260 wire bytes，
确实转发、收到 HTTP200 头，22.004 秒后取消，正文 0 bytes，无原生 token 回执。
作者原生输入 28,864 tokens、4.898 秒；双 reader 输入 1,876 /1,865 tokens、并行
5.652 /2.101 秒。仅模型关键路径约 32.6 秒。当前材料20项/19readings，材料块37,308
bytes 占 user 内容71.2%，其中9项 recent_dialogue占15,213 bytes。不能沿用旧170KB
来源/50k作者结论，也不能将一个超时归结为唯一的输入大小问题。

本轮新增一个 WorldOccurrence committed/activated，没有新的 settlement。
共22笔已知原生用量，估算 **0.44452118 元**；1笔未知预留 **0.716148 元**，不是确认扣费。
已 operator_stopped，所有客户端关闭，独立 reconciliation verified。运行及费用统一为：
`output/private-audits/release-clean-continuation-20260920-05/run`，
**1592 usage /1589 reservations /70 unknown holds，ledger467/revision203**。
冷重放哈希为 `21dad848c277f3c33896f2df5f2760f16318789659847e2c3fcbd89f79d1165d`，
机制检查无 finding，2张旧v22回执原样继承并冷验；新增可见回执为0。

代码复查确认 Started/Resumed 已有授权尝试材料及 World consequence 消费者；缺的是
定向、去重的进行中结果生产调度。下一项补此生产入口，保留当前审核、效果去重、退避
和旧回执解释，不从计划、Clock、同地点或参与者直接推导角色动作。

## 进行中活动结果入口：离线资格

现已接通精确 ActivityStarted/Resumed 源事件的一次结果机会。原角色意图必须可从
原 pin、模型审计和正文哈希读出；最新 owned active head 与当前 Clock 只限定尝试和
经过的时间，不证明成功、到场或感知。作者可以 no_op，或提出同一授权尝试的客观
结果候选；仍经过原两级来源审核、结算和发布，再由原 Life/Experience 消费者处理。
每次 ecology wake 至多选择一个 development 分支，完成优先；不可读/已消费活动
不会阻挡另一个当前活动。重启仅恢复已接受效果，失败沿用原退避。

109 项定向检查通过，包含完整120-case冻结基线，未刷新golden；另2项离线输入捕获
通过。独立复核发现新活动的作者失败会被 activity_transitioned 覆盖退避，现已修复并
覆盖 started/resumed/completed。同一 mock 宿主的 start/resume、两级审核、结算发布、
Life response/Experience、冷恢复与后续完成 Context 读取通过；这不证明真实模型质量。
普通及 completed 两类的 manifest、作者、来源审核和新事实权限审核请求，共8项与
冻结0132ded4源码进程原字节一致。Ruff/diff检查通过。

05 原库的只读 backup 确认 ledger467/revision203，角色当前活动于10:01+08开始，
10:36+08仍 active；61字原角色意图可读，结果入口非空且已到调度时间。原文件哈希
与冷重放不变。此前Life失败来自独立world-stimulus worker，不是前置aftermath异常。
证据在 `output/private-audits/release-active-attempt-20260920-01/`；06已准备继承05完整
运行及1592/1589/70累计账本，尚未启动。本次修复未增加审核协议或旁路消费者。

## 同一身份06续跑（41d3ec57，96至130分钟）

正常宿主先恢复旧inbound。初稿来源请求71,192bytes，原生19,881输入tokens，19.384秒
返回完整合法verdict：环境桌子、画稿、路人等通过，当前“我还行”被正确视作当前表达；
“上午在图书馆外那条路上走了一段”只有active生命周期依据而被拒绝。精确反馈传给同一
作者后，纠正仍写“上午从图书馆出来，在校园那条路上走了一段”；随后来源请求74,169bytes、
21项fixed facts，真实forwarded且HTTP200后22.004秒无正文超时。没有新增schema错误。
`corrective_invalid`不能被解释为第二次语义审判；实际终态为source read TimeoutError。

seq483为真实角色选项对应的lifecycle提案，seq485为ActivityAbandoned。作者选择的token
在原offered列表中对应放弃该活动；ecology先执行角色生命周期决定，再读current active
head。原计划已结束，新active入口合法不运行：作者、两级审核、接受、发布均0，资格为
not_exercised。seq490是既有环境事件结算，不得计为新尝试结果。没有强改选择或补造结果。

130分钟停止，33次真实转发，无本地上限拒绝；32笔已知原生用量估算0.88308870元，
1笔未知预留0.787329元。运行和累计账本统一到06/run：1625 usage/1622 reservations/
71 unknown holds，ledger518/revision217。process_terminal和clients_closed为true，
独立对账verified；冷重放哈希ca8df2fe4b2c871fc1decfec8c6ea881e031e03c6d0997070860128ff34dc712，
机制检查无finding，2张旧v22回执原样冷验。新增角色交付、Life response和Experience均0。
最终终态、独立对账及只读诊断位于 `output/private-audits/release-clean-continuation-20260920-06/`。

## Life13：生命周期状态和既有情绪材料

06六份Life12请求均精确匹配已保存preparation，重演为5 rejected、1 uncertain，未发现
schema或Fact值绑定错误。“计划被中止”却没有合格lifecycle reading：真正上游缺口是
WorldLifeContextCompiler只提供planned/active/completed，abandoned状态未进原Capsule。
另“那份温和已经在心里了”被拒为无过去情绪，而作者实际见过warmth1484及01:00开始、
01:03更新时间；旧合格reading仅保留dimension字符串。当前条件意愿被强化为用户保证
将来说话，仍是另一类语义误读；无源路过/看见及把角色自己的话归给用户仍应被拒。

Life13复用原Plan authority与Capsule bindings提供暂停/放弃的当前状态，不附带意图、
位置、执行结果或感知；只选最近3项。新profile和model_view显式启用新scope，旧profile
和普通聊天不显示它，普通selected source compiler也不新增权限。Affect专用reader核验
同owner/cursor/原accepted来源与作者实际呈现，仅提供维度、pinned强度、记录时间及来源
标识；不引入强度阈值，不把decay控制参数当历史事实或未来保证，不证明情绪原因。
两类读取共用Life13现有一次审核，不新增模型层；Life1–12保持原编译与恢复路径。

76项定向检查覆盖实际mock作者—Life审核—冷回执及两个状态，另116项context/profile/
旧reader检查含完整120-case冻结基线，未刷新golden。独立复核实际Paused Plan的普通
visible编译仍为unsupported，已有同event的current_situation仅baseline_only。
root用完整f1da6600临时源码独立进程与新代码比较12种旧preparation/readings，并冷验
1张真实旧Life10回执，全部一致。证据在 `release-life-state-readers-20260920-01/` 私有目录。
06原prefix重建的Affect material identity与原实际review相同、作者展示精确匹配；生成的是
新snapshot/request，不能声称恢复或升级旧pin。真实Life13生产请求和接受仍待续跑验证。
