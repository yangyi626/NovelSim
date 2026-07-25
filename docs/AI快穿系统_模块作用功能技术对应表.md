# AI 快穿系统：模块—作用—功能—技术对应表

> 项目定位：面向小说、影视、漫画等叙事 IP 的可交互世界运行平台。用户以“快穿者”身份进入不同世界，通过自由行动改变人物命运、剧情走向和世界状态，并形成可持续演化、可回放、可分支的个人世界线。

---

## 1. 总体架构原则

系统采用 **Action → State → Observation** 的运行闭环：

```text
用户行动 Action
    ↓
行动理解与规则验证
    ↓
世界状态更新 State
    ↓
角色与剧情推演
    ↓
文字、语音、立绘、动画、视频等表现 Observation
```

核心技术思想：

1. **显式世界状态**：人物、关系、剧情、任务、物品、规则和事件均以结构化形式维护。
2. **规则约束推演**：LLM 负责理解和提出候选变化，程序规则决定变化是否合法。
3. **角色认知隔离**：角色只能依据自己知道、看到、听到或推断出的信息行动。
4. **事件溯源**：所有世界变化以不可变事件记录，支持回放、回滚和世界线分支。
5. **表现层解耦**：文字、2D、3D、语音和视频只是世界状态的不同渲染方式。
6. **多模型协同**：不同任务使用不同级别的语言、向量、语音、图像和视频模型。
7. **可扩展世界接入**：小说经过“世界编译”后，以统一 Schema 接入系统运行。

---

# 2. 系统总览表

| 层级 | 核心模块 | 主要作用 | 核心技术 |
|---|---|---|---|
| 客户端层 | Web、移动端、PC端 | 承载用户游玩、创作、管理和社区功能 | Next.js、React、TypeScript、Flutter/React Native、Tauri |
| 用户交互层 | 对话、选项、探索、任务、背包、关系 | 将世界能力转化为可操作的游戏交互 | React、Zustand、PixiJS、Phaser、Three.js |
| 多模态呈现层 | 文字、立绘、语音、音乐、动画、视频、3D | 将结构化世界状态转化为用户可感知内容 | Live2D、Web Audio、TTS、图像生成、视频生成、Unity/Unreal |
| 接入层 | API、流式通信、鉴权、限流、安全 | 统一承接客户端请求并保护后端系统 | FastAPI、REST、SSE、WebSocket、Nginx、JWT、Redis |
| 核心引擎层 | 行动理解、规则检查、状态转移、角色 Agent、导演 Agent | 执行一次完整的世界交互与演化 | Python、Pydantic、LangGraph、规则引擎、Utility AI |
| 世界状态层 | 物理、认知、关系、剧情、任务、规则、事件 | 保存世界的权威事实和动态状态 | PostgreSQL、JSONB、状态机、事件溯源 |
| 记忆与知识层 | 原著知识、角色记忆、世界知识、检索 | 为角色和推演模型提供受权限约束的上下文 | PostgreSQL FTS、pgvector、Hybrid RAG、Reranker |
| 世界初始化与编译层 | 小说解析、实体抽取、事件抽取、关系图谱、规则抽取、状态快照、世界线实例化 | 将原始小说转换为可运行的世界模板，并创建用户专属世界线 | LLM、信息抽取、知识图谱、时间线与因果图、事件溯源、人工审核 |
| 外部模型层 | LLM、Embedding、Reranker、TTS、图像、视频 | 提供生成、推理、理解和多模态能力 | Model Gateway、多模型路由、结构化输出 |
| 数据层 | 关系数据、向量数据、缓存、对象存储、日志 | 存储权威状态、检索记忆和媒体资源 | PostgreSQL、pgvector、Redis、MinIO/S3 |
| 中间件层 | 异步任务、事件流、调度、工作流 | 执行耗时任务、延迟事件和长周期流程 | Celery、Redis Streams、Temporal |
| 管理与创作层 | 世界编辑器、角色编辑器、剧情编辑器、审核后台 | 支持作者、策划、运营和管理员配置世界 | React、低代码表单、图编辑器、JSON Schema |
| 测试评估层 | 单元测试、轨迹测试、一致性评估、模型评估 | 验证世界长期稳定性与角色一致性 | pytest、Hypothesis、Playwright、Eval Pipeline |
| 可观测性层 | 日志、指标、链路、成本监控 | 定位故障并统计模型成本和系统性能 | OpenTelemetry、Prometheus、Grafana、Loki/ELK |
| 安全合规层 | 内容安全、权限、隐私、审计、版权保护 | 降低生成内容、用户数据和 IP 使用风险 | RBAC、审核模型、脱敏、审计日志、水印 |
| 部署运维层 | 容器化、负载均衡、弹性扩缩、CI/CD | 稳定运行并支持持续迭代 | Docker、Kubernetes、Nginx、GitHub Actions、Terraform |

---

# 3. 客户端层

| 模块 | 作用 | 主要功能 | 技术实现 | 关键机制 |
|---|---|---|---|---|
| Web 客户端 | 提供完整网页版游戏体验 | 登录、选世界、对话、探索、任务、关系、存档 | Next.js、React、TypeScript | SSR/CSR 混合渲染、组件化页面 |
| 移动端客户端 | 提供移动端沉浸式体验 | 竖屏剧情、语音交互、推送、离线缓存 | Flutter 或 React Native | 跨平台 UI、推送通知、本地缓存 |
| PC 客户端 | 提供桌面端增强体验 | 大屏场景、快捷键、资源预加载、桌面通知 | Tauri 或 Electron | Web 技术桌面封装 |
| 3D 客户端 | 支持自由移动和复杂场景 | 角色控制、场景探索、战斗、过场动画 | Unity 或 Unreal Engine | ECS、动画状态机、导航、物理系统 |
| 创作者客户端 | 支持作者和策划构建世界 | 导入小说、编辑角色、配置任务、审核分支 | React、图编辑器、富文本编辑器 | 可视化世界编排 |
| 管理后台 | 支持平台运营和治理 | 用户、模型、内容、世界、成本、审核管理 | Next.js Admin、Ant Design | RBAC、审计、数据看板 |

---

# 4. 用户交互层

| 模块 | 作用 | 主要功能 | 技术实现 | 关键算法/机制 |
|---|---|---|---|---|
| 自由文本行动 | 允许用户用自然语言行动 | 输入对话、行为、计划、心理活动 | React 输入组件、SSE | 流式提交、输入历史 |
| 推荐选项 | 降低自由输入门槛 | 根据当前状态推荐可执行操作 | LLM + 规则过滤 | 候选动作生成、可行性排序 |
| 场景探索 | 让用户在空间中探索世界 | 场景切换、热点点击、地图移动 | PixiJS、Phaser、Three.js | 场景图、导航图、交互区域 |
| 角色互动 | 呈现人物交互及反馈 | 对话、表情、动作、关系变化 | React、PixiJS、Live2D | 角色状态绑定、口型同步 |
| 任务系统 | 引导用户推进主线和支线 | 任务接取、条件检测、奖励、失败 | PostgreSQL、状态机 | 任务依赖图、条件触发 |
| 背包与能力系统 | 管理物品、技能和身份能力 | 获取、使用、合成、转移、消耗 | PostgreSQL、规则引擎 | Inventory 约束、资源事务 |
| 人物关系面板 | 展示关系变化 | 好感、信任、恐惧、敌意、秘密 | React 图表、关系边表 | 多维关系模型 |
| 世界日志 | 展示用户造成的历史变化 | 关键事件、原著偏离、角色命运 | Event Store 查询 | 时间线聚合、事件摘要 |
| 世界线管理 | 管理平行剧情分支 | 存档、读档、回滚、分叉、对比 | Event Sourcing、Snapshot | 分支树、版本控制 |
| 快穿任务中心 | 连接不同小说世界 | 世界选择、宿主身份、系统任务、积分 | 状态机、任务编排 | 跨世界账户状态 |
| 社交与分享 | 传播个人世界线 | 分享结局、世界线卡片、评论、排行 | Web 社区模块 | 内容审核、推荐算法 |
| 多人协作模式 | 多用户共同进入世界 | 组队行动、投票决策、角色分工 | WebSocket、房间服务 | 并发控制、回合同步 |

---

# 5. 多模态呈现层

| 模块 | 作用 | 主要功能 | 技术实现 | 关键机制 |
|---|---|---|---|---|
| 文字叙事 | 输出剧情、对白和系统提示 | 旁白、对白、心理描写、事件总结 | LLM、模板引擎 | 状态约束生成、风格控制 |
| 静态立绘 | 展示角色视觉形象 | 表情差分、服装、姿态 | PNG/WebP、图像生成 | 角色一致性资产库 |
| Live2D 角色 | 提供动态角色表现 | 口型、眨眼、姿态、情绪动作 | Live2D Cubism SDK | 参数驱动动画 |
| 2D 场景 | 提供视觉小说和轻游戏表现 | 背景、人物位置、镜头、粒子 | PixiJS、Phaser | 场景树、Tween、粒子系统 |
| 3D 场景 | 提供自由移动和复杂演出 | 地图、角色、NPC、战斗、物理 | Unity、Unreal、Three.js | ECS、动画蓝图、行为树 |
| 语音合成 | 为角色对白配音 | 多角色音色、情绪、语速 | TTS 服务、音色模型 | 角色音色映射、情绪控制 |
| 语音输入 | 支持用户语音行动 | ASR、语音转文本、说话人检测 | Whisper 类 ASR | VAD、流式识别 |
| 音乐与音效 | 增强场景氛围 | BGM、环境音、动作音、转场音 | Web Audio API、FMOD/Wwise | 状态驱动音频切换 |
| CG 生成 | 生成关键剧情插图 | 场景图、角色 CG、结局海报 | 图像生成模型 | 角色一致性、姿态控制 |
| 动画短片 | 展示重要事件 | 过场、剧情高潮、结局视频 | 图生视频、文生视频 | 关键帧约束、角色一致性 |
| 视频世界模型 | 生成连续可交互画面 | 相机控制、实时事件、世界探索 | 交互式视频世界模型 | Action/State 条件生成 |
| 渲染指令生成器 | 将世界状态转为表现指令 | 背景、角色、镜头、音乐、特效编排 | LLM + JSON Schema | 状态到表现映射 |

---

# 6. 接入层

| 模块 | 作用 | 主要功能 | 技术实现 | 关键机制 |
|---|---|---|---|---|
| API Gateway | 统一承接客户端请求 | 路由、转发、鉴权、限流 | Nginx、Kong 或 Traefik | 反向代理、TLS |
| REST API | 提供常规业务接口 | 用户、世界、角色、任务、存档 | FastAPI | OpenAPI、异步请求 |
| SSE 服务 | 流式返回剧情和模型输出 | 文本流、阶段进度、状态通知 | FastAPI StreamingResponse | 单向事件流 |
| WebSocket 服务 | 支持双向实时交互 | 多人同步、实时场景、语音状态 | FastAPI/WebSocket Gateway | 心跳、断线重连 |
| 鉴权服务 | 确认用户身份和权限 | 登录、刷新 Token、第三方登录 | Auth.js、OAuth2、JWT | Access/Refresh Token |
| API 限流 | 控制高频和恶意请求 | 用户限流、模型限流、IP 限流 | Redis、网关插件 | Token Bucket、滑动窗口 |
| 幂等控制 | 防止行动重复提交 | 去重、重复请求返回同一结果 | Redis + PostgreSQL | Idempotency Key |
| 输入安全 | 防止攻击进入核心引擎 | Prompt 注入检测、参数验证 | Pydantic、审核模型 | Schema 白名单 |
| 内容审核入口 | 过滤违规输入和输出 | 文本、图像、语音、视频审核 | 规则 + 审核模型 | 多模态审核 |
| API 版本管理 | 支持客户端长期兼容 | `/v1`、`/v2`、灰度接口 | FastAPI Router | 兼容策略 |

---

# 7. 核心引擎层

## 7.1 核心模块总表

| 模块 | 作用 | 主要功能 | 技术实现 | 核心算法/机制 |
|---|---|---|---|---|
| 行动理解器 | 将自然语言转换为结构化行动 | 意图识别、实体链接、槽位抽取、指代消解 | LLM Structured Output、Pydantic | NER、实体链接、槽位填充 |
| 行动补全器 | 补齐执行动作所需参数 | 地点、对象、方式、目标、隐蔽性 | LLM + 世界上下文 | 缺失槽位推断 |
| 可行动作生成器 | 生成当前状态下可执行的动作集合 | 推荐行动、NPC可选行动 | 规则引擎 + LLM | Action Masking |
| 规则检查器 | 判断行动是否合法和可行 | 时间、位置、能力、物品、身份、剧情检查 | Python、规则 DSL | Preconditions–Effects |
| 权限与知识检查器 | 控制角色可读取的信息 | 视野、传闻、记忆、秘密、权限 | Python、知识图 | 信息可见性约束 |
| 世界状态转移模型 | 计算动作后的世界变化 | 状态补丁、成功概率、事件触发 | LLM + 确定性程序 | StatePatch、Utility、采样 |
| 结果结算器 | 对概率和数值结果作确定性结算 | 成功、失败、部分成功、代价 | Python | Seeded Sampling、数值规则 |
| 角色 Agent 层 | 模拟角色自主决策 | 目标、认知、情绪、关系、行动、对白 | LLM、LangGraph | BDI、Utility AI、行为树 |
| 群体 Agent 调度器 | 决定哪些角色在本轮运行 | 在场角色、相关角色、延迟事件角色 | 事件总线、调度器 | Event-driven Activation |
| 剧情导演 Agent | 决定如何组织和呈现剧情 | 节奏、视角、悬念、伏笔、交互机会 | LLM、剧情图 | Story Beat、候选排序 |
| 世界导演 | 控制更高层世界演化 | 大事件、社会变化、跨区域变化 | 规划模型、规则系统 | HTN、事件规划 |
| 一致性审查器 | 检查状态、认知和叙事冲突 | 时空、因果、人设、知识边界 | Python + LLM | 不变量、因果图 |
| 状态提交器 | 可靠提交世界变化 | 事务、版本、事件、快照 | PostgreSQL | Event Sourcing、乐观锁 |
| 延迟事件调度器 | 在未来时间触发事件 | 冷却、约会、追杀、任务期限 | Temporal/Redis | 优先队列、时间轮 |
| 世界线分支器 | 创建平行世界状态 | 分支、合并、对比、继承 | Event Store | Branching、Snapshot |
| 回放与解释器 | 解释世界为何发展到当前状态 | 事件链、因果链、角色动机 | Event Graph + LLM | 因果追踪、轨迹摘要 |

---

## 7.2 行动理解器

| 子功能 | 作用 | 技术 | 算法 |
|---|---|---|---|
| 意图分类 | 判断用户是攻击、交谈、调查还是移动 | 小型 LLM、分类模型 | Few-shot 分类 |
| 实体识别 | 找出角色、地点、物品、事件 | LLM、规则词典 | NER |
| 实体链接 | 将“她”“那杯酒”等映射到系统 ID | PostgreSQL、向量检索 | 精确匹配、模糊匹配、语义召回 |
| 槽位抽取 | 提取目标、方式、工具、地点、目的 | Structured Output | Slot Filling |
| 指代消解 | 判断代词和省略内容指向 | LLM + 对话上下文 | Coreference Resolution |
| 歧义检测 | 判断行动是否存在关键歧义 | 置信度模型 | Confidence Threshold |
| Action Schema 校验 | 保证行动符合系统格式 | Pydantic、JSON Schema | 类型与枚举校验 |

示例输出：

```json
{
  "action_type": "swap_object",
  "actor_id": "user",
  "target_id": "female_lead_wine_cup",
  "method": "create_distraction",
  "cover_action": "collide_with_maid",
  "visibility": "covert",
  "declared_goal": "prevent_poisoning"
}
```

---

## 7.3 规则检查器

| 规则类型 | 作用 | 示例 | 技术实现 |
|---|---|---|---|
| 时空规则 | 检查是否在正确时间和地点 | 不在宴会厅不能换酒 | 场景图、时间线 |
| 物品规则 | 检查物品持有和可访问性 | 没有解药不能替换毒酒 | Inventory 约束 |
| 身份规则 | 检查身份权限 | 平民不能进入皇宫内殿 | RBAC 式身份权限 |
| 能力规则 | 检查技能和属性 | 不会医术无法精准解毒 | 属性系统 |
| 世界观规则 | 检查魔法、科技、修炼规则 | 禁止无代价复活 | 规则 DSL |
| 剧情规则 | 检查关键事件的前置和后置条件 | 宴会未开始不能触发下毒 | 剧情状态机 |
| 信息规则 | 检查行动是否依赖未知事实 | NPC 不知道密信内容 | 知识边界 |
| 因果规则 | 检查是否有原因支撑结果 | 未攻击不能直接受伤 | 事件依赖图 |
| 数值规则 | 检查数值范围和计算 | 好感度范围、生命值下限 | Python 数值约束 |
| 安全规则 | 拦截系统级越权操作 | 用户要求“直接改数据库” | 工具白名单 |

规则表达建议：

```yaml
rule_id: swap_poisoned_cup
preconditions:
  - actor.location == target.location
  - target_cup.accessible == true
  - banquet.stage < toast_started
  - actor.inventory contains antidote
effects:
  - female_lead.poisoned = false
  - villain.plan_status = disrupted
  - maid.suspicion += 0.2
```

---

## 7.4 世界状态转移模型

| 子模块 | 作用 | 技术 | 算法/机制 |
|---|---|---|---|
| 状态上下文构建器 | 选择本轮推演所需状态 | PostgreSQL、RAG | Context Assembly |
| 候选结果生成器 | 生成可能发生的结果 | 强推理 LLM | Structured StatePatch |
| 成功概率计算器 | 计算行动成功率 | Python | Utility Function、Logistic |
| 确定性结算器 | 根据随机种子决定最终结果 | Python | Seeded Sampling |
| StatePatch 验证器 | 检查候选修改是否合法 | Pydantic、规则引擎 | Operation Whitelist |
| 状态合并器 | 合并多角色、多事件产生的补丁 | Python | 冲突消解、优先级 |
| 衍生状态计算器 | 更新由基础状态推导出的字段 | Python | Derived State |
| 事件触发器 | 根据变化触发新事件 | Event Bus | 条件订阅 |

推荐限定操作集合：

```text
SetFlag
IncrementValue
MoveCharacter
TransferItem
UpdateBelief
UpdateEmotion
CreateRelation
CreateEvent
ScheduleEvent
CompleteTask
StartPlotArc
EndPlotArc
ChangeIdentity
ChangeFaction
```

---

## 7.5 角色 Agent 层

| 子模块 | 作用 | 技术 | 算法/机制 |
|---|---|---|---|
| 角色身份模型 | 保存身份、背景和社会位置 | PostgreSQL、JSONB | Character Profile |
| 性格模型 | 约束角色长期行为风格 | 数值人格、标签 | Trait Vector |
| 目标系统 | 保存短期和长期目标 | PostgreSQL | Goal Stack |
| 信念系统 | 保存角色认为真实的事实 | Knowledge Store | Belief State |
| 情绪系统 | 建模情绪及其衰减 | Python、LLM | Appraisal、Decay |
| 关系系统 | 建模角色间多维关系 | 关系边表 | Trust/Affection/Fear |
| 记忆系统 | 检索角色过去经历 | pgvector、FTS | Hybrid RAG |
| 候选行动生成 | 生成角色当前可做的行动 | LLM + 规则 | Action Proposal |
| 行动评分 | 选择最符合目标和人格的行动 | Python | Utility AI |
| 计划系统 | 保存角色跨多轮计划 | LangGraph/Temporal | Plan Stack、HTN |
| 对白生成 | 生成符合身份和情境的语言 | LLM | Persona-conditioned Generation |
| 认知更新 | 更新角色知道和相信的内容 | Python + LLM | Observation-to-Belief |

角色决策评分：

```text
行动效用 =
目标收益
+ 人格一致性
+ 关系影响
+ 情绪驱动
+ 环境机会
- 风险
- 成本
```

---

## 7.6 剧情导演 Agent

| 子模块 | 作用 | 技术 | 算法/机制 |
|---|---|---|---|
| 剧情状态读取 | 获取当前主线、支线和伏笔 | PostgreSQL | Plot State |
| 候选事件生成 | 生成下一批可展示事件 | LLM | Event Proposal |
| 剧情规则过滤 | 排除不满足条件的事件 | 规则引擎 | Preconditions |
| 节奏控制 | 控制紧张、舒缓和高潮 | 数值张力曲线 | Tension Curve |
| 视角控制 | 选择用户、角色或全知视角 | LLM + 规则 | Viewpoint Policy |
| 信息揭示控制 | 决定透露多少秘密 | Knowledge Graph | Reveal Budget |
| 伏笔管理 | 埋设、保持和回收伏笔 | Plot Thread Store | Thread Tracking |
| 用户参与度控制 | 确保剧情留出行动机会 | 启发式评分 | Agency Score |
| 场景编排 | 组织对白、动作、镜头和氛围 | Render Instruction Schema | Story Beat |
| 原著偏离管理 | 计算和控制世界线偏离 | 事件对齐 | Canon Divergence |

候选剧情事件评分：

```text
Score =
当前相关性
+ 角色目标推进
+ 悬念价值
+ 用户参与度
+ 伏笔回收价值
+ 世界变化价值
- 重复度
- 逻辑风险
```

---

## 7.7 一致性审查器

| 检查类型 | 检查内容 | 技术实现 |
|---|---|---|
| 状态一致性 | 死亡角色不能无条件复活 | 状态不变量 |
| 时空一致性 | 同一角色不能同时出现在两地 | 时间与位置约束 |
| 物品一致性 | 同一物品不能同时被多人持有 | 所有权约束 |
| 数值一致性 | 属性不能超范围 | Schema 校验 |
| 因果一致性 | 结果必须有原因 | 事件依赖图 |
| 认知一致性 | 角色不能知道未获得的信息 | Knowledge Boundary |
| 人设一致性 | 行为不能无理由背离人格 | LLM Judge |
| 情绪一致性 | 情绪变化需要事件支撑 | 情绪状态机 |
| 剧情一致性 | 主线、支线和伏笔不能互相冲突 | Plot Graph |
| 文本状态一致性 | 生成文本必须符合已提交状态 | LLM + Fact Check |
| 原著一致性 | 未偏离前应保持原著事实 | Canon Alignment |
| 世界观一致性 | 生成内容不能违反世界规则 | Rule Validation |

---

## 7.8 状态提交与事件溯源

| 模块 | 作用 | 技术 | 机制 |
|---|---|---|---|
| 事务提交器 | 原子化提交状态变化 | PostgreSQL Transaction | ACID |
| 事件存储 | 保存不可变世界事件 | PostgreSQL | Append-only Event Store |
| 状态快照 | 加速世界加载 | JSONB Snapshot | 周期快照 |
| 版本控制 | 防止并发覆盖 | PostgreSQL | Optimistic Locking |
| 回滚系统 | 恢复到历史版本 | Event Replay | Replay |
| 分支系统 | 创建平行世界线 | Branch ID、Parent Version | Branching |
| 冲突解决 | 处理多人或多 Agent 并发修改 | Python | Priority、Merge Policy |
| 审计记录 | 保存模型、规则、随机种子 | Audit Log | 可复现轨迹 |

---

# 8. 世界状态层

| 状态类型 | 作用 | 主要字段 | 技术实现 |
|---|---|---|---|
| 物理世界状态 | 描述客观可观察世界 | 时间、地点、天气、场景、物体 | PostgreSQL + JSONB |
| 角色身体状态 | 描述角色身体和能力 | 生命、伤势、体力、技能、装备 | 关系表 + JSONB |
| 角色内部状态 | 描述角色心理和计划 | 目标、情绪、动机、计划 | JSONB |
| 角色认知状态 | 描述角色知道和相信什么 | 已知事实、误解、怀疑、秘密 | Knowledge Store |
| 人物关系状态 | 描述角色之间关系 | 好感、信任、恐惧、敌意、债务 | Relation Edge Table |
| 阵营状态 | 描述组织和政治关系 | 阵营、职位、声望、联盟 | Graph/Relation Table |
| 剧情状态 | 描述当前叙事位置 | 主线、支线、剧情阶段、伏笔 | Plot Graph、FSM |
| 任务状态 | 描述用户和角色任务 | 目标、进度、期限、奖励 | Task Graph |
| 世界规则 | 约束世界的运行方式 | 魔法、科技、身份、死亡、时间 | Rule DSL |
| 经济状态 | 描述资源和交易 | 货币、物价、库存、资产 | PostgreSQL |
| 社会状态 | 描述群体和舆论变化 | 声望、传闻、社会事件 | Agent Simulation |
| 原著对齐状态 | 衡量与原剧情关系 | 已发生节点、被阻止节点、偏离度 | Canon Graph |
| 快穿系统状态 | 保存跨世界能力 | 系统等级、积分、任务、道具 | Global Account State |
| 事件日志 | 保存所有已发生变化 | 行动、前置、结果、参与者 | Event Store |

---

# 9. 记忆与知识层

| 模块 | 作用 | 主要功能 | 技术实现 | 关键算法 |
|---|---|---|---|---|
| 原著知识库 | 保存小说原文和设定 | 章节、人物、地点、事件、规则 | PostgreSQL、对象存储 | 分块、索引 |
| 世界知识图谱 | 保存实体和关系 | 人物、地点、物品、事件、组织 | PostgreSQL 图式表/Neo4j | Entity-Relation Graph |
| 角色情景记忆 | 保存角色亲历事件 | 事件片段、对话、情绪 | pgvector | Episodic Memory |
| 角色语义记忆 | 保存角色总结出的知识 | 人物认识、世界认识 | PostgreSQL + 向量 | Semantic Memory |
| 用户共同记忆 | 保存用户和角色共同经历 | 对话、承诺、冲突、救援 | Event Store + pgvector | Shared Memory |
| 工作记忆 | 保存当前场景上下文 | 当前人物、动作、临时目标 | Redis | Short-term Cache |
| 长期记忆摘要 | 压缩过长历史 | 人物阶段总结、关系总结 | LLM Summarization | Hierarchical Summary |
| 混合检索器 | 找回相关事实和记忆 | 关键词、语义、时间、重要性 | PostgreSQL FTS + pgvector | Hybrid RAG |
| 重排器 | 对召回结果重新排序 | 相关性与权限排序 | Cross-Encoder | Reranking |
| 权限过滤器 | 防止角色读取未知信息 | 角色、来源、可见性、时间 | Metadata Filter | Access-aware Retrieval |
| 遗忘与衰减模块 | 模拟记忆强度变化 | 时间衰减、情绪强化、重复强化 | Python | Decay Function |
| 记忆冲突处理 | 处理误解和新证据 | 旧信念、新事实、可信度 | Belief Revision | Bayesian/Rule-based |

推荐检索流程：

```text
角色与时间权限过滤
    ↓
关键词检索 + 向量检索
    ↓
RRF 融合
    ↓
Cross-Encoder 重排
    ↓
重要性、时间和情绪加权
    ↓
返回角色可访问上下文
```

---

# 10. 小说世界初始化与编译层

## 10.1 模块定位

小说世界初始化不是“把整本 TXT 放入大模型上下文”，而是将一本线性小说离线编译为可被核心世界引擎执行的标准世界模板，再根据用户选择的剧情入口创建独立运行时世界线。

核心转换关系：

```text
小说 TXT
    ↓
原著解析与结构化
    ↓
可执行世界模板
    ↓
关键剧情锚点与状态快照
    ↓
用户专属世界线实例
    ↓
持续运行、偏离、分支、回放
```

一本小说最终对应：

```text
一个不可变的原著世界模板
+ 一条原著事件时间线
+ 一组关键剧情状态快照
+ 多个用户独立运行时世界线
```

### 初始化的三个层次

| 初始化层次 | 输入 | 输出 | 执行时机 | 是否直接修改 |
|---|---|---|---|---|
| 原著解析 | 小说 TXT、补充设定、作者资料 | 章节、场景、实体、关系、事件、原文证据 | 小说首次导入或源文件更新 | 原始数据不可变 |
| 世界模板初始化 | 结构化原著数据 | 时间线、因果图、世界规则、角色认知、剧情锚点、状态快照 | 每个小说版本执行 | 通过新版本更新 |
| 用户世界线初始化 | 世界模板、进入锚点、用户身份、快穿任务 | 用户独立 Timeline、根事件、宿主状态和初始任务 | 用户创建新世界线时 | 运行时持续修改 |

必须严格区分：

```text
canonical_*：原著不可变数据
runtime_*：用户介入后产生的可变数据
```

---

## 10.2 世界初始化总体流程

```text
1. TXT 文件导入
        ↓
2. 编码识别、清洗和原文版本固化
        ↓
3. 卷、章节、段落与场景切分
        ↓
4. 人物、地点、组织、物品、能力等实体抽取
        ↓
5. 别名合并、实体消歧和身份揭示建模
        ↓
6. 人物关系、组织关系和空间关系抽取
        ↓
7. 原著事件与状态变化抽取
        ↓
8. 叙述顺序和真实时间线还原
        ↓
9. 事件因果图、依赖图和知识传播图构建
        ↓
10. 角色画像、目标、认知、记忆和语言风格初始化
        ↓
11. 世界规则、能力体系和约束条件抽取
        ↓
12. 主线、支线、伏笔、结局和原著锚点构建
        ↓
13. 关键锚点前后完整世界状态快照生成
        ↓
14. 全文、向量、图谱和事件索引构建
        ↓
15. 自动一致性检查与人工审核
        ↓
16. 标准世界包发布
        ↓
17. 用户进入时加载快照并实例化独立世界线
```

---

## 10.3 初始化模块总表

| 模块 | 作用 | 主要功能 | 技术实现 | 核心算法/机制 | 主要输出 |
|---|---|---|---|---|---|
| 源文件接入器 | 接收小说及补充资料 | TXT上传、编码识别、哈希、版本登记 | FastAPI、Python、MinIO/S3 | MIME识别、SHA-256 | SourceDocument |
| 文本清洗器 | 去除非正文噪声并保留证据位置 | 广告、水印、异常空行、乱码和重复段落处理 | Python、Regex | 规则清洗、重复检测 | CleanText |
| 章节结构解析器 | 恢复卷章层级 | 卷、章、标题、段落编号 | Regex + LLM | 层次切分 | Chapter、Paragraph |
| 场景切分器 | 将章节拆为可执行场景 | 时间、地点、人物、视角和主题转场识别 | LLM、Embedding | 语义突变检测、边界分类 | Scene |
| 实体抽取器 | 识别世界中的对象 | 人物、地点、物品、组织、身份、技能、概念 | Structured LLM、NER | Entity Extraction | EntityCandidate |
| 实体消歧器 | 合并同一实体的多种称呼 | 姓名、别名、称谓、化名、未知身份合并 | PostgreSQL、pgvector、LLM | Entity Resolution | CanonicalEntity |
| 属性抽取器 | 构建实体静态与动态属性 | 外貌、身份、能力、境界、所有权、状态 | LLM + Pydantic | 属性归一化 | EntityAttribute |
| 关系抽取器 | 建立角色及组织间关系 | 亲属、主仆、敌对、恋爱、联盟、上下级 | LLM、图模型 | Relation Extraction | RelationEdge |
| 地点与空间图构建器 | 建立世界空间拓扑 | 地点层级、包含、相邻、可达、权限 | LLM + Graph | Scene Graph、Navigation Graph | LocationGraph |
| 事件抽取器 | 把情节转成结构化事件 | 参与者、行动、前置、结果、见证者、证据 | Structured LLM | Event Extraction | CanonicalEvent |
| 状态补丁生成器 | 将事件转换为状态变化 | 位置、生命、关系、知识、任务、剧情变化 | LLM + 规则 | StatePatch Induction | CanonicalStatePatch |
| 时间线构建器 | 还原世界真实发生顺序 | 倒叙、回忆、并行事件、时间跳跃处理 | LLM + 图算法 | Temporal Constraint Graph、拓扑排序 | CanonicalTimeline |
| 因果图构建器 | 建立事件的原因与依赖 | causes、enables、prevents、reveals、motivates | LLM + Graph | Causal Graph | CausalEdge |
| 知识传播构建器 | 确定角色何时知道什么 | 目击、听闻、阅读、推断、误解、传闻 | 事件图 + 规则 | Visibility Propagation | KnowledgeRecord |
| 角色画像生成器 | 初始化可运行角色模型 | 性格、目标、价值观、语言风格、能力 | LLM + 人工审核 | Character Profiling | CharacterTemplate |
| 角色认知初始化器 | 初始化每个锚点的角色认知 | 已知事实、信念、怀疑、秘密、错误认识 | Knowledge Store | Belief State Construction | CharacterBelief |
| 世界规则抽取器 | 提取并形式化世界规律 | 魔法、修炼、身份、政治、死亡、时间规则 | LLM + YAML DSL | Rule Induction | WorldRule |
| 剧情图构建器 | 建立主线、支线和结局结构 | 剧情阶段、转折、条件、伏笔和结局 | LLM + Graph Editor | Plot Graph | PlotArc、PlotNode |
| 原著锚点生成器 | 选择适合介入的关键节点 | 死亡、婚姻、战争、真相揭示、身份暴露 | LLM + 启发式评分 | Anchor Importance Scoring | CanonAnchor |
| 状态回放器 | 从原著事件计算任意时点状态 | 事件顺序重放、状态合并、衍生状态计算 | Python、PostgreSQL | Event Replay | CanonicalState |
| 状态快照生成器 | 固化关键节点的完整世界状态 | 锚点前后、卷首、重大变化后生成快照 | PostgreSQL JSONB | Snapshotting | CanonicalSnapshot |
| 原著索引器 | 支持运行时查找原文和设定 | 全文、向量、实体、事件和证据索引 | PostgreSQL FTS、pgvector | HNSW、RRF | SearchIndex |
| 质量验证器 | 检查世界包的结构和逻辑质量 | 时空、因果、身份、关系、认知和规则冲突 | Python + LLM Judge | Invariant Check | ValidationIssue |
| 人工审核工作台 | 供作者和策划修正初始化结果 | 时间线、图谱、角色、规则、锚点、快照审核 | React、React Flow | Human-in-the-loop | ApprovedWorld |
| 世界包生成器 | 发布标准化可运行世界模板 | Manifest、实体、事件、规则、快照、索引 | JSON/JSONL/YAML、数据库 | Schema Validation | WorldPackage |
| 世界线实例化器 | 创建用户专属世界副本 | 选择锚点、加载快照、注入身份和任务 | PostgreSQL、Event Store | Copy-on-write、Branching | RuntimeTimeline |

---

## 10.4 源文件接入与文本标准化

### 输入约束

世界初始化的主要输入是一份小说 TXT 文件，同时允许补充：

| 输入类型 | 作用 |
|---|---|
| 小说 TXT | 原著正文，是世界初始化的主要依据 |
| 人物表 | 作者或编辑补充标准人名、身份、别名 |
| 世界设定文档 | 补充正文未完整说明的世界规则 |
| 时间线文档 | 对复杂倒叙、多视角作品提供人工校正 |
| 地图与关系图 | 提供地点、组织和人物关系依据 |
| IP 配置 | 版权、版本、适用范围和内容限制 |

### 文本处理内容

| 处理项 | 实现方式 | 结果要求 |
|---|---|---|
| 编码识别 | `charset-normalizer`，UTF-8/GBK兜底 | 全文统一为 UTF-8 |
| 文件指纹 | SHA-256 | 保证同一源文件可追踪 |
| 广告和水印清理 | Regex、黑名单、重复模板识别 | 不误删正文 |
| 标点统一 | Unicode Normalize | 保留原文语义 |
| 异常空行处理 | 段落规则 | 一段一个稳定 ID |
| 重复章节检测 | Hash、MinHash | 标记重复，不静默覆盖 |
| 章节标题识别 | Regex优先、LLM兜底 | 恢复卷章层级 |
| 原文证据定位 | 字符偏移、段落ID、章节ID | 所有抽取结论可回溯 |

原始文件必须保留，不允许只保留清洗后的文本。

```json
{
  "document_id": "doc_001",
  "novel_id": "novel_001",
  "source_version": "1.0.0",
  "source_hash": "sha256:...",
  "encoding": "GB18030",
  "object_key": "novels/novel_001/source.txt",
  "status": "normalized"
}
```

---

## 10.5 章节、段落与场景切分

章节是出版结构，场景才是世界运行和事件抽取的基本单位。一个章节可能包含多个地点、多个时间段或多个叙述视角。

### 场景边界判断信号

| 信号 | 示例 |
|---|---|
| 时间变化 | “三日后”“当晚”“与此同时” |
| 地点变化 | 皇宫切换至丞相府 |
| 主要人物集合变化 | 原场景人物离开，新人物出现 |
| 叙述视角变化 | 女主视角切换至反派视角 |
| 显式转场 | 分隔符、空行、场景标题 |
| 事件主题变化 | 宴会对话切换为暗杀行动 |
| 长时间跳跃 | 回忆、梦境、前世片段 |

推荐采用三级切分：

```text
规则识别候选边界
    ↓
Embedding 检测语义突变
    ↓
LLM 根据相邻段落判断最终边界
```

场景输出：

```json
{
  "scene_id": "scene_001_03",
  "chapter_id": "chapter_001",
  "paragraph_start": "p_0032",
  "paragraph_end": "p_0058",
  "narrative_time": "当日晚间",
  "location_candidates": ["丞相府后院"],
  "participant_candidates": ["夜轻歌", "侍女秋月"],
  "viewpoint_character": "夜轻歌",
  "summary": "夜轻歌确认自己重生，并从侍女口中确认当前时间。"
}
```

---

## 10.6 实体抽取、消歧与身份演化

### 实体类型

| 类型 | 示例 |
|---|---|
| 人物 | 主角、配角、路人、未知人物 |
| 地点 | 国家、城市、宗门、房间、秘境 |
| 组织 | 皇室、家族、宗门、佣兵团 |
| 物品 | 武器、丹药、密信、神器 |
| 能力 | 技能、魔法、修炼功法、天赋 |
| 身份 | 皇帝、太子、三小姐、卧底 |
| 阵营 | 正派、反派、政治派系 |
| 世界概念 | 灵力、血脉、境界、系统积分 |
| 事件 | 战斗、死亡、婚礼、揭密 |
| 规则对象 | 契约、禁制、法律、宗门制度 |

### 实体消歧策略

```text
1. 标准名称和别名精确匹配
2. 同场景人物共现与身份约束
3. 模糊字符串匹配
4. Embedding 语义相似度
5. 全文上下文中的 LLM 判定
6. 人工审核低置信度候选
```

必须支持“暂时未知身份”与“后文揭示身份”，不能在前期场景中泄露后文真相。

```json
{
  "entity_id": "char_unknown_017",
  "display_name": "黑衣人",
  "entity_type": "character",
  "identity_status": "unresolved",
  "resolved_to": "char_villain_003",
  "resolution_event_id": "event_08321",
  "canonical_truth_visible_after_event": "event_08321"
}
```

### 实体结论证据

每个字段必须记录原文证据和置信度：

```json
{
  "entity_id": "char_001",
  "attribute": "identity",
  "value": "夜家三小姐",
  "confidence": 0.98,
  "evidence": [
    {
      "chapter_id": "chapter_001",
      "paragraph_id": "p_0102",
      "start_offset": 523,
      "end_offset": 541
    }
  ]
}
```

---

## 10.7 关系、空间、物品与组织初始化

### 人物关系采用多维状态

不能只保存“朋友”或“敌人”，应同时保存公开关系、私人关系和数值维度。

```json
{
  "source_id": "char_001",
  "target_id": "char_002",
  "public_relation": "主仆",
  "private_relation": "信任",
  "dimensions": {
    "affection": 0.35,
    "trust": 0.82,
    "fear": 0.12,
    "hostility": 0.05,
    "respect": 0.66,
    "debt": 0.20
  },
  "valid_from_event_id": "event_0008",
  "valid_to_event_id": null
}
```

关系数据由三部分组成：

```text
关系初始值
+ 原著关系变化事件
+ 任意剧情时点的关系快照
```

### 空间图

| 边类型 | 含义 |
|---|---|
| contains | 皇宫包含御花园 |
| adjacent_to | 东院与正厅相邻 |
| reachable_from | 密道可通往城外 |
| requires_permission | 内殿需要皇族权限 |
| hidden_connection | 只有特定角色知道的通道 |
| distance | 场景间估算移动成本 |

### 物品状态

物品需要保存：

```text
所有者
当前位置
数量
可见性
耐久或消耗
功能
使用条件
来源
唯一性
历史转移事件
```

### 组织与阵营

组织初始化包括：

```text
组织层级
成员与职位
阵营目标
内部派系
盟友与敌对关系
资源与领地
公开信息与秘密信息
```

---

## 10.8 原著事件与状态变化抽取

事件是小说从“文本知识库”升级为“可执行世界”的核心。

### 标准事件字段

| 字段 | 含义 |
|---|---|
| event_id | 原著事件唯一标识 |
| event_type | 对话、移动、战斗、死亡、发现、交易等 |
| actors | 主动实施者 |
| targets | 行动目标 |
| participants | 其他参与者 |
| location | 事件地点 |
| narrative_order | 小说中的讲述顺序 |
| chronological_order | 世界中的真实顺序 |
| preconditions | 事件能够发生的前置条件 |
| actions | 参与者做了什么 |
| effects | 对世界状态造成的变化 |
| witnesses | 直接见证者 |
| information_outputs | 事件产生的新事实 |
| downstream_events | 被该事件影响的后续事件 |
| evidence | 对应原文段落 |
| confidence | 抽取置信度 |

```json
{
  "event_id": "event_00124",
  "event_type": "assassination_attempt",
  "actor_ids": ["char_villain_01"],
  "target_ids": ["char_heroine_01"],
  "location_id": "loc_palace_03",
  "narrative_order": 930,
  "chronological_order": 124,
  "preconditions": [
    "villain_has_poison",
    "heroine_attends_banquet"
  ],
  "actions": ["poison_wine"],
  "effects": [
    {
      "operation": "set",
      "path": "characters.char_heroine_01.poisoned",
      "value": true
    }
  ],
  "witness_ids": ["char_maid_01"],
  "source_paragraph_ids": ["p_0891", "p_0892"]
}
```

### 状态补丁操作白名单

原著事件和运行时事件统一使用受控 StatePatch，不允许生成任意数据库操作。

```text
SetFlag
SetAttribute
IncrementValue
MoveCharacter
TransferItem
ChangeOwnership
UpdateRelation
UpdateBelief
UpdateEmotion
CreateEvent
ScheduleEvent
StartPlotArc
CompletePlotArc
StartTask
CompleteTask
ChangeIdentity
JoinFaction
LeaveFaction
CharacterDeath
CharacterRevival
```

`CharacterRevival` 等高风险操作必须受世界规则限制。

---

## 10.9 时间线还原

小说叙述顺序不等于世界时间顺序，必须分别保存：

```text
Narrative Order：原文讲述顺序
Chronological Order：事件真实发生顺序
```

### 时间线处理对象

| 情况 | 处理方式 |
|---|---|
| 明确日期 | 解析为世界历法时间 |
| 相对时间 | 建立 `after/before + offset` 约束 |
| “与此同时” | 标记为时间重叠或并行 |
| 回忆 | 叙述顺序靠后、真实顺序靠前 |
| 梦境和幻境 | 标记为非物理时间线 |
| 前世与今生 | 建立不同 Era/Timeline |
| 无法确定 | 保留候选顺序并进入审核 |

```json
{
  "event_id": "event_00124",
  "narrative_order": 930,
  "chronological_order": 124,
  "absolute_time": null,
  "relative_time": {
    "after_event_id": "event_00120",
    "offset": "3 days"
  },
  "temporal_confidence": 0.87
}
```

### 核心算法

```text
时间表达识别
+ 事件先后关系抽取
+ Temporal Constraint Graph
+ 拓扑排序
+ 冲突检测
```

发生矛盾时不应静默猜测：

```json
{
  "issue_type": "temporal_conflict",
  "events": ["event_101", "event_102"],
  "candidate_orders": [
    ["event_101", "event_102"],
    ["event_102", "event_101"]
  ],
  "requires_review": true
}
```

---

## 10.10 因果图与剧情偏离传播

时间先后并不等于因果关系。因果图负责解释某个原著事件为什么发生，以及用户改变事件后哪些后续节点必须失效或重算。

### 因果边类型

| 类型 | 含义 |
|---|---|
| causes | 直接造成后续事件 |
| enables | 提供必要条件 |
| prevents | 阻止另一事件 |
| motivates | 改变角色动机 |
| reveals | 揭示事实或身份 |
| depends_on | 依赖前置事件 |
| contradicts | 与另一事实冲突 |
| accelerates | 提前事件发生 |
| delays | 推迟事件发生 |
| substitutes | 可作为原著事件的替代路径 |

```json
{
  "cause_event_id": "event_00120",
  "effect_event_id": "event_00124",
  "relation_type": "enables",
  "strength": 0.91,
  "required": true,
  "evidence_ids": ["p_0870", "p_0891"]
}
```

### 偏离传播流程

```text
用户阻止原著事件
    ↓
将对应 Canon Event 标记为 prevented
    ↓
遍历 required causal edges
    ↓
失效依赖事件
    ↓
保留仍满足条件的事件
    ↓
为失效剧情生成替代事件候选
    ↓
规则验证与角色 Agent 推演
    ↓
形成新的运行时世界线
```

原著偏离度不能只使用单一百分比，建议分维度计算：

```text
事件偏离度
角色命运偏离度
关系网络偏离度
世界规则偏离度
主线结构偏离度
结局偏离度
```

---

## 10.11 角色画像、目标与认知初始化

角色模板必须区分静态属性、动态状态和认知状态。

### 静态角色档案

```json
{
  "character_id": "char_001",
  "name": "夜轻歌",
  "aliases": ["轻歌", "三小姐"],
  "identities": ["夜家三小姐"],
  "personality": {
    "cautious": 0.85,
    "decisive": 0.92,
    "compassionate": 0.55
  },
  "language_style": {
    "tone": "冷静简洁",
    "verbosity": "low",
    "preferred_expressions": []
  }
}
```

### 动态角色状态

```json
{
  "long_term_goals": ["复仇", "保护亲近之人"],
  "current_goals": ["调查前世死亡原因"],
  "current_plan": ["隐藏重生事实", "收集敌人信息"],
  "emotion": {
    "anger": 0.52,
    "fear": 0.18,
    "hope": 0.63
  }
}
```

### 世界真相与角色信念分离

```json
{
  "fact_id": "fact_villain_is_poisoner",
  "world_truth": true,
  "beliefs": [
    {
      "character_id": "char_heroine",
      "value": "unknown",
      "confidence": 0.0
    },
    {
      "character_id": "char_villain",
      "value": true,
      "confidence": 1.0
    }
  ]
}
```

### 角色知识来源

```text
亲眼目击
亲耳听闻
他人告知
阅读文档
角色推断
错误传闻
身份记忆
快穿系统授予
```

每条认知都需要记录来源和可见时间：

```json
{
  "character_id": "char_maid",
  "fact_id": "fact_wine_is_poisoned",
  "belief_value": "suspected_true",
  "confidence": 0.60,
  "source_type": "observation",
  "source_event_id": "event_00124",
  "valid_from_event_id": "event_00124"
}
```

用户改变过去后，只重新传播受影响的知识，不允许所有角色自动获得全局真相。

---

## 10.12 世界规则初始化

世界规则分为两类：

| 类型 | 含义 | 处理要求 |
|---|---|---|
| 显式规则 | 原著明确描述的规律 | 可直接抽取并引用证据 |
| 归纳规则 | 从多次事件中总结出的规律 | 必须保留置信度并人工审核 |

### 规则分类

```text
物理规则
修炼或魔法规则
能力克制规则
身份与权限规则
政治与组织规则
法律和伦理规则
死亡与复活规则
时间旅行规则
快穿系统规则
物品使用规则
剧情保护规则
```

规则结构：

```yaml
rule_id: rule_magic_001
category: cultivation
description: 灵力消耗后需要通过休息、功法或丹药恢复
priority: 80
conditions: []
constraints:
  - spiritual_power cannot increase without recovery_source
exceptions:
  - character_has_infinite_energy_artifact
confidence: 0.92
evidence_ids:
  - p_0211
  - p_0783
review_status: approved
```

规则执行应分成：

```text
自然语言规则说明
    ↓
结构化规则 Schema
    ↓
Python/DSL 可执行条件
    ↓
规则测试用例
```

LLM可以解释规则和生成候选规则，但不能成为唯一规则执行器。

---

## 10.13 剧情图、伏笔与原著锚点

### 剧情图包含

```text
主线 Plot Arc
支线 Subplot
剧情阶段 Phase
关键事件 Beat
伏笔 Thread
条件 Condition
替代路径 Alternative
结局 Ending
```

### 原著锚点

原著锚点是适合用户介入、状态快照和任务生成的关键剧情节点。

典型锚点：

```text
角色死亡前
退婚或婚礼前
身份揭示前
战争爆发前
关键误会形成前
世界灾难发生前
最终结局前
```

锚点结构：

```json
{
  "anchor_id": "anchor_004",
  "name": "女配死亡",
  "canonical_event_id": "event_0821",
  "importance": 0.94,
  "modifiable": true,
  "recommended_entry_offset": "-7 days",
  "downstream_dependency_count": 37,
  "checkpoint_before": "snapshot_004_before",
  "checkpoint_after": "snapshot_004_after"
}
```

### 锚点评分

```text
AnchorScore =
剧情重要性
+ 用户干预空间
+ 角色命运价值
+ 下游影响范围
+ 情绪价值
+ 可理解性
- 世界状态缺失风险
- 因果不确定性
```

锚点必须经过人工审核，否则可能选择用户无法实际干预的时间点。

---

## 10.14 原著状态回放与状态快照

原著文本是事件记录的来源，但用户进入世界时需要加载的是某一时点的完整世界状态。

状态计算方式：

```text
初始世界状态
+ 按时间顺序执行原著 Event 与 StatePatch
= 任意事件之后的 Canonical State
```

### 快照生成位置

| 快照位置 | 原因 |
|---|---|
| 故事开始 | 世界根状态 |
| 每一卷开始 | 加速大跨度进入 |
| 关键锚点之前 | 支持用户提前干预 |
| 关键锚点之后 | 支持从原著结果继续 |
| 重要角色死亡前后 | 状态差异大 |
| 阵营或世界规则重大改变后 | 防止长距离重放 |
| 固定事件间隔 | 控制加载成本 |

快照应覆盖：

```text
世界时间
地点和场景状态
人物位置
人物身体与能力状态
人物目标和计划
人物关系
人物知识与信念
组织与阵营
物品归属
主线与支线状态
世界规则激活状态
未完成事件和延迟事件
原著偏离状态（原著模板中为0）
```

```json
{
  "snapshot_id": "snapshot_anchor_004_before",
  "canonical_event_index": 820,
  "world_time": "天武历1024年五月初七",
  "physical_state": {},
  "character_states": {},
  "character_beliefs": {},
  "relations": {},
  "factions": {},
  "items": {},
  "plot_state": {},
  "active_rules": [],
  "pending_events": []
}
```

加载任意进入点时：

```text
最近的 Canonical Snapshot
+ 快照后至进入点的少量原著事件
= 进入点完整状态
```

---

## 10.15 原著知识库与索引初始化

世界初始化需要同时生成可检索知识，不应在运行时扫描整本小说。

| 索引 | 内容 | 技术 |
|---|---|---|
| 全文索引 | 原文段落、章节标题、人名、物品名 | PostgreSQL FTS / Elasticsearch |
| 向量索引 | 场景、事件、人物经历、世界设定 | pgvector HNSW |
| 实体索引 | 标准名、别名、身份、称谓 | PostgreSQL |
| 事件索引 | 参与者、地点、时间、类型 | PostgreSQL |
| 时间索引 | 世界时间和相对顺序 | B-tree |
| 因果索引 | 前因、后果和依赖路径 | 图式关系表 |
| 证据索引 | 抽取结论到原文位置 | PostgreSQL |
| 角色知识索引 | 每个角色可访问事实 | PostgreSQL + Metadata |

检索采用：

```text
实体与时间过滤
    ↓
全文检索 + 向量检索
    ↓
RRF 融合
    ↓
Cross-Encoder 重排
    ↓
原文证据与知识权限过滤
```

---

## 10.16 自动验证与人工审核

由于整本小说的实体消歧、时间线、因果关系和角色认知不可能完全依赖自动模型，世界初始化必须是“自动抽取 + 程序验证 + 人工审核”的组合流程。

### 自动检查

| 检查类型 | 检查内容 |
|---|---|
| Schema 检查 | 所有对象符合 JSON Schema |
| 引用完整性 | 事件引用的人物、地点和物品必须存在 |
| 时序检查 | 前置事件不能晚于结果事件 |
| 时空检查 | 人物不能在同一时间出现在冲突地点 |
| 身份检查 | 身份揭示前不能公开真实身份 |
| 所有权检查 | 唯一物品不能被同时持有 |
| 关系检查 | 关系变化需要对应事件 |
| 因果检查 | required 依赖不能形成非法循环 |
| 知识检查 | 角色知识必须存在合法来源 |
| 规则检查 | 结构化规则必须能被执行 |
| 快照检查 | 从快照回放后的状态必须一致 |
| 证据检查 | 关键结论必须绑定原文证据 |
| 重复检查 | 重复实体、事件和章节必须标记 |
| 原著覆盖检查 | 核心章节和关键角色不能被遗漏 |

### 人工审核优先级

```text
高优先级：
关键角色身份
角色死亡与复活
主线事件
剧情锚点
世界核心规则
全书时间线冲突
角色知识边界

中优先级：
人物关系变化
地点连接
物品所有权
支线因果关系

低优先级：
普通场景摘要
低影响路人实体
非关键语气和标签
```

人工修改必须保留：

```text
修改前值
修改后值
修改者
修改时间
修改原因
关联证据
```

---

## 10.17 世界包输出规范

初始化完成后发布标准化 `Novel World Package`。

```text
world_package/
├── manifest.json
├── source/
│   ├── novel.txt
│   └── source_metadata.json
├── structure/
│   ├── volumes.json
│   ├── chapters.jsonl
│   ├── paragraphs.jsonl
│   └── scenes.jsonl
├── entities/
│   ├── characters.jsonl
│   ├── locations.jsonl
│   ├── organizations.jsonl
│   ├── items.jsonl
│   ├── abilities.jsonl
│   └── aliases.jsonl
├── relations/
│   ├── character_relations.jsonl
│   ├── faction_relations.jsonl
│   └── spatial_edges.jsonl
├── events/
│   ├── canonical_events.jsonl
│   ├── state_patches.jsonl
│   ├── timeline_edges.jsonl
│   └── causal_edges.jsonl
├── characters/
│   ├── profiles.jsonl
│   ├── goals.jsonl
│   ├── beliefs.jsonl
│   └── language_styles.jsonl
├── plot/
│   ├── plot_graph.json
│   ├── plot_arcs.jsonl
│   ├── anchors.jsonl
│   ├── threads.jsonl
│   └── endings.jsonl
├── rules/
│   ├── world_rules.yaml
│   └── rule_tests.jsonl
├── snapshots/
│   ├── root_snapshot.json
│   └── anchor_snapshots/
├── knowledge/
│   ├── facts.jsonl
│   ├── knowledge_records.jsonl
│   └── evidence_links.jsonl
├── indexes/
│   ├── fulltext/
│   └── vector/
├── validation/
│   ├── validation_report.json
│   └── review_history.jsonl
└── assets/
    ├── portraits/
    ├── backgrounds/
    ├── audio/
    └── video/
```

`manifest.json`：

```json
{
  "world_id": "world_001",
  "title": "示例小说世界",
  "world_version": "1.0.0",
  "schema_version": "world-schema-v1",
  "source_hash": "sha256:...",
  "character_count": 126,
  "location_count": 74,
  "event_count": 8432,
  "anchor_count": 28,
  "snapshot_count": 61,
  "rule_count": 94,
  "validation_status": "approved"
}
```

---

## 10.18 用户世界线实例化

世界包发布后，用户进入小说时不再重新处理 TXT，而是从世界模板实例化运行时世界线。

### 实例化流程

```text
用户选择小说
    ↓
选择快穿任务或剧情锚点
    ↓
读取锚点对应 Canonical Snapshot
    ↓
回放快照至精确进入时刻之间的原著事件
    ↓
创建 Runtime Timeline
    ↓
绑定宿主身份或创建外来身份
    ↓
注入快穿系统能力、限制与任务
    ↓
配置用户拥有的原著知识范围
    ↓
初始化用户和角色关系
    ↓
写入 player_arrived 根事件
    ↓
创建第一个运行时状态快照
    ↓
启动世界运行引擎
```

### 世界线实例

```json
{
  "timeline_id": "timeline_user_1038_001",
  "world_id": "world_001",
  "world_version": "1.0.0",
  "parent_type": "canonical",
  "parent_snapshot_id": "snapshot_anchor_004_before",
  "entry_anchor_id": "anchor_004",
  "current_version": 1,
  "canon_divergence": {
    "event": 0.0,
    "character_fate": 0.0,
    "relationship": 0.0,
    "plot": 0.0
  }
}
```

### 宿主身份注入

| 进入方式 | 初始化内容 |
|---|---|
| 穿成原著角色 | 继承身体、身份、社会关系和宿主记忆 |
| 创建新角色 | 分配合理身份、地点、资源和社会关系 |
| 灵魂附身 | 区分宿主记忆与用户自身认知 |
| 系统投放 | 可设置“外来者”身份和世界排斥机制 |
| 多身份切换 | 保存多个宿主实例和切换限制 |

```json
{
  "user_id": "user_1038",
  "host_character_id": "char_cannon_fodder_07",
  "identity_visibility": "hidden",
  "host_memory_policy": "partial_inheritance",
  "canonical_knowledge_policy": "plot_summary_until_anchor_010",
  "system_level": 1,
  "inventory": ["system_token"]
}
```

### 原著知识权限

用户对原著的了解必须显式配置：

```text
知道整本原著
只知道当前任务摘要
只拥有宿主记忆
记忆模糊且会逐渐恢复
只知道某些关键人物结局
随着世界偏离，未来原著知识逐渐失效
```

### 根事件

```json
{
  "event_id": "runtime_event_000001",
  "timeline_id": "timeline_user_1038_001",
  "event_type": "player_arrived",
  "actor_id": "user_1038",
  "host_character_id": "char_cannon_fodder_07",
  "entry_anchor_id": "anchor_004",
  "effects": [
    "timeline_created",
    "player_identity_bound",
    "system_task_started"
  ]
}
```

运行时状态采用 Copy-on-write：

```text
原著世界模板保持不变
用户世界线只保存相对模板发生的运行时事件和状态差异
```

---

## 10.19 推荐数据表

| 表名 | 主要用途 |
|---|---|
| novels | 小说和 IP 基础信息 |
| novel_source_versions | 原始 TXT 版本和哈希 |
| volumes | 卷结构 |
| chapters | 章节结构 |
| paragraphs | 原文段落和字符位置 |
| scenes | 场景切分结果 |
| entities | 统一实体主表 |
| entity_aliases | 别名、称谓和化名 |
| characters | 人物基础档案 |
| character_profiles | 性格、语言风格和背景 |
| character_goals | 长期、短期目标和计划 |
| character_beliefs | 角色认知和信念 |
| locations | 地点及层级 |
| spatial_edges | 地点可达和空间关系 |
| items | 物品及属性 |
| organizations | 组织与阵营 |
| canonical_relations | 原著人物和组织关系 |
| canonical_events | 原著事件 |
| canonical_event_participants | 事件参与者 |
| canonical_state_patches | 原著事件状态补丁 |
| canonical_timeline_edges | 时间约束 |
| canonical_causal_edges | 因果依赖 |
| knowledge_facts | 世界事实 |
| knowledge_records | 角色知识来源和置信度 |
| world_rules | 世界规则 |
| rule_evidence | 规则原文证据 |
| plot_arcs | 主线与支线 |
| plot_nodes | 剧情节点 |
| plot_threads | 伏笔和未完成线索 |
| canon_anchors | 原著关键锚点 |
| canonical_snapshots | 原著状态快照 |
| world_packages | 已发布世界包 |
| initialization_jobs | 初始化任务 |
| initialization_issues | 自动验证问题 |
| review_records | 人工审核记录 |
| runtime_timelines | 用户世界线 |
| runtime_events | 用户世界线事件 |
| runtime_snapshots | 用户状态快照 |
| player_roles | 用户宿主和身份 |
| system_tasks | 快穿任务 |
| memory_chunks | 原著和运行时记忆索引 |

---

## 10.20 初始化任务编排与工程实现

长篇小说的初始化是长周期批处理任务，不能由一个同步 API 请求完成。

### 任务图

```text
source_ingest
    ↓
normalize_text
    ↓
chapter_parse
    ↓
scene_segment
    ↓
并行执行：
    ├── entity_extract
    ├── event_extract
    ├── location_extract
    └── relation_extract
    ↓
entity_resolution
    ↓
timeline_build
    ↓
causal_graph_build
    ↓
character_initialize
    ↓
rule_compile
    ↓
plot_graph_build
    ↓
anchor_generate
    ↓
canonical_replay
    ↓
snapshot_generate
    ↓
index_build
    ↓
validate
    ↓
human_review
    ↓
publish_world_package
```

### 技术分工

| 技术 | 作用 |
|---|---|
| FastAPI | 创建、查询和控制初始化任务 |
| Temporal | 编排长周期、可恢复的初始化工作流 |
| Celery | 执行可并行、相对独立的抽取任务 |
| Redis/RabbitMQ | 任务队列和进度消息 |
| PostgreSQL | 保存结构化中间结果和任务状态 |
| MinIO/S3 | 保存源文件、世界包和大规模中间产物 |
| LangGraph | 需要多轮推理、修正和人工确认的局部 Agent 流程 |
| Model Gateway | 路由不同模型并记录成本、版本和失败 |
| OpenTelemetry | 追踪每个初始化阶段耗时 |
| Prometheus/Grafana | 监控任务吞吐、失败率和成本 |

### 长篇小说处理策略

```text
章节级局部抽取
    ↓
卷级实体与关系合并
    ↓
全书级实体消歧
    ↓
全书级时间线和因果图
    ↓
关键角色和关键剧情专项校正
    ↓
全局一致性检查
```

禁止采用：

```text
整本 TXT 一次性输入长上下文模型
→ 直接要求输出完整世界
```

原因包括人物遗漏、身份泄露、时间线冲突、因果幻觉、证据丢失和无法增量修正。

---

## 10.21 增量更新与版本管理

当小说 TXT 更换版本、增加番外或人工修正世界模板时，不能重新覆盖旧世界。

### 版本对象

```text
Source Version：原始小说文件版本
Extraction Version：抽取流水线和模型版本
World Version：审核后发布的世界包版本
Runtime Timeline Version：用户世界线事件版本
```

### 增量更新流程

```text
检测源文件差异
    ↓
识别受影响章节和段落
    ↓
重新执行局部场景、实体和事件抽取
    ↓
计算受影响实体、时间线和因果子图
    ↓
重建相关快照与索引
    ↓
运行回归验证
    ↓
发布新的 World Version
```

已有用户世界线默认绑定创建时的世界版本，不应被新版本静默修改。需要迁移时必须执行显式迁移策略。

---

## 10.22 初始化质量指标与验收标准

| 指标 | 含义 |
|---|---|
| 章节识别准确率 | 卷章结构是否正确 |
| 场景边界准确率 | 时间、地点和人物转场是否正确 |
| 核心实体召回率 | 主要人物、地点、物品是否遗漏 |
| 实体消歧准确率 | 别名和未知身份是否正确合并 |
| 关键事件召回率 | 主线和关键支线事件是否覆盖 |
| 事件字段准确率 | 参与者、地点、结果是否正确 |
| 时间线冲突率 | 事件顺序中无法解决的冲突比例 |
| 因果边准确率 | required 因果依赖是否可靠 |
| 角色知识越权率 | 角色是否提前知道未来或秘密 |
| 世界规则可执行率 | 抽取规则是否能转成程序约束 |
| 锚点可干预率 | 锚点之前是否存在真实行动空间 |
| 快照回放一致率 | 从快照和事件回放得到的状态是否一致 |
| 原文证据覆盖率 | 关键结论是否有可定位证据 |
| 人工修正率 | 自动结果需要人工修改的比例 |
| 世界包发布通过率 | 是否通过全部强制验证 |
| 单本书初始化成本 | 模型、计算和人工审核总成本 |
| 新版本增量处理比例 | 更新时无需重算的数据占比 |

关键发布门槛建议：

```text
所有关键人物均完成身份和别名审核
所有主线事件均有原文证据
所有剧情锚点通过人工审核
required 因果边无未解决冲突
核心世界规则均可执行
快照回放结果一致
角色知识不存在严重未来泄露
```

---

## 10.23 世界初始化端到端结果

一本 TXT 小说完成初始化后，应产生三类成果。

### 原著知识层

```text
章节与场景
实体与别名
人物关系
原著事件
时间线
因果图
原文证据
全文和向量索引
```

### 可执行世界模板层

```text
角色状态与目标
角色认知边界
世界规则
剧情图
任务条件
关键锚点
原著状态快照
状态补丁与回放逻辑
```

### 运行时实例模板层

```text
用户可选进入点
宿主身份方案
快穿任务模板
用户原著知识权限
初始关系配置
世界线根事件
原著偏离计算规则
```

最终公式：

```text
TXT 小说
→ 结构化原著知识
→ 可执行世界模板
→ 关键剧情状态快照
→ 用户专属运行时世界线
```

世界初始化模块的核心目标不是生成一份小说摘要，而是：

> 将一本静态、线性、非结构化的小说，编译成具有显式状态、可执行规则、角色认知、事件因果、剧情锚点和可分支时间线的运行时世界。


---

# 11. 外部模型能力层

| 模型能力 | 主要任务 | 技术要求 | 路由策略 |
|---|---|---|---|
| 小型语言模型 | 分类、抽取、重写、简单 NPC | 低延迟、低成本 | 高频任务优先 |
| 强推理语言模型 | 状态推演、规则解释、关键剧情 | 高推理、稳定 JSON | 关键节点调用 |
| 长上下文模型 | 原著理解、世界编译、长轨迹审查 | 长上下文、中文能力 | 离线或低频调用 |
| 角色对话模型 | 对白、人物风格、情绪表达 | 角色一致性 | 按角色批处理 |
| Embedding 模型 | 原著与记忆向量化 | 中文语义、长文本 | 批量离线 |
| Reranker | 检索结果重排 | Cross-Encoder | 检索后调用 |
| ASR 模型 | 用户语音转文本 | 流式识别、中文准确率 | 实时调用 |
| TTS 模型 | 角色语音输出 | 多音色、情绪、韵律 | 异步或流式 |
| 图像生成模型 | 立绘、背景、CG | 角色一致性、姿态控制 | 缓存与复用 |
| 视频生成模型 | 过场和连续画面 | 时序一致性、动作控制 | 高价值节点 |
| 内容审核模型 | 文本、图像、语音审核 | 多模态安全 | 输入输出双检 |
| 评分模型 | 评价剧情和角色一致性 | 稳定判别 | 离线评估或重生成 |

统一通过 `Model Gateway` 调用：

```text
业务模块
    ↓
Model Gateway
    ├── 模型路由
    ├── 结构化输出
    ├── 超时与重试
    ├── 限流
    ├── 缓存
    ├── 成本统计
    ├── 降级策略
    └── 多供应商切换
```

---

# 12. 数据层

| 数据模块 | 保存内容 | 技术选型 | 关键机制 |
|---|---|---|---|
| 关系型数据库 | 用户、角色、世界、任务、关系、事件 | PostgreSQL | ACID、索引、分区 |
| JSON 状态库 | 不同世界特有的灵活字段 | PostgreSQL JSONB | GIN 索引 |
| 向量存储 | 原著片段、角色记忆、事件摘要 | pgvector | HNSW、相似度检索 |
| 全文检索 | 人名、物品、章节、事件关键词 | PostgreSQL FTS/Elasticsearch | BM25 |
| 缓存 | 会话、热点状态、限流、临时结果 | Redis | TTL、Hash、Sorted Set |
| 事件流 | 异步任务和系统事件 | Redis Streams/Kafka | Consumer Group |
| 对象存储 | 图片、音频、视频、模型资源 | MinIO/S3 | 版本、哈希、CDN |
| 日志存储 | 系统日志、模型日志、审计日志 | Loki/ELK | 索引、保留策略 |
| 分析仓库 | 用户行为、留存、剧情路径 | ClickHouse/BigQuery | OLAP |
| 备份系统 | 状态、事件、媒体和配置备份 | PostgreSQL Backup、S3 | 增量备份、灾备 |

---

# 13. 中间件与工作流层

| 模块 | 作用 | 主要功能 | 技术实现 |
|---|---|---|---|
| 异步任务队列 | 执行耗时任务 | 图片、语音、视频、批量抽取 | Celery + Redis/RabbitMQ |
| 事件总线 | 在模块间传播事件 | 状态变化、任务完成、角色唤醒 | Redis Streams/Kafka |
| 延迟任务调度 | 在未来触发世界事件 | 倒计时、期限、冷却、约会 | Temporal、定时队列 |
| 长周期工作流 | 管理跨小时或跨天流程 | 世界编译、批量生成、恢复 | Temporal |
| Agent 编排 | 编排一次交互内的推理流程 | 分支、循环、重试、人审 | LangGraph |
| 分布式锁 | 防止同一世界并发写入 | 世界线锁、任务锁 | Redis Lock |
| 配置中心 | 管理规则、模型和环境配置 | 动态切换、灰度配置 | Apollo/Consul/环境变量 |
| 服务发现 | 支持多服务部署 | 服务注册、健康检查 | Consul/Kubernetes |
| CDN | 加速媒体资源访问 | 图片、音频、视频 | Cloud CDN |

---

# 14. 管理与创作工具层

| 工具 | 作用 | 主要功能 | 技术实现 |
|---|---|---|---|
| 世界编辑器 | 编辑世界全局结构 | 时间线、地点、阵营、规则 | React、Graph Editor |
| 角色编辑器 | 编辑角色模型 | 身份、性格、目标、语言风格 | JSON Schema Form |
| 人物关系编辑器 | 编辑关系网络 | 关系类型、多维数值、秘密 | Cytoscape.js/React Flow |
| 剧情图编辑器 | 编辑主线、支线和条件 | 节点、分支、前置、结果 | React Flow |
| 规则编辑器 | 配置世界规则 | 条件、效果、优先级、冲突 | YAML/DSL Editor |
| 任务编辑器 | 创建系统任务和世界任务 | 条件、进度、奖励、失败 | Form Builder |
| 场景编辑器 | 绑定场景和表现资源 | 背景、角色位置、音乐、特效 | 2D/3D Editor |
| Prompt 管理器 | 管理模型提示词 | 版本、实验、灰度、回滚 | Prompt Registry |
| 模型路由后台 | 配置不同任务模型 | 模型、价格、超时、优先级 | Admin Dashboard |
| 内容审核后台 | 审核违规或高风险内容 | 人工复核、封禁、申诉 | Moderation Console |
| 世界质量面板 | 查看世界运行问题 | 冲突、遗忘、角色越权、重复 | Analytics Dashboard |
| 用户运营后台 | 运营活动和内容推荐 | 世界推荐、任务活动、奖励 | CRM/Admin |
| 资产管理器 | 管理图片、语音和视频 | 标签、版本、版权、复用 | DAM System |

---

# 15. 测试与评估层

| 测试模块 | 作用 | 技术实现 | 核心指标 |
|---|---|---|---|
| 单元测试 | 测试规则和状态函数 | pytest | 规则覆盖率 |
| 属性测试 | 自动生成极端输入 | Hypothesis | 不变量违反率 |
| API 测试 | 测试接口正确性 | pytest + httpx | 成功率、错误码 |
| 端到端测试 | 测试完整用户流程 | Playwright | 流程完成率 |
| 压力测试 | 测试并发和延迟 | Locust/k6 | P95 延迟、吞吐 |
| 状态回放测试 | 验证事件可重放 | Event Replay | 状态一致率 |
| 轨迹级测试 | 验证几十轮后的长期一致性 | 场景测试集 | 事实保持率 |
| 角色一致性评估 | 检查人物是否稳定 | LLM Judge + 人工 | 人设一致率 |
| 知识越权评估 | 检查角色是否知道不应知道的信息 | 自动规则 | 越权率 |
| 因果一致性评估 | 检查事件因果是否合理 | Event Graph | 因果冲突率 |
| 原著对齐评估 | 检查未偏离部分是否忠于原著 | Canon Graph | 锚点保持率 |
| 剧情多样性评估 | 检查分支是否真正不同 | Embedding/统计 | 分支差异度 |
| 检索评估 | 测试记忆召回质量 | Recall@K、MRR | 召回率 |
| 模型回归测试 | 模型升级后防止质量下降 | Eval Dataset | 胜率、错误率 |
| 成本评估 | 控制单轮交互成本 | Token/媒体统计 | 单轮成本 |

重点轨迹示例：

```text
第 1 轮：用户救下原著中必死角色
第 10 轮：相关角色更新认知和关系
第 30 轮：反派调整后续计划
第 60 轮：系统仍承认角色存活且世界线已偏离
```

---

# 16. 可观测性层

| 模块 | 作用 | 技术实现 | 监控内容 |
|---|---|---|---|
| 分布式追踪 | 追踪一次行动经过的完整链路 | OpenTelemetry | 每阶段耗时 |
| 指标监控 | 监控系统和业务指标 | Prometheus | QPS、延迟、错误率 |
| 可视化面板 | 展示系统运行状况 | Grafana | 服务、模型、成本 |
| 日志系统 | 保存运行和错误信息 | Loki/ELK | 异常、调用记录 |
| 模型调用监控 | 统计模型性能和成本 | 自定义 Gateway Metrics | Token、价格、失败率 |
| 世界质量监控 | 监控剧情运行质量 | 自定义指标 | 冲突率、遗忘率 |
| 用户体验监控 | 监控交互体验 | 前端埋点 | 首字延迟、完成率 |
| 告警系统 | 自动通知故障 | Alertmanager | 服务不可用、成本异常 |

一次交互 Trace 示例：

```text
action.parse              320 ms
memory.retrieve           110 ms
rule.check                 20 ms
world.transition         2100 ms
npc.react                1450 ms
director.generate        1800 ms
consistency.check         480 ms
state.commit               35 ms
render.prepare            120 ms
```

---

# 17. 安全与合规层

| 模块 | 作用 | 技术实现 | 主要措施 |
|---|---|---|---|
| 身份与权限 | 控制用户、作者、运营、管理员权限 | RBAC/ABAC | 最小权限 |
| 数据隔离 | 防止用户访问他人世界线 | PostgreSQL 行级校验 | Tenant ID |
| 内容安全 | 审核输入和生成内容 | 规则 + 多模态审核模型 | 输入输出双检 |
| Prompt 注入防护 | 防止用户绕过系统规则 | 指令分层、工具白名单 | 不信任用户输入 |
| 工具调用安全 | 限制 Agent 可调用的操作 | Function Schema | 操作白名单 |
| 状态修改安全 | 防止 LLM 直接修改数据库 | StatePatch 验证 | 事务提交 |
| 隐私保护 | 保护用户数据和对话 | 加密、脱敏、最小收集 | 数据生命周期 |
| 审计日志 | 记录敏感操作 | Append-only Log | 可追溯 |
| 版权管理 | 管理小说 IP 和生成资产 | 授权记录、内容指纹 | 权利校验 |
| 内容水印 | 标记 AI 生成内容 | 图片/视频水印 | 可识别性 |
| 风险角色保护 | 防止危险内容失控 | 世界规则、审核策略 | 场景级限制 |
| 灾备与恢复 | 防止数据丢失 | 备份、跨区存储 | RPO/RTO |

---

# 18. 部署与运维层

| 模块 | 作用 | 技术实现 |
|---|---|---|
| 容器化 | 统一运行环境 | Docker |
| 容器编排 | 管理大规模服务 | Kubernetes |
| 反向代理 | 流量入口和负载均衡 | Nginx/Ingress |
| CI/CD | 自动测试、构建和部署 | GitHub Actions/GitLab CI |
| 基础设施即代码 | 自动管理云资源 | Terraform |
| 数据库迁移 | 管理数据库版本 | Alembic |
| 灰度发布 | 小范围发布新模型和功能 | Feature Flag、Canary |
| 自动扩缩容 | 根据流量调整实例 | HPA |
| GPU 调度 | 管理本地模型推理资源 | Kubernetes GPU Operator |
| CDN | 加速静态资源 | Cloud CDN |
| 备份恢复 | 数据库和对象存储灾备 | PITR、S3 Replication |
| 多环境管理 | 开发、测试、预发、生产隔离 | Namespace、配置中心 |

---

# 19. DevOps 与代码工程

| 模块 | 作用 | 推荐技术 |
|---|---|---|
| 代码管理 | 版本控制与协作 | Git、GitHub/GitLab |
| Monorepo | 管理前端、后端和共享 Schema | Turborepo/Nx |
| Python 依赖 | 管理后端依赖 | uv/Poetry |
| 前端依赖 | 管理前端依赖 | pnpm |
| API Schema | 前后端共享接口 | OpenAPI、JSON Schema |
| 类型生成 | 自动生成客户端类型 | openapi-typescript |
| 代码质量 | 格式化和静态检查 | Ruff、mypy、ESLint、Prettier |
| 数据库迁移 | 表结构版本管理 | Alembic |
| Feature Flag | 控制功能灰度 | Unleash/LaunchDarkly |
| Secret 管理 | 管理密钥和凭证 | Vault/Cloud Secret Manager |
| 制品管理 | 保存镜像和构建产物 | Container Registry |
| 文档 | 保存架构和接口文档 | Markdown、MkDocs、OpenAPI |

---

# 20. 推荐完整技术栈

## 20.1 前端与客户端

```text
Web：Next.js + React + TypeScript
状态管理：Zustand
UI：Tailwind CSS + shadcn/ui
2D：PixiJS / Phaser
3D：Unity / Unreal Engine / Three.js
移动端：Flutter 或 React Native
桌面端：Tauri
动态角色：Live2D
音频：Web Audio API / FMOD / Wwise
```

## 20.2 后端与核心引擎

```text
语言：Python
API：FastAPI
数据校验：Pydantic
ORM：SQLAlchemy
迁移：Alembic
Agent 编排：LangGraph
规则引擎：Python + YAML DSL
长工作流：Temporal
异步任务：Celery
```

## 20.3 数据与检索

```text
权威状态：PostgreSQL
灵活状态：PostgreSQL JSONB
向量检索：pgvector
全文检索：PostgreSQL FTS / Elasticsearch
缓存：Redis
事件流：Redis Streams / Kafka
对象存储：MinIO / S3
分析仓库：ClickHouse
```

## 20.4 AI 与多模态

```text
LLM：多模型路由
结构化输出：JSON Schema / Function Calling
Embedding：中文向量模型
Reranker：Cross-Encoder
ASR：Whisper 类模型
TTS：多音色情绪语音模型
图像生成：角色一致性图像模型
视频生成：可控视频模型
视频世界模型：状态条件交互视频生成
内容审核：多模态审核模型
```

## 20.5 部署与观测

```text
容器：Docker
编排：Kubernetes
网关：Nginx / Kong
CI/CD：GitHub Actions
基础设施：Terraform
追踪：OpenTelemetry
指标：Prometheus
面板：Grafana
日志：Loki / ELK
```

---

# 21. 核心数据实体

| 实体 | 主要内容 |
|---|---|
| User | 用户账户、权限、资产、快穿等级 |
| Novel/IP | 小说、版权、版本、作者 |
| World | 世界设定、规则、时间线、状态 |
| Timeline | 用户个人世界线和分支 |
| Character | 角色身份、属性、人格、目标 |
| CharacterBelief | 角色认知、误解、怀疑和秘密 |
| CharacterRelation | 好感、信任、恐惧、敌意等 |
| Location | 地点、层级、可达关系 |
| Item | 物品、属性、所有权、状态 |
| Faction | 阵营、组织、成员和关系 |
| PlotArc | 主线、支线、剧情阶段 |
| Task | 任务目标、条件、奖励和失败 |
| Rule | 世界规则、前置条件和效果 |
| Event | 已发生的不可变世界事件 |
| StateSnapshot | 某版本完整世界状态 |
| Memory | 原著片段、角色经历、对话和总结 |
| Asset | 图片、语音、音乐、动画、视频 |
| PromptVersion | 模型提示词及版本 |
| ModelInvocation | 模型调用、耗时、Token 和费用 |

---

# 22. 完整运行流程

```text
1. 用户输入自由行动
        ↓
2. 接入层鉴权、限流和输入审核
        ↓
3. 行动理解器输出结构化 Action
        ↓
4. 实体链接与歧义处理
        ↓
5. 读取当前世界状态和角色可见知识
        ↓
6. 规则检查器验证行动是否可行
        ↓
7. 世界状态转移模型生成候选 StatePatch
        ↓
8. 数值与概率结算器确定实际结果
        ↓
9. 相关角色 Agent 生成反应和后续行动
        ↓
10. 世界导演处理场景外和长期世界变化
        ↓
11. 剧情导演决定叙述焦点、节奏和信息揭示
        ↓
12. 一致性审查器检查状态、因果、人设和认知
        ↓
13. 状态提交器写入事件日志并更新快照
        ↓
14. 渲染指令生成器输出文字、立绘、语音和镜头指令
        ↓
15. 多模态呈现层向用户展示结果
        ↓
16. 异步生成图片、语音、动画或视频
        ↓
17. 记录日志、指标、模型成本和质量数据
```

---

# 23. 项目真正的核心技术壁垒

| 核心能力 | 技术价值 |
|---|---|
| 小说世界结构化编译 | 将非结构化小说转化为可执行世界 |
| 显式叙事世界状态 | 让剧情变化可验证、可编辑、可追踪 |
| 规则约束状态转移 | 防止 LLM 任意续写和破坏世界逻辑 |
| 角色认知隔离 | 让角色只依据自身知识行动 |
| 长期角色记忆 | 保持几十轮甚至数百轮人物连续性 |
| 可回放事件溯源 | 支持调试、回滚和世界线分支 |
| 原著对齐与偏离建模 | 同时支持忠于原著和自由改写 |
| 事件驱动多 Agent 演化 | 控制成本并模拟自主角色反应 |
| 多模态状态渲染 | 同一世界状态可输出文字、2D、3D和视频 |
| 世界包标准 | 让新小说能够低成本接入同一引擎 |

---

# 24. 总结

完整 AI 快穿系统并不是单一的大语言模型应用，而是一套由以下部分组成的 **叙事世界操作系统**：

```text
小说世界编译器
+ 显式世界状态
+ 规则引擎
+ 状态转移模型
+ 角色 Agent
+ 剧情导演
+ 长期记忆
+ 事件溯源
+ 多模态表现
+ 创作与运营工具
```

基础工程采用主流的 Web、数据库、云原生和 AI 技术；系统创新集中在：

```text
非结构化小说 → 可执行世界
用户行动 → 可验证状态变化
角色认知 → 自主行为
状态历史 → 平行世界线
结构化世界 → 多模态呈现
```
