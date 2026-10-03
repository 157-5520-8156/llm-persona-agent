# 来源校验读取与固定全天成本续修

基线 ab169368。持续 goal active，未切换生产或发送 QQ 测试消息。

## 已保留的修复

只从远程作者响应结束到首次 CaptureDelivery 采样，发现 verify_recorded_candidate 两次反序列化整段模型审计历史。ModelResultAuditProjection 继承 ModelResultRecordedPayload；其构造验证器已检查 parent_model_call_id 与不可变 audit_json 的一致性、canonical/hash 和谱系。

现在先按经过验证的投影父调用编号定位，只完整解析命中的子调用；父调用、原 capability、cursor、proposal、来源、审阅证据和回执仍逐次验证。选中子调用的 JSON 父编号与投影不一致仍拒绝。没有缓存权限成功或接受结论。

同一生产副本2490条审计、交替四次：原校验0.189/0.132秒，新校验0.0485/0.0480秒，receipt_hash完全相同。专项测试加入500条无关审计，确认当前校验不解析它们；原有篡改、原始宿主/供应商锚定与冷恢复检查继续执行。不能将这一段节省比例当作整轮提速。

另一组真实四轮首条捕获仍6.63/5.72/5.60/6.37秒。它与正在进行的后台副本共用本机，不能作严格速度A/B。5秒与真实QQ资格未通过。

## 全天测试工具接线

生产库含 world:companion-v2:geoff（五条旧启动记录）与实际 qq-c2c World。旧 benchmark_daily_cost.py 要求整个库只有一个World，直接拒绝实际快照。现在严格选择 --primary-user-id 指定的目标World；缺失仍拒绝，保留其他World并记录到provenance。所有时钟、读取和事件报告仍按目标World隔离，没有删除或混用历史。

固定一份快照、24个小时目标、50条预先写好的用户测试输入；--follow-runtime-dues尊重每个实际声明的到期边界，不只跑小时目标。--virtual-clock避免真实跨日，使用真实V4.1/BGE-M3与CaptureDelivery。诊断预算是高护栏，预算拒绝/技术失败/未知账单不能作为节省。快照代码、请求/响应、usage差额及输入保留。

该程序已真实启动并持续推进；截至本轮记录约33条命令/100次调用，约CNY2.07，尚未完成24小时。这个较短时段已超过1.5元目标，不是达标证据，不外推为稳定日费。生活生成、novel-origin/source检查及重写是主要费用，聊天不是唯一部分。

## 已撤掉的缓存候选

World Author普通请求约4.3万输入tokens，时间/本次示例在稳定权限材料之前。尝试把同类型请求的稳定结构、权限和位置材料放前，并把本次指导放到用户数据末尾。所有来源/权限值保持，普通同模式共享前缀约7.2k→60k字符，完成后果模式约23.5k→58.9k字符。

真实三份相同输入：原请求费用约CNY.134，新约.106，后两条新请求cache_hit升到23680。但原3/3通过本地完整草稿与绑定检查，新只有1/3通过；两份新草稿降低了结果privacy。按每份合法结果算，新候选更贵，**不能作为保质降本成功**。已撤掉整个候选生产实现，保留私有结果。

初次试验验收脚本误把provider manifest中的manifest_hash和location capability_ref当作可输入模型字段；原始模型调用均计费，未重新重复付费。后来去除这两种展示字段、重新计算并与原展示值逐项对齐，才对保存的六份原始响应重验。上述3/3、1/3来自这次重验，不隐藏验收脚本错误。

后续应在保留请求约束的指令层级与隐私耦合质量前提下设计缓存，不只看字节相同或费用下降。

## 留给下一次 goal 续跑

- 当前全天进程是本轮确认存活的exec session10141、PID21747；重新检查该handle/PID及progress，不因短暂无输出重启。程序可能继续推进，以上是记录时的状态。
- 工作目录 output/private-audits/daily-workload-20261003/baseline-world-bound；读取result.json确认真正完成、错误、拒绝、未知账单与全部频率/投递结果。原仅World数检查失败的baseline目录没有真实调用，保留。
- World缓存失败候选证据 output/private-audits/local-critical-path-20261003/world-prefix-paid；原始失败验收与corrected-validation.json都保留。
- 保留的是8行父调用定位与全天World选择接线，不部署未达标候选。5秒、1.5元/天、保质、长期及发布资格继续未完成。
