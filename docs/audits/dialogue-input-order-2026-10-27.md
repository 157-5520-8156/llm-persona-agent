# 对话输入排列：单变量尝试（2026-10-27）

本次只修改当前主目录的 slim4/v25 新请求排列，不启用 native-dialogue-comparison 工作树中的新协议、账务或备援机制。

## 改动

在既有引用编码完成后，将当前快照移到协议/计划材料之后；快照内把 conversation/recent_dialogue 放在最后；最终保留 current_trigger_message 及可选 role_result_correction 收尾。只重排字典成员，不修改值、不移动数组元素、不删除材料或改变引用映射。旧协议路径、已有已保存请求和回执不经过此重排。DeepSeek、角色设定、工具和审核策略保持原样。

代码位于 `character_interior/inbound_context_presentation.py` 的 `dialogue_tail_inbound_material`，由 `inbound_author.py` 在计算实际请求身份之前调用。

## 本地证据

- 新增顺序检查先在旧实现出现 3 项失败；实现后通过。
- 临时 SQLite + MockTransport：角色可以用现有 messages/wait 自主发一条短反馈并保留等待，后续请求仍保有此前连续输入、角色反馈和新消息。没有新增倾听枚举或固定话术。
- 在旧排列下接受但尚未发送的回复，冷重开后保留原 request bytes 和审查证据，恢复原 Action 且只发送一次，不重新调用作者。
- 8 个相关测试文件：188 passed，12 skipped。运行时禁止外网、禁止读取生产 data/.env 内容，使用私有 HOME/TMPDIR 并清理；Ruff 与改动文件的 diff whitespace 检查通过。
- 两份此前冻结输入中，重排前后逐对象及引用解码结果完全相等，长度分别保持 64,197 和 66,220 字符。recent_dialogue 起点由约 21% 移至约 82%。这是字符位置，不是模型注意力测量。
- 这两份相邻冻结输入的共同前缀由 4,125 降至 3,512 字符；它不是实际 provider cache-hit 指标，不能据此声称成本或延迟不变。

## 运行与效果边界

仅两处源文件在运行进程启动后发生变化，已正常重启 QQ 适配器加载。健康端点返回 200、角色 ready；总体仍有重启前已存在的账本存储与主动考虑逾期告警。实际运行仍为 DeepSeek flash、grounded_review_v25、sampled。

本次没有付费模型对照或自动发送 QQ 测试消息，也没有证明自然度改善。后续以实际交谈中是否接住具体内容、能否自然继续以及性能是否变差判断这个候选；若没有改善，不追加行为脚本来掩盖结果。
