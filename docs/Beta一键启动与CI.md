# Beta 一键启动与 CI 门禁

> 实现状态：已完成并通过本地验收。Git 里程碑：
> `e12db9a feat: 建立 Beta 一键启动与 CI 门禁`。

## 一键启动

Windows 用户可以直接双击项目根目录的 `start.cmd`。它会：

1. 优先使用项目 `.venv` 中的 Python；
2. 前端产物不存在时自动执行 `npm ci` 和 `npm run build`；
3. 后台启动 FastAPI 与独立编译 Worker；
4. 等待 `GET /api/meta/contract` 健康检查通过；
5. 打开 `http://127.0.0.1:8000`。

对应命令：

```powershell
.\novelsim.ps1 start --open-browser
.\novelsim.ps1 status
.\novelsim.ps1 restart
.\novelsim.ps1 stop
```

也可以直接使用跨平台 Python 入口：

```powershell
.venv\Scripts\python.exe -m web.stack start --open-browser
.venv\Scripts\python.exe -m web.stack status
.venv\Scripts\python.exe -m web.stack stop
```

常用参数：

- `--port 9000`：修改 Web 端口；
- `--no-worker`：只启动 Web；
- `--build`：强制重新构建 Vue；
- `--runtime-dir <path>`：使用隔离的 PID 与日志目录。

## 进程安全

启动器只记录和管理自己创建的精确 PID：

```text
data/runtime/
├── stack.json
├── web.out.log
├── web.err.log
├── worker.out.log
└── worker.err.log
```

- 端口已占用时拒绝启动；
- Web 提前退出或健康检查超时时，回收本轮已创建的进程；
- `stop` 只终止 `stack.json` 记录的 Web/Worker 进程树；
- 记录系统启动标记，电脑重启后即使 PID 被复用也不会误停新进程；
- 不会扫描或终止其他 Python 服务；
- PID 状态文件与日志不进入 Git。

当前真实 quick 编译演练使用独立的
`data/benchmarks/production-drill.sqlite3` 和单独 Worker，不由默认
`data/runtime/stack.json` 管理，避免试玩启停影响基准任务。

## GitHub Actions

`.github/workflows/ci.yml` 在 Pull Request 和 `main` 推送时并行运行：

### Python 3.8 确定性门禁

- 核心 API v1 契约；
- 全部默认 pytest 回归；
- 版本化 20/60 回合轨迹回归；
- 两本真实小说 SHA256、章节和场景指纹。

### Vue 与浏览器门禁

- `npm ci`；
- Vue 生产构建；
- 安装 Chromium；
- Playwright 创作者、审核者、发布者 RBAC 发布闭环。

CI 强制使用 SQLite FTS5 且关闭真实编译质量模型，不需要 API Key，不会产生
LLM 费用。PostgreSQL、Qdrant Local 和真实 LLM marker 继续由专门环境手动运行。

Playwright 启动命令已改为 Windows/Linux 双平台路径；本地仍默认使用
`.venv\Scripts\python.exe`，CI 使用 `NOVELSIM_PYTHON=python`。

## 已验证结果

- `198 passed` 本地确定性测试；
- 版本化长轨迹回归 `5/5` 通过；
- 两本真实小说 SHA256、章节数和场景数扫描通过；
- Vue 生产构建通过，共转换 27 个模块；
- Playwright 创作者/RBAC/审核发布 E2E 通过；
- 使用隔离数据库真实执行 `start → status → stop`，Web 健康检查与进程树回收通过；
- 跨系统启动路径覆盖 Windows 本地 `.venv` 和 Linux CI `python`。

CI 不负责运行高成本真实 LLM 演练。首轮 quick 全书演练使用独立数据库和 Worker，
其状态与最终结果记录在
[`真实全书编译生产演练.md`](./真实全书编译生产演练.md)。
