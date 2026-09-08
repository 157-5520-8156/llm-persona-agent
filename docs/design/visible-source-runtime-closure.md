# 完整可见正文审核的运行时闭合设计

状态：运行时闭合尚未安装或部署。原已选来源表、完整作者载体、独立凭据基础和
同 pin 的完整输出恢复已分片实现；它们尚未形成发稿 gate。依据 CONTEXT、ADR-0010、
trial-07/08 的正文漏报、trial-09 的相关模型诊断，以及截至 `11909fb5` 的调用链
复查。小屋和移动排除。

## 已确认的缺口

当前 `semantic_chat_composition` 丢弃传入的 source reviewer，三个 inbound review
入口仍只验证声明中的引用。旧低层 repair 入口直接抛错，不能靠打开一个参数接通。
可复用的纠正机制是 `InboundTurnFaculty → CharacterInterior Core → correct_role_result`
的同一角色、同一 snapshot、一次纠正。不得复活旧的作者/备选/appeal 嵌套重写链。

来源表的新投影解决了可读材料和主体丢失，但 `_source_closure_evidence` 仍主要按
已声明 `world_claims` 选择资料。`include_visible_authorities` 只补部分 identity /
relationship / interaction 项目。`world_claims=[]` 的原聊天还没有完整支持材料，
普通 Fact 的 slim value 也不能视作完整 source proof。

当前首个完整 expression unit 会提前交给 materializer；tail 在首 Action 之后处理。
因此只检查 tail 无法收回已发出的首句。另一个入口是冷恢复：已记录 Proposal 可经
`_existing_observation_outcome` 再进入授权；单纯进程内“已审过”标志不能覆盖此路径。

## 一条纵向薄片

先从原受信 Capsule 的**原已选成员**派生独立审核视图，复用现有完整 Capsule 和
Fact/Dialogue proof 验证方法，不把新检索或当前 head 材料混入旧 pin。该视图与普通
聊天 prompt 分离：不伪造 slim value 对应的 bindings，不改变角色实际看到的旧正文。
传记、报告、角色活动等来源仍各自保留原 scope、时间、主体、隐私和状态限制。

新增显式 whole-candidate 路径，在同次作者最终稿完整后进行一次审核，通过后才
返回第一个可交付 ModelOutput。reply_only、full_turn、Recall 后最终稿和纠正后的
替换稿走同一入口。完整候选审核会增加首句等待时间，不能继续宣称保持原首 Beat SLO；
需要实际测量正常和纠正路径，不能先释放文本来隐藏等待。

单一审核模块接受完整候选、原 pin、已选 proof 和原 alias 映射。合法 `unclosed`
结果向同一角色提供精确正文坐标和可用材料，允许原 Core 的一次重选；完整替换稿
重新审核。审核超时、预算拒绝、非法返回是技术失败，不能解释为角色沉默，也不能
据此要求角色改词。trial-09 错主体的非法 verdict 正属于需要明确处理的情况，
不能将宿主结构拒绝冒充模型语义判断成功。

新增持久的 `VisibleSourceReviewReceipt`，绑定完整 candidate、Capsule/cursor、
已选材料、alias、审核请求/响应、typed verdict 和最终 Beat 映射。具体合同版本须
在实现时分配；旧审计不补授新资格。凭据沿 ModelOutput、ModelResultAudit 与
RecordedModelResultAudit 保存，由 Proposal 原 `model_result_ref` 引用。新接受、
冷恢复和回放均核对同一凭据，Acceptance 冻结其身份。现有 subcall hash 只能证明
调用关联，不能单独证明正文已通过审核。

## 必须共同成立的验证

- 首稿拒绝或审核技术失败前零可见 Action；同一角色最多一次纠正，完整替换稿重审。
- 替换稿仍失败时零可见 Action，保留两次原始结果及精确技术/语义分类。
- 原 paid terminal、审核凭据和原 pin 冷恢复后不再询问作者；缺失或错配的凭据
  不能被已有 Proposal 绕过。
- 当前私态和未来意图可以合法 source_free；来源足够的报告、Fact、活动也能通过。
  原 slim Fact 与完整已选 proof 分开，未来 Plan、活动开始、阶段结束、目标达成分开。
- 实际供应商请求仍逐次准入。正常路径增加一次审核；角色重选通常再增加一次作者
  和一次审核，计入同一 turn 与月度账本，不能通过独立 DB 或隐藏重试降低表面费用。

小样本通过只支持该组反例。后续仍需要真实多轮聊天及加速生活观察误拒、漏报、
重复话题、记忆持续性和花费。任何未安装的参数或 stub 都不作为已完成机制计数。

## 接入点复核：先采用完整 atomic 载体

全稿审核的首片可以采用现有 atomic decision/Recall 工具合同。它保留完整
ExpressionDraft 与 Appraisal 能力；旧 compact `reply_only/full_turn` 是 stream
载体，不能重标成 atomic 后复用。现 `_compile_combined_cognition_envelope` 即使
收到完整 stream events 也只读取 head；将完整 raw 传入旧 parser 不等于取得了全稿。

新显式模式的 composition 需要把有效 `expression_episode_mode` 设为 `off`，
让 Deliberation 主操作走 `propose`，同时不预启 tail，诊断也应报告真实有效模式。
仅让 `stream_provider_available=False` 还不够：配置仍为 stream 时主操作仍可能
调用 `propose_stream_head`。完整 atomic 头若进入原 tail continuation，原代码把
缺少 owned head 当成失败并可能再问一次作者。

作者的纠正入口也必须遵守同一模式。当前 `correct_role_result` 根据供应商是否
支持 stream 选择 `propose_stream_head`，会把外层 atomic 调用的 Core 纠正切回
增量头。现有 Core 的一次 ordinal 与同 snapshot 机制仍可复用，不能靠只改外层
mode 解决这个问题。上述是已核实的接入设计；参数能力和最终 guard 安装须分别验收。

## 已完成输出的独立恢复依赖

在 Core terminal 已写入、Proposal 尚未记录的窗口，旧 inbound `.1` decision
仅保存输出引用与 hash，完整 ModelOutput 留在 Faculty 的进程缓存。`b9d7ed4b`
的新 `.2` decision 已保存完整原输出及被普通 `model_dump` 排除的
physical/subcall/candidate audits，与原 terminal 同行持久化；保留原 turn、
capability、cursor、proposal 和作者身份。它仅修复同 pin 的 public adapter
消费口；普通冷入站尚不会提交这个原 ModelInput，不能宣称那条链已恢复。

Recall 的 live seal 使用进程随机 HMAC，不能把旧 seal 序列化后在新进程重用。
已实现的窄恢复先从安装的 turn store 读取原终态/prepared/snapshot 并核对原
recorded trace，再向私有端口传递不可序列化的已验证 proof 来重建 live seal。
它不重新检索、取消验证或固定 HMAC。已有完整 head 的恢复也不等于尚未结束的
HTTP tail 可以恢复；这些窗口必须分别计数。

Receipt 接线后，接受门位于 `derive_expression_plan_material()`，回放门位于
`_expression_plan_manifest_recorded()`；这也覆盖已有 Proposal 的冷恢复。新审核
资格须由明确合同或 policy 要求，不能以“有 receipt 才选择新版”的可删字段方式
降级。旧 model audit 和 expression manifest 保留原字节，但不补授新审核资格。

## 原材料与审核要求的下一条交接

Deliberation 验证 compiler-issued handle、创建原 ModelInput 后，仍同时持有原
Capsule 和尚未追加 InnerLifeSnapshot/Recall 的原正文。这是编译来源表的最薄入口。
当前没有将该 handle 传入 author 的现成钩子；不能在 author 收到改写后的 ModelInput
时假装那就是 compiler 原输入，也不能使用一个进程级 latest Capsule 缓存。

新增显式宿主 requirement 需要进入原调用身份及 Faculty capability，并与完整
冻结表的 hash 绑定。表本身走宿主专用材料口，不塞入聊天正文。现 capability
snapshot binding 只存 ref/hash，不含 payload；因此持久 carrier 还须保留原
requirement，供接受和恢复独立判定。仅从 output/receipt 是否带字段选择 `.9`
仍可删除字段降级，不是合格接线。

实际 author alias 在最终 provider compaction 后产生，应由该次调用直接冻结。
纠正和 Recall 不能以当前 head 重编原表；新增材料只可来自该回合已经验证的原
Recall/prefetch trace，当前 composer 尚未为这些额外来源或部署身份完成资格。

审计载体计划使用新版本，但容量与独立锚点必须先明确：当前 prepared 上限
512000 bytes，旧 audit JSON 上限 262144 bytes；不得截掉候选或来源来适配。
`_checked_output`、`_audit`、`_strict_audit` 都手工重建字段，需要显式保留。
原作者以 winning invocation 与完整 Proposal/CharacterInterior lineage 绑定，
审核者以真实 source_review subcall 绑定；Record 单行没有 subcall tuple，冷读
须 join 独立 ModelResult。不能把 receipt 自报的 author/review 抄作 expected。
