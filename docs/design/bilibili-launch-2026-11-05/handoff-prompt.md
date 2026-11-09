# 交接提示词（复制以下全部内容到新会话）

你将接手 Girl-Agent 项目的 B 站产品发布会视频制作。这是一个空会话，以下是你需要的全部上下文。

## 1. 先读这些文件（按顺序，读完再动手）
1. `AGENTS.md`：项目宗旨。角色自主，系统只守事实与权限边界。视频里所有表述都不能违背它。
2. `docs/design/bilibili-launch-2026-11-05/script-v4.md`：**完整制作剧本（唯一权威）**。包括美术圣经、9 段分镜与旁白、小票机视觉语言、Dashboard 实录画中画规范（2.9 节）、技术约束与踩坑、给动效 AI 的提示词（第 4 节）、语音素材清单（第 5 节）。
3. `output/bilibili-launch-v4/src/scenes/S2Explode.tsx`：已完成的标杆段落（剧本第 2 段），后续每段都照它的骨架写（Rig + World + Post + DOM 叠层 + toWorld 冻结 + DashboardPip）。
4. `output/bilibili-launch-v4/src/three/parts.tsx`、`src/three/props.tsx`、`src/lib/Pip.tsx`、`src/lib/util.ts`、`src/theme.ts`：可复用部件与设计令牌。
5. 证据边界参考：`docs/design/bilibili-launch-2026-11-03/01-project-evidence.md`、`04-selected-cases-and-opening.md`。

## 2. 当前状态
- 工程：`output/bilibili-launch-v4/`，Remotion 4 + React Three Fiber 9 + drei 10 + @react-three/postprocessing 3，依赖已装好。
  - 预览：`npm run studio`
  - 渲染样片：`npm run render:sample`（输出 `out/style-sample-15s.mp4`；最新确认版是 `out/style-sample-v3-receipt.mp4`，约 17 秒）
- **已完成**：第 2 段“一句话的爆炸图”样片，含小票机、两张小票、来源线、线框假话撞玻璃闸门碎裂、Dashboard 画中画占位、宋体标题“她可以嘴硬。但不能撒谎。”，用户已认可方向。
- **未完成**：第 0、1、3、4、5、6、7、8 段；旁白与配乐；全片 `Full` Composition（用 `<Series>` 串起各段并挂音轨）；Dashboard 实录素材（用户尚未录屏，画中画目前是占位）。

## 3. 用户偏好（务必遵守）
- 要“回形针式”的发布会：画面控制全场，元素进场、讲完、退场，灵动、详略得当；冷幽默，旁白一本正经，画面负责好笑。
- 讨厌：PPT 感、毛坯房式的空画面、专有名词堆砌、v3 那种 Dashboard 截图堆满屏。
- 3D 一律**用代码做**（Remotion + R3F），不用 AI 生成视频。
- 用户中文沟通，回复要简洁，要给建设性意见而不是一味附和。
- 部分对话是用户基于真实运行记录“美化”的情景还原：片尾保留合规小字；只有真实记录才在小票上印序号；画中画只放真实系统画面。

## 4. 技术硬约束（违背就会出 bug）
- 动画只由 `useCurrentFrame()` 驱动；随机只用 `seeded()`；3D 中文只用 CanvasTexture（`useTextTexture`）；2D 字幕和标题用 DOM 叠层。
- `RoundedBox` 的 `radius` 必须**小于最薄边的一半**，否则产生退化法线，配合 Bloom 会让整帧变黑（已踩坑）。
- 物件出场/退场用 `visible` 或 `scale` 控制，**不要条件卸载网格**。
- `ThreeCanvas` 需 `gl={{preserveDrawingBuffer: true}}`；`Post` 组件必须照抄 S2Explode 的首帧预热 + 逐帧补渲。
- 带贴图的不透明材质，Canvas 先铺底色，不能 `clearRect` 留透明（否则渲染成黑色）。
- 每次渲染后抽帧查黑帧：
  `npx remotion ffmpeg -loglevel error -i out/X.mp4 -s 96x54 out/vf/f%03d.png`，再用 PIL 统计亮度均值，< 150 即异常。
- 构图自查：先用 `npx remotion still ... --frame=N` 渲 6 张关键帧拼图检查（主角清晰、三层景深、无穿模、不挡字幕），再渲视频。

## 5. 建议的下一步
1. 按剧本第 4 节 4.1 的提示词做**第 1 段“视锥剔除”**（`src/scenes/S1Frustum.tsx`），这是全片第一次“满色”的大场面，优先级最高。
2. 再按 3 → 5 → 4 → 6 → 0/8 → 7 的顺序逐段制作，每段在 `Root.tsx` 注册独立 Composition，做完先给用户看静帧拼图和短片。
3. 全部段落完成后，建 `Full` Composition 串联，接入旁白（文件编号见剧本第 5 节）。
4. 等用户提供：Dashboard 录屏（放 `public/pip/<ID>.mp4`）、她看 AI 伴侣视频的真实反应录屏（段 0/8）、旁白声线选择、开源与成本定案。

开始前先读完上述文件，然后用一两句话复述你理解的全片主线和下一步计划，等用户确认后再动手。
