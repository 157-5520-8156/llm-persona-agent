# Anthropic 本地代理接入与隔离试用

基线 `e32eca51`。用户指定 `http://localhost:8788`、Anthropic 协议，并选择代理返回的 `gemini-3.8-flash-medium`。用户说明是订阅计划，5小时1.5亿、每周5亿token；这不是已经由供应商账单核实的额度。不得套用DeepSeek价格，也不推断代理名称代表其真实上游身份。

本轮交付可复用的原生Messages适配器及受限试用入口；先通过已有生产宿主的显式模型注入接口验证。默认生产配置、真实QQ和旧DeepSeek账本不修改，不把试用宣称为已完成线上供应商迁移。试用内所有远程文本角色和既有后台审阅均注入新代理，DeepSeek构造被拒绝，无回退；本地BGE与轮次边界模型保持原配置。

- 复用现有请求发出/完成、取消、熔断、容量、调用次数与来源绑定机制；仅将协议差异隔离为请求、响应和流式解码适配点，旧DeepSeek和OpenAI兼容请求保持原字节形状。
- 原生`/v1/messages`、`x-api-key`、顶层system、工具input_schema与tool_choice；不把OpenAI参数直接发给Anthropic。只支持已使用的文本输入，无法无损转换的角色或内容拒绝，不自行重排中途system。
- 工具名称、单工具约束、正文与来源保持；错误模型标识、截断、多工具、未知块、异常结束均为技术失败。思考块不作为可见文本输出；不声称代理提供强制JSON Schema保证。
- SSE按message/content-block生命周期还原文本或工具JSON，累积usage采用覆盖而非累加。缓存读/写与普通输入分开识别，总输入是三者之和；缺用量或非终态流保留未知。请求哈希对应实际原生请求，不伪装成DeepSeek用量。
- 订阅试用只记录token，不写伪造的人民币费用。已有金额门槛仅在隔离副本显式关闭，由试用请求数及token预留限制替代；默认真实账本及预算保持不变。
- 固定普通聊天从`inbound_text`进入，CaptureDelivery捕获，输入只有用户自然文本，不加标准答案。聊天保持sampled非阻塞模式，所有新开关在发请求前断言实际生效值。完整1259条前史保留，后台任务不删除、不伪装已完成。
- 分别记录冷启动、聊天首条捕获、模型请求、输出处理、结构失败、未回应及调用用量；模拟发送节奏不冒充真实QQ延迟。短连接测试不证明长期故事质量。

协议依据：[Messages API](https://platform.claude.com/docs/en/api/messages/create)、[流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming)、[缓存用量](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)。这些只作为协议依据，代理兼容性与本项目效果必须实测。
