# Web 界面：AI 快穿系统

玩家试玩 + 世界创作台的单页应用。Vue 3 + Vite 前端 + FastAPI 后端。

## 快速开始

### 0. Windows 一键启动

配置好项目根目录 `.env` 后，直接双击 `start.cmd`。它会自动构建缺失的前端产物，
后台启动 Web 和独立 Worker，通过健康检查后打开浏览器。

```powershell
.\novelsim.ps1 start --open-browser
.\novelsim.ps1 status
.\novelsim.ps1 stop
```

运行日志和精确 PID 位于 `data/runtime/`。启动器只停止自己创建的进程树，不会
扫描或结束其他 Python 服务。详细说明见
[`docs/Beta一键启动与CI.md`](../docs/Beta一键启动与CI.md)。

### 1. 配置 LLM Key
确保项目根目录有 `.env`（参考 `.env.example`）：
```
LLM_API_KEY=your-key
LLM_BASE_URL=https://api.gpt.ge/v1
LLM_MODEL=qwen3.6-plus
NO_PROXY=*
# 可选，默认 data/world.sqlite3
WORLD_DB_PATH=data/world.sqlite3
# 当前保持 SQLite 权威存储；启用 Qdrant Local Mode 语义检索
WORLD_DATABASE_URL=
MEMORY_VECTOR_BACKEND=qdrant
QDRANT_PATH=data/qdrant
QDRANT_COLLECTION=character_memories
MEMORY_EMBEDDING_MODEL=text-embedding-3-small
MEMORY_EMBEDDING_DIMENSIONS=1536
MEMORY_REFLECTIONS_ENABLED=true
MEMORY_REFLECTION_INTERVAL=5
MEMORY_REFLECTION_MIN_EPISODES=3
# 可选，创作者世界包目录
WORLD_PACKAGE_DIR=worlds
# 创作者账户、令牌和审核审计
AUTH_DB_PATH=data/auth.sqlite3
# 独立 Worker 使用的 SQLite 控制面
COMPILER_DB_PATH=data/compiler.sqlite3
```

### 2. 构建前端（首次/改前端后）
```bash
cd web/frontend
npm install        # 首次
npm run build      # 产物输出到 ../static/
```

### 3. 启动后端
```bash
# 首次启用 Qdrant（项目 Python 3.8 固定使用客户端 1.11.3）
.venv/Scripts/python.exe -m pip install -e ".[qdrant]"

# 推荐在项目根目录运行（run.py 会自动把根目录加入路径，所以在哪跑都行）
.venv/Scripts/python.exe web/run.py

# 另开终端运行独立编译 Worker；Web 进程不再执行 LLM 编译任务
.venv/Scripts/python.exe -m compiler.worker
```

> 若直接在 `web/` 目录里跑 `python run.py` 也可以——`run.py` 已处理路径，会自动切回项目根目录。

浏览器打开 **http://localhost:8000** 即可游玩。

## 开发模式（前后端热更新）

两个终端：
```bash
# 终端 1：前端 dev server (端口 5173，自动热更新)
cd web/frontend && npm run dev

# 终端 2：后端 (端口 8000)
.venv/Scripts/python.exe web/run.py --reload
```
浏览器开 **http://localhost:5173**（Vite 会把 `/api` 代理到 8000）。

## 界面说明

- **左栏·剧情流**：每回合一张卡片（旁白 / 对白 / 系统提示 / NPC 自主反应徽章）。
- **右栏·世界状态**：时间场景、在场角色（含 NPC 情绪/目标/计划）、关系数值条。
- **底部输入框**：自然语言描述你想做的事，Ctrl+Enter 发送。
- **NPC 自主反应开关**：默认开启——夜清清/林管家会在你行动后自主反应（更烧 token 但体验完整）。
- **自动续玩**：浏览器保存当前会话 ID，刷新页面或重启后端后会恢复同一世界状态。
- **存档管理**：可新建多条世界线，并对存档进行改名、载入、导出、导入和删除。
- **世界创作台**：点击顶栏“创作台”，可编辑角色、角色心理、角色认知、地点、物品、关系、世界规则、剧情线和介入锚点。
- **版本化创作**：内置包只读，可另存为可编辑版本；保存后可直接从该世界包开局试玩。
- **创作治理**：账户登录、RBAC、人物关系图、不可变修订历史、版本差异、审核审计，以及草稿→审核→批准→发布状态流。
- **全书编译**：SQLite 租约队列、独立 Worker、场景缓存、暂停/继续/取消、章节/卷/全书快照和自动质量审核。

## 创作者后台

创作台管理的是开局世界模板，不会直接修改玩家已经运行中的存档：

1. 选择内置或编译生成的世界包。
2. 点击“另存为新版本”，创建 `worlds/<package_id>.json`。
3. 在结构化表单中编辑世界元信息、角色、心理目标与计划、认知边界、地点层级、物品归属、关系、规则和剧情线。
4. 使用“高级 JSON”继续编辑确定性规则等完整 `WorldState` 字段。
5. 点击“校验”检查 Schema 与跨实体引用，再“保存并试玩”。

保存使用修订号防止旧页面覆盖新修改；模板版本固定为 `0`，玩家游玩后的版本变化只存在于所选数据库的世界线存档。校验会检查地点层级循环、物品归属、角色背包、Agent 目标/计划引用，以及角色认知中的重复事实、来源和关键词。

每次保存和审核状态变化都会形成修订历史；修改已批准或已发布内容会自动回到草稿。创作者、审核者、发布者和管理员使用独立 RBAC 权限，关键操作记录到账户数据库的不可变审计事件。

首次使用创作台可在登录页创建首个管理员，也可以使用命令行：

```bash
.venv/Scripts/python.exe -m web.manage_users bootstrap admin your-password
.venv/Scripts/python.exe -m web.manage_users create editor your-password --roles creator
```

## 持久化

- 世界状态、`WorldEvent`、剧情回合和角色记忆元数据继续保存在 `data/world.sqlite3`；Qdrant 只保存可从 SQLite 重建的向量索引。
- 每个有效回合在同一事务中写入新状态、事件和剧情记录，并校验旧版本，避免并发请求互相覆盖。
- NPC 记忆使用 SQLite FTS5 + Qdrant 语义召回混合重排；Qdrant 暂时不可用时自动降级为 FTS5，不阻塞回合。
- 默认 `QDRANT_PATH=data/qdrant` 是进程内 Local Mode，不需要 Docker 或独立服务；填写 `QDRANT_URL` 后可切换 Qdrant Server/Cloud。
- Local Mode 保持单进程启动；多 Worker 或多机器部署必须改用 `QDRANT_URL`，不能共享同一个本地路径。
- 情景记忆按世界线和角色隔离，自动限制每个角色最多 500 条；可从事件链和剧情历史重建。
- NPC 默认每 5 个世界版本检查一次尚未处理的经历；至少 3 条跨事件证据可提炼为带证据链的反思记忆。同一主张幂等更新，且与角色当前认知冲突的反思不会进入决策上下文。
- 反思写入前由独立模型执行证据蕴含评分；低于门槛、证据覆盖不足或过度推断的候选失败关闭。
- `GET /api/session?session=<id>` 可恢复会话。
- `GET /api/events?session=<id>` 可查看用于审计和回放的事件日志。
- `GET /api/saves`、`PATCH /api/saves/<id>`、`DELETE /api/saves/<id>` 提供基础存档管理。
- `GET /api/saves/<id>/export` 下载完整 JSON 备份；`POST /api/saves/import` 校验并导入备份。
- 导入会验证格式版本、世界状态、事件版本链和剧情历史，并以新会话 ID 原子写入，不覆盖原存档。
- `GET /api/creator/packages`、`GET /api/creator/packages/<id>` 读取世界包。
- `POST /api/creator/packages/<id>/clone` 创建可编辑版本。
- `POST /api/creator/packages/validate` 校验草稿；`PUT /api/creator/packages/<id>` 保存新修订。
- `POST /api/creator/compiler/jobs` 创建全书编译任务；`GET /api/creator/compiler/jobs` 查询进度。
- `GET /api/creator/compiler/jobs/<id>` 查看逐章状态和快照；`POST .../actions` 执行暂停、继续或取消。
- `POST /api/auth/login`、`GET /api/auth/me` 提供创作者 Bearer 身份；`GET /api/creator/audit` 查询审核审计。
- 核心 API 已冻结为 `1.0.0`；机器可读清单在 `contracts/api-v1.json`，运行时可查询 `GET /api/meta/contract`。
- 恢复会话时会重建玩家输入、旁白、对白、系统提示和 NPC 反应卡片。

PostgreSQL 启动、配置、SQLite 迁移和真实契约测试见 [`docs/PostgreSQL部署与迁移.md`](../docs/PostgreSQL部署与迁移.md)。
Qdrant 架构、配置、重建与升级方式见 [`docs/Qdrant向量检索.md`](../docs/Qdrant向量检索.md)。
中文召回基准、实测指标和最终权重见 [`docs/记忆检索评测.md`](../docs/记忆检索评测.md)。
反思生成、证据链、冲突保护和离线重建见 [`docs/反思与语义记忆.md`](../docs/反思与语义记忆.md)。
真实 LLM 长轨迹评分见 [`docs/LLM长轨迹评分.md`](../docs/LLM长轨迹评分.md)。
编译器 C 与创作者审核发布流见 [`docs/编译器C阶段与创作者发布流.md`](../docs/编译器C阶段与创作者发布流.md)。
SQLite 编译任务、断点续跑和编译器 D 见 [`docs/编译任务与全书编译D.md`](../docs/编译任务与全书编译D.md)。
独立 Worker、RBAC、E2E 和多小说基准见 [`docs/生产化基线_Worker_RBAC_E2E.md`](../docs/生产化基线_Worker_RBAC_E2E.md)。
Beta 一键启停、健康检查和 CI 门禁见 [`docs/Beta一键启动与CI.md`](../docs/Beta一键启动与CI.md)。
真实 quick/stress/full 编译演练见 [`docs/真实全书编译生产演练.md`](../docs/真实全书编译生产演练.md)。

## 架构

```
web/
├── app.py              # FastAPI: 试玩、存档、创作者 API + 托管 static
├── auth.py             # SQLite 账户、令牌、RBAC 和审核审计
├── manage_users.py     # 本地账户管理命令
├── run.py              # 启动脚本
├── static/             # 前端构建产物 (gitignore)
└── frontend/           # Vue 3 + Vite 源码
    ├── src/App.vue     # 三栏布局根组件
    ├── src/components/
    │   ├── CreatorStudio.vue # 世界包创作者后台
    │   ├── CompilationJobs.vue # 全书编译任务进度页
    │   ├── StoryFeed.vue   # 左栏剧情流
    │   ├── SaveManager.vue # 多世界线存档管理
    │   ├── StatePanel.vue  # 右栏世界状态
    │   └── TurnInput.vue   # 底部输入
    └── vite.config.js  # outDir=../static, dev 代理 /api
```

## 边界

- 内置华容巷基准世界；创作者可克隆或载入编译器输出的世界包并从指定默认角色开局。
- 支持本机创作者账户与分权审核；玩家存档仍是本机世界线，暂无云同步和租户隔离。
- 当前正式开发组合是 SQLite + Qdrant Local Mode；PostgreSQL 后端代码保留但不参与当前运行。
- 每回合需调 LLM，约 10–60 秒；开启 NPC 反应会更久。
