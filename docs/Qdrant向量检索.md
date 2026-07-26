# Qdrant 向量检索

## 当前方案

项目采用“SQLite 权威数据 + Qdrant 派生索引”的双存储结构：

```text
回合提交
  ├─ SQLite：世界状态、事件、剧情回合、完整记忆记录
  └─ Qdrant：memory_id、作用域过滤字段、embedding

NPC 召回
  ├─ SQLite FTS5：中文词法候选
  ├─ Qdrant：语义候选（session_id + character_id 过滤）
  ├─ SQLite 权威认知：过滤已过期或相反的反思记忆
  └─ 混合重排：语义 80% + 词法 10% + 重要度 5% + 新近性 5%
```

SQLite 始终是事实来源。Qdrant 命中的 `memory_id` 必须回查 SQLite 后才可进入 NPC 上下文，因此陈旧向量点不会产生“幽灵记忆”。Qdrant 检索失败时自动降级为 SQLite FTS5；世界状态和回合提交不依赖向量库。

## 为什么当前不需要 Docker

Qdrant Python Client 官方支持 Local Mode：

```python
QdrantClient(path="data/qdrant")
```

它在 Web 进程内运行并持久化到磁盘，不需要 Qdrant 服务进程。未来只需设置 `QDRANT_URL` 和可选的 `QDRANT_API_KEY`，同一套索引代码即可连接 Qdrant Server 或 Cloud。

Local Mode 的同一路径只能由一个进程持有，因此当前继续使用单进程 Uvicorn。需要多进程、多机器或更高数据规模时，将 `QDRANT_URL` 指向 Qdrant Server/Cloud，而不是让多个 Worker 共用 `data/qdrant`。

项目仍使用 Python 3.8，因此锁定 `qdrant-client==1.11.3`；该版本官方元数据要求 Python ≥3.8。升级项目运行时到 Python 3.10+ 后，应重新评估并升级到当前客户端版本。

参考：

- [Qdrant Client Local Mode 与服务端切换](https://github.com/qdrant/qdrant-client)
- [qdrant-client 1.11.3 Python 兼容性](https://pypi.org/project/qdrant-client/1.11.3/)

## 安装与配置

```powershell
.venv\Scripts\python.exe -m pip install -e ".[qdrant]"
```

`.env`：

```dotenv
WORLD_DB_PATH=data/world.sqlite3
WORLD_DATABASE_URL=

MEMORY_VECTOR_BACKEND=qdrant
QDRANT_PATH=data/qdrant
QDRANT_COLLECTION=character_memories
QDRANT_URL=
QDRANT_API_KEY=

MEMORY_EMBEDDING_MODEL=text-embedding-3-small
MEMORY_EMBEDDING_DIMENSIONS=1536
# 未填写时复用 LLM 的 Key 与 Base URL
MEMORY_EMBEDDING_API_KEY=
MEMORY_EMBEDDING_BASE_URL=
```

当前 LLM 网关已经实际验证 `text-embedding-3-small` 返回 1536 维向量。

50 查询中文剧情基准实测 Hit@4 为 100%、MRR 为 0.957、nDCG@4 为 0.967，详见 [`记忆检索评测.md`](记忆检索评测.md)。

## 重建索引

Qdrant 是派生数据，可随时从 SQLite 中的完整记忆表重建：

```powershell
# 全部存档
.venv\Scripts\python.exe -m engine.rebuild_qdrant

# 只重建一个存档
.venv\Scripts\python.exe -m engine.rebuild_qdrant --session <session_id>
```

重建先按世界线清理旧点，再幂等写入当前权威记忆。日常写入、容量裁剪、记忆重建和删除存档也会同步维护 Qdrant。

情景记忆之上已经增加带证据链的反思记忆。反思记录与普通记忆一起进入 Qdrant，但仍由 SQLite 保存完整证据和结构化主张；生成、幂等更新、冲突保护与离线重建见 [`反思与语义记忆.md`](反思与语义记忆.md)。

## 验证

```powershell
# 默认测试（使用隔离假客户端，不要求 Qdrant 依赖）
.venv\Scripts\python.exe -m pytest

# 真实 Qdrant Local Mode 契约
.venv\Scripts\python.exe -m pytest tests/integration/test_qdrant_local.py `
  -m qdrant -o addopts=

# 真实中文召回质量门禁
.venv\Scripts\python.exe -m engine.evaluate_memory_retrieval
```
