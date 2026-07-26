# Web 界面：AI 快穿系统

状态面板 + 对话框 的单页应用。Vue 3 + Vite 前端 + FastAPI 后端。

## 快速开始

### 1. 配置 LLM Key
确保项目根目录有 `.env`（参考 `.env.example`）：
```
LLM_API_KEY=your-key
LLM_BASE_URL=https://api.gpt.ge/v1
LLM_MODEL=qwen3.6-plus
NO_PROXY=*
```

### 2. 构建前端（首次/改前端后）
```bash
cd web/frontend
npm install        # 首次
npm run build      # 产物输出到 ../static/
```

### 3. 启动后端
```bash
# 在项目根目录
.venv/Scripts/python.exe web/run.py
# 或
.venv/Scripts/python.exe -m uvicorn web.app:app --port 8000
```

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

## 架构

```
web/
├── app.py              # FastAPI: /api/start, /api/turn + 托管 static
├── run.py              # 启动脚本
├── static/             # 前端构建产物 (gitignore)
└── frontend/           # Vue 3 + Vite 源码
    ├── src/App.vue     # 三栏布局根组件
    ├── src/components/
    │   ├── StoryFeed.vue   # 左栏剧情流
    │   ├── StatePanel.vue  # 右栏世界状态
    │   └── TurnInput.vue   # 底部输入
    └── vite.config.js  # outDir=../static, dev 代理 /api
```

## 边界

- 固定载入华容巷世界（`examples/huarong_lane`），玩家扮演夜轻歌。
- 会话状态存内存，刷新页面 = 新开局（暂无存档/读档）。
- 每回合需调 LLM，约 10–60 秒；开启 NPC 反应会更久。
