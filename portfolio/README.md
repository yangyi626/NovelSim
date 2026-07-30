# NovelSim 求职版交付包

本目录只放可公开、可复现且不依赖私有小说全文的作品集资产。

## 原创公开世界

`worlds/secret-letter-v1.json` 是“密信疑云”原创演示世界：

- 5 个角色、2 个地点、1 封密信和 1 条可观察事实；
- Free / Script 两种场景模式；
- NPC 无玩家命令时可完成“观察 → 传播 → 联盟”；
- 玩家可选择公开真相、销毁密信或携信离开；
- 内容来源标记为 `original_for_novelsim_portfolio`；
- 内容许可标记为 `CC-BY-4.0`；
- SHA-256：
  `be328a02de27c6e7c74d3099b6b5e455a56e54e3b67c183aa3ab6c8cd55f6748`。

重新导出并校验：

```powershell
.\.venv\Scripts\python.exe -m examples.secret_letter.package
.\.venv\Scripts\python.exe -m examples.secret_letter.package --check
```

运行三个代表性分支：

```powershell
.\.venv\Scripts\python.exe -m examples.secret_letter.demo --route none
.\.venv\Scripts\python.exe -m examples.secret_letter.demo --route expose_truth
.\.venv\Scripts\python.exe -m examples.secret_letter.demo --route destroy_letter
```

每次运行均从 version 0 创建隔离快照，并打印工具序列、事件 ID、版本、传播记录、
证据数量、联盟和结局。场景源码变化后，`--check` 会失败，必须重新导出并更新
本页哈希。

## 作品集导航

- 架构、状态机和因果图：`docs/作品集架构与因果图.md`
- 录屏分镜与面试演示：`docs/求职版演示脚本.md`
- 客观评测和消融：`docs/结构化场景评测.md`
- 只含实测数字的简历描述：`docs/简历项目描述.md`

## Unity 单镜实机视频

- 成片：[`video/NovelSim-core-demo-v1.mp4`](video/NovelSim-core-demo-v1.mp4)
- 结构化运行报告：[`video/NovelSim-core-demo-v1.json`](video/NovelSim-core-demo-v1.json)
- 时长：138.50 秒；H.264、1024×576、约 30 FPS；
- 真实运行：session `54a4ebac235287e4`，v0 → v1；
- 对抗输入：`WORLD_CONCEPT_UNAVAILABLE`，拒绝前后均为 v1；
- SHA-256：
  `692605fc1a1cb89bcf70c1cf3841f5c1d9bd283bc71edbc1770337f02986ff26`。

视频由 Windows 构建通过真实 HTTP 连续运行后直接录制，没有用静态图片拼接。
画面内字幕代替音轨，展示交互、提交、规则拒绝、同会话恢复和实测结果。Windows
构建本身仍是本地产物，不提交到 Git。
