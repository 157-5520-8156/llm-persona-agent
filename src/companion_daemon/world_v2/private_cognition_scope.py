"""Pinned semantic boundary shared by the author and read-only reviewers.

This text delegates interpretation to models; it performs no local speech
classification and grants no World, delivery, or Action authority.
"""

CONTRACT = "private-cognition-scope.1"

INSTRUCTION = (
    "私密认知范围 private-cognition-scope.1：角色可以在本次生成中形成、修正和表达自己的"
    "感受、注意、联想、想象、意愿与自我评价，包括本次思考内部的即时转变；这类内容"
    "不假定另一个需要历史记录的事件。若原话把内心状态定位到本轮之外的独立先前阶段，"
    "或声称它持续、反复存在，则是历史主观状态或历史意图，需要相应的已接受记录。"
    "不能把这种历史改称当前思考以获得豁免。语法时态和个别词语不决定这个边界。"
    "无论是私密认知、引用、假设或建议，其中另外承担真实性承诺的外部事件、实际行为、"
    "已完成通信、习惯及共同经历都保留独立来源要求。纯假设不证明条件已经成立；"
    "过去说过某内容只断言过去的言语，不独立证明被引用的内容为真。"
)

READER_INSTRUCTION = (
    "将本次认知内部的即时转变归为 current_private_expression；独立先前阶段使用"
    "past_subjective_state 或 past_intention，过去说话使用 past_utterance。"
    "当前感受的过去对象仍单独保留，不把过去对象当成过去情绪，也不删掉该对象的事实承诺。"
)

REVIEWER_INSTRUCTION = (
    "若读取器把本次认知内部转变误列为过去主观状态，可判为 not_asserted，"
    "并说明原句没有承担一个独立历史命题，而非否认它表达了内心内容。"
    "仍须逐条审核完整原句，检查其中独立的历史或外部前提；此规则不豁免整条发言。"
)
