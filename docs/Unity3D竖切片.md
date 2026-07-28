# Unity 3D 竖切片

## 目标

本阶段只验证一条可玩的服务器权威闭环，不提前进入大规模建模、战斗和动画生产：

```text
进入华容巷
  → 第三人称移动
  → 接近 NPC
  → 提交自然语言行动
  → FastAPI 调用世界引擎
  → SQLite 原子保存 WorldState / WorldEvent
  → Unity 呈现 Narrative 和最新状态版本
```

Unity 只保存显示缓存。世界事实仍由 `WorldState + WorldEvent` 表达并由 SQLite
持久化；客户端不能直接修改角色、关系、物品或剧情状态。

## 技术基线

- Unity：`6000.3.15f1`，属于 Unity 6.3 LTS；
- 渲染：URP `17.3.0` 依赖、Universal Renderer 和 Pipeline Asset；
- 输入：Input System，保留 Legacy Input Manager 编译降级；
- 网络：`UnityWebRequest` + JSON；
- API：冻结的 NovelSim `1.0.0` 契约；
- 首个平台：Windows PC。

工程位于 [`unity/NovelSim3D`](../unity/NovelSim3D)。代码拆分为：

| 模块 | 职责 |
|---|---|
| `ApiContractV1` | 固定 Unity 消费的 API v1 路径和版本 |
| `NovelSimApiClient` | 请求、错误包装和契约主版本检查 |
| `WorldSessionManager` | 会话、回合、PlayerPrefs 存档指针和服务端权威状态镜像 |
| `ThirdPersonMotor` | WASD 移动与第三人称相机 |
| `PlayerInteractor` | 发现附近 NPC，并让 E 键与自动验收共用同一提交路径 |
| `NovelSimHud` | 服务地址、行动输入、状态和剧情回传 |
| `HuarongLaneVisualDirector` | 程序化生成雨夜古巷、人物、灯光和天气 |
| `VerticalSliceBootstrap` | 装配可玩的服务器权威验证场景 |
| `StandaloneInteractionSmokeRunner` | Windows 包的真实 HTTP 交互/恢复验收入口 |
| `NovelSimWindowsBuild` | 固定场景和 Windows x64 的正式构建管线 |

## 当前完成

- 已建立可由 Unity 6.3 LTS 打开的工程与依赖清单；
- 已实现 `/api/start`、`/api/session`、`/api/turn` 客户端；
- 已检查响应头 `X-NovelSim-Contract`，拒绝不兼容主版本；
- 已实现第三人称移动、NPC 接近交互和剧情 HUD；
- 已实现湿石路、古宅、牌楼、灯笼、雨雾、积水、竹影和低多边形人物；
- 已实现右键环视、滚轮变焦，以及适配剧情游戏的半透明 HUD；
- 已提供首次导入自动创建场景和 Build Settings 的 Editor 工具；
- `NovelSim > Play Vertical Slice` 会自动进入 Play Mode 并聚焦 Game 视图；
- 启动时优先恢复 `NovelSim.LastSessionId`；存档不存在时才创建新世界线；
- 已提供 Windows x64 一键构建与“两次进程启动、同一存档版本”的恢复验收；
- 已提供 3 个 Unity EditMode 测试、3 个 PlayMode 测试和 2 个 Python
  静态契约门禁。

## 构建与验证

当前工作机已经使用 Unity `6000.3.15f1` 完成 C# 编译、EditMode `3/3`、
无图形 PlayMode `3/3`、DX12 可视化 Play Mode 和 Windows x64 构建。

```powershell
Set-Location unity\NovelSim3D
.\run-tests.ps1
.\build-windows.ps1
```

真实 Windows 验收必须先启动 FastAPI，然后运行：

```powershell
.\run-windows-smoke.ps1
```

脚本第一次启动独立包时清空本地指针、创建世界线并通过与玩家 E 键完全相同的
`PlayerInteractor.TryInteract()` 路径提交真实 `/api/turn`；第二次启动只恢复
存档。只有两次运行的 `session_id` 与世界版本完全一致才通过。

后端停止、网络超时或 API 契约不兼容时，客户端只显示错误并保留最后一次服务端
镜像，不生成本地伪状态。构建产物位于
`unity/NovelSim3D/Builds/Windows/NovelSim3D.exe`，该目录不纳入 Git。

## 后续

可玩闭环完成后，下一阶段进入正式角色模型/动画、NavMesh、Addressables、
场景资源映射和 Timeline 演出。当前程序化场景继续作为低成本回归基准。
