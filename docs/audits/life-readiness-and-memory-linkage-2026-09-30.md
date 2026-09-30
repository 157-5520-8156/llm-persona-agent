# 生活停滞与新前史联动：生产修复

2026-09-30。用户授权自行定位生活停滞、验证前史联动并推进有限邀请演示。

## 已确认的根因和修复

1. 生产Epoch继承了一个2026-08-08的NPC待处理指针，但当前世界缺少其actor决策事件与可用身份闭包。`has_due_work`仍返回true，实际NPC执行被拒绝，普通World机会被跳过。就绪判断现在复用NPC实际执行的source-closed身份视图，缺失actor决策的指针不提供执行权限；历史行和World事件不被修改。
2. 只有就绪判断还不够：NPC已考虑/无动作/只有私有状态变化时仍占住了整轮普通World机会。现在保持NPC先运行的次序；其没有落地环境事件或计划时，已有到期的Life Development仍可以运行。确切主人公行动结果优先级保持，新的同wake计划仍走原活动选择/接受。环境与角色仍由模型决定，不编程规定角色行动或情绪。
3. 生活作者tier1、必需来源闭包tier4导致当天用量超过软额度一半后，作者仍付费而审阅会被拒绝。必需来源闭包、新来源审阅和同作者修订现在与父生活步骤同tier1；每日/月度/软总额、调用权限和事实验收不变。
4. 真实生日回忆没有发出的重要本地原因是prepared记录超过1.536MB内层限制，而该记录含回忆前后两个完整上下文。新`character-interior-prepared-turn.3`沿用已经存在的外层4MB总记录上限；v2保留原边界，v1兼容。没有扩大模型输入、输出、超时或token预算，也没有截断原始记忆。
5. sampled普通文本的来源证明在作者侧收集prefetch→pull，在完成结果侧收集pull→prefetch，导致同样两个可信audit产生不同表哈希。验证器先将每个carrier audit精确绑定到独立结果里的实际audit，再按已经记录的呈现顺序重建；未记录/伪造/重复audit仍拒绝。真实已保存的失败生日输出离线重新验证通过，未绕过来源闭包。
6. Recall后提示曾同时要求slim的meaning_of_this/my_state和旧expression_draft.private_turn_state。已按slim/旧接口分支一致说明。当前简短提示版本5-slim明确前史的active指仍记得，而非事情现在仍发生。相同世界引用按集合去重，词句和未知引用保留，未知引用仍需原校验。
7. Core原先将本地校验错误抽象为invalid_role_result，隐藏了1.5MB错误。增加只用于本地诊断的错误类型/有限详情和缺失lineage/status日志，没有可见模板回复。

## 实验与范围

- 完整生产副本，实际V4.1、配置BGE-M3、原角色/World/来源权限；QQ只使用CaptureDelivery，未发测试消息。
- NPC试验明确复现旧指针被选中、执行返回actor_identity_unavailable。
- 加速按生产due触发，不真实跨日；预算修复后副本WorldOccurrence72→74，两个新事件均结算。进一步在最新生产副本验证NPC state_advanced后World opportunity不再被跳过：72→73，Life Development返回occurrence_committed。
- 新前史前后对照不是严格单变量消融：before为导入前12条档案的生产快照，after为导入后72条快照，含初始化事件与账目差异；聊天/检索与随机性也不同。早期生日样本多次技术失败，家庭回答还曾将七月安排说成当前近况；拒绝与失败记录均保留，不能只拿最终两条算总体成功率。
- 最终两题均生成、授权并本地捕获。生日回忆包含奶油糊盒壁、爸爸转盒子/找打火机、父母唱乱歌等F09细节；家庭回答明确“我回去那阵子”引用F20，有闷、不好开口等本人反应。表明记忆可以进入当前表达，但不证明所有内心决策因记忆改变，未完成纯记忆on/off消融。
- 需Recall的最终两轮整段驱动约8.66秒和7.18秒；日志ingress约7.12秒与6.27秒。仍未达普遍5秒/P95资格，未证明编造率5%、月费或真实QQ平台回执。

## 测试和发布

相关主组合562项通过；最终来源证明/重启恢复组合32项通过；NPC/Life83项通过；Life/QQ scheduler/scenario组合154项通过。包含伪造recall audit拒绝、旧prepared v2字节上限、v3恢复、有效NPC待办恢复和无效果NPC让出机会等范围。不是全库重新测试，不将这些相加成独立总数。

最终冻结包与备份路径见私有`output/private-audits/life-stall-20260930/release-manifest.json`、`fairness-activation.json`。源码基线cd8915df加本轮工作树修复。启用前保存一致性数据库、启动器与策略文件；仅重建受旧缺失actor权限阻塞的idle调度overlay，不改角色no_op/hold、记忆或World事件。最后生产健康running/ok，scheduler4轮完成，最近生活时间2026-09-30T10:49:39.434505Z。旧外部桥接不可用、旧retry_identity_unavailable诊断另计，不伪装发布合格。

冷回放在冻结候选及完整加速副本上hash相同、0 findings；新回复来源证明在已保存的真实生日输出上独立重验通过。当前所有证据与原始provider用量留在私有目录，公开文本只描述必要范围。后续普通聊天延迟、记忆on/off主观决策对照、负面情绪/长期重复与真实QQ验收仍待完成。

## 最后部署补充

第一次修复就绪数据后，生产仍有NPC合法state_advanced（只更新内心、无环境动作）占住World机会的问题。已补充上述第2项的让出规则，并在最新生产副本真实复现/验证：NPC state_advanced仍保留，Life Development获得到期机会并返回occurrence_committed，WorldOccurrence72→73。最终包为`life-recovery-20260930-f3cee057cdb2`，备份与记录在fairness-activation-backup/fairness-activation.json。最终线上/health为running/ok，生活事件已在真实当前时间生成，未把加速副本覆盖进生产。

QQ桥接另有实际问题：3000端口没有监听、QQ原本没有运行。已启动已有QQ，但API仍未就绪，Cua读取应用窗口超时；存在QQ.app与QQ 2.app两个副本。现有NapCat热更新脚本检查显示基础入口已打补丁，当前热更新目录目标不存在，脚本未改应用文件。已询问用户当前QQ登录/窗口状态。没有发送真实QQ验收消息，不能宣称QQ已恢复投递。
