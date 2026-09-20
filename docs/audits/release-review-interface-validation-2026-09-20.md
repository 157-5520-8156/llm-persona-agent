# 来源审核接口与真实续跑

本页保存各阶段证据；**当前闭合试验为08**，其前的运行／费用检查点均已过期。
最新发布判断与唯一有效继续点见 [当前发布状态](release-status-2026-09-13-current.md)。
资格仍为 `manual_only / qualification_incomplete`；不以离线测试、API200或材料送达替代交付。

## 08：首轮交付、次轮拒绝与真实类型接点（6d71b5f4）

证据在 `output/private-audits/release-clean-continuation-20260920-08/`，包括独立
continuation-reconciliation、chat-trial-inspection与active-attempt-trial-inspection。
第一轮输入146分钟，3条文字交付，无纠正：角色表示闲、描述有来源的画本环境、表达未来
出去看的打算。4次聊天调用完整返回；作者4.545秒，来源审核10.709秒，不能将其当作端到端延迟。
seq594提案、595接受、599表达计划、后续3次ActionDelivered，新增1张v22来源审核回执。
实际作者`.27`，4项automatic_prefetch保留原memory_kind／authority／epistemic_scope。

真实settled WorldLifeContextItem不含context_kind，导致`.26/.27`外层World标签未呈现；
原测试fixture自行加字段，未覆盖生产类型。这是对下面离线呈现结论的限制与纠正，
不能将首轮成功归因于并未实传的标签。内层环境权限仍保留，来源审核正确限制行动推论。

11:28角色自主ActivityStarted（seq660），真实active-attempt-consequence.1绑定原活动
及当前Clock。World作者首稿完整返回，但JSON在字符9381处结构不合法；纠正选择no_op。
main_invalid_recovered只表示格式恢复，未进入来源审核、接受或结算，不等于尝试结果成功。

158分钟自然追问“你出门了吗？我刚歇完，准备再画一会儿。”，初稿“正要出门，刚在磨蹭”
含无来源的过去行为；纠正“还没呢／我这就去看看那些摊着的画／你接着画吧”仅否定行动
状态无依据。8次调用完整返回，非超时；环境和未来意图通过，无新聊天Action／交付。
这与World外层标签漏接不是同一个已证明原因。ActivityStarted不证明已出门，也不证明未出门。
后台seq735/738另有真实Life response与Experience，仍引用已有环境结算，不是本轮尝试结果。

26次实际转发全部known并核验原生用量，估算0.70176968元，无新增unknown。
operator_stopped且clients_closed，独立对账verified；完整账目1678/1675/72，ledger758/rev317。
08/run成为统一运行／账目继续点。冷重放通过，2张旧与1张新v22来源回执全部冷验。
这些是capture渠道证据，实际QQ、连续聊天、最终全量与月费仍未合格。

## World.2作者：使用已有strict tool出口（离线验证）

08首稿2612输出tokens完整返回却含坏JSON；旧入口只用json_object，未调用适配器已有的
strict工具能力。新`world-consequence-author-tool.1`仅在World`.2`且provider明确支持时
启用，用已有no_op/propose合同组成精确replacement外壳，原parser已支持这个外壳。
执行绑定两分支的source_kind互斥，oneOf投影到anyOf保持两种合法来源，未扩语义权限。

工具合同及实际tool_choice的canonical hash在持久化前写入新messages身份；同角色纠正
使用同一工具，坏输出原字节保留，不能本地补括号或替角色no_op。旧消息编译器、sidecar
恢复及原来源／新事实权限审核、接受流程不改。无strict能力的原调用路径保持不变。

54项定向及邻近检查通过：真实DeepSeek adapter配MockTransport验证beta路径、thinking
工具选择、propose/no_op、一次角色纠正、两次坏输出仍技术失败、原错误字节和旧pin恢复。
独立只读review未发现边界阻断；未调用真实provider，不宣称新wire已取得供应商或生活结算资格。

## 新快照28：正式结算类型接入（离线验证）

正式WorldLifeContextItem保持原schema不动。新编译器从保留的Capsule完整值校验typed
对象、source_ref、value_hash及可见语义值一致性，再向派生recent_self_experiences和
week_diary标明settled World类型；不推断行动、感知或结果。来源过滤、正文、隐私与
原绑定不变，未知／缺字段／替换来源／篡改内容不获得新标签。

54项定向检查通过，包含真实mock宿主结算→WorldLifeContextCompiler→正式Capsule→
compact上下文→snapshot，测试明确断言原producer没有context_kind。旧`.23/.25/.26/.27`
已保存显示不被重新标记，版本集合冻结字面量。独立完整a28f277c源码对照12种旧Life
编译与2张真实`.10/.13`回执相同；证据在`release-settled-type-20260920-01/`。
此修复不等于第二轮否定行动状态已可交付，也未增加新模型调用。

08实库的query_only临时备份也通过：正式resolver选出的2条settled在两处显示标签，
原content语义／authority hash/ref保持一致，逐源隐私和异actor隔离通过；原DB/WAL
指纹不变，编译阶段0写、provider0。`actual08-context-check.json`记录结果。
这是新编译的`.28`，不声称升级08旧pin或已取得新对话交付。

## 新快照27：保留召回材料原权威（离线验证）

07的Experience已通过private_impressions送达角色，问题是present_prompt把
`defeasible_interpretation / private_interpretation / reflective`当成恒定包装省略。
`.27`原样保留这三项已有标签，缺失、null或不同标签不被展开器补造。正文、source_ref、
时间及隐私不变；旧`.25/.26`保持原压缩，`.26`日记来源类型不会因版本升级丢失。
仅含scoped World材料的新编译使用`.27`；普通`.23`快照本轮不启用，不能称为全局修复。

63项定向检查包含实际mock角色HTTP、双向呈现、缺失标签、旧版本和脱敏；120-case
冻结基线、Ruff/diff检查通过。完整a28f277c旧源码独立进程与新代码比较12种Life编译
及真实`.10/.13`回执一致；旧present_prompt对照07全部11份实际快照展示也逐字相同。
证据在私有`release-prefetch-authority-20260920-01/`。没有新增审核协议或模型调用。

08准备已验证从07恢复World，先合并1652/1649/72完整费用再读取水位1652，原hold
与全部非账目表保持不变。39项离线准备检查通过，实际transport wrapper覆盖普通与
strict beta请求；默认0调用，显式40调用／600秒。drain恢复正常默认8，避免07四次
非空结果恰好用完检查次数；不提升物理／时长上限、不改变角色选择。此为启动前准备证据；实际运行结果见上方08。

## 原输入模型对照：本地门禁拒绝，未评价模型

冻结7502151f下，原07初稿native请求仅替换model，11秒上限／1次调用／无重试。
实际转发为0：测试门禁只放行普通chat路径，原adapter的strict tools使用beta路径。
原准备自测绕过该门禁，不能证明完整传输入口。新增离线测试原样抽取旧门禁类，让
实际adapter穿过它，复现拒绝及零下游调用；只修正URL的离线对照才进入fake下游。
原capture没有保存URL，此处是冻结代码确定性重现，不称为已捕获的请求URL。

原脚本、授权和终态未修改。独立`reconcile-v2.py`修正usage与reservation不同列结构的
检查，仍核验原脚本hash、全部继承账行和非账目表不变。`reconciliation-v2`为verified：
1652 usage/1649 reservations/72 unknown holds，新known费用为0，新增未知预留1.190718元
原样保留，非确认扣费。没有World写入、来源审核、接受、交付或新回执，也无模型质量结论。
该阶段累计账本位于`output/private-audits/release-author-model-comparison-20260920-01/run/world.sqlite`；
08已将其完整合并，旧继续点已过期。probe目录不具备journey恢复资格。

## 07后的来源类型呈现修复（离线验证）

新snapshot `.26` 在原材料栏和结构日记中保留 `settled_world_occurrence` 与
`committed_experience` 类型。World条目声明按字段授权：环境、授权尝试结果和角色
私有回应的原文及权限保持分离；参与者和地点不产生亲历依据。旧`.25`继续使用其原
结构日记renderer，旧`.23`普通材料与recalled episode不被重新标记。来源过滤仍先于展示。
自动召回已有的类型被另一处全局压缩省略，本补丁未改该路径，也未新增审核版本或模型调用。

28项呈现／隐私定向检查、34项Life／可见来源邻近检查通过；120-case冻结机制基线
保持原值，Ruff及diff检查通过。完整89232c38源码的独立进程与新代码对照，12种旧Life
preparation/readings和07两张真实`.10/.13`回执精确一致。证据在私有
`output/private-audits/release-context-provenance-20260920-01/`。尚无此补丁的真实聊天资格。

## 07：真实Life13接受与剩余失败（89232c38）

私有证据目录为 `output/private-audits/release-clean-continuation-20260920-07/`。
正常宿主继承06完整运行与累计账目，在130至146分钟继续同一干净身份，保留v22聊天，
使用冻结代码默认安装的Life `.13`。本次没有升级或重编译旧回执，也没有恢复旧的低费用账本。

### Life材料、审核和实际提交

`life13-trial-inspection.json` 对照实际客户端请求、完整工具返回与已保存preparation：
9份Life作者请求都收到 `stimulus_appraisal.lifecycle.1` profile，展示同owner的abandoned
状态和既有Affect；5份审核请求均包含对应新readings／permissions，且精确匹配保存的
原preparation。工具为 `review_life_candidate_v7`，协议为 `life-source-review.13`。

| 原审核capture前缀 | 结果 | 证据范围 |
|---|---|---|
| `20e95d1e` | rejected | 无来源的当前“手上拿着相机”不能由环境或计划证明 |
| `f6392922` | accepted | 同角色纠正删除该无源事实，34个字段通过；实际形成下述提交 |
| `65fdb11f` | uncertain | 候选写自己早上路过该路；环境和参与者不能证明其行走 |
| `abcf154e` | rejected | 6项失败，包括条件意愿被强化为用户将回来／开口，以及用户态度的范围问题 |
| `6466bee8` | evidence_validation_failure | 对条件可能性返回 `supported` 但 `supports=[]`，本地严格拒绝 |

接受的纠正稿提交了 `CharacterLifeResponseRecorded`（seq538）和 `ExperienceCommitted`
（seq541）；同一角色意图形成 `ActivityPlanned`（seq543）。这证明一次环境来源—生活
反应—Experience提交经过真实生产链，但不证明后续记忆召回、对话使用或长期多样性。
接受的审核没有引用新增生命周期／Affect权限；真实展示已验证，正确语义使用仍未验收。

新Plan的窗口从11:28开始，本次停在11:26。旧活动早已在06由角色放弃；本轮未产生
Started/Resumed，因此新active结果生产入口仍为 `not_exercised`，相关author／review／
acceptance／publication均未执行。seq529/530的新环境事件只是committed／activated，
本轮无新增settlement；它与已接受Experience所引用的既有结算来源不可混为一件事。

条件意愿反例继续保留：“他回来时自然接住他的话”被强化成他必将回来；末次纠正明确
写“若他再开口，就自然接住，不追问”，审核也将其解释为条件可能性，却放入必须有来源
支持的fact claim并返回空supports，触发 `Life factual support is missing`。这是已完整
返回后的结构失败，不是超时，也不能本地补写支持或改判成功。初稿无源行动／持物的
正确拒绝与此范围错误须分别修复，不能为提高通过率放宽事实权限。

### 回来聊天

146分钟输入“我歇一会儿。你这会儿在忙什么？”后没有新增角色交付。8次聊天链调用
全部完整返回，没有timeout；初稿来源审核13.448秒，纠正11.043秒。初稿编写“刚在
图书馆坐了一会儿”“我看了会儿”，均无角色行动来源；纠正把看画改成未来打算，
却改写为无依据的“刚在校园路上晃”，再次被拒。初稿将“不忙”视作需记录的状态，
纠正则视作当前表达，保留这一解释不一致；不影响两稿无源过去行动的拒绝依据。

seq563为 `paired_expression_reselection_invalid`，seq564/565是两份候选invalid；
seq566–571只是6次审核子调用已返回，不是候选accepted，也没有进入可见接受／发送／回执。
当前location正确标为 `no_authority`，新计划正确标为未来，不能证明图书馆停坐、行走或看画。

`chat-trial-inspection.json` 确认一个具体呈现缺口：纯已结算环境卡被归入
`recent_self_experiences`，内层environment scope尚在，外层却缺来源种类／认知权限标记。
新Experience541已在 `automatic_prefetch/private_impressions` 实际送达作者，并非完全
丢失；但原内容facet／类型scope未保留。后续应修正来源类型呈现，保留正文、时间、哈希、
原审核权限和旧快照编译；该诊断不证明展示修正必然解决模型编造行为。

### 终态、冷恢复和累计账目

`demo-terminal.json` 保留原始 `reconciliation=pending`；独立的
`continuation-reconciliation-status.json` 和 `continuation-reconciliation.json` 均为
`verified`。进程与全部客户端关闭、无关闭错误，真实终止原因为 `drain_limit_reached`；
不是到达40次物理上限。本轮26次转发，0次本地上限拒绝，26份完整响应逐一关联原生用量。
已知费用按仓库价格估算 **0.70211972元**，新增unknown预留为0；这不是整月费用或实扣证明。

唯一运行／累计费用继续点：`output/private-audits/release-clean-continuation-20260920-07/run`，
ledger **580** / World revision **234**，**1651 usage /1648 reservations /71 unknown holds**。
前1625条usage／1622条reservation逐行精确保留，71笔旧unknown没有消失。当前usage账为
1570 known／3 legacy／7 not_billed／71 unknown；reservation为1577 settled／71 billing_unknown。
冷重放hash为 `75a1a4d8fe9f95b5b0e41999b7938d89c94646802ac7a0ed126b75a73ae98d80`，
机制检查无finding；2张旧v22可见回执原字节继承并冷验，新增可见回执和交付均为0。

证据以terminal、独立reconciliation、`life13-trial-inspection.json`、`chat-trial-inspection.json`、`run/evidence.jsonl`
和实际 `run/model-inputs.jsonl` 为准；旧准备README不代表试验尚未启动。
本次仍未完成真实QQ、24小时持续运行、最终冻结全套与约100元月费资格。

## 历史记录（03至06阶段，以下检查点均非当前继续点）

历史起点代码为 `64f41e06`。以下记录保持各阶段的失败与离线修复边界；其中当时的
“尚待实跑”不覆盖上述07结果，过去的成功也不覆盖目前尚未通过的发布门槛。

### 可见聊天

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

### 生活链

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

### 费用与继续点

| 试验 | 实际调用 | 已知估算 | 新未知预留 |
| --- | ---: | ---: | ---: |
| v23 非推理来源对照 | 4 | 0.30638940 元 | 0 |
| 同来源推理配置对照 | 2 | 0 | 1.418922 元 |
| Life `.11` 续跑 | 4 | 0.15491072 元 | 0 |

未知预留不是已确认消费。各阶段均独立对账；旧账按稳定主键保留，推理试验没有改动
World，Life 续跑完整继承最新累计账目。第一次续跑启动的私有目录约束错误发生在
宿主构建前，0 次调用；另建目录后继续，原始失败记录保留。

该阶段运行及费用检查点（已过期）：
`output/private-audits/release-clean-continuation-20260920-03/run`。
累计 **1553 usage /1550 reservations /66 unknown holds**，ledger **405**、revision **193**。
冷重放一致、机制检查无 finding，旧 2 张 v22 回执原字节继承并冷验证，新增回执 0。
没有存活的付费进程，没有真实 QQ 或部署。旧检查点只能作历史证据，不能直接恢复旧账。

### 回归收敛

全量缺陷扫描在主动审核超时用例处停止，已有 6285 passed、18 failed、2 xfailed；
剩余 3003 项另行运行。这是查找回归的扫描，不能记作全量通过。

- `a983933c` 恢复 atomic v1/v2 原编译字节，8 条原 golden 不变；v3 保持修前字节。
  核心 9 项及更新 fixture 41 项通过，相邻其余 117 项已通过；35 张真实回执冷验通过。
- `3989d9a9` 把纯 reviewer 配置从执行 runtime 分离，保留数据库创建前的配置拒绝；
  修正价格时间和 persona 的旧测试断言，生产计费和角色配置未改。209 项通过。
- `2ee667fc` 修正架构检查的模块边界，避免把 world_v2 误认作 legacy world；25 项通过。

当时尚在处理的失败包括旧生活/感知测试未准备合法来源，以及主动审核超时后的审计收尾。
这些离线修复不代表正常聊天误拒、生活记忆链或演示上线已验收。

### 后续 ce28d96d 真实续跑和独立反证

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
这是显示检查；当时事件正文尚未读取，卡片不能讲清发生了什么；后续05已接入有权限的环境摘要。

随后在 `2dafb527` 做独立 `report-uptake-support-basis-experiment.1`，仅改变诊断返回
接口，不写 World、不生成可见回执、不修改生产审核协议。正常报告承接通过，但
“counterpart 送 companion”被错误地当作用户自述“家里送我”的忠实承接，也被放过。
因此第二例立即终止，未运行其余两例，**实验不可推广**。这说明显式列出承接类型
不足以保证人物关系匹配；旧 Boolean 接口曾成功，不能宣称 Boolean 本身是根因。
两次均有原生用量，估算 0.1830816 元，新增未知预留 0；全非计费表及旧账逐行不变。

该阶段运行检查点（已过期）：`output/private-audits/release-clean-continuation-20260920-04/run`。
该阶段费用检查点（已过期）：`output/private-audits/release-report-uptake-basis-20260920-01/run/world.sqlite`，
**1569 usage /1566 reservations /69 unknown holds**。其 World 与前者相同。
该阶段要求后继试验继承1569条完整累计账目；当前继续点已更新至本页顶部07，不得恢复旧账。
两次试验均已闭合对账；没有存活的付费进程、真实 QQ 或部署。

### 本批离线回归完成范围

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

### Life 作者权限说明对齐

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

### 同一身份 05 续跑与面板实测（b60fa314）

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
定向、去重的进行中结果生产调度。当时下一项是补此生产入口，保留审核、效果去重、退避
和旧回执解释，不从计划、Clock、同地点或参与者直接推导角色动作。

### 进行中活动结果入口：离线资格

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
运行及1592/1589/70累计账本，此时尚未启动；其后实际结果见下一节。本次修复未增加审核协议或旁路消费者。

### 同一身份06续跑（41d3ec57，96至130分钟）

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

### Life13：生命周期状态和既有情绪材料

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
新snapshot/request，不能声称恢复或升级旧pin。此离线阶段尚待真实续跑；结果现见本页顶部07。
