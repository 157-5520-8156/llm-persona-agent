# 原生检查工具的说明长度边界

目标继续，未部署。上一轮能力说明对照属于证据progress；本轮转回协议载荷、费用与一次成功率。

## 重复载荷的离线检查

将六份真实来源检查的工具review参数与USER的完整输出schema比较。USER约5600字符；原生约5149字符。原生strict子集缺少不少minLength/maxLength/minItems/maxItems等约束，不能删掉USER副本说成完全重复。用JSON Patch保留完整差异约5830字符，反而更大；没有采用。

World Author原生schema已经使用本地$def复用，重新展开再提取也是17925字符，没有新增收益。一般来源检查1088字符，无可提取重复节点；新事实来源检查5242→4449字符，完全展开与原参数逐项相等。后者单独省约793字符，相对约4万输入tokens很小，不能冒称目标费用已经实现。

## 实测暴露并修正的缺口

只在私有代码将原生工具参数用现有local_schema_references模块提取重复结构，保留整个原请求正文、来源与温度。六次真实V4.1检查，三份supported、两份unsupported与原对照一致；另一份因reason超过本地2000字符上限被拒绝。所有原结果保留。

代码中一般来源检查已用pattern映射原始reason长度上限；新事实来源检查没有携带等价边界，DeepSeek strict投影会去掉maxLength，于是生成后才由本地解析器报错。这是可定位的协议遗漏，不需要改变世界事实或角色决定。

现在共享_review_tool_contract从原canonical review_model取maxLength，向原生reason加入等价`^[\s\S]{1,N}$`。新事实检查description也明确：在原长度内说明，详细发现放在原坐标数组，不重复整份来源/候选。没有改canonical长度、删发现、截断生成内容、减少检查或模板代答。一般来源检查原来相同的pattern仍相同。

测试覆盖2000字符允许、2001字符拒绝；原公共HTTP、完整解析、来源证据、旧版/冷重放组合135项通过，Ruff和diff检查通过。原生重复结构压缩没有加入生产实现，仅保留本次长度边界修复。

对原先失败的同一份输入，加等价边界并沿用已被服务接受的私有压缩原生参数，真实一次生成1432字符说明，完整原解析器通过并判unsupported。该实验不是无压缩整个生产链条的成功率资格，也不能从单份样例声称其他内容或字段永不超限。

六次候选费用0.24490260元、一次纠正边界费用0.04087076元，合计0.28577336元。均独立debug key/usage库，不发送QQ，包含失败调用。没有以已暖旧请求与首次新schema的账单推导节省。

## 后续验收

保留可复现的原生边界修复；费用验收仍需完整日负载与同缓存条件，普通首回复仍未稳定<5秒。重复USER schema不是全可删除，World工具已压缩，停止在小schema上盲目加codec。下一步从生活生成次数/必要检查/纠正调用的实际目的和重复材料量定位大头，保留现有来源/隐私闭包和角色权限。

私有证据output/private-audits/review-schema-transport-20261004/：results.json的离线diff、probe-results.json六份真实结果、reason-bound/probe-results.json和原始request/response/usage.sqlite。API$def本地复用沿用项目已经服务接受的实现，首次官方旧链接重定向/超时没有作为新增兼容性证明。主模型仍为已配置V4.1，未测E5/Laya/其他模型。
