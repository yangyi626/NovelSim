# 生产化基线：Worker、RBAC、E2E 与多小说基准

## 当前结论

本阶段把编译控制面、创作者治理、浏览器验收和质量回归从单机原型提升为可重复运行的稳定基线：

```text
FastAPI 创建任务
  → SQLite 排队与租约
  → 独立 compiler.worker
  → 全书编译与真实 LLM 质量门禁
  → creator / reviewer / publisher 分权审核
  → SQLite 审计事件
```

核心 API 契约冻结为 `1.0.0`，机器可读清单位于
[`contracts/api-v1.json`](../contracts/api-v1.json)。

## 独立编译 Worker

Web 服务不再持有 `ThreadPoolExecutor`，只负责创建和控制任务。Worker 使用
`BEGIN IMMEDIATE` 原子领取任务，并写入：

- `worker_id`
- `lease_expires_at`
- `heartbeat_at`
- `attempt_count`

长时间 LLM 请求期间由独立心跳线程续租；多个 Worker 不会重复执行同一任务。
租约失效的任务安全转为 `paused`，继续后复用场景缓存。

运行：

```powershell
.venv\Scripts\python.exe web\run.py
.venv\Scripts\python.exe -m compiler.worker
```

## 账户与 RBAC

账户、令牌和审计保存在 `data/auth.sqlite3`。

- 密码：PBKDF2-SHA256，260,000 次迭代。
- 令牌：客户端持有明文 Bearer token，SQLite 只保存 SHA256 摘要。
- 首次初始化：仅当账户库为空时允许创建首个管理员。
- 停用账户会立即删除其全部令牌。

权限边界：

| 操作 | 权限 |
|---|---|
| 编辑世界包、管理编译任务 | `creator` |
| 提交审核 | `creator` |
| 批准、驳回 | `reviewer` |
| 正式发布 | `publisher` |
| 用户管理 | `admin` |

每次创建/控制编译任务、保存/克隆世界包、审核状态变化和权限拒绝都会写入
`audit_events`。Worker 自动质量门禁进入待审核也以 `system` 身份审计。

## 多小说真实全书基准

基准清单位于 [`benchmarks/novels.json`](../benchmarks/novels.json)：

| 小说 | 字节 | 章节 | 场景 |
|---|---:|---:|---:|
| 第一狂妃：废柴三小姐 | 29,348,994 | 4,228 | 7,362 |
| 第一狂妃：绝色邪王宠妻无度 | 6,564,753 | 1,935 | 2,317 |

第二本使用 `1.第1章标题` 格式，文本加载器已增加该格式并把章节扫描从约
95 秒优化到约 4 秒量级。

字节数和 SHA256 基于“解码文本 → 统一 LF → UTF-8”后的规范化内容计算，
因此 Windows 的 CRLF 检出与 GitHub Actions 的 Linux LF 检出会得到同一指纹。

运行方式：

```powershell
# 验证原文 SHA256、章节数与场景数
.venv\Scripts\python.exe -m compiler.benchmark scan

# 创建两本真实全书任务
.venv\Scripts\python.exe -m compiler.benchmark enqueue

# 启动独立 Worker
.venv\Scripts\python.exe -m compiler.worker

# 汇总进度、缓存、耗时和质量评分
.venv\Scripts\python.exe -m compiler.benchmark report
```

本阶段真实烟测结果：

| 小说 | 范围 | 耗时 | 质量分 | 结果 |
|---|---:|---:|---:|---|
| 第一狂妃：废柴三小姐 | 首章 | 223.449 秒 | 0.90 | 通过 |
| 第一狂妃：绝色邪王宠妻无度 | 首章 | 148.136 秒 | 0.90 | 通过 |

全书共9,679个场景。两书各20章的`quick`真实LLM生产演练已经完成，并真实验证
暂停恢复、缓存重放、硬调用预算、质量阻断和审核退回；`stress`和`full`尚未启动。
最终指标、问题与报告位置见
[`真实全书编译生产演练.md`](./真实全书编译生产演练.md)。

## 正式 E2E 与长轨迹回归

Playwright E2E 位于 `web/frontend/e2e/`，使用隔离 SQLite 和临时世界包目录，
真实启动 FastAPI，验证：

1. 创作者登录、克隆和提交审核；
2. 创作者无法批准；
3. 审核者批准但不能发布；
4. 发布者正式发布；
5. 审计日志可见；
6. 浏览器控制台无错误。

运行：

```powershell
cd web\frontend
npm run test:e2e
```

版本化长轨迹库位于
[`benchmarks/trajectories/v1.json`](../benchmarks/trajectories/v1.json)，
覆盖 20/60 回合合法链、版本断裂、死亡角色行动和未知目标故障注入：

```powershell
.venv\Scripts\python.exe -m engine.trajectory_regression
```
