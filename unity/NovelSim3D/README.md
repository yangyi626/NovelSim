# NovelSim 3D

首个 Unity 竖切片客户端，固定使用 Unity `6000.3.15f1`（Unity 6.3 LTS）。

服务端工具表现通过 `/api/presentation-events` 增量拉取，并以
`sequence + command_id` 幂等消费；重连时先读取 `/api/presentation-snapshot`
恢复权威角色、物品和联盟状态，再从本地确认游标继续。`ToolEventDispatcher`
负责导航、对白/知识提示、物品状态和联盟 HUD，不能直接修改服务端世界事实。

当前闭环：

```text
第三人称移动
  → 靠近夜清清并按 E
  → POST /api/turn
  → FastAPI / 世界引擎 / SQLite
  → 返回权威 WorldState 与 Narrative
  → Unity 更新只读状态镜像和剧情 HUD
```

## 打开

1. 安装 Unity Hub 和 Unity `6000.3.15f1`，至少选择 Windows Build Support。
2. 在 Unity Hub 中打开本目录 `unity/NovelSim3D`。
3. 首次导入会自动创建并绑定 URP Render Pipeline Asset。
4. 首次导入后会自动生成 `Assets/NovelSim/Scenes/VerticalSlice.unity`；
   也可以手动执行菜单 `NovelSim > Setup Vertical Slice`。
5. 在仓库根目录运行 `start.cmd`，确认 FastAPI 地址为
   `http://127.0.0.1:8000`。
6. 在 Unity 打开 `VerticalSlice`，进入 Play Mode。

WASD 控制移动，按住鼠标右键环视，滚轮调整镜头，靠近夜清清后按 E 提交
结构化自然语言行动；F1 打开世界调试面板并可修改 FastAPI 地址。服务不可用时
客户端保留场景并显示可重试错误，不会伪造本地世界结果。

也可以使用菜单 `NovelSim > Play Vertical Slice` 直接打开场景并进入 Play
Mode。

## 验证

- Unity Test Runner：运行 `NovelSim.EditModeTests` 和
  `NovelSim.PlayModeTests`；
- PowerShell 一键回归：`.\run-tests.ps1`；
- 仓库契约门禁：
  `.venv\Scripts\python.exe -m pytest -q tests/unit/test_unity_contract.py`；
- Unity `6000.3.15f1` 首次导入、C# 编译、EditMode `6/6`、PlayMode
  `7/7`、DX12 可视化画面联调和 Windows x64 构建均已通过；
- 真实 Windows E 交互已推进权威世界、消费表现事件，并通过独立进程存档恢复；
- `run-windows-smoke.ps1` 还会让销毁、截走、公开真相三条密信路线分别经过
  Unity → HTTP → SQLite，并逐条验证独立进程恢复的 session、version 和游标。
