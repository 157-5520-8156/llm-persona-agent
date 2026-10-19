# 冷历史读取隔离

冻结 GLM 延续副本继续运行；本修复在独立工作区开发，不写生产 QQ 或该副本。

原完整历史校验约 113.60 秒，同一 Ledger 的当前读取也等待 113.60 秒；独立 SQL 读取
仅 1.92 毫秒。问题包含 Python 账本锁占用，不能以事件循环仍响应来代替聊天不受阻证明。

沿用原事件 hash、upcast、reducer、旧版本兼容及来源校验。冷读取使用只读 WAL 事务，
对齐父账本已验证的 mutation epoch，结束时再检查历史代际；外部历史改变则拒绝结果。
读视图仅初始化 reducer 需要的字段，不复制写入器或实时编码缓存。嵌套事务验证保留原锁。
关闭会等待读取结束，热 head／缓存读取不排队等冷重放。

时钟窥探遇到尚未验证的私有 Outlook 时只返回一个后台唤醒提示；真实后台先完整校验，
失败仍进入原技术失败记录。NPC／事件细节的更早截止不被该提示覆盖。无模型调用或权限
来自窥探结果。WAL 读写与事务快照边界参考 [SQLite 官方文档](https://www.sqlite.org/wal.html)。

56k 行原始快照对照：历史目标 hash 与原方法一致；完整校验 128.75 秒期间，同一 Ledger
当前读取 0.179 毫秒。历史计算仍需时间，本修复解决它占住即时读取的问题。
没有放宽校验、删除历史或改动角色行为与生活频率。

另修正旧技术失败的过期顺序：必须有原 attempt policy 与可读原始来源，且确已超过原 TTL，
才允许进入既有技术过期回执。未到期或缺少期限／来源证明仍等待，绝不伪造作者身份或成功。
原失败字节保留，过期与角色 no_change 分开。

证据：`output/private-audits/cold-history-reader-20261019/`。
`lock-red.log`、`peek-red.log` 为已复现的失败；`snapshot-lock-result.json` 为大快照对照；
`reader-recovery-focused.log` 160 项通过。测试覆盖同时追加、只读、关闭、篡改、嵌套原子验证
及旧任务过期／未过期／来源不明。完整回归和真实服务重启仍待完成。

## 实际刷新输入与 GLM 契约

真实副本的准备路径在调用模型之前触发 `outlook_input_too_large`：编码后 112742 bytes。
拆分发现 `draft_schema` 自身约 20.8 KB，且同一完整 schema 又放入 `response_tools`。
新 `world-outlook-native.7` 只在分别持久化的 `response_tools` 提供完整 schema，正文保留其
载体标记；正文的世界状态、历史、选择、来源 ID 和权限目录不截断。100000 bytes 正文
上限保持原值。旧付费 .1..6 请求仍读原正文/schema；重启使用原保存的工具及请求 hash，
不重建已经付费的契约。历史锚点选择必须获该次保存 schema 的许可，未知 ID 或覆盖字段
继续拒绝。GLM 的 JSON 文本载体不移动 schema 根，因此保留原 `$defs/$ref`，避免无意义
展开；原 native 根包装仍执行必要的引用重定位。模型版本明确改为 json-object-schema.2。

e4576 全套冻结回归为 10911 passed / 2 failed / 19 skipped / 2 xfailed；原失败日志保留。
两处旧测试假定只有 v1/v2 活动接口，不认识规划用的 v4。其中替换计划测试还只等待一次
后台处理；现在分别运行私有规划及新时钟上的执行，继续断言旧计划 abandoned、新计划
active，且原来源和权限均保留。QQ 垂直用例继续断言计划完成、事件结算及角色经历。
这只是测试支持现有模型契约，未给生产添加角色模板或改变执行结果。修正后相关 139 项
通过。新增大请求与冷重启对照覆盖 schema 分离且原证据完整、原付费工具不重新生成。

真实 v6 续跑又复现两个边界：新自主规划接受两项意图后，正文余量不足再次超限；World
大纲首轮误混 keep/revise 字段，纠正后止于明年同月前一天，少于原要求的365未来日。
因此未将 v6 的 HTTP200 或自主规划通过当成整个闭环通过，全部请求、纠正和失败保留。
新增 `.8` 请求将实际 JSON/native 载体写入原请求和重试身份；JSON 载体的提示与纠正
统一使用 JSON 对象，保留 native 载体原工具消息，旧付费 .1..7 不重写。年度 schema 和
失败提示给出精确的最早截止日期，原校验不变。Context 进一步去掉重复的机器证明封套，
保留所有事实条目、来源 refs、可用性及隐私/完整性标记；完整证明仍在 pinned Context
及来源校验链。冻结原包对照98136→90932 bytes，解码相等，事实与 refs 逐项一致，0HTTP。
相关71项通过；覆盖大请求、JSON/native 各自的纠正协议、保存后冷重启、锚点未知ID及覆盖
字段拒绝。ebfdd12c 的全套运行在4589项通过时因这组真实新证据中止，日志保留，不能称全套
已通过；修复后需重新冻结全套及续跑同一个实际副本。

## 无人值守后续对照

c4b06672（候选等价4279a3fe）的完整冻结回归：10918 passed、19 skipped、2 xfailed，
1367.59秒，1716 Python hash 与冻结清单一致。v7真实续跑已接受覆盖至2027-10-31的大纲，
但45次保存的Outlook尝试有37次invalid，其中15次擅自增加根字段；另有历史锚点
`authority_hash`误填状态`unchanged_token`。这些与HTTP200、偶发超时分开，全部保留。

新`.9`只改变新请求的呈现与诊断：JSON keep/revise分支分别展示自己的完整字段，补回
根对象示例，明确token命名空间；格式错误时仍能向同一作者反馈独立存在的引用错误。
不修改、删除或替模型补全字段，只有原先一次受约束纠正。旧`.1..8`付费消息、工具与
恢复语义不升级。枚举两种载体的有效/无效根对象组合证明schema接受集合相同；真实
HTTP边界测试覆盖“多余根字段同时含错误引用”在首个纠正里均有反馈，保存原坏输出，
冷重启不重新计费或重建工具。相关62项通过，具体模型输出改善仍需真实续跑证明。
证据：reference-feedback-red.log、json-branches-red.log、reference-branches-focused.log。
保留原世界和原定2026-10-20T05:48:48+08终点，不因本次诊断改动重置或延长窗口。

## 累积生活状态的来源与请求工作集

0abd5fab/dd5a0bfd冻结全量10923passed/19skipped/2xfailed，1300.89s，1716Python hash核实。
v8无人值守触发重复invalid_projection，阻塞Outlook进入模型：实际快照35个不同来源，
其中34个Plan和1个时钟；不是重复源。完整Situation不可直接装进32源的单条Context。

复用原Situation/Resolver生成显式部分的Context视图：来源仍最多32，时钟、位置、资源、
注意力、原主要安排保留，当前执行优先；计划/目标/承诺超出部分计数明确展示，原完整
状态、课表和动作目录不变。若同伴信息不完整，标记预算导致不可用而非声称独处；元数据
仍保留原完整Situation的隐私等级。每个实际展示字段的源继续通过原完整绑定/哈希校验，
不允许删除可见事实的绑定，不启用aggregate snapshot替代。无新增角色行为规则。
旧Situation新增空标记默认不序列化，原保存实例的语义hash核实保持不变。

继而在同一实际快照完整准备路径发现110089bytes：输入还自动扩张所有live计划/事件。
新outlook-working-sources.2只提供本次可见、原故事依赖和变化所需的来源；来自完整可信
Capsule锚点的计划仍提供结构化安排，即使模型Situation已按字符退化。许可验证仅用原
请求已提供来源。全部实时状态仍用于后台变化监测，未降低生活/命运频率或删除实体。
旧working-sources.1的事实状态、提供范围和付费请求解释保留。滚动输入policy.4区分
新上下文；付费恢复保存原请求的rolling contract，不伪称新版本。

同一完整thread对象在current_outlook及realized_story_history重复；新scope复用精确
retain_thread_id请求引用，解码恢复每个字及来源，历史版本不同不合并，未知ID/覆盖字段
拒绝。原共享字符串规则及校验上限不变。实际同前缀原输入110089bytes，scoped+lossless
重构原包估算97681bytes；完整最终宿主准备对照另存，不能把估算冒充实测改进。

242相关测试通过：2/35/1000计划、100目标、同伴缺源、withhold保持、丢绑定拒绝，
0/10/1000未提供live计划/terminal历史下请求稳定，变化仍触发更新、旧付费重启与原
rolling contract、精确引用的往返/历史版本/拷贝独立/未知和覆盖拒绝。原35源实例的
保留32源逐条对应原事件，原输入/核心/源hash不变；0HTTP。错误夹具先因缺少owner权限
失败，不算overflow复现；真正红对照在同一生产切片函数关闭有限视图时35/1000均撞原
32上限。早期探针日志重名启动失败保留为harness错误，不计供应商失败或验收成功。
证据：situation-bound-red-control.log、working-context-final-v3-scoped.log、
situation-overflow/{result,fixed-source-slice-result}.json、outlook-situation-budget/。
