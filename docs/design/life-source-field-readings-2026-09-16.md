# 生活来源的字段读法与实际展示范围

状态：纯准备/选择校验接口；尚未安装为 Life 语义审核或接受凭据。

`life_source_readings.py` 消费已验证的 `LifeSourceView` 与其原始快照，建立字段目录。
它不接受候选声明或按候选关键词挑来源，也不构造聊天 Beat。目录每项绑定原始材料身份、
字段指针、完整值、实际展示范围、说话人/所有者和来源权限；选取时重新编译并核对整个目录。

## 字段边界

- 结算只允许 environment.text 和 authorized_attempt_result.text。环境不能支持角色动作。
- 活动的 accepted_intention.text 只证明意图；status/存在的生命周期时间字段只证明生命周期。
  意图文字不顺带获得生命周期或动作完成权限。
- 角色旧发言只能支持曾经说过，不支持其内容真的发生。用户旧发言可作为其说话人的报告，
  没有当前 counterpart 绑定时使用 `source_owner` 和原始 speaker_ref；不能因此声称它属于
  当前聊天对象或客观世界事实。该读法只接受原始对话 ID/声明事件引用，不接受 hash 别名。
- 主观历史只读取原始 meaning/dimension，并保留主观历史权限；原有隐私、所有者和事件证明
  继续生效。感觉的内容不证明动作、外部原因或他人内心。
- 人生记忆只读取保留下来的原文。原文中的已声明历史人物仍可作为主体，不能替换成当前用户。
- 传记按原有坐标编译器读取年龄、学业阶段、季节、居住标签、活动中的生活阶段与已结算
  传记坐标。`life_biographical_readings.py` 从实际展示的同一父记录重新推导坐标，要求
  字段/ID、完整类型和值、原始逻辑时间、内容地址均一致。数字、列表和对象保留原类型；
  它们只有 `biographical_coordinate / companion` 权限，不会因此获得过去动作、完成结果、
  意图或他人经历权限。生活阶段描述的语义仍须后续审核，目录不替它证明任意嵌入经历。

采用已有来源能力验证器，但不沿用旧实验的未知类型标量默认读取。v3 的 Fact 精确值通过单独的
`require_fact_value` 校验已接受值的哈希和模型选取的原文片段；整条观察仍无直接字段权限。
旧值绑定缺失、历史 Fact 的 RecallDocument 适配以及其它未覆盖来源被明确排除，不能默认把
ID、hash、标签和时间戳当成直接证据。旧聊天协议与其目录没有修改。
FactRecallItem 的 source_excerpt 是接受记录绑定的整条 Observation 文本，而持久化 Fact
保留 opaque value_ref/hash；新增可选绑定只校验选取值，不自行解出谓词值。不能把“已接受一个语义槽”
扩大为“整条观察里的每件事都作为该事实接受”。补读法时须保留这个区别，不能按关键词拆值。

## 展示身份

读取必须匹配本次实际 source_inventory 的来源和材料范围。再按同一语义字段核对原始值
是否完整展示：周记的 world_consequence 外层包装、旧对话缓存布局和紧凑 Appraisal 的
meaning 列使用已知布局还原。相同字节出现在别的字段或元数据里不算目标字段已展示。
未显示、截断、不支持的布局或未知读法不猜测补齐，而进入 excluded 诊断。
传记父记录必须在 `biographical_context` 的实际目录中且只出现一次；时间缺失不使用
当前时间补齐。生活阶段/结算坐标按原有 ID 选择规则匹配，不依赖列表顺序；重复 ID 排除，
不能凭“其中一个碰巧匹配”放行。受限隐私坐标沿用原有编译器的排除规则。

新增结构化传记读法将目录版本提升到 `life-source-readings.2`。旧 v1 文件仍作为历史证据保存，
不会被悄悄当作 v2 接受。目录未安装为持久化接受凭据；此次没有改变既有聊天协议或作者提示。
后续精确 Fact 值选择使用 `life-source-readings.3`；细节见
`docs/design/life-fact-value-readings-2026-09-16.md`。旧目录版本同样保留原始审计范围。

该目录目前没有新增运行调用或 World 写入。`require_reading` 只校验模型明确选取的来源
字段/权限；它不会判断候选的事实是否完整、措辞是否准确，也不证明模型选择的语义范围正确。
`write_authority=false`、`complete_source_coverage=false`、`semantic_coverage=not_assessed`
始终保留。不能用存在 reading_id、引用差集为空或本地校验通过宣称生活正文已获事实闭包。

公开临时 SQLite 的雨停结果可生成环境字段，不能用该字段支持角色完成散步、过去意图、
旧感受或旧发言。活动、主观历史与导入/保留人生记忆的正例使用真实本地接受产物。
语义判断未调用真实模型，生活写入前审核尚未安装，两个既有正文事实反例仍 strict xfail。
下一步需补齐其余来源读法，并让语义核验在这些权限内核对完整候选；不能靠目录替模型判断。

验证记录：`docs/audits/life-source-readings-validation-2026-09-16.json`。
传记增量：`docs/audits/life-biographical-readings-validation-2026-09-16.json`。临时 SQLite 开启
ecology 后生成六项传记坐标，使用真实结构化角色路径的 MockTransport 输入和关闭后恢复的
checkpoint 验证。生活阶段对象与结算坐标的重排、重复 ID、改写/隐私反例是读取器层测试，
不冒充这些对象的完整 World 结算链验收。
