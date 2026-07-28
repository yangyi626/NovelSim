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
3. 首次导入后创建并绑定 URP Render Pipeline Asset，确认基础材质正常显示。
4. 首次导入后会自动生成 `Assets/NovelSim/Scenes/VerticalSlice.unity`；
   也可以手动执行菜单 `NovelSim > Setup Vertical Slice`。
5. 在仓库根目录运行 `start.cmd`，确认 FastAPI 地址为
   `http://127.0.0.1:8000`。
6. 在 Unity 打开 `VerticalSlice`，进入 Play Mode。

HUD 中可以修改 FastAPI 地址。WASD 控制移动，靠近守卫后按 E 提交结构化
自然语言行动。服务不可用时客户端保留场景并显示可重试错误，不会伪造本地世界
结果。

## 验证

- Unity Test Runner：运行 `NovelSim.EditModeTests`；
- 仓库契约门禁：
  `.venv\Scripts\python.exe -m pytest -q tests/unit/test_unity_contract.py`；
- 当前工作机未安装 Unity Editor 或 .NET SDK，因此 C# 编译和 Play Mode 验收需
  在安装 Unity 6.3 LTS 后完成。
