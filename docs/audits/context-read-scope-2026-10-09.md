# 一轮上下文的一致读取与材料共享

状态：隔离分支的结构改进；稳定两秒、完整回复和生产资格仍未通过。基线af814fa0（runtime d3d7fb06），本轮runtime 2c10c762。设计见 `docs/design/context-read-scope-2026-10-09.md`。

## 本轮实现

当前投影核验通过后，Context编译在一个短期内容快照内完成。SQLite使用独立只读连接固定快照，内存Adapter复制不可变记录表；不占用共享writer连接的读事务。正常返回、异常或嵌套退出均关闭lease；跨线程复制ContextVar不能借用原线程快照。未知Adapter保持普通读取路径。下一轮重新开快照、重新验证来源和候选资格。

LifeContent、结构化Experience及Recall的重复读取共享本轮已验证材料。先形成完整材料，再独立应用各个视图的单项和总量预算；不把聊天裁剪后的文本当作Recall原文。保留structured_content_unavailable与budget_exhausted的原有区别，包括显式null回应。前史证明和候选资格复用分别绑定相关源投影集合身份、候选、actor/privacy、cursor、限读ref及视图参数。

仅缓存已证实不可变的结果；失败或可变结果不污染已知对象ID集合，持有对应对象直至退出，防止ID重用。内容读取缓存有2048条/16MiB估算上限，材料缓存4096项；没有完整跨请求依赖图或跨请求授权缓存。跨scope弱引用缓存只保存不可变对象的纯结构检查，不保存来源资格。

SQLite数据库路径来自实际已打开的main数据库，后续cwd变化不会切换数据源；`:memory:`使用独立backup快照。关闭的store不得新建lease，旧lease退出后不可继续使用。同一轮内故意保留已固定的内容，包括缺失值；下一轮看到新增或损坏。快照只覆盖同步Context编译，在模型请求前释放。

没有修改角色的动机/措辞/行动决定、原文覆盖、检索排序、预算、deadline或生活触发频率；没有新增模型调用或embedding环节。收益来自减少重复工作。

## 固定完整输出对照

四个固定case的完整Capsule和完整Recall corpus均与基线逐字节一致，1629份documents保留。不是只比较数量或摘要hash。

| case | 基线秒 | 本轮秒 |
| --- | ---: | ---: |
| 完全冷态 | 4.471 | 4.057 |
| 世界前缀变化后下一轮 | 1.018 | 1.008 |
| 再下一轮 | 1.260 | 1.210 |

同一Context重复请求约0.00007秒，不当作下一轮聊天速度。冷态仍超过固定脚本1.5秒门槛，因此该脚本退出1；这里的局部改善不等于入口耗时或长时性能资格。

## 冻结后的普通入口测量

沿实际配置、完整前史库副本、BGE、build_qq_c2c_host和public inbound_text，使用与上一轮相同的六个自然问题。每组独立启动，输入计时不含进程启动。关闭profile，不与全suite并行。截获DeepSeek HTTP待发边界，实际远端请求和可见回复均为0；之后的deferred是人工截获，不能算角色沉默或成功回复。

| 请求 | A组秒 | B组秒 |
| --- | ---: | ---: |
| 最近生活及后续事项 | 2.459 | 2.407 |
| 高中同学经历 | 2.089 | 2.037 |
| 童年家庭经历 | 1.983 | 1.927 |
| 朋友冲突与和好 | 2.148 | 2.051 |
| 当前小事唤起旧人 | 2.179 | 2.224 |
| 大学第一周 | 1.902 | 1.767 |

A/B中位数分别2.119/2.044秒；合并12轮中位2.070秒，范围1.767–2.459，4/12<=2秒。上一轮冻结样本中位2.148秒，本轮低约3.7%，属于小幅改善；样本与机器波动不足以证明稳定收益率、p95或生产SLA。两组严格2秒脚本均退出1，目标仍红。两次启动分别42.525/40.621秒，仍需单独优化，不能藏入“热请求”结果。

开发中间样本及profile仅用于定位，不用最佳一组替代最终冻结结果。所有慢样本和失败门槛都保留。原费用账本只读核对：CNY5上限、166次请求、已知花费4.628753、未知预留0.231340、剩余0.139907均未变化。当前待发请求预留报价0.217016–0.220538高于剩余额度，未重置/提高预算，未发送付费生成请求。

## 回归与复核

147项相关测试通过（12.96秒）。新增测试覆盖跨线程/嵌套/异常释放、写入与篡改在下一scope可见、WAL checkpoint、未知Adapter、actor/privacy/cursor/预算/限读ref变化、结构化文本与null、多预算逐字节一致、可变对象不能进入缓存、cwd变化、内存SQLite和关闭后的lease。修改的8个Python文件Ruff及编译检查通过。

全suite在2c10c762冻结运行一次：**1 failed / 10415 passed / 19 skipped / 2 xfailed / 1既有Starlette依赖warning**，1193.93秒，退出1。结束后核对8个修改Python文件SHA-256全部未变；只改文档、不并行测速。不能记为全量绿色。

唯一失败为 `test_full_reviewer_timeout_and_retry_survive_the_author_deadline`：第二次模拟审核sleep0.21秒、单次timeout0.22秒，仅10ms余量；整套运行中触发source_review_timeout，最终采用quick，未达到本用例期待的主模型结果。同一冻结源码独立重复5次全部通过，随后整个 `test_deliberation.py` 的115项通过（4.95秒）。该用例使用测试专用_Resolver，没有创建本轮读取scope；deliberation、interactive_turn_budget、context_capsule及测试文件均与基线完全相同。目前证据支持定时敏感，而非已证实的本轮功能回归；未取得可靠的隔离失败复现，不能声称根因已修复或整套已通过。未放宽生产deadline、修改该用例或重跑全套来覆盖失败。

### Standards

静态复核未发现可操作的硬标准违规或启发式代码异味。共享结果绑定当前projection、对象身份及完整读取参数；跨线程、嵌套作用域和退出后的读取不会复用旧权限结果。SQLite快照固定后，正常及异常退出释放事务；LifeContent裁剪顺序和抑制原因保持。

### Spec

静态复核未发现可确认的Spec缺陷。scope生命周期、未知Adapter兼容、完整材料与独立预算、当前投影的来源证明、不可变结果检查均符合设计。两轴均0条可确认发现；复核者未运行测试、测速或供应商调用，不能代替性能和运行验收。

## 仍待推进

当前重复材料共享已减少一部分工作；冻结样本的Context native中位1.176秒，其余请求前耗时中位0.922秒（逐条入口耗时减Context，不能把两个中位数直接相加）。入口整体只下降约0.08秒，不能据此继续无限叠加缓存。下一步应以请求入口到HTTP边界的实际阶段开销选择主因，分别处理剩余Context构造和账本提交/请求准备；任何更大范围复用都需要明确依赖与失效证据。不能靠减少记忆内容、跳过来源检查或缩减角色生活活动换指标。

本轮没有全量长期并发、RSS/长期WAL、完整真实回复延迟或故事质量验收，也没有生产/真实QQ部署。真实回复还包括远端模型时间。

## 原始资料与证据

[SQLite隔离](https://www.sqlite.org/isolation.html)与[WAL](https://www.sqlite.org/wal.html)支持采用独立短事务快照；[ContextVar文档](https://docs.python.org/3/library/contextvars.html)说明context可复制，因此实现另加线程与closed检查。这些是机制依据，不是本项目性能证据。

私有证据目录 `output/private-audits/context-read-scope-20261009/` 包含红绿回归、开发诊断、`fixed-equivalence.json`、两组`frozen-2c10c762-*-result.json`、`frozen-summary.json`、`runtime-manifest.json`、`static-reviews.json`、全suite日志、`source-freeze-verification.json`、`deadline-isolated-repeat.json`、`deadline-path-baseline-check.json`和整模块复测日志。固定完整输出保留在前一Context诊断目录的read-scope-views标签中。
