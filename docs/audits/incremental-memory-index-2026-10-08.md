# 统一记忆索引：提前准备与增量复用

基线68f1ad93，隔离分支codex/role-memory-qualification。用户批准导入后预计算、新增/修改增量更新、聊天复用，并希望后续生活与社交共用系统。实现6732006f，生命周期修正4d576d5d/b62c47b4；没有修改生产配置、数据库或真实QQ。

## 已实现的行为

- 统一采用既有RecallCorpusCompiler/RecallDocument，包含前史、生活Experience/WorldLife、旧对话、事实、私人印象及有来源的长期理解。统一检索表示，原始事实、转述与主观理解的权威和格式仍分别保留。
- 语义索引缓存当前Context的不可变行。第一次只算缺失正文，后续查询只算query；正文不变而隐私、来源或时间等元数据改变时，只绑定新Context，不重复计算向量。
- 前台查询不再每次把全库向量取出、重建临时语义索引。排名、故事关系、字数预算、actor/subject/时间/隐私约束保持原状；仍是精确逐候选评分，本次没有引入ANN。
- 启动在前史初始化完成后准备。后台记忆工作drain及生活活动推进完成后，通过同一Context来源解析器准备。相同水位不重复工作；外部写入在下次维护或正常Context刷新时反映。
- SQLite缓存负责模型身份+正文哈希的持久复用及原预算。完成的64条批次逐批保存，中断或重启后复用成功部分。失败有degraded、精确预算错误与冷却重试，不假装全库ready。
- 状态查看不等待向量供应商长锁；忙时用明确stale/busy的预算快照，不把未知用量写成0。关闭停止后续批次，等待在途访问结束后关闭共享存储。

这层只让已有证据更快进入可回忆范围；不决定角色该记住谁、如何评价、是否说话，也不自动把整段聊天或模型生成内容升级为事实。前史直接初始化的例外不扩张为所有运行期记忆强制保留。

## 完整人生库的实际尝试

新验证库：`output/private-audits/incremental-memory-index-20261008/world.sqlite`，从上一轮dense-life的accepted-world克隆。实际普通来源编译得到1,628个文档，15个只供上下文展开，1,613个可搜索。新增1,037条人生记忆全部进入该语料；另有生活、旧对话、事实、感受/理解等来源。

**整库真实BGE预计算尚未完成。** 克隆继承的当日向量账本已经消耗198,911估算token；本次完成若干批次至242,113后，下一批被250,000日上限拒绝。缓存现覆盖746个检索文档，其中170个来自新增人生，仍有867个文档待计算。来源已存入不等于语义索引已全部预热。

余下867条不同正文按当前UTF-8字节保守算法需218,075预留token，按现配置估算CNY0.0314028；这是静态计划量，不是已发生用量或调整预算的授权。

没有重置预算、更换空账本或增大上限。当前复制库保留完成进度，之后预算恢复时可在同一库继续准备。另以只读方式比对副本与基线的全部39,900条事件存储字节（包括库中其他World），逐字节哈希一致；这不是一次新的冷回放。原目标World事件序号39,895、semantic hash `db5d4e2d85fba52419251823378ca2cb00a9fc857d0208f6fa53b1b0e1cb2be1`均不变。

本轮新增5次本地BGE HTTP请求（4批正文、1批查询），共43,376估算token，配置账面增量估算CNY0.006246144；这不是云端账单。DeepSeek作者/保留/对话请求0；Codex会话成本未计入。旧DeepSeek共享实验账本及其未知预留没有调整。

## 有范围的性能对照

只用已缓存的746个当前来源文档，4条固定查询，每条5次；新旧路径使用相同真实BGE向量、query、cursor、权限、seed和原排名逻辑。查询向量先准备，两边均为缓存热状态。逐次比较完整RecallResult，20/20完全相同。

| 查询 | 旧路径中位数 | 复用路径中位数 | 减少 |
|---|---:|---:|---:|
| 高中共同经历 | 324.85 ms | 224.40 ms | 30.9% |
| 程屿与分开 | 245.69 ms | 141.49 ms | 42.4% |
| 第一次帮工的钱 | 289.11 ms | 183.03 ms | 36.7% |
| 母亲的活动 | 353.98 ms | 230.34 ms | 34.9% |

这是缓存子集上查询路径的CPU/本地缓存收益，不是全1,613文档、冷请求或整轮聊天延迟。检索内容未进行自然对话语义评分，也没有把直接喂材料给模型当用户聊天验收。

## 检查与已修复问题

公开Recall/应用接口覆盖：准备后只算query、相同正文的当前权限绑定、正文修改、移除、旧cursor不变、缓存跨进程重建、供应商失败重试、启动和后台入口、后续生活来源，以及受控并发health/close。

初始135项重点检查中134过，1个既有shutdown测试绕过构造器未初始化新增可选字段；补齐fixture后通过。初始210项检索回归通过。修正关闭生命周期后核心69项通过。全量回归在b62c47b4冻结运行：**10,332 passed / 19 skipped / 2 xfailed**，1268.25秒、exit0；另有1条依赖弃用警告。之后仅修改交付文档。所有改动Python文件Ruff和编译检查通过。

### Standards

按AGENTS.md、ADR0010和技能标准审查当前实现，无未解决的硬标准违反或可操作异味。复用只涉及向量和索引，当前来源权限重新绑定；未新增角色语义审核或规则行为。

### Spec

发现并修复一项P2：整轮预计算持锁导致health阻塞，并使close继续启动后续批次。加入65文档的受控并发测试，验证状态立即可读且取消后只完成在途批次。最终独立复核通过；另验证首批64条已完成后关闭，重启只计算剩余1条，用量不重置。未发现新增阻塞。

## Hindsight借鉴范围

延续[已核实差距图](../research/hindsight-gap-map-2026-10-07.md)，借鉴写时准备、更新水位、可观察失败与来源失效。本次没有接入Hindsight服务，也没有另添LLM抽取/审核。官方[Retain](https://hindsight.vectorize.io/developer/api/retain)的同document_id替换行为适合对照更新机制；本项目原始历史仍不可变，变化反映在派生索引。官方[Recall](https://hindsight.vectorize.io/developer/api/recall)的证据回读与混合检索继续作为参考，不以其指标推断本地效果。

## 使用与恢复

新增命令 `scripts/prepare_world_memory_index.py` 只读取已接受来源、写派生索引/向量缓存，不调用角色或投递。普通生产构造器在配置语义embedding时自动接线；本次没有重启或切换实际部署。

验证库继续准备时使用原路径，不再传`--clone`：

```sh
PYTHONPATH=src python scripts/prepare_world_memory_index.py \
  --database output/private-audits/incremental-memory-index-20261008/world.sqlite \
  --world world:companion-v2:qq-c2c:geoff --actor agent:companion --subject user:geoff \
  --env-file /Users/geoff/Projects/Girl-Agent/.env \
  --env-file '/Users/geoff/Library/Application Support/Girl-Agent/production-text-policy.env' \
  --output output/private-audits/incremental-memory-index-20261008/resumed.json
```

预算未恢复前不要重复跑整库请求。私有证据同目录包含initial.json/log、cache-coverage.json、warm-subset-performance.json、benchmark_cached.py、qualification.json、各测试日志。后续普通聊天从这个保留预计算进度的库再克隆，并显式使用一致配置；首次克隆之后不得用旧空副本覆盖。
