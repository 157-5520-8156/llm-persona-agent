# 状态哈希、旧协议故障与生日来源完整性

未部署、未发送 QQ 消息。原全天副本 exec session 54594 继续运行，未因本轮代码修改重启。

## 保留：同一状态摘要的流式计算

`_state_hash_from_fragment_bytes` 原先将全部字段拼为 state_chunks，再多次拼接完整 material。虽然 `_encode_state_delta` 只重新编码改变的字段，哈希阶段仍分配整份状态的副本。

现在按原字段排序、原 JSON 字节、原 cursor、reducer version 和 World ID 分段喂入同一个 SHA-256。每个字段仍参与当前提交的完整性检查。没有改变摘要算法、提交顺序、SQLite 持久化、CAS、来源检查或 effect-once。

实际副本状态字段共 61,622,167 bytes，最大的 model_result_audits 约 35.5MB。交替两组，每次八次哈希：原 57.63/54.76ms → 新 28.42/28.46ms，临时分配峰值 184,876,809 → 1,791 bytes，摘要完全相同。约 27ms 是每次状态哈希的局部节省，不是整次提交或回复已达标。完整状态仍需扫描一次，长期 O(history) 开销没有被此修改消除。

独立测试以完整 canonical `_state_hash_material` 为 oracle，对空状态、空数组/null、Unicode/引号/换行、较大字段进行摘要比对；原有提交、并发拒绝、迁移、篡改、重启与回放测试保留。证据：`output/private-audits/streamed-state-hash-20261004/`。

## 保留：旧 compact gate 类型分支修复

扩大的回归出现六条失败，明确异常为 `'InboundGateToolContract' object has no attribute 'native_slim_core'`，不是角色沉默或测试时限随机失败。新 native slim 属性属于 InboundToolContract；旧 compact gate 不提供它。调用点现在先确认协议类型再读属性，不给旧协议添加不存在的能力，也不改它的 payload 或解码。

最终 SQLite、inbound prompt、tool、native core、角色 author、来源 receipt 和版本接线回归 433 项通过；Ruff 与 diff 检查通过。原六条失败及其重新验证日志保留。

## 生日：区分记录、可见片段与生成

真实档案 `prehistory-record:celia-expanded-family-f09` 明确记载：母亲已开始唱生日歌，父亲跟进快了一句；切蛋糕时角色拿走完整草莓，塌掉那边划给父亲，父亲还吃了刮下来的奶油。

原全天回答把唱快的人说成母亲，且没有回答蛋糕如何分配。这是人物归属和回答覆盖问题。一个通用角色建议的私有对照在两条原输入上改善了它们，slim 展开通过，但不是完整运行验收，两个调用约 0.05463344 元。

完整副本追问仍生成“塌的那边我爸我妈分了”，首条约 9.999 秒，账本一笔约 0.0295 元。这条建议因此已撤回，没有加入生产提示词；不能凭两个定向输出推广。

进一步核对实际请求发现，生日记录确实被选中了，角色看到的 automatic_prefetch.items.3.text 只有固定的 480 字符，恰好结束在“塌掉的那边划给父”，没有后续完整内容。`recall_corpus.py` 当前以宽度 480、步长 416 生成前史窗口；选择到了相关窗口，不等于给到了完整事件。原始记录存在、检索进入候选、阅读完整性和角色扩写必须分开核对。截断不能为母亲参与分蛋糕的补写提供来源，但也不能将本次失败全部归因于提示词。

下一步采用成熟的 child-to-parent 思路：[LangChain 官方 ParentDocumentRetriever](https://reference.langchain.com/python/langchain-classic/retrievers/parent_document_retriever/ParentDocumentRetriever) 用小片段检索、较完整父文档作上下文。这里只参考机制，不引入整个框架：保留 BGE-M3 和已有排序，在同一已验证的记录、Actor、Archive、时间、隐私和总阅读预算内补齐选中事件；完整来源仍需回到原记录，不能把相邻未验证或他人私有故事拼入。须验证排序对照、实际可见内容、来源回执和回答，尚未实现或达标。

证据：`output/private-audits/birthday-answer-attribution-20261004/`、`output/private-audits/birthday-runtime-grounding-20261004/`。

## 原生耗时证据

未使用 cProfile 的普通聊天：首条捕获 7.582s，角色供应商 2.226s，全部前景供应商 3.127s（包含辅助 endpoint），Context 1.539s，多次 ledger_commit 合计 2.047s，首次角色调用从输入开始约 3.116s。样本与正在加速运行的另一副本共享本机，不能拿它作独占生产 p95。Capture 也不是 QQ 送达。

该普通聊天一笔约 0.0248 元；生日完整副本一笔约 0.0295 元；两条提示建议对照均计费。没有把 Source/API succeeded、只有“想想”的台词或一次充分回答当作全日质量和速度验收。

原运行截至本轮已完成 26/50 条聊天、最近完整命令约 3.8009 元，已超过目标，尚未完成。候选完整日对照等待同一个基线到达终态。源码状态哈希与旧 compact gate 修复留在当前工作树；正文提示建议已撤回。5s、1.5 元/天、完整质量与生产资格仍未关闭。
