# PostgreSQL + pgvector 部署与迁移

## 1. 本地启动数据库

项目提供仅用于本地开发的 Compose 配置：

```powershell
docker compose -f deploy/postgres/docker-compose.yml up -d
```

默认连接：

```text
postgresql://transmigration:local-development-only@127.0.0.1:5432/transmigration
```

生产环境不得沿用示例密码，应使用密钥管理服务注入连接字符串，并限制数据库公网访问。

## 2. 安装生产依赖

```powershell
.venv\Scripts\python.exe -m pip install -e ".[production]"
```

PostgreSQL 驱动使用 `psycopg2-binary 2.9.x`，兼容当前 Python 3.8。数据库镜像已经包含 pgvector 扩展；应用首次连接会创建 `vector` 扩展、JSONB 表、GIN 全文索引和 HNSW 向量索引。

## 3. 配置后端

在 `.env` 中设置：

```dotenv
WORLD_DATABASE_URL=postgresql://transmigration:local-development-only@127.0.0.1:5432/transmigration
```

设置后 Web 自动使用 PostgreSQL；留空则继续使用 `WORLD_DB_PATH` 指向的 SQLite。

启用向量召回：

```dotenv
MEMORY_EMBEDDING_MODEL=your-embedding-model
MEMORY_EMBEDDING_DIMENSIONS=1536
MEMORY_EMBEDDING_API_KEY=your-key
MEMORY_EMBEDDING_BASE_URL=https://your-provider.example/v1
```

如果嵌入服务与聊天模型使用相同的 key 和地址，后两项可以留空，系统会复用 `LLM_API_KEY` 和 `LLM_BASE_URL`。

当前 HNSW `vector` 索引支持 1～2000 维。维度一旦用于建表，不应直接修改；更换维度时应新建索引列并批量重嵌入。

未配置 `MEMORY_EMBEDDING_MODEL` 时，PostgreSQL 仍使用 GIN 全文检索；配置后自动执行全文与向量双路召回，再综合相关性、重要度和时间新近性重排。

## 4. 迁移现有 SQLite 存档

```powershell
.venv\Scripts\python.exe -m engine.migrate_storage `
  --sqlite data/world.sqlite3 `
  --postgres-url "postgresql://transmigration:local-development-only@127.0.0.1:5432/transmigration"
```

迁移过程：

1. 校验并导出每条 SQLite 世界线。
2. 原子导入 PostgreSQL，生成新的会话 ID。
3. 根据事件链和剧情历史重建角色情景记忆。
4. 输出旧会话 ID 到新会话 ID 的 JSON 映射。

迁移不会修改原 SQLite 文件，可以在验证新库后再自行归档旧库。

## 5. 运行真实数据库契约测试

```powershell
$env:TEST_POSTGRES_URL="postgresql://transmigration:local-development-only@127.0.0.1:5432/transmigration"
.venv\Scripts\python.exe -m pytest -m postgres -o addopts=
```

契约测试会验证会话创建、事务提交、事件/回合读取、中文记忆检索和删除级联，并在结束时删除测试会话。
