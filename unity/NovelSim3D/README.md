# NovelSim 3D

首个 Unity 竖切片客户端，固定使用 Unity `6000.3.15f1`（Unity 6.3 LTS）。

当前闭环：

```text
第三人称移动
  → 靠近华容巷守卫并按 E
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

WASD 控制移动，按住鼠标右键环视，滚轮调整镜头，靠近守卫后按 E 提交
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
- Unity `6000.3.15f1` 首次导入、C# 编译、EditMode、PlayMode 和 DX12
  可视化画面联调已经通过；仍需完成靠近守卫按 E 的真实 LLM 回合验收。
