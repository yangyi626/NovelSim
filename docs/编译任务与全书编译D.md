# 编译任务与全书编译 D

## 目标

全书编译不再作为一次不可恢复的长时间命令运行。当前实现把控制面、缓存、
领域编译和发布审核拆开：

```text
TXT
  → SQLite 编译任务
  → 场景缓存（原文哈希 + Prompt 版本 + 模型 + 已知实体）
  → BookCompiler D
  → 章节 / 卷 / 全书快照
  → 真实 LLM 长轨迹质量门禁
  → WorldPackage 草稿或待审核修订
```

全部控制面数据保存在 `data/compiler.sqlite3`，不需要 Docker 或
PostgreSQL。

## SQLite 表

- `compiler_jobs`：任务状态、计划、当前章节、整体进度、质量结果和产物。
- `compiler_job_chapters`：逐章状态、缓存命中、新抽取次数和错误。
- `compiler_scene_cache`：可跨任务复用的结构化 `SceneExtraction`。
- `compiler_job_snapshots`：章节、卷、全书三级 `WorldState` 快照。

运行任务在进程异常退出后会自动恢复为 `paused`。点击继续时，从第一章重建
全局状态，但已成功抽取的场景全部命中缓存，不会重复调用 LLM。

## 状态机

```text
queued → running → completed
           │
           ├→ paused → queued → running
           ├→ cancelled
           └→ failed → queued → running
```

暂停和取消是协作式的：当前正在执行的单次 LLM 请求不会被强行中断，结果安全
落盘后，在下一章开始前停止。

## 编译器 D

`compiler/book_compiler.py` 在 C 阶段角色状态、伏笔和目标演化之上增加：

- `global_identity`：同一人物跨改名、转世或时间线使用稳定身份键。
- `incarnation`：记录当前肉身或社会身份。
- `timeline_id`：可由创作者按章节规划，也可由抽取结果提供。
- 全书级别名表和身份表，并显式记录同名冲突，避免静默误合并。
- 每章快照、每卷快照和最终全书快照，均保存稳定状态哈希。
- 将抽取场景转换为连续 `WorldEvent`，供真实 LLM 长轨迹评分。

命令行直接运行 D 阶段：

```powershell
.venv\Scripts\python.exe -m compiler.cli `
  novels\第一狂妃：废柴三小姐.txt `
  --stage D `
  --volume-size 20 `
  --package-id first_crazy_book `
  --out worlds\first_crazy_book.json
```

不传 `--chapters` 时 D 阶段编译全书。

## API

- `POST /api/creator/compiler/jobs`：创建并启动任务。
- `GET /api/creator/compiler/jobs`：任务列表和稳定进度。
- `GET /api/creator/compiler/jobs/{job_id}`：逐章状态和快照元数据。
- `POST /api/creator/compiler/jobs/{job_id}/actions`：`pause`、`resume`、
  `cancel`。

小说路径只允许读取项目 `novels/` 目录下的 TXT，避免创作者接口读取任意本机
文件。

## 自动审核

编译完成后，系统把场景事件交给 `LLMTrajectoryEvaluator`，检查因果连贯、
角色一致、目标推进、世界状态一致和重复控制。

- 评分通过：保存世界包，并自动从 `draft` 推进到 `pending_review`。
- 评分未通过或异常：仍保存可修复的草稿，但不会自动提交审核。
- 完整评分报告同时写入任务和 `manifest.compiler.quality_gate`。

可在本地关闭真实评分：

```env
COMPILER_QUALITY_GATE_ENABLED=false
```

关闭后产物保持草稿，不会绕过质量门禁进入待审核。
