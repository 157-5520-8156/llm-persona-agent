# 入站原子表达：分支独立 v3

第 11/13 批实际作者反复漏写未选分支的两个 null 字段。v2 的附加提示未得到稳定效果；
第 13 批已有完整候选，却因该传输负担消费唯一纠正并超时。v3 从结构上移除无用分支
字段，仍要求完整角色 Decision 或 Recall，不由宿主填充、删改台词或选择分支。

## 契约

仍只有一个强制工具，唯一外层字段 `result`。其内部 `anyOf` 分支分别拥有自己的
properties、required 和 `additionalProperties=false`：

- Decision：`result_kind`、`appraisal_draft`、`expression_draft`。
- 可用的 Recall：`result_kind`、`recall_request`、`private_turn_state`。

每个原分支在补跨分支 null **之前**复制，随后独立投影为 strict Schema。嵌套 appraisal、
affect、timing、关系、生活意图、私态和表达能力约束保留。DeepSeek 文档支持对象内
`anyOf`，要求每个对象自己的属性全部 required；并不要求不同分支共享属性。
参见[官方 Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)。实际供应商对新
Schema 的接受和稳定性仍待验证，文档声明不能代替真实调用。

解码先查单一外层和重复字段，再按实际工具中选定分支的精确字段集查验。混入其他分支
的字段即使是 null 或空对象也拒绝。v3 不调用旧 padding cleanup；移除传输壳后仍进入
原 canonical 语义/能力验证，再做完整可见正文审核。初始 Recall 不可用或进入 final、
after_recall 时，Schema 和派生的系统字段表都不再列出 Recall。

工具名和 identity 为新 v3；默认仍 v1，旧 v1/v2 生成与解码保留。显式选择 v3 必须启用
原子 whole-candidate 路径及注入有用量记录的全文 reviewer。CLI 使用
`--require-visible-source-review --visible-author-tool-version 3`；这不是生产默认开启。
普通作者截止、纠正次数、来源权限、动作接受及费用准入不变。原两消息请求连同 tools、
tool choice 和 identity extras 继续冻结核验；旧 v2 回执只按其原请求解释。

## 已完成的离线验证

139 项相关检查通过（33.72 秒），包含各阶段 now/later/silent、Recall 权限、重复/混入
字段拒绝、完整嵌套 Schema 等价、实际 CLI→host→角色→审核→Action 与冷 replay。
另扩展 v3 的来源拒绝后一次重选、完整替换再审核及失败零 Action，16 项通过（9.17 秒）。
这些测试的供应商与投递是替身，不是新真实模型资格。

旧六组工具、choice 和 identity 与改动前 `a4ecc9df` 在同进程中逐项相等。固定
`PYTHONHASHSEED=0` 的原始黄金哈希进入回归；旧初始 Schema 有既存 set-derived
required 列表次序，不能据此声称旧字节跨所有 hash seed 稳定。本片不迁移旧契约。
原始第 13 批候选在仅改变分支包裹语法的内存对照中可通过结构检查，旧 v2 仍拒绝；
它没有被重新发送，没有补做真实来源审核，也没有被计作已交付。

日志：`/tmp/girl-agent-v3-branch-gate3.log`、`/tmp/girl-agent-v3-review-correction.log`。
