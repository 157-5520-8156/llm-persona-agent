# 记忆呈现与真实作者对照

状态：完成8组作者阶段的定性诊断；不构成完整聊天或发布验收。
起点 `fc13d296`。所有云调用使用隔离副本，未发送真实QQ、未修改生产库。

## 实验与限制

同一冻结World分别给当前semantic4200检索结果、人工核实的既有相关来源。
每组每条件一次，Flash原生tool3、4096输出、temperature0.7、thinking关闭。
不是检索算法竞赛，也没有改变生产检索排名、门槛或角色行为规则。

第一批16次调用遗漏现行visible-source requirement，导致作者仍收到旧政策，cadence
也未对齐。这是测试接线错误，结果保留但不作为当前版本证据，费用全部计入。
第二批修正为实际10旅程的完整system政策、工具、能力与cadence配置，16次均返回
schema合法结果；独立评估为12份未审核decision草稿、4个未执行Recall请求。

**第二批仍有范围限制：新增来源没有携带运行时trusted Recall trace。**
文字呈现不能自行授予引用权限，部分相关Fact不在作者的许可表中。因此这批可用于
发现文本理解与呈现问题，不能据此断言生产审核能通过或检索方案改善了聊天。
其余六题还是显式的合成问题，只有旧T13/T29保留原提问pin。四个Recall未继续，
不能计作成功、失败或角色沉默。没有为缺失链路编造回执或关闭校验。

题目和评分标准在结果前固定；另一代理仅阅读匿名条件结果，未读分组映射。
系统人设本身喜欢桂花乌龙，T29共同近期对话已有姓名，所以答对这些题不是纯召回收益。
每条件一个样本也不能给出总体准确率。旧fixture对话时间与逻辑时间不一致亦保留为限制。

## 直接观察

- 两份妹妹年级输入均含“今年高三”，作者仍选择Recall；其中一份错误地认为未提供年级。
- 两份演示输入均含原话与发生时间。一份答出原“下周三”，另一份忽略时间并追问用户。
  问题要求原下一周的哪一天，不要求必须输出完整日期；不能因只答周三就判错。
- 问角色妹妹时，两份都断言没有妹妹，其中一份补成独生；完整人设也没有这个否定事实。
- 未知气温和更改茶偏好的日期没有直接编出数值或日期；其中两次选择Recall，尚无终端回答。
- 一般闲聊没有强塞召回事实。茶建议中的角色口味有独立人设支持，不能误判为主体冒用；
  “常喝”和“暖胃不刺激”的额外断言则不由该人设或用户偏好证明。

这些现象说明需要分别检查来源呈现、来源使用和最终审核；不能全部归因于漏召回。

## 本次窄修复

`RecallDocument`原本保存主体和对话说话人，`interior_recall_item`却在模型阅读中丢弃。
现将真实`subject_refs`及非空`speaker_ref`原样传入，不把索引所属角色当成事实主体，
不改写第一人称原文，不推断缺失说话人，也不规定角色应答、追问或沉默。

沿用现有index版本绑定读取形状，新hybrid.8的实际字段计入6000字节预算；旧index的
阅读保持原形状。检索打包、Core呈现、prehistory审核重建共用同一读取函数，避免
旧回执失效或将未展示的材料扩充为来源。此修复不宣称解决年级理解、时间使用或否定编造。

修复提交`26e775d3`：94项聚焦测试通过，覆盖原生作者HTTP提示、字节预算、可信trace、
prehistory审核及冷重放；禁用debug账本后的3项native/旧格式测试另复跑通过。
聚焦命令为项目venv的`python -m pytest -q`，范围为`tests/world_v2/`下的
`test_prehistory_recall_review.py`、`test_recall_attribution.py`、`test_recall_packing.py`、
`test_recall_short_cues.py`、`test_recall_index.py`、`test_recall_corpus.py`，耗时14.90秒。
Ruff检查覆盖本次4个源码和3个测试文件，diff检查通过；独立源码复核未发现可操作缺陷。
这是接口与恢复证据，未追加付费overlay作模型效果证明。

## 下一处已独立复现的阻断

在该修复工作树上，用现有合成Fact经过真实corpus compiler、coordinator签发trace、
RecallPort、Core合并和`InboundTurnFaculty.consider`，在调用供应商前截获原生输入：
自动记忆中有该Fact，作者也收到verified trace，但原Context没有该Fact时，
`counterpart_history`引用权限仍不包含它，现有机械校验返回无效索引`[0]`。
仅对同一verified trace使用已有`augment_model_content_with_recall`的离线诊断，结果为`[]`。

这确认了**自动预取Fact从呈现到可引用来源之间的局部生产接口缺口**，不再只是前述
overlay的限制。测试停止于作者边界，尚不证明真实审核、接受或送达。没有将诊断用的
手动augmentation混入生产修复；完整审核目前仅专门补充prehistory，故不能只加一行
作者权限就宣称修好。下一步限定为把同一可信、实际呈现来源贯穿权限、审核与回放，
随后用一个完整对话链验证。先保留本次小修复，不继续叠加检索排名方案。

证据：私有目录`native-prefetch-permission-proof.json`。
该单例使用测试fixture并在provider前停止，零云调用。

## 时间、费用与后续继续点

第二批作者调用耗时1.817–4.342秒，中位3.202秒；不是首条消息或完整聊天延迟。
第一批估算0.2857576元，第二批0.27814192元，合计新增**0.56389952元**。
共32次，全部有原生usage，无新增unknown。费用是仓库价格表估算，不是供应商账单。

运行状态仍从`output/private-audits/release-clean-continuation-20260921-10/run`恢复。
**最新累计费用已在`output/private-audits/recall-chat-comparison-20260921-01/run-v2/world.sqlite`：
1764 usage /1761 reservations /73既有unknown holds。** 原历史计费行及所有非计费表
逐表保持不变。下次恢复10运行，须先合并本次全部32次用量及reservation，再进行供应商
调用或水位计算；不能从旧1732行账本直接续跑。两个probe都不是可恢复的完整运行宿主。

私有证据目录：`output/private-audits/recall-chat-comparison-20260921-01/`。
`prepared-pairs-v2.json` SHA256 `3f36ea1cf77b038dc44fc216f09ae172620ac6666fb311c8ea8cff1118992a1d`；
`evaluation-v2.json` SHA256 `aba467ffe4150281bbda1bd54576ec77ef897951f131ab055ed7578f05a7fe35`。
原始两批输入/响应、废弃原因、匿名映射、逐项评估和两次对账均保留。
