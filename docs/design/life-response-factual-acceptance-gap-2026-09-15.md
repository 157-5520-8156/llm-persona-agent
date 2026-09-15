# 生活反应写入前缺少正文事实核验

状态：已从真实输入、原始响应和提交事件定位，并在完整本地链条复现；尚未修复。
这是一项发布阻断，不是通过增加日志或来源标签就已解决的问题。

## 已核实的路径

真实调用 `model-input:215c67697a2e49fb8b6946dab8cb8678` 使用
`character_role_world_stimulus_appraisal_v1`。其实际输入同时提供：

- `recently_ended_activities` 中的原始散步意图，以及
  `activity_lifecycle_ended_not_intention_fulfilled` 边界。
- `recent_self_experiences` 与结构化 `week_diary` 中的已结算结果：
  “The evening walk along the campus paths is under way; the route out from the
  library side is open and the outdoor air is cool and still.”

原始响应却在 summary、brief_rationale、meaning_candidates、线程 reason_summary
和 life_responses 中重复写成走完一圈；life_responses 还写了上午坐在图书馆东侧。
本次确认前者没有得到上述意图/结果的支持；后者的全部更早来源尚未完整核验，不能宣称
已排除所有历史依据。当前保存的整份原始请求可继续用于检查，而不是只留几个节选。

账本顺序为：2368 `CharacterLifeResponseRecorded` → 2371 `ExperienceCommitted`
→ 2378 `AppraisalAccepted`。这证明未经正文事实核验的角色反应进入了生活与主观历史，
并不证明其中描述的动作发生了。后续作者能够读到这些材料；它们可能构成编造持续存在的
路径，但不能仅凭先后顺序断言它们导致每一次聊天编造。

## 实现为什么没有阻止它

`structured_role.py` 的 `_validate_proposals` 调用
`validate_character_life_response_coverage`，只检查 offered source 的完整覆盖。
`character_life_response_runtime.py` 校验角色身份、提案、来源事件、哈希、版本及 effect-once。
`character_life_experience_runtime.py` 再证明已接受响应的来源，组合成 Experience。
这些证明均不包含 response_text 的事实语义核验；代码注释也明确承认这一范围。

`test_life_response_factual_acceptance_gap.py` 通过公开 SQLite 运行链复现两种输入：
只给出雨停的环境结果，角色却描述已出门绕湖买咖啡，或引用上午图书馆落座的经历。
目前两者都被保存为响应和 Experience。测试将这一具体异常标成 strict xfail，其他设置、
传输或运行错误不能被该标记吞掉。现有 null 和自由感受的正例继续运行。
**14 passed / 2 xfailed 表示这个事实性阻断仍存在，不能算全部验收通过。**

## 已补齐：同角色纠正的完整原稿

`74778d8e`新增`rejected_role_result.py`，保存原始provider结果全文，绑定完整角色请求、
实际请求/响应hash、原调用ID与作者身份。消费时重新验证，跨回合、主体、召回状态、来源
情境或作者的替换在调用前被拒绝。全文只放入纠正数据，不进入来源目录或World Context。
默认请求没有新字段；纠正仍由Core执行一次。原稿上限128 KiB（UTF-8字节），超限保留
`role_rejected_candidate_unavailable`技术失败，不截断或代填null。

原实现先检查derived status，导致遗漏life_responses时只报笼统schema错误。现在先检查
offered来源覆盖，向同一角色反馈确切缺失字段/来源，随后照常检查status。没有放松接受条件。
完整本地链证明纠正成功与再次失败各自沿原路径处理，未增加第三次作者调用。

角色/Life组176项通过、2项事实性缺口仍为strict xfail；另68项纠正/持久化检查通过，组间
有重叠。新增17项覆盖原文/身份篡改、请求JSON冷读取、完整链和超限技术失败。
本次没有付费调用，尚无真实纠正效果资格证据。详见
[本次验证](../audits/life-role-correction-validation-2026-09-15.json)。
**这是后续语义拒绝所需的纠正接口，不是Life事实核验已实现。**

## 下一步实现边界

1. 在同一角色候选进入可消费的生活反应、Appraisal、Thread、Experience 前执行事实核验。
   需覆盖候选中会进入后续上下文的全部自由正文，不能只拦 response_text 后让同一经历从
   summary 或 meaning_candidates 再进入系统；也不能靠作者声明为空证明全文没有事实。
2. 核验读取同一个候选产生前的 pinned World Context，保留当前结果、原始意图、可读记忆、
   对话与主观历史各自的权限。候选自身、同批尚未接受的 Appraisal 和拟写入的 Experience
   不能成为自己的依据。只给结算正文会误拒有独立依据的往事，因此不是完整设计。
3. 角色继续决定感受、态度、解释、未来打算或 null；核验者只能指出嵌入事实缺少哪种依据，
   不能要求她产生特定感受、联系用户或删除全部生活反应。当前感受与对历史事件的断言须分开。
4. 来源读取、固定命题核验、候选接受各自保持模块职责。可复用已经可重放的来源能力与核验
   组件，但不要把 Life 伪装成聊天 Beat 或直接依赖聊天运行时的私有方法。也不应不经测量
   就复制整套昂贵聊天调用链到每次后台反应。
5. 明确语义拒绝反馈给同一角色一次受约束重选；仍失败或核验超时记技术失败，保留原事件与
   有界重试。不得将失败改成 null、no_change 或已完成的响应。旧记录仍能按原协议重放，
   新的已核验状态必须绑定候选字节、原始来源、协议版本和实际核验结果，防止重启绕过。

验收需同时包含有依据的既往动作、自由感受、选择 null、无依据完成经历、假设与未来意图、
来源更正、同批自证、纠正后换一项编造、重启/CAS，以及实际 Life→记忆→自然聊天的连续链。
真实模型误读和误拒仍须独立验收；本地契约正确不能替代它们。现有部署默认值未改，没有
通过删掉生活机制或提高调用时限来掩盖这个缺口。
