# Life 引用 Fact 的精确值边界

状态：已实现来源准备和引用选择校验；未安装 Life 正文语义接受。

## 问题与现有事实

`fact_draft_adapter.py` 的观察提取协议要求角色选出的 value 是原始消息中至多 256 字符的
精确片段，随后将其 SHA256 保存在 Fact 的 value_ref/value_hash 中。FactRecallItem 则
保留整条 Observation 的 source_excerpt，旧读取没有带出这个值绑定。于是消费者有整条
消息可读，却不能确定其中哪个片段对应已接受的谓词值。

例如消息“用户决定取消周五的报告，仍保留周四的约定。”只将“保留周四的约定”选作
schedule.commitment 的值时，整条消息和“取消周五的报告”都不能借用该 Fact 值的权限。
它们是否有其它报告或事实依据，需要分别核验，不能反过来断言它们一定为假。

## 实现

- `FactObservationValueBinding` 只识别现有 observed_message / value:observation:<SHA256>
  协议。未知 opaque value 或 operator source 没有此读法。它验证模型提供的引用是否仍为
  原文片段且哈希等于已接受值；不枚举子串、不用关键词、不再次调用模型做提取。
- Ledger resolver 在完成现有 Fact/Observation 事件与绑定核对后，从对应 FactValues 带出
  可选 binding。历史 before-image 和 epoch 来源沿用各自已核对的原始值；旧 Capsule 无字段
  仍可读取，序列化不补 null，不对旧原始输入伪造新证据。
- `life_fact_readings.py` 要求原始 Fact 或其接受事件引用、实际主体、原文、谓词、状态和
  展示时间字段一致。内容 hash、观察事件别名和未知来源不能获得 Fact 值权限。
- 目录 v3 的 Fact 项 `permissions=[]`。普通 `require_reading` 无法直接授权整段文字。
  `require_fact_value` 必须提供精确引用、主体和范围；重编完整来源目录后核对值绑定，再返回
  原始谓词与时间上下文，并保留完整 observation_context，避免引用片段脱离否定等上下文。
  这个观察上下文本身仍不是已接受值。调用者仍须判断候选声明是否属于这个谓词，以及原文是否真的支持声明。
- 当前值只适用 accepted_fact，历史值只适用 historical_accepted_fact。代码不猜当前聊天对象，
  使用原始 subject_ref。旧 binding 缺失或来源未展示时明确排除，不赋予默认语义权限。

紧凑事实视图现在保留 status，并保留存在的 valid_from/valid_to；值绑定仍留在宿主来源档案，
不会放入角色可见材料。普通 relevant_facts 实际只选择当前事实：撤销后旧 Fact 会进入 Recall
语料，而不是自动出现在当前列表。因此“旧事实被误当成仍有效”的生产表现在本轮没有被证实。
历史 Fact 经 RecallDocument 展示后的来源适配仍是独立缺口，本轮未宣称完成该路径。

## 验证范围

临时 SQLite 使用现有 Fact 提取/接受链，只模拟提取模型；随后调用真实 StructuredCharacterRoleFaculty
与队列模型，核对其实际 messages、LifeSourceView 和原始 Capsule。关闭序列化后重新读取，精确值
可以选取，整句、另一个真实片段、改写、错主体及错状态范围均拒绝。撤销链确认当前来源消失，
重开 SQLite 后旧值和有效期仍可恢复。历史值目录权限测试明确是读取器层样例，不冒充 Recall
到 Life 接受的完整链测试。

该机制没有新增模型调用，也没有任何正文语义放行或 World 写入能力。完整候选的隐含事实漏报、
来源语义判断、同角色纠正及写入前接受仍未闭合；月费、长期真人感和交付验收仍未完成。
验证见 `docs/audits/life-fact-readings-validation-2026-09-16.json`。
