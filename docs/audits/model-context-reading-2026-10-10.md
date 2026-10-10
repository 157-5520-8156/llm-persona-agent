# 首次调用的上下文运输与全用途账单

2026-10-11跟进：用户已批准新增最多0.5元，两轮完整前史普通聊天云端复验已完成，spent0.076120元；无同步审核/纠错，源忠实性及四秒仍未通过。详见`first-recall-cloud-2026-10-11.md`。下文“待答复”是10月10日阶段状态，不能作为当前授权状态。

基线035fd117；实现9fadab69，兼容修正cf27a5a6及bfc20cbf。Spec见`docs/design/model-context-reading-2026-10-10.md`。所有代码在隔离worktree，未改生产库、QQ渠道、生产配置、模型、生活频率、输出上限或费用上限。

## 聊天车道的更正

从实际.env及production-text-policy.env核验：ordinary_text_review_mode=sampled，grounded_review_v25，native_slim_core=False，reference_wire_enabled=False。普通文本不等待同步语义审核。此前两条前史夹具强制blocking，约五秒包括夹具审核；该时间不能用于描述当前聊天。

此次不把审核放行当作源忠实性，也不恢复同步聊天审核。局部真实应用测试继续证明：一次作者调用即授权、Action effect-once、冷重放成立；非权威异步观察不改事实。测试供应商是MockTransport，不能证明DeepSeek首次生成质量。

## 已修改

发现一般后台视图的材料白名单会抹掉Core已经合并的selected_recall。最小对照在proactive_contact最终模型呈现处重现KeyError，而非数据库无记忆或检索无结果。

现在已有检索结果时，当前角色后台视图继承selected_recall，包含原content/source_refs、Faculty材料绑定和来源目录。普通15种builtin目的的30项无编码/引用编码测试通过；另一个完整proactive角色provider调用测试验证仅调用一次即收到检索。没有新增检索、模型或向量调用，没有替角色决定是否表达。

聊天当前3/slim及角色后台共用model-context-reading.1说明：材料不等于完整人生，未提供不等于未发生；过去事实/过去主观记录与现在形成的理解分开；下一页/关联指针不授权未读细节。说明不进入事实目录，不改变registered source_excerpts等来源形状，不重写canonical snapshot。外部World Author保留已有版本化事实/机会合同及原请求字节，不接收角色的私有检索或内心创作权。

静态Standards检查发现初版无条件改变入站V1/V2字节。cf27a5a6使用原preserve_legacy_authoring边界修复；真实CLI/host/Core夹具覆盖首次和Recall后请求，旧版本不带新字段，当前版本带新字段。Spec无确认缺陷，Standards原P2修复后无新发现。这是代码检查，不是运行时聊天审核。

## 仍未解决的边界

world_stimulus_appraisal的LifeSourceView拥有独立且固定的来源呈现/目录合同。使用该视图时，本次保持原合同（包括review_contract=None），不偷偷增加来源；读取说明明确标出selected_recall未呈现。仅保护了旧接口，**没有修好这一通道的检索运输**。将新回忆及其独立核验trace补入Life来源目录，必须单独版本化并验证，不能把30项一般角色视图测试算成该通道也合格。

本次不是稳定跨轮记忆bank上线，也没有实现/验证90%实际缓存命中。读取说明是输入改进，不能保证模型不会捏造感受或漏掉故事；还需要首次生成的真实源忠实性对照。取消同步审核不消除这项质量验收。

## 输入与时间证据

9fadab69阶段使用完整现有前史克隆、实际配置普通入口，六次均到达HTTP边界：2.487/2.206/1.921/2.094/2.126/1.782秒，中位2.110秒，仅2/6不超过两秒。请求被本地截获，0真实模型调用、0可见回复；夹具严格两秒assert仍失败。不能与旧2.070秒小样本差异宣称改善或退化，也不代表首回复延迟。

最终bfc20cbf在完整suite前、独立负载下复验六次：2.432/2.144/1.927/2.100/2.130/1.807秒，中位2.115秒，2/6≤2秒。全部是character_inbound_initial_slim作者请求，携带model-context-reading.1；辅助请求0、真实调用0、可见输出0。严格两秒断言仍失败。原始完整前史、当前配置、输出上限和普通入口不变；这仍是到HTTP准备完成，不是用户首回复。

最终cf27a5a6尝试与完整suite并发，只获得一份本地Qwen typed-input endpoint请求后主动中断，以免混入资源争用样本。它是消息收束判断，不是角色作者请求。保留中断日志、克隆和请求，不将它算作独立性能验收。每种用途需要适合的上下文：该辅助判断只读输入节奏，不应塞入二十年前史。后续截获器分开标记辅助/角色请求且使用唯一attempt文件，避免覆盖。

最终说明自身632 UTF-8字节；将当前rule常量代入此前作者frame，保留原selected_recall呈现状态，只增删该字段并保持其他值和紧凑序列化，孤立wire开销为669字节。该计算不是最终作者请求捕获、语义重放或token费用。之前六份说明为626字节，差别来自第三人称措辞修正。

## 所有用途的费用审计

新增`scripts/audit_model_context_calls.py`：只读captured request与usage，不连接数据库或模型；核验原请求SHA，按唯一reservation join，按model/purpose汇总真实input/output/hit/miss token、known bill、status、字节量。哈希错误和歧义join拒绝；unknown、失败、缺token和missing usage明确保留。全部输入只读取一次，报告哈希绑定该批原字节；不按字符猜token、不填假定命中率、不按历史价格推测新费用。

历史ad52b795完整day的384份请求：334份DeepSeek与原usage完整匹配，0账单漏join；另50份本地Qwen请求没有对应usage行，报告为missing usage，不能用0已知费用宣称所有调用免费。334份已知费用合计7.0340元，是历史50次输入、虚拟24小时加速运行，**不是当前版本支出或100条/日月成本**。

|历史用途|调用数|已知费用CNY|
|---|---:|---:|
|聊天作者|52|1.2967|
|生活作者|58|1.8344|
|生活刺激理解|32|0.4786|
|生活来源校验|32|1.0175|
|记忆整理|25|0.3314|
|记忆保留|11|0.1034|
|其余已计费用途|124|1.9720|

完整17组（含未匹配组）在私有JSON。生活、来源准备/重写、主动联系、生命周期、异步观察和失败费用都在统计范围内；不只看聊天。不能通过停掉生活、缩少机会、删源或忽略失败使数字变绿。

本轮无新增付费。原专项账本仍176requests、spent4.688811、unknown reserve0.231340、remaining0.079849元，总上限5、batch2未动。完整输入发送前约0.22元预留大于余额；不清除旧未知账单、不重开账本规避总额、不缩输出来伪装严格对照。预留不是实际消费。

## 验证与状态

局部相关组合346项通过；账单/新视图/旧协议/普通聊天组合48项通过；Ruff和Python parse通过。首次冻结cf27a5a6完整suite：5 failed、10449 passed、19 skipped、2 xfailed、1 warning，1245.61秒。保留完整失败日志，不用后续局部通过替换它。

其中纠正trailer位置两项、外部World Author旧请求字节一项是兼容回归，bfc20cbf已修；另外当前V3compiler hash及scenario manifest因新model-context-reading.1输入改变，需要明确新身份。8个历史V1/V2 hash不改；当前V3新合同hash显式记录。对照035fd117原src+configs+fixtures完整复现旧场景manifest0f32339c…793f3；新源120场景所有谓词及回放通过，只有npc_world_impact.01的replay_hash变化，输出、事件、状态、调用、后台活动、回执字段完全相同，明确升级机制baseline .111。参见`scenario-baseline-111-2026-10-10.json`。仅src的初始旧源对照缺configs，未用于通过结论，也保留证据。

修正后相关70项通过；最终冻结bfc20cbf完整suite：**10454 passed、19 skipped、2 xfailed、1 warning，1232.14秒，exit0**。10个修改Python文件的SHA冻结前后完全相同；后续仅改文档。此前定时敏感单项本轮通过，没有改deadline或声称根治该独立时序问题。Standards及Spec补审未发现最终代码新问题；指出的审计草稿陈旧World Author共享描述已修正。

已准备只走两轮普通入口的debug云端工具`qualify_first_recall.py`，保留原完整前史、模型、上下文、输出上限、sampled车道和原生来源绑定；默认断网截获。未执行live，没有追加额度或创建新预算账本。请求最多0.5元独立验证额度的问题仍待用户答复；不能将已准备、--help可运行或离线通过当作云端首次语义合格。

冻结、全部账单组、字段开销、完整suite日志在`output/private-audits/model-context-reading-20261010/`；六份截获输入及中断最终尝试在`output/private-audits/context-input-cost-20261009/`。生产资格仍未闭合：首次云端语义、LifeSourceView新检索目录、全部目的完整生活对照、实际命中/100元月成本/100条每天/四秒均不宣布达标。
