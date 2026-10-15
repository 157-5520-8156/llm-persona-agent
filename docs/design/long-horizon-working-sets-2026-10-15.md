# 长期运行的调度工作集与命运输入

起点 `83fcee0f`。用户要求先解决本地调度开销和命运输入持续增长，再继续原跨日验收。
此文记录修复范围与验收约束；跨日与发布资格仍未完成。沿用 ADR0010，冻结无关功能和大重构。

## 增长边界

设 H 为已完成历史，A 为当前工作及被角色继续引用的依赖，D 为新提交事件。
归档、索引的磁盘空间以及首次冷重建允许随 H 增长；完整历史仍可审计、检索和重放。
正常热调度应读取 A 与 D，不因增加无关已完成历史而重新验证全部 H。
模型输入保留固定资源上限，不能把更高字节上限作为解决方案。
活跃工作本身增加须如实暴露；不能通过删失败任务、降低生活频率、强制遗忘或替角色
决定结束事项来伪造常数开销。必要依赖超过容量时应有明确失败，不能静默截断。

## 现有模块内的改动

- Outlook 的完整权威目录与本轮来源工作集分开。工作集包含当前未结束实体、当前草案
  明确条件/目标、已有 Context 和历史检索实际提供的来源、当前故事的已实现来源。
  已结束但仍被引用的实体继续携带精确状态和 token；其余历史保留在账本与检索中。
  新请求携带工作集契约，纠正与恢复按保存的请求检验所提供的引用。旧请求解释不变。
- 原源码直接把全部历史加入 condition_sources；真实失败包编码113942字节。旧的局部
  重构诊断曾降到75175字节，但最终源码须重新捕获，不把旧样本当最终资格。
- 当前源变化按32个不同实体分批提供；游标只随真实安装的作者结果推进。旧 live
  计划转为终态仍须呈现；历史 pin 排除未来 offer；先按角色范围过滤再应用容量门。
  新工作集明确区分撤回 Fact，旧保存请求仍按原契约恢复；两者 token 计算不变。
- MemoryUnderstandingReader 共用完整批次证明函数。调度只求当前候选的既有成功审核
  指纹，实际角色工作仍读取、校验完整理解和当前来源/隐私。历史指纹与前后继顺序进入
  reader 私有的临时 SQLite 索引，页缓存固定1MiB，退出后可删除并从原记录重建。
- 增量位置必须使用账本事件序号和稳定 trigger_id。当前 trigger_processes 会在生活
  流程完成时删除元素，下标/长度不能作为追加游标。分页使用事件自身序号，不能使用
  批次结束序号，否则一页切开同批事件时会漏记录。
- 仅验证过的 SQLite 当前头与未覆写的读取接口可复用批次证明。外部历史重验证、
  显式重建、历史倒读、内容 UPDATE/DELETE/REPLACE 均使对应缓存失效。已有内容快照
  不得混用 writer 当前代次。未知 adapter 保持完整读取。
- 永久坏批次仍保留。无关内容追加只重查失败批次，不重验全部有效历史；若先前失败
  的批次确实恢复，则按回执顺序重建，以重新判断后继有效性。批次索引更新必须原子。
- WorldStimulus 的恢复工作集同样须能因迟到审计、原位完成、相关接纳歧义而重新加入。
  单纯时钟推进不得重新检查所有已证明结算的历史效果。实际执行的来源、CAS、effect-once
  和回执门槛保持；恢复顺序按首次打开顺序，异常时不发布半成品索引。
- Outlook近期故事固定保留最近12个不同线程，每线程最多12个已实现来源；完整审计
  接口仍保留经历与记忆关系。当前草案只核验明确引用的历史anchor。投递按thread索引
  分页，保留原全局逆序及24个有效不同anchor，而非截断固定条数后假装查完。
- 对账前沿只留running操作和真实committed中间效果。running原位终结必须重读；失效、
  历史重验证或倒读重建。失败记录不删，原取消/恢复执行器继续负责实际效果。
- Proactive只在确有旧接口候选时推导旧重试状态；SocialAction复用现有不可变解码器，
  不缓存来源/接纳判定。当前修复针对已测热点，不能宣称全项目所有历史扫描都已消失。

## 必须完成的检查

1. 固定当前任务与明确历史依赖，增加10/100倍无关终态记录，比较输入字节、实体读取数
   和调度证明读取数。既看正常成功历史，也保留至少一条永久失败。
2. 删除较早生活 process 后再新增记忆 process，总长度不减时仍发现新工作。
3. 冷重建、跨进程追加、历史 cursor、内容篡改、旧快照、缺回执、前后继替换/退休及
   早期失败恢复均与完整 reader 一致；不能仅核对最终 HTTP 或 schema。
4. 真实完整库副本做零API对照，原事件字节、原源库 SHA、1259条前史和旧任务保留。
   冷启动、实际有工作、空闲调度分别报告，不把局部耗时当产品 SLA。
5. 最终源码做针对性检查、双轴复核及一次冻结的完整回归，再从原 v13 终态续跑。
   原始19/1统计保留，独立复核20/2继续继承；72小时起点和费用账本不重置。

## 参考与适用范围

[SQLite partial indexes](https://www.sqlite.org/partialindex.html) 支持只索引相关状态行；
[SQLite scrolling window queries](https://www.sqlite.org/rowvalue.html#scrolling_window_queries)
说明按稳定键推进，避免偏移量随历史增长。
[Materialized View pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/materialized-view)
用于区分可重建读取索引与原始事实。采用的是这个职责划分，不是引入新的服务层。
这些资料不证明本项目的正确性或性能；上述实际增长与恢复测试才是交付证据。

## 当前离线验证

- `working-set-combined.log`: 272 passed，含Outlook、Memory、WorldStimulus、SocialAction与SQLite。
- 真正的计划增长控制保留2050条其他角色active计划；本角色来源仍正确，不触发误用的容量门。
- 65条新终态按32/32/1完整提供，重复读取不消费，已安装但回执前崩溃能补accepted后续批。
- 100次修订的最近12线程与完整倒查一致；支持rewind，外部篡改拒绝。
- 1000条无关offer保持分页顺序；1000条失败offer与一个committed中间效果的对账保持任务及失败。
- `scheduler-working-set-v12`和`...-v2-v12`仅为诊断：发现私有runner导入时把checkout src
  放在PYTHONPATH前，故不能作为源码冻结证据。已补显式package路径绑定；最终测量必须
  使用新的路径断言、源码hash和零API完整库副本。早期材料原样保留并加scope note。

## 冻结实现02511e9a的离线证据

- 完整跟踪仓库资源随源码冻结；之前src-only测量缺少资源，已加scope note，不作性能依据。
- `scheduler-qualified-resources-v12/profile-result.json`：45153条事件、2890模型审计、1131提案、
  2051流程保持；源SHA与全部原事件字节不变、付费API0。首次索引6.497s，最后热空闲1.385s，
  中间两轮accepted分别0.749/0.490s，不把它们混作空闲样本。due数量四轮都是78。
  该数是完整库诊断样本，含profiler开销及同机负载，不是聊天SLA或持续性能资格。
- `source-history-growth-v2/result.json`：真实SQLite已结束计划0/10/100/1000，全部保留；
  当前来源始终1行、240B、146 SQLite VM steps。固定活跃任务时，历史增长未增加该查询工作。
- `memory-history-growth-result.json`：实际审核历史3/30/300批，均保留1个永久坏批次；
  无关内容追加只读该坏批次的2份内容，与完整reader的acknowledgement一致。
- `outlook-qualified-resources-v13/comparison.json`：相同失败pin45175，113942B ->74496B，
  仍为100000B限制；条件来源190->26，所有当前条件与保留token一致。World Context、basis、
  history material/offer、NPC能力/隐私、计划日程、地点权限等14个字段与原包完全相同。
  原失败与全部事件保留。独立冷终态仍45183、1259前史、同一semantic_hash。付费API0。
- Standards/Spec最终只读复核均无新增阻塞。v13私有续跑准入仅允许指定operator stop，
  10个负例拒绝；原20/2、inflight、72h起点、结果和数据库SHA都须保持。
- 代理只读预检发现版本已为0.9.13；后续数据应按新版本单列，不能归因于旧0.9.12或本地优化。
  用户随后确认：更新了软件，网络节点未变。代理升级由用户执行；单次超时不足以归因于升级。

`final-regression-v12/result.json`：实现02511e9a，全量10680 passed /19 skipped /2 xfailed，
1条既有Starlette弃用警告，1293.25s；1705个Python文件hash全程一致。Ruff与diff检查通过。
这些离线结果不构成72小时或发布通过。

## v14真实续跑结果：仍未完成跨日

严格继承v13原始72h起点、20/2失败计数和费用后，首轮新请求261已通过本地输入检查。
Anthropic Messages线路，gemini-3.8-flash-medium，max_tokens64000，实际HTTP体87177B；
连接和发完请求2.84ms，此后90s未收到响应头，按原绝对deadline取消。代理0.9.13日志在
取消时记录infer读取上游流context canceled与HTTP502；不能倒过来把该502认作超时前的原因。
代理指标仅新增1infer_fail，无限流/内部重试/端点切换记录；尚不能拆分上游生成与传输时间。

按未变的三次无恢复失败规则停止：累计21、连续3，18.0866/72h，day1/day3问答均未开始。
未通过增时、改校验、换回DeepSeek或重置计数继续。此轮1proxy/0DeepSeek；未知用量保留
152201token reservation。累计256proxy、10652094reported-or-reservedtokens，5旧DS及
1.218144CNY保守预留保持。此数不是供应商账单。

`live-v14/independent-terminal-audit.json`独立冷读取当前头与全量原事件前缀比较：
45188条原all-World事件全部保留，终态45192；当前World45187/1259前史/1373活跃记忆。
数据库quick_check正常，审计未改终态DB，实际进程exit0与业务失败分别记录，无in-flightHTTP。
此审计未额外执行全量reducer rebuild；原始结果及21/3 checkpoint均已保存。

本地修复资格已完成；剩余阻塞是代理长请求无法可靠完成，完整跨日和发布资格仍未通过。
在没有新的原因证据或外部状态变化前，不重复相同付费试验，也不绕过停止门槛。


## 后续上线验收授权与标准（2026-10-15）

用户明确要求继续冻结功能，允许异步偶发超时，观察自动恢复、积压消化和聊天延迟；
撤销三次失败停止规则，保留所有失败与原校验，最后验证真实QQ。此授权取代上面的停试约束，
不改写v14原始结果与21/3，不重置72h起点或费用。原512proxy/20Mtokens、128DS/5CNY、
每段5400s准入预算保持。超时/冷却/退避仍由实际生产代码执行。

本轮只新增私有验收驱动`run_release_recovery.py`和带独占锁的launcher；产品源码继续冻结于
02511e9a的1705个Python文件hash。脚本沿原30秒节拍、8Action/1background推进，保留day1/day3
固定问答；每小时及失败/Outlook接纳时记录积压。48项旧来源缺口继续计入总量并单列，不能
靠排除它们宣称所有工作已完成。新增/可处理工作的数量和等待年龄须在恢复后回落。

一个额外固定聊天探针在真实代理请求等待5秒后并发进入原QQC2CHost.inbound_text，仍为
CaptureDelivery。稳定message_id与逐次dispatch证据保存；恢复投递不当作首次延迟。
HTTP调用按ContextVar记录执行通道，ModelResult用精确request_hash匹配，未知归因单列。
原失败窗口计数保留并明确为mixed-window，聊天进展不能当作后台恢复。发送窗口计时还须
核对实际action/receipt；这不是同输入的配对因果试验，也不代表QQ网络耗时。

收尾先标pre-close，再等待host任务静止，独立冷读取final-counts及terminal backlog。费用
总账由整个子进程生命周期的launcher锁保护，原未知用量预留不清除。结构/事实/隐私、
CAS、effect-once与真实回执仍为硬门槛，horizon结束不等于这些指标通过。

零API控制：原21/3经历4次失败仍继续为25/7，真实进展后才成为25/0；原失败行全部保留。
同一inflight在聊天写checkpoint期间仍保留；并发归因、冷恢复dispatch身份与双进程锁控制
通过。4项真实host/background隔离回归通过，Spec/Standards最后复核均关闭。
launcher锁FD由child继承，父进程异常退出也不会释放仍活动的子进程所持费用锁。
真实QQ只在隔离恢复/积压/聊天验收之后进行，按已配置的个人测试目标及单出站owner执行。
