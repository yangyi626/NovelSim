# BookWorld 前端外壳迁移说明

## 目标

在不继续投入 Unity 客户端的前提下，最快形成可在线演示、可录屏、可解释规则
门禁的 NovelSim 求职 Demo。BookWorld 提供成熟的“地图 + 角色 + 事件流 + 状态”
信息架构，NovelSim 保留自己的权威状态、规则引擎、NPC Agent、记忆、存档和评测。

## 实现决策

没有运行或嫁接 BookWorld 的 Python Agent 后端，也没有把 NovelSim 数据转换为
BookWorld 原始预设格式。迁移采用更低风险的前端视图适配：

```text
NovelSim WorldState / TurnResult
              │
              ▼
       Vue 视图模型适配
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
  地图/角色  事件流   状态/规则/场景
```

继续使用 Vue 3 + Vite 是为了复用当前创作台、存档管理和 API 封装；FastAPI 的
`/api/start`、`/api/session` 和 `/api/turn` 保持不变。BookWorld 的 WebSocket
连续生成协议没有被引入，因为 NovelSim 当前是一回合一次权威提交，REST 请求与
事务边界更一致，也能避免维护第二套服务端状态。

## BookWorld 到 NovelSim 的界面映射

| BookWorld 区域 | NovelSim 实现 | NovelSim 增强 |
|---|---|---|
| 地图面板 | `WorldMap.vue` | 从 `WorldState.locations` 绘制地点层级，并叠加当前场景和角色位置 |
| 角色档案 | `CharacterProfiles.vue` | 展示玩家/NPC、身份、位置、情绪和当前目标 |
| 聊天消息 | `StoryFeed.vue` | 展示权威回合、NPC 自主反应、拒绝码和拒绝详情 |
| 输入与控制 | `TurnInput.vue` | 保留自然语言行动，并加入正常/非法两条求职演示输入 |
| 状态面板 | `StatePanel.vue` | 展示世界时间、角色心理和关系数值 |
| 场景面板 | `InspectorPanel.vue` | 增加状态、规则判定、场景三个标签页 |
| 无对应核心能力 | 规则判定页 | 显示 ACCEPTED/REJECTED、规则原因以及权威版本是否变化 |

## 关键演示闭环

输入：

```text
夜轻歌开飞机离开华容巷
```

本地真实浏览器验收结果：

- 判定：`REJECTED`；
- 拒绝码：`WORLD_CONCEPT_UNAVAILABLE`；
- 原因：当前世界不存在“飞机”概念或实体，角色也无相关驾驶能力；
- 详情：`unresolved_references = 飞机`；
- 权威状态：仍为 `v0`，非法行动没有写入世界状态；
- 前端控制台：无错误或警告。

这条链路直接展示 NovelSim 相比纯提示词小说模拟器的差异：LLM 只负责理解候选
意图，确定性世界门禁决定什么可以成为事实。

## 运行与验证

```powershell
# 一键启动
.\novelsim.ps1 start --open-browser

# 前端生产构建
Set-Location web\frontend
npm run build

# 正式浏览器 E2E
npm run test:e2e

# 规则、回合、Web 持久化与 API 契约
Set-Location ..\..
.\.venv\Scripts\python.exe -m pytest -q `
  tests\unit\test_web_persistence.py `
  tests\scenarios\test_turn_pipeline.py `
  tests\unit\test_rules.py `
  tests\unit\test_api_contract.py
```

首轮迁移验收结果：前端生产构建通过；浏览器 E2E `1/1`；相关 Python
回归 `26/26`。随后增加的无需 API Key 一键演示把浏览器 E2E 扩展为 `2/2`，
演示、密信场景、世界规则与 Web 持久化相关回归为 `36/36`。

一键演示的三个案例、接口契约和证据口径见 [`一键演示模式.md`](一键演示模式.md)。

## 复用与归属边界

BookWorld 采用 Apache License 2.0。NovelSim 借鉴其三栏信息架构，前端组件使用
Vue 重新实现，并连接 NovelSim 自有 API；不包含 BookWorld Agent Runtime、提示词
或小说数据。归属说明保存在 `web/frontend/THIRD_PARTY_NOTICES.md`，本地克隆目录
`bookworld/` 被 `.gitignore` 排除，避免把独立仓库意外提交进 NovelSim。

## 当前非目标

- Unity 可游玩客户端继续保留，但不作为当前 Demo 的交付依赖；
- 暂不把回合接口改为流式 WebSocket；
- 暂不增加登录首页、云存档、移动端和复杂美术资源；
- 暂不迁移 BookWorld 的模型、记忆数据库和世界预设格式。
