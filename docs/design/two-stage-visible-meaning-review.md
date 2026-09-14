# 隔离候选含义读取与来源核对

状态：隔离原型，语义与延迟仍未合格，不授予回执或Action权限。

## 最新：互不参考的双读取

`visible_independent_meanings`现将两份实际原句读取的固定命题一起核对来源。每个命题保留读取者编号和原命题编号；相似、重复或冲突命题都不合并，不投票，不用一份读法替代另一份。模型只核对各自固定命题，代码执行各自的来源用途和主体权限。有一份读取未提取事实，不能隐藏另一份发现的无依据事件；两份都无事实也只是不需要来源探针，不构成原始候选批准。

实际捕获核对了33对Pro/Flash请求：每对调用都有不同捕获/预留身份，实际请求均只含同一组原句，不含世界材料或对方的解释；两份请求均先于任一终态响应记录。这里证明输入隔离和真实调用，不声称模型错误在统计上独立。

v4双读取曾共同把未来有空/散步当成已发生前提，误拒开放问题。v5新增条件表示，修复未来问题并保留条件中的过去经历，却把普通条件陈述硬塞进问题字段、返回空问题；也把当前说话态度和请求推成客观事实。v6把条件放到整个Beat，保留独立问题/事实前提，并区分当下言语功能与实际事件。最后一轮12组新调用全部符合预设诊断，包括普通未来邀约、条件中的过去未回复和过去受伤反事实。该轮34次调用，审核链约2.4–8.6秒，**不包括角色作者，不证明12秒完整聊天链通过**。

另外三组故意破坏第一份解释、保留第二份真实读取的来源探针，都拒绝了原始错误经历。但调换送行参与者那组同时误拒了“家里人送用户”的报告承接正例；源模型错误要求独立客观事件证据。运行器的`AssertionError`是预设正例未满足，模型返回本身合法，不能列为供应商格式失败或改成全通过。这个误拒仍保留为问题。

本阶段162项相关检查通过，64份含义成功、2份含义格式失败及31份来源结果按原准备重读；97次新增调用全部有已知用量，估算0.9260333元，未新增未知预留。完整记录见[双读取验证](../audits/release-independent-meanings-validation-2026-09-14.json)。正式聊天、回执、QQ、记忆压缩和长周期验收未被本轮替代。

下一片需要正式原始候选批准与调用凭据：绑定完整作者Proposal、原来源pin、两次实际含义请求/返回、来源请求/返回及各自独立ProviderSubcallAudit；无事实分支不得以两个空数组直接绕过完整语义覆盖。审核协议应在作者运行前进入原始requirement/capability，不能让凭据自身选择一个更弱的旧审核版本。旧合同按旧规则重放，新合同单独接入同角色纠错和实际12秒交互验证，不把探针塞进旧v8回执。

## 完整性检查的后续反例

新增`visible_meaning_fidelity`，仅接收原句和已冻结解释，不读取世界来源。它与固定事实来源探针的组合会核对精确依赖：任何完整性异议都否决相应Beat；无事实项也必须由独立检查明确通过，不能把来源探针的`not_assessed`直接转成批准。组合输出仍为诊断，没有供应商调用权威、正式回执或Action权限。

真实复测证明“让另一个模型检查已有解释”仍有被待检解释带偏的风险。v1中Flash漏掉过去事件被标为当前私人表达的错误，Pro同时漏掉该错误和真实v3问句前提遗漏。v2逐项核查事件类别、主体和事实覆盖，Flash拦住前两类错误，却放过将“你送我”改成“你家人送你”的解释；Pro有5份JSON格式失败，不能当作语义拒绝。v3将原句与解释放到同一Beat并要求原句摘录和命题忠实性判断，最后12组仍只有11组符合预期：错误参与者解释继续被Flash认可，即便它正确引用了原句。这不是结构校验或更换更强模型已经解决的问题。

本阶段142项相关检查通过；55份合法返回和5份格式失败按原准备重读一致，旧v1/v2结果保持原结论。60次新增调用均有已知用量，估算0.2320083元，未知预留未增加；完整记录见[句意完整性验证](../audits/release-meaning-fidelity-validation-2026-09-14.json)。其中组合检查使用上一阶段冻结的来源结果，不能算新增角色对话或完整在线链。最终Flash单独完整性调用约1.1–3.8秒，不包括含义作者、来源核对和角色作者。

该阶段提出了真正独立的第二份原句读取，后续实现及限制见本页顶部。即使两份读取一致也不是形式化的语义完备证明；无事实分支仍需要独立覆盖证据和真实资格测试，不能以“两份空数组”直接绕过审核。当前完整性检查不接入正式聊天。

## 最新诊断进展

新增`source_only=True`入口，只展示固定命题和完整来源，隐藏候选原句及原句重读任务。它只能返回`facts_supported / facts_rejected / not_assessed`，没有`beat_outcomes`批准接口，不能为无事实的表达授予权限。默认两步原型的完整性检查仍保留，不把移除该检查伪装成生产修复。两轮相同冻结含义/来源的五组目标事实均符合预期，支持了“含义重读受来源干扰”的诊断，但仍是小样本。

候选读取的v3精简了重复原文和释义，完整原句由输入Beat编号在主机端绑定；Pro耗时约2–4.3秒，但漏掉了错误送行问句的前提。v4将`questions`拆成`requested_information`和独立的`premises`，前提有自己的固定编号，不能归入私人表达类别。新的条件感受/过去哭泣、过去否定/当前意愿、说过会做饭/实际给对方做过饭也进入真实读取测试。

两份v4返回在一个完整JSON对象之后多了闭括号。新增显式选择的`single-object-closing-tail.1`传输，只允许去除完整对象后的有限多余闭括号，不修补缺失字段、截断、引号或额外文本，不从两个对象中选择一个。原始字节/哈希和归一化标记保留，旧严格准备记录仍按旧规则失败；原请求与所有解码字段值不变。随后来源核对出现额外“补充”占位键，现改为固定事实编号数组并验证每个事实恰好一次，不忽略未知项。

最后一轮新调用的九组串行诊断全部符合预设目标：8组含事实的读取/核对约4.2–8.0秒，1组纯开放问题只有含义读取、约2.3秒且未进行来源核对。这里不包含角色作者耗时。**这些结果不是9条原始回复获准，不证明完整候选覆盖可靠、正式回执有效、12秒聊天链通过或可发布。** 前一轮的额外占位键失败仍保留；两轮串行调用共有4次明确的结尾括号归一化。

本阶段110项检查通过，36份含义结果及25份来源结果按冻结准备重读一致；62次调用均有已知用量，约0.769元，无新增未知预留。证据见[固定命题链诊断](../audits/release-fixed-meaning-chain-validation-2026-09-14.json)。

## 已实现的边界

`visible_candidate_meaning`只接收候选原句，不接收World、历史或来源证据。模型展开人称，区分实际事件/状态、过去意图、过去言语，以及当下私人表达/当前意愿，并将问句请求的未知答案与预设前提分开。代码核对完整原文覆盖并固定原始请求、返回与含义映射；它不靠关键词、正则或固定社交规则判断含义。v1/v2原始准备记录继续按各自的结构读取。

`visible_meaning_source_review`读取固定命题与完整的证据条目。每个命题可用的来源用途由第一步的已固定类别和主体约束：实际经历不能借用旧自述或旧意图的权限。第二步不能修改第一步的类别或主体。拒绝可以保留只作诊断的引用；不会因为拒绝说明没有支持证据而伪装成发送成功。v2为无事实命题的候选保留完整性审核，但不发送供应商禁止的空属性对象。

默认两步原型的第二步还收到原句与第一步完整解释，以检查漏读和误读。这个设计**尚未真正隔离语义重读**：真实模型会参考来源反过来解释原句，再拒绝原本正确的固定含义。新增的固定事实探针用于验证分离后的来源判断，并没有替代完整候选审核。因此仍不是可直接接入生产的完成态。

## 真实证据

- 第一批单独含义读取能把“你送我出发”解释为用户送角色，并把“上午坐在那里”识别为实际经历；也出现私人表达误分类、请求答案被扩成命题、角色受事标签与命题正文不一致，以及JSON/枚举格式错误。
- Pro读取复杂反例用了8767ms；随后Flash来源核对被11秒整链截止取消。不能改长普通截止来宣布通过；新增未知预留保留。
- Flash两步链的第二轮曾同时正确拒绝落座与错误送行前提，接受环境事实及正确家庭问题。第三轮复测仍误拒正常报告承接，并出现带证据的第二步反过来重读原句。单次正确结果没有关闭发布门槛。
- 第三轮里，过去意图/完成结果、正确的旧话回顾/实际行动、纯开放问题三组对照符合预期，空属性对象400没有重现。全部六组耗时约3.6–8.5秒；这是**审核两步本身**，没有包含作者初稿和纠错时间，不能当作12秒完整聊天链通过。
- 第二步有一次将“黑着的时候”列入遗漏，同时在说明中又称其不构成遗漏；另一次称用户纠正“家里人送我”的消息不算报告该事件。结构校验无法证明这些语义判定正确，所有原始返回保留。

## 更正测试用例

原`speech-versus-action`用“我上午跟你提过图书馆那排灯”作为正例，这个预期错误。原话时间为2026-09-14T05:01:00Z，即当前World使用的+08:00时区下午13:01，不能证明“上午说过”。旧结果与原预期原样保存，不追改为通过；新增`speech-recollection-versus-action`改用有证据的“我之前跟你提过图书馆那排灯”。因此不能用旧场景的拒绝证明模型误拒正常旧话。

## 下一步不能跳过的工作

1. 明确完整候选的批准条件。独立读取负责事实/前提的完整识别，来源阶段只核对固定命题；必须保留缺失前提、过去情绪被错标私人表达等反例作为否决测试。不能把探针的`not_assessed`直接转成批准，也不能只凭完整Beat编号就宣称语义没有遗漏。
2. 建立新协议的完整证据身份：原候选、实际含义请求和返回、来源请求和返回、固定命题/来源映射及其规则版本必须同属一次审核。旧请求与回执按旧规则读取，不可将两次调用塞进一个未经证明的旧回执。
3. 在隔离完整聊天中验证作者、上述审核、同角色纠错、交付和重启读取，沿用12秒总时限。最近4.2–8.0秒是审核两步本身，尚不能证明加入作者后仍足够快。继续扩展问法与生活/记忆来源，不能用本轮9组诊断代表长期真人感。
4. 隔离实验没有改变角色行为选择、正式审核版本或部署配置。角色对话、生活长线、记忆压缩和月成本的其他目标仍保持。

当前原型最多处理32个事实命题，沿用条目传输的标量和摘录长度上限；还没有完整细分“活动生命周期本身的陈述”等所有生产reader语义。超过原型范围时拒绝准备，不静默丢弃事实。这些限制也必须在正式接线前处理。

本阶段82项本地检查通过；25份含义结果与11份来源结果按冻结准备记录重读一致。这些都是结构与可恢复性证据。38次物理调用、费用、未知预留和逐轮结果见[验证记录](../audits/release-candidate-meaning-validation-2026-09-14.json)。


## Explicit independent runtime protocol (2026-09-14)

The experimental version 9 port now pins `visible-independent-review.1` in the
original Deliberation requirement before character authoring. It requires two
explicit metered clients with distinct model identities. Both read exactly the
original Beats, without World evidence or the other reading; all factual readings
remain separate when the source model receives the original evidence catalog.
Meaning `.7` adds an explicit completeness assertion and unresolved-detail list.
Incomplete or ambiguous reading is a technical failure, not proof of fabrication.
Two positively complete, represented nonfactual readings permit source-free
classification; empty fact arrays alone do not.

The `.9` receipt retains the entire candidate/source bundle, both raw readings,
the optional source result, and each actual provider binding. Source-call identity
also binds both original reader response hashes. Cold verification joins the
original author capability and every independently recorded provider subcall;
rehashed receipts and protocol downgrades cannot select weaker approval rules.
Native usage and cancellation audits survive failed interpretation, source
rejection and the same character's single constrained reselection.

This port is explicit-only; production settings/defaults remain unchanged. The
longitudinal CLI can select version 9 with separate Pro and Flash reader clients.
Offline public application tests cover inbound/proactive authorization, source
rejection/reselection, failure accounting, tamper rejection and cold replay.
These tests qualify plumbing, not model semantics. Previous `.6` diagnostic
successes do not qualify new `.7` wire behavior, latency, complete real chat,
production delivery or monthly cost. Those real checks remain pending.


The first real `.7` trial did not qualify the port: 8/12 diagnostic matches,
one false factual reading of current willingness, one inconclusive reading due
to unresolved omitted referents, one missing-field result, and one malformed
JSON result. This suggests the next design must distinguish complete semantic
representation from evidence-level referent resolution, and explicitly test
actual provider structure enforcement. No local field filling, factual-mode
rewriting or example-specific phrase filter is an acceptable repair. Version 9
remains explicit and unqualified; this first frozen contract and all raw failures
must remain reconstructible when a revised protocol is introduced. See
`docs/audits/release-independent-complete-validation-2026-09-14.json`.


## Complete semantic reading `.8` (experimental)

The next compiler distinguishes unbound referents from unrepresented semantics:
readers keep the original deictic relationship and unspecified time/object rather
than inventing a real identity or refusing otherwise complete syntax. A language
ambiguity that changes who did what still remains inconclusive. Meaning modes
remain reader judgments; local code neither converts attitude into fact nor
removes unsupported facts. Output instructions now explicitly place coverage
fields before nested arrays. Missing coverage fields and malformed objects still
fail; no values are locally filled in. `.7` compiler bytes remain frozen and the
existing version 9 runtime still uses `.7` pending actual `.8` qualification.

Provider diagnosis checked the captured native `tool_calls[].function.arguments`:
the two bad `.7` results were already malformed/missing fields before local
parsing. The tool had `strict: true`, and the current client selects `/beta` for
such requests. The [official DeepSeek strict guide](https://api-docs.deepseek.com/zh-cn/guides/tool_calls/)
requires both, but that documented promise cannot override observed failures.
This experiment changes output instructions; it does not claim provider schema
reliability or silently relax validation.


`.8` fresh results improved to 11/12 matches, with no missing coverage or JSON
failure. Pro still expanded conversational willingness into objective ability or
permission. Inspection found the inherited instruction to extract each predicate
can conflict with pragmatic interpretation of an entire qualified utterance.
Experimental meaning `.9` uses one cohesive instruction: read the full utterance
and its modal/conditional qualifications first, then extract independently
asserted propositions. It preserves the same explicit coverage and schema
checks. This is a model-task repair, not a phrase filter or local fact rewriter;
new live qualification is required. All `.7`/`.8` compiler bytes remain readable.
