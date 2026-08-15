# NovelSim：服务器权威的多 Agent 叙事游戏

NovelSim 把自然语言变成**候选游戏行动**，再由确定性规则、受控工具和事件存储决定
什么真正发生；Unity 只表现已经提交的世界事实。它解决的不是“让 LLM 多写几句
对白”，而是让 3–5 个有限认知 NPC 能在真实游戏运行时自主行动，同时不把幻觉、
越权意图或时代错误写进世界。

## 招聘方 30 秒速览

| 必答问题 | 当前实现与证据 |
|---|---|
| 解决什么 Game AI 问题？ | 自然语言行动、NPC 自主决策、多角色信息传播与玩家干预，最终落到可回放的游戏状态，而不只是生成文本。 |
| 为什么不能只用 LLM？ | 提示词没有事务、权限和因果保证。6 个违规探针中，仅提示词的 G0 放过 `6` 个，完整确定性闭环 G3 放过 `0` 个。 |
| 如何保证事实与认知一致？ | SQLite `WorldState + WorldEvent` 是唯一权威源；角色 belief 必须带来源事件和父证据，所有 `StatePatch` 经实体、规则、能力、时空、认知和因果校验后原子提交。 |
| NPC 如何调用工具并在 Unity 执行？ | LLM 只提议 `ToolCall`；状态机负责校验、导航、执行、重试和恢复；Unity 按 `sequence + command_id` 消费已提交事件，驱动 NavMesh、对话、物品和联盟 HUD。 |
| 多 Agent 出现了什么行为？ | 原创“密信疑云”中，守卫观察证据并逐跳传给管家和盟友，满足共同证据和关系阈值后才结盟；玩家可公开、销毁或截走密信，得到不同可回放结局。 |
| 长时协作如何避免卡死或执行旧计划？ | `JointPlan` 为每个 NPC 保存 Action Chain；`WaitAgent/WaitState` 显式表达协作依赖，Wait Graph 检测循环等待，规则式 Staleness Checker 在实体销毁、目标死亡等永久失效时触发依赖闭包内的局部重规划。 |
| 比基线提升了什么？ | 世界门禁违规放过 `6→0`；记忆 Hit@4 `0→0.84`、MRR `0→0.805`。主观写作对同模型 direct-prompt 强基线为诚实的 `3–3`；6 题真人盲标与 Judge 一致 `5/6`、Cohen's κ `0.667`，样本小，不宣称整体领先。 |
| 如何在本地 5 分钟运行？ | `copy .env.example .env` 后双击 `start.cmd`；离线证据链演示运行 `.\.venv\Scripts\python.exe -m examples.secret_letter.demo --route none`。 |

实测基线：

- 9/9 固定场景通过；非法 Patch、未知实体接受、因果越权和认知泄漏均为 `0`；
- 20/20 真实 `qwen3.7-plus` 固定回归完整运行，目标成功 `18/20`；
- 14 个已提交事件的回放、因果证据、叙事覆盖和结构化事件依据均为 `100%`；
- 6/6 个固定种子 10 回合动态扰动场景完成；确定性 Runtime 基准中失效 Recall、死锁恢复、重规划有效性和回放一致率均为 `100%`，非法提交为 `0`（外部 LLM 调用为 `0`，不冒充真实模型结果）；
- 45 次模型调用共 `49,255 Token`，总回合 P50/P95 为
  `10.554s/16.469s`；
- Windows x64 构建、真实 E 交互、事件命令消费和重启存档恢复已通过本地
  smoke；三条密信路线也分别经 Unity → HTTP → SQLite 实包运行并在独立进程
  中恢复相同终态。
- [2 分 18 秒 Unity 单镜实机视频](portfolio/video/NovelSim-core-demo-v1.mp4)
  已完成：同一 session 从 v0 提交到 v1，非法“开飞机”输入被拒且仍为 v1，
  最后展示真实 LLM 评测结果。

架构图、状态机和信息传播因果图见
[`docs/作品集架构与因果图.md`](docs/作品集架构与因果图.md)；完整指标口径见
[`docs/结构化场景评测.md`](docs/结构化场景评测.md)。Pairwise 已使用用户提供的
6 题真人盲标离线校准，一致率 `83.33%`、Cohen's κ `0.667`，且
`llm_calls_added=0`；主观标签不覆盖客观世界规则门禁。

## 当前状态

项目已经完成可试玩、可创作、可编译、可审核的 Beta 主链路：

```text
TXT 长篇小说
  → 独立编译 Worker
  → 实体消歧 / 多时间线 / 分层快照
  → WorldPackage
  → 创作者审核与发布
  → 玩家自然语言回合
  → 联合计划 / 显式等待 / 失效与死锁检测
  → 规则校验 / NPC Agent / 长期记忆 / 事件持久化
  → Unity 3D 服务器权威竖切片
```

- 权威数据：SQLite；
- 向量检索：Qdrant Local Mode，可降级到 SQLite FTS5；
- 后端：FastAPI + Pydantic；
- 前端：Vue 3 + Vite；
- 3D 客户端：Unity 6.3 LTS + URP，关节化低多边形角色、程序动画、运行时 NavMesh、真实 E 交互、存档恢复和 Windows x64 构建已验证；
- 编译任务：SQLite 租约队列 + 独立 Worker；
- 治理：账户、RBAC、修订历史、审核审计和发布权限；
- 质量：444 个本地确定性测试、9 案例结构化场景评测、20 局真实 LLM
  回归、6 组双基线 BOOKWORLD 风格盲测、G0–G3 世界门禁消融、版本化长轨迹
  回归和 Playwright E2E；
- API：核心契约冻结为 `1.0.0`。

截至 2026-07-28，首轮两本小说 quick 真实 LLM 编译演练已经完成，共验证40章、
49个场景、断网暂停恢复、缓存重放、质量门禁和自动审核。第一本评分0.896且进入
`pending_review`；第二本评分0.826，但因2个高严重度问题被硬门禁退回`draft`。
2026-07-29复用25个场景缓存完成目标生命周期修复，重评分0.880、阻断项0，
活跃现代目标0；修复世界包revision 2已进入`pending_review`。`stress`和正式
全书档尚未启动。

2026-08-14新增首个原著长程片段闭环：以《第一狂妃：废柴三小姐》第1章检查点
为输入，使用真实`qwen3.7-plus`推演第2--5章；16次调用、67,391 Token、0脚本
回退，动态依赖变化触发2次局部重规划，最终完成夜家大堂终态。隐藏原著关键事件
匹配8/10，加权召回78.45%，匹配顺序与权威回放均为100%。详见
[`docs/原著长程真实LLM推演.md`](docs/原著长程真实LLM推演.md)。

2026-08-14真实性评测升级为 v2：将纯原著复现 `clean` 与动态纠错
`perturbed` 分开统计；姬月的三次关键空间转移改为 LLM 可请求、世界引擎固定校验和
提交的受限角色能力，并新增 LLM 角色行动/环境事件的原著召回贡献。最终真实 LLM
`clean` 达到关键事件 `10/10`、加权召回与独立顺序均 `100%`；`perturbed` 完成
`1` 次依赖变化与 `1` 次真实重规划，关键事件仍为 `10/10`，顺序 `88.9%`。两种
协议的环境召回贡献均为 `0%`，回放一致率均为 `100%`。

## 快速体验

1. 复制 `.env.example` 为 `.env`，填写 LLM 配置。
2. Windows 直接双击 `start.cmd`。
3. 浏览器打开 `http://127.0.0.1:8000`。

也可以使用命令：

```powershell
.\novelsim.ps1 start --open-browser
.\novelsim.ps1 status
.\novelsim.ps1 stop
```

启动器会自动构建缺失的前端产物，并在后台分别启动 Web 和编译 Worker。PID 与日志
保存在 `data/runtime/`。详细说明见
[`docs/Beta一键启动与CI.md`](docs/Beta一键启动与CI.md)。

### Unity 3D 竖切片

安装 Unity `6000.3.15f1` 后，在 Unity Hub 中打开
`unity/NovelSim3D`。首次导入会生成 `VerticalSlice` 场景；保持 `start.cmd`
运行，进入 Play Mode 后即可用 WASD 接近夜清清并按 E，把交互送入真实世界引擎。
场景包含程序化湿石路、古宅、牌楼、雨雾、动态灯笼、关节化风格人物和剧情 HUD；
夜清清会在运行时 NavMesh 上巡行并在玩家接近时注视玩家。按住 Shift 疾跑，
鼠标右键环视，滚轮调整镜头，F1 打开世界调试面板。
锁定版本的 C# 编译、URP 绑定、EditMode `6/6` 和无图形 PlayMode `7/7`
已经通过。启动后会自动恢复上次服务端世界线；没有有效存档时才创建新世界线。

生成并验收 Windows 包：

```powershell
Set-Location unity\NovelSim3D
.\build-windows.ps1
.\capture-windows-preview.ps1
.\run-windows-smoke.ps1
.\record-showcase.ps1 -DurationSeconds 130
```

`run-windows-smoke.ps1` 会通过与 E 键相同的代码路径执行真实回合，并让三条
密信路线分别经过 Unity → HTTP → SQLite；每次再启动独立进程验证相同
`session_id`、世界版本和表现游标被恢复。
`record-showcase.ps1` 使用固定 SHA-256 的 FFmpeg 二进制，只录 Unity 窗口区域，
并同时校验真实 HTTP 回合、非法动作拒绝、存档恢复、结构化报告和 MP4 整段解码。
已有 Windows 构建时，也可以回到仓库根目录双击 `start-unity-demo.cmd`，
一次启动后端与 Unity 客户端。
详见 [`docs/Unity3D竖切片.md`](docs/Unity3D竖切片.md)。

## 验证

```powershell
# Python 确定性回归
.venv\Scripts\python.exe -m pytest

# 版本化长轨迹回归
.venv\Scripts\python.exe -m engine.trajectory_regression

# 6 类固定种子多 Agent 动态扰动（10 回合）
.venv\Scripts\python.exe -m evaluation.long_horizon

# 9 案例确定性评测
.venv\Scripts\python.exe -m evaluation

# 20 局真实模型评测（支持 --resume）
.venv\Scripts\python.exe -m evaluation.real_runner `
  --resume --output evaluation\reports\real-llm-v1.json `
  --markdown evaluation\reports\real-llm-v1.md

# 两本真实小说源文件指纹
.venv\Scripts\python.exe -m compiler.benchmark scan

# Unity 工程与 API v1 静态契约
.venv\Scripts\python.exe -m pytest -q tests/unit/test_unity_contract.py

# Unity C#、交互/恢复回归与 Windows 构建
unity\NovelSim3D\run-tests.ps1
unity\NovelSim3D\build-windows.ps1

# Vue 构建与正式浏览器 E2E
Set-Location web\frontend
npm run build
npm run test:e2e
```

Pull Request 和 `main` 推送会由 `.github/workflows/ci.yml` 自动执行无真实 LLM
费用的确定性门禁。

## 文档导航

| 文档 | 内容 |
|---|---|
| [`docs/实现进度.md`](docs/实现进度.md) | 当前完成度、测试结果、技术栈和下一步 |
| [`docs/结构化场景评测.md`](docs/结构化场景评测.md) | 9 案例客观指标、20 局真实回归、Pairwise、G0–G3 与无记忆消融 |
| [`docs/作品集架构与因果图.md`](docs/作品集架构与因果图.md) | 项目架构、Agent 状态机、信息传播/联盟与非法飞机输入因果图 |
| [`docs/求职版演示脚本.md`](docs/求职版演示脚本.md) | 2–3 分钟录屏分镜、验收清单和 10–15 分钟面试演示 |
| [`docs/简历项目描述.md`](docs/简历项目描述.md) | 仅使用实测数字的简历版本与不可声称边界 |
| [`docs/求职版交付审计.md`](docs/求职版交付审计.md) | 必须交付项、真人校准结果和最终实包验收证据 |
| [`portfolio/README.md`](portfolio/README.md) | 原创公开密信世界包、SHA-256 与现场演示命令 |
| [`docs/plan.md`](docs/plan.md) | 项目最终目标和原始总体蓝图 |
| [`docs/GameAI_LLM_Agent求职版计划.md`](docs/GameAI_LLM_Agent求职版计划.md) | 当前求职版目标、里程碑、验收门槛和非目标 |
| [`docs/Beta一键启动与CI.md`](docs/Beta一键启动与CI.md) | 一键启停、日志、健康检查和 CI |
| [`docs/真实全书编译生产演练.md`](docs/真实全书编译生产演练.md) | quick/stress/full 真实编译演练 |
| [`docs/生产化基线_Worker_RBAC_E2E.md`](docs/生产化基线_Worker_RBAC_E2E.md) | Worker、账户权限、审计、E2E 和多小说基准 |
| [`docs/核心API契约v1.md`](docs/核心API契约v1.md) | 已冻结的核心 API v1 契约 |
| [`docs/Unity3D竖切片.md`](docs/Unity3D竖切片.md) | Unity 工程、第三人称交互、API 闭环与验收边界 |
| [`docs/BookWorld前端迁移.md`](docs/BookWorld前端迁移.md) | BookWorld 风格三栏 Web Demo、复用边界和非法行动验收 |
| [`docs/一键演示模式.md`](docs/一键演示模式.md) | 无需 API Key 的规则、合法提交与多 Agent 三条演示链路 |
| [`docs/Unity角色建模参考.md`](docs/Unity角色建模参考.md) | 公开建模调研、原创三视图、女主与守卫规格 |
| [`docs/编译任务与全书编译D.md`](docs/编译任务与全书编译D.md) | SQLite 编译任务、断点续跑和全书编译 |
| [`docs/反思与语义记忆.md`](docs/反思与语义记忆.md) | 反思记忆、证据链和语义一致性 |
| [`docs/Qdrant向量检索.md`](docs/Qdrant向量检索.md) | Qdrant Local/Server 架构与运维 |

## 当前边界

- Unity 3D 已完成关节化程序角色、基础走路/待机/交互动画、运行时 NavMesh、
  动态雨夜场景、真实服务端回合、存档恢复和 Windows x64 交付闭环；外部正式
  FBX、Mecanim 动画片段、战斗和多模态尚未接入。
- SQLite 是当前正式开发基线；PostgreSQL 后端保留，但不要求 Docker 或数据库服务。
- Qdrant Local Mode 适合本机单进程；多 Worker 或多机部署需要 Qdrant Server/Cloud。
- 玩家存档仍为本地世界线，团队多租户和云同步尚未实现。
- 真实 LLM 编译耗时和费用受模型服务影响，`full` 档必须先估算并显式确认。
