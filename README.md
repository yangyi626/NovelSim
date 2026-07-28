# NovelSim / AI 快穿系统

把长篇小说编译成可运行的结构化世界，并让玩家通过自然语言改变世界线的 AI 叙事系统。

## 当前状态

项目已经完成可试玩、可创作、可编译、可审核的 Beta 主链路：

```text
TXT 长篇小说
  → 独立编译 Worker
  → 实体消歧 / 多时间线 / 分层快照
  → WorldPackage
  → 创作者审核与发布
  → 玩家自然语言回合
  → 规则校验 / NPC Agent / 长期记忆 / 事件持久化
```

- 权威数据：SQLite；
- 向量检索：Qdrant Local Mode，可降级到 SQLite FTS5；
- 后端：FastAPI + Pydantic；
- 前端：Vue 3 + Vite；
- 编译任务：SQLite 租约队列 + 独立 Worker；
- 治理：账户、RBAC、修订历史、审核审计和发布权限；
- 质量：207 个本地确定性测试、版本化长轨迹回归、跨平台双小说指纹基准和 Playwright E2E；
- API：核心契约冻结为 `1.0.0`。

截至 2026-07-28，首轮两本小说 quick 真实 LLM 编译演练已经完成，共验证40章、
49个场景、断网暂停恢复、缓存重放、质量门禁和自动审核。第一本评分0.896且进入
`pending_review`；第二本评分0.826，但因2个高严重度问题被硬门禁退回`draft`。
`stress`和正式全书档尚未启动。

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

## 验证

```powershell
# Python 确定性回归
.venv\Scripts\python.exe -m pytest

# 版本化长轨迹回归
.venv\Scripts\python.exe -m engine.trajectory_regression

# 两本真实小说源文件指纹
.venv\Scripts\python.exe -m compiler.benchmark scan

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
| [`docs/plan.md`](docs/plan.md) | 项目最终目标和原始总体蓝图 |
| [`docs/Beta一键启动与CI.md`](docs/Beta一键启动与CI.md) | 一键启停、日志、健康检查和 CI |
| [`docs/真实全书编译生产演练.md`](docs/真实全书编译生产演练.md) | quick/stress/full 真实编译演练 |
| [`docs/生产化基线_Worker_RBAC_E2E.md`](docs/生产化基线_Worker_RBAC_E2E.md) | Worker、账户权限、审计、E2E 和多小说基准 |
| [`docs/核心API契约v1.md`](docs/核心API契约v1.md) | 已冻结的核心 API v1 契约 |
| [`docs/编译任务与全书编译D.md`](docs/编译任务与全书编译D.md) | SQLite 编译任务、断点续跑和全书编译 |
| [`docs/反思与语义记忆.md`](docs/反思与语义记忆.md) | 反思记忆、证据链和语义一致性 |
| [`docs/Qdrant向量检索.md`](docs/Qdrant向量检索.md) | Qdrant Local/Server 架构与运维 |

## 当前边界

- 当前目标是先完成稳定的 Web Beta 与真实全书生产演练，Unity 3D 和多模态尚未开始。
- SQLite 是当前正式开发基线；PostgreSQL 后端保留，但不要求 Docker 或数据库服务。
- Qdrant Local Mode 适合本机单进程；多 Worker 或多机部署需要 Qdrant Server/Cloud。
- 玩家存档仍为本地世界线，团队多租户和云同步尚未实现。
- 真实 LLM 编译耗时和费用受模型服务影响，`full` 档必须先估算并显式确认。
