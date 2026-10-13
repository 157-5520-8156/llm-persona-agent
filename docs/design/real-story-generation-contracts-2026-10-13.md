# 真实长期故事验收与生成前合同

基线 `1d7b4e6a`。继续已经授权的完整前史隔离验收，沿用5元总上限、全账本64次真实请求和原14模拟日结束时间。保留旧任务、失败和所有原始模型结果；续跑复制已有副本，不能清队列或修改角色输出制造成功。

真实v4/v5/v6依次暴露：通用execution schema未表达类型互斥；basis字段允许任意字符串而运行时只认事件authority；NPC隐私下限参与校验却没有提供给作者，故事继承隐私错误提示也缺位置。v7首次成功安装真实编排，随后旧记忆整理出现同批forget来源又据此保留understanding，纠正后仍失败。

本轮修复原则：把已经存在的硬规则表达在生成输入及schema中，不增加独立审核模型，不改角色选择，不放宽执行权限。

- 新作者schema以kind区分五类合法execution形状，必需参数非null；lane/NPC身份一致、participants唯一，conditions具有状态或精确token。存储模型与旧null/default载体解析不改。
- basis_refs列出本次合法执行依据，不能与经历实体ID混淆；已有不可变机会的合法引用语义需保留。去掉schema冗余title注释以控制输入量，所有字段和验证条件保留。
- NPC隐私下限来自同一pinned projection，输入与校验一致；模型自行选择合法隐私。不得由主机自动改写privacy或删除故事链接。
- 新请求使用版本化具体错误信息；旧保存请求继续原错误/重放解释。故事隐私错误可列出所有受影响机会和最低级别，避免让纠正者猜测。
- 记忆整理的显式source-token格式，将既有forget/新understanding来源互斥规则写入条件schema。角色仍可retain并建立理解，或forget且不建立冲突理解。summary用于说明本次选择，不由代码替换任何选择或引用。quote-handle格式继续其既有绑定/校验。
- v8又发现注意来源误判：模型使用宿主citeable_sources已提供的能力/任务/standing-understanding来源，但attention只校验snapshot子集。注意记录应使用同一个pinned目录；未提供来源仍拒绝，decision/World claim的单独授权校验不变。
- 真实失败记录先做最小离线反例，再继续付费；费用预留、未知费用保留及失败统计不变。故事连贯性/交织/选择后果/用户介入/普通聊天回忆须分别以实际执行证据判断，不用草案合法或单测通过冒充质量通过。

方案依据：JSON Schema可在生成格式中用条件/组合表达结构依赖；Pydantic生成schema的自定义after验证器不会自动表达所有跨字段规则。[JSON Schema条件校验](https://json-schema.org/understanding-json-schema/reference/conditionals)、[Pydantic discriminated unions](https://pydantic.dev/docs/validation/latest/concepts/unions/)。本项目仍自行验证效果，不把格式机制当作模型必然一次正确的保证。
