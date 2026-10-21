# GLM 服务端结构约束：公开一手资料核对

日期：2026-10-21。公开资料研究阶段仅查文档与 SDK 发布文件，0 模型请求、0 QQ，未读密钥或 .env；随后两次受控网关探测及适配结果见末节。World 与运行源均未改动。当前实际反例来自主任务：生命周期 `noticed` 非 null 时，契约要求 `user_channel_completion` 为字符串 `none`，真实 GLM 却产出 null；本文未重跑该收费反例。

## 结论

**`json_object` 能约束 JSON 载体，不等于服务端保证 JSON Schema 的 const/类型/条件分支。** 当前官方资料未提供可确认的 GLM 原生 `json_schema + strict` 或 strict 工具约束；不能因 SDK 接受 object 参数、OpenAI 兼容字样、或模型支持工具调用就认为该能力存在。第三方网关可能另有语法约束，也可能忽略未知字段，必须对精确入口/模型/模式独立核实。

| 项目 | 官方文档已确定 | 未确定/适用边界 |
| --- | --- | --- |
| 精确模型 | 已有 [GLM-5.3-Flash/FlashX 官方页](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash)，列明文本参数同 GLM-5.3，支持 Function Calling 和 JSON 结构输出 | 不仅是旧4.x资料；但页面未承诺服务器强制任意 schema |
| `response_format` | [国内 Chat API](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)和[Z.AI Chat API](https://docs.z.ai/api-reference/llm/chat-completion)公开枚举列 text/json_object | 均未列 json_schema、schema strict；文案有“三种”而只枚举两项的编辑瑕疵，不能据此补猜第三项 |
| 工具 JSON Schema | `tools.function.parameters` 描述参数 schema | 非服务端强制解码的保证；Z.AI 明确提醒调用函数前仍须校验参数 |
| `tool_choice` | [官方工具调用指南](https://docs.bigmodel.cn/cn/guide/capabilities/function-calling)明确目前默认且仅支持 auto | required/指定函数的 forced choice 未获官方确认；工具调用不保证必然发生 |
| `tools.function.strict` | 官方当前工具字段与示例未列该参数 | 不应推定支持；也不能说永远不支持，可能是文档不完整或网关扩展 |
| 官方“Schema 验证” | [中文指南](https://docs.bigmodel.cn/cn/guide/capabilities/struct-output)与[英文指南](https://docs.z.ai/guides/capabilities/struct-output)示例把 schema 放提示，API仍传json_object，回来后客户端 jsonschema.validate | 这是提示驱动+本地校验，不能误读为服务端编译/约束 schema |

官方 Flash 页还明确思考只能 enabled，推荐 reasoning_effort max。这不是本项目必须用 max 的效果证据；协议探测应固定现有有效模式，不能顺带换推理预算后把差异归于结构约束。

## SDK 对照

官方 [Python SDK 指南](https://docs.bigmodel.cn/cn/guide/develop/python/introduction)指向 `zai-sdk`。本次下载但未安装/执行 [PyPI 0.2.3 发布 wheel](https://pypi.org/project/zai-sdk/0.2.3/)，只用标准库 ZIP 读取 `zai/api_resource/chat/completions.py`：

- `response_format: object | None`、`tools: Optional[object]`、`tool_choice: str`，原样加入请求 body。
- 这类宽类型是序列化能力，不是后端支持证明；没有在该请求构造路径发现 strict schema 编译器。
- wheel SHA256：`f8d6417f8cff58d5b6cba5c27577ee55aa817ae825c6c88fe883f6f6f9265d90`。原wheel、PyPI metadata及摘取源文件保存在同目录 `glm-public-docs/`。
- 搜索索引仍有 `zai-org/z-ai-sdk-python`，但此次 GitHub 页面/raw/git ls-remote 均未能读取该仓库（404/Repository not found）。不据缓存 issue 状态断言最新实现；以可读取官方 API和发行包为本次证据。

## 与 DeepSeek 的有限对照

[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)同样把 json_object 定义为有效 JSON 输出，并提醒截断/空内容。[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)则**单独**明确 Beta 端点、每个 function.strict=true、服务端 schema 验证和受支持子集。这种明确服务端契约是 GLM 当前资料尚缺的证据；不能把另一厂商 Beta 能力迁移推定到 GLM/当前第三方入口。

## 原探测设计（下文保留；执行结果见末节）

只在隔离探针中向当前第三方精确模型发**1次**合成短请求：`response_format.type=json_schema`，嵌套 `json_schema.name/strict=true/schema`；schema 用很小的对象，必需 noticed 对象与 `"user_channel_completion": {"type":"string","const":"none"}`、additionalProperties=false。提示故意请求 completion=null，制造输出格式约束与普通指令的区分。不要加入真实角色/世界/聊天，不执行任何返回工具。

保持现有已知有效推理模式、有限输出预算（需覆盖该模型思考，过低导致 length 应单列“截断”）、禁自动重试、记录精确脱敏请求/原响应/status/finish_reason/model/usage/未知计费。判断：

- 明确400不支持 format/strict/const：此入口当前不支持该探针，不自动改走一串额外付费试验。
- 200但null/额外字段/缺字段：不能称强约束生效；有忽略/不执行的直接反例。
- 200且合法：仅说明该合成样本符合，不足以证明服务端严格执行；需要网关明确保证/元数据，以及后续更贴近真实条件分支的受控验证后才换生产 carrier。
- 语法支持、拒绝未知参数、正确返回位置和语义效果分开记录。当前无官方支持证据，不先投入 forced/required tool + strict 组合多轮碰运气。

**建议边界**：若入口确认存在真实服务端 schema 约束，可复用当前结构契约减少低级格式错；不能删客户端严格校验、事实来源审核、角色自主选择、CAS/effect-once或失败记录。若不支持，精确同模型纠错和原技术失败/自动重试仍保留；不能把 null 悄悄补成 none 来冒充模型遵约，也不把这次已明确的生成违约继续归咎于尚未找到的提示矛盾。


## 当前第三方入口的实测与应用范围

本轮对用户已配置的 api.edgefn.net / GLM-5.3-Flash 执行两次有明确目的的独立请求，没有自动重试，均不写 World 或 QQ。原始请求、响应、用量和限制保存在 `output/private-audits/autonomous-sequence-20261020/glm-strict-probe/` 与 `glm-actual-strict-probe/`；token 用量已报告，实际收费金额未知。

1. 小合成对照：普通提示要求 null，schema 要求字符串 const=none。json_schema/strict=true 返回 none，200/stop，2.917秒，59输入+17输出token。说明此样本遵守了格式要求，不能单凭它判断后端是否真正采用约束解码。
2. 同一份真实失败 WorldStimulus 请求：只把 response_format 从 json_object 改成 json_schema，原 messages、完整 schema、推理模式和64000输出上限均保持。返回通过原完整 schema，200/stop，14.028秒，26839输入+214输出token。这与第一次真实输出引用未提供的 Plan ID 形成结构层对照，但输出仍有“天还没亮”“通知掠了一眼”等需要原事实审核的陈述；未安装、未借用其他稿件的审核回执。一次合法结构不证明首稿成功率或长期稳定性提高，也不能把缓存不同的时延当纯模式收益。

现有 BackgroundGLMModel 增加显式 `WORLD_V2_BACKGROUND_TOOL_RESULT_CARRIER=json_schema` 选项。它复用同一个单结果载体，把原完整 schema/root 交给 response_format，并保留原提示、命名绑定和严格本地解析/事实审核；模式有独立模型版本和请求身份。遇到不支持、非法结果或工具调用混入时不偷偷改用其他载体、不修补模型字段。默认配置仍未改动，正在运行的 v53 仍使用原 json_object。

旧 native/native_or_json_object/json_object 六个完整工具/身份/请求对照的5893字节一致，新编译器的请求与第二次真实探测完全相等。现有 Life 审核公共入口及冷回执验证覆盖新/旧文本载体；这些离线证据不替代新模式的真实完整因果链验收。原生产 QQ 与保留世界没有因探测改变。
