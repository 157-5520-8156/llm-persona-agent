# 世界前瞻首版与中央 NPC 执行：隔离验证

状态：`manual_only / qualification_incomplete`。基线9cb07f2b；首次实现27bd40fc；恢复与成本修正f219855b。后续仅补本文和上下文记录。

## 实现范围

按用户最新要求，只有主角独立思考。命运层统一作者预写带条件和时间窗口的外部事件、NPC原话；同批脚本投放零模型调用。中央模式禁用旧NPC独立推理与普通事件即时生成；保留主角CharacterInterior、未预写行为后果的世界结算以及记忆处理。

采用独立SQLite sidecar保存事项图、原始请求/回复、原作者身份、CAS版本、逐次调用占位和投放回执。当前选中脚本沿既有OccurrenceContentCoordinator和WorldOccurrence生命周期执行。没有把未来草案写成过去记忆，没有新建NPC思考服务，没有新增独立生成后语义审核器。

计划、事实、NPC身份和隐私变化均重新检查；只使相应条件分支失效。正常脚本Occurrence进展不单独购买整批重写。既有日程只提供公开结构字段，NPC capability提供身份摘要、共同经历和计划指针。预算沿既有作用域，不改账本或上限。

开关：`WORLD_V2_LIFE_OUTLOOK_ENABLED`，默认false。代码位于现有隔离工作树，没有更改真实QQ、生产配置、进程或生产数据库。

## 证据

- 当前f219855b：26项前瞻专项测试通过；与LifeEcology、LifeDevelopment、应用构造/关闭及旧NPC模块共272项回归通过，19.03秒。
- 修改的15个Python文件Ruff和编译通过；git diff --check通过。
- 应用公共入口的离线fixture演练：中央作者1次、NPC模型0次，连续投放3个事件；冷启动重复wake新增事件0，作者仍1次；replay与持久化snapshot一致。未来哨兵文本在投放前不在World事件中。
- 演练cursor为World16 / Deliberation12 / Sequence28，snapshot hash `297055ec5fa6aea00b1cebd673537513f6dca58c70635a8d894419208ddf00a2`。这些数字是fixture机制证据，不是真模型语言质量证据。
- 全库一次运行终态：10,356 passed / 19 skipped / 2 xfailed / 1既有依赖warning，1201.96秒，退出0。该进程启动于27bd40fc，运行期间工作树更新为f219855b；部分已导入模块可能仍为启动版本，因此不能称作最终提交冻结源码的全库验收。最终修正另由上述272项覆盖。

私有证据目录：`output/private-audits/world-outlook-pilot-20261009/`，包含`fixture-result.json`、可复现`run_fixture_pilot.py`、`fixture-runtime.log`、`f219855b-regression.log`及全库日志。三线可读示例见`docs/design/world-outlook-three-thread-example.md`。

## Standards

最初发现两项P2：纠正已发出但结果未知时会重发；更换模型配置后恢复旧回复会误记新模型身份。均已修复并经独立复核，2项针对性回归通过，当前无未解决发现。每个ordinal调用前持久化占位，未知结果不重购；恢复保留原作者身份。

## Spec

最初发现P1：通用激活器可能绕过预写机会过期条件；P2：已持久化的有效作者回复无法恢复安装。均已修复并经独立复核，4项针对性回归通过，当前无未解决发现。通用激活器永久跳过前瞻中间态；专属恢复检查当前条件并取消无效的未激活记录。有效原答复重用，缺失安装确认可补齐。

## 资格边界

本轮真实供应商请求0，未使用或重置既有DeepSeek实验余额。NPC原话只证明发生过这次说法，不证明内容为真。结构校验不能证明环境自然语言完全没有越权，仍需真实作者质量验证；当前没有上线资格。

首版只覆盖环境事件和NPC有归属的原话，不包含所有NPC动作、离屏生活或旧NPC待执行计划迁移。也没有完成角色在真实聊天中对这些事件的自然回应、完整记忆形成、几周叙事质量、长期成本和延迟验收。现有fixture只覆盖指定执行与恢复接口，其通过不将上述项目升级为通过。
