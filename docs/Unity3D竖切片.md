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
- 渲染：URP `17.3.0` 依赖已锁定；渲染管线资产待 Unity 首次导入后创建和验证；
- 输入：Input System，保留 Legacy Input Manager 编译降级；
- 网络：`UnityWebRequest` + JSON；
- API：冻结的 NovelSim `1.0.0` 契约；
- 首个平台：Windows PC。

工程位于 [`unity/NovelSim3D`](../unity/NovelSim3D)。代码拆分为：

| 模块 | 职责 |
|---|---|
| `ApiContractV1` | 固定 Unity 消费的 API v1 路径和版本 |
| `NovelSimApiClient` | 请求、错误包装和契约主版本检查 |
| `WorldSessionManager` | 会话、回合和服务端权威状态镜像 |
| `ThirdPersonMotor` | WASD 移动与第三人称相机 |
| `PlayerInteractor` | 发现附近 NPC 并把交互转成服务端行动 |
| `NovelSimHud` | 服务地址、行动输入、状态和剧情回传 |
| `VerticalSliceBootstrap` | 无美术资产时生成可玩的验证场景 |

## 当前完成

- 已建立可由 Unity 6.3 LTS 打开的工程与依赖清单；
- 已实现 `/api/start`、`/api/session`、`/api/turn` 客户端；
- 已检查响应头 `X-NovelSim-Contract`，拒绝不兼容主版本；
- 已实现第三人称移动、NPC 接近交互和剧情 HUD；
- 已提供首次导入自动创建场景和 Build Settings 的 Editor 工具；
- 已提供 2 个 Unity EditMode 测试和 2 个 Python 静态契约门禁。

## 验证边界

当前工作机没有 Unity Editor 和 .NET SDK，所以已完成 Python 契约与后端回归，
但 C# 编译、Unity Test Runner 和 Play Mode 尚未在本机执行。安装
Unity `6000.3.15f1` 后，首要验收是：

1. Package Manager 无解析错误；
2. 创建并绑定 URP Render Pipeline Asset，确认基础材质正常显示；
3. `NovelSim.EditModeTests` 2/2 通过；
4. 空场景进入 Play Mode 后自动出现玩家、地面和守卫；
5. `start.cmd` 启动的后端可创建世界线；
6. 玩家靠近守卫按 E 后，SQLite 世界版本增加，HUD 展示服务端剧情；
7. 后端停止时只显示错误，不产生本地伪状态。

## 后续

完成上述 Play Mode 验收后，再推进角色模型/动画、NavMesh、Addressables、
场景资源映射和 Timeline 演出。第二本小说的目标生命周期修复可以与美术资产
生产并行，但不会改变客户端“服务端权威”的边界。
