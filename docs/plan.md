> **文档定位**
>
> 本文是项目最终目标与原始产品级架构蓝图，不代表当前每项基础设施都已启用。
> 截至 2026-07-27，实际 Beta 基线为 **FastAPI + Vue 3 + SQLite + Qdrant
> Local Mode + 独立编译 Worker**，不要求 Docker、PostgreSQL、Redis 或
> Temporal。当前完成度和下一步以 [`实现进度.md`](./实现进度.md) 为准。

可以把完整系统拆成两条并行主线：

[
\boxed{
\text{小说世界初始化与编译}
\quad+\quad
\text{运行时叙事世界引擎}
}
]

最重要的开发顺序不是先接大模型、做3D场景或解析整本小说，而是：

> **先定义世界数据标准，手工构造一个小世界验证运行时，再让AI自动把TXT编译成同样的数据格式。**

否则世界编译器没有明确输出目标，运行引擎也不知道应该消费什么数据。

---

# 一、最终采用的技术基线

结合你要做的第三人称、分场景、半开放式 **3D AI快穿RPG**，建议固定为：

| 部分         | 技术                                   |
| ---------- | ------------------------------------ |
| 3D游戏客户端    | Unity 6.3 LTS + URP + C#             |
| Web创作与管理后台 | Next.js + TypeScript + React         |
| 后端API      | Python + FastAPI + Pydantic          |
| 数据库        | PostgreSQL + JSONB                   |
| 向量检索       | pgvector                             |
| 缓存与短期队列    | Redis                                |
| Agent编排    | LangGraph                            |
| 长工作流       | Temporal                             |
| 异步生成       | Celery Worker                        |
| 对象存储       | MinIO / S3                           |
| 部署         | Docker Compose → Kubernetes          |
| 监控         | OpenTelemetry + Prometheus + Grafana |

Unity 6.3是当前LTS版本，官方支持到2027年12月；虽然Unity 6.5已经是更新的Supported版本，但你的项目适合固定在6.3 LTS，避免频繁升级。Unity可以发布到Web端，不过浏览器平台存在托管C#线程、文件系统和性能方面的限制，所以LLM、世界推演和小说编译必须放在服务器端，Unity只负责输入、渲染和本地表现。([Unity][1])

---

# 二、先确定三个系统边界

## 1. Unity客户端负责什么

```text
玩家移动
场景加载
角色动画
镜头控制
交互对象
对话与任务UI
音频和特效
向服务器发送Action
表现服务器返回的结果
```

Unity不能成为世界事实的唯一保存位置。

## 2. AI世界引擎负责什么

```text
理解用户行动
检查行动合法性
结算世界变化
驱动角色Agent
维护人物关系
维护角色认知
推进剧情
保存世界事件
生成表现指令
```

## 3. 小说世界编译器负责什么

```text
TXT清洗
章节和场景切分
实体与关系抽取
事件抽取
时间线和因果图
角色画像
世界规则
剧情锚点
状态快照
世界包发布
```

最终形成：

```text
TXT小说
   ↓ World Compiler
WorldPackage
   ↓ Runtime Engine
用户世界线
   ↓ Render Instructions
Unity客户端
```

---

# 三、第一步：先定义统一数据协议

这是整个项目真正的起点。

不要先开发角色Agent，而是先定义下面六个核心Schema。

## 1. Action

表示玩家或NPC想做什么。

```json
{
  "action_id": "action_001",
  "action_type": "swap_object",
  "actor_id": "player",
  "target_ids": ["wine_cup_01"],
  "parameters": {
    "replacement_item_id": "antidote_cup",
    "method": "create_distraction",
    "visibility": "covert"
  },
  "declared_goal": "prevent_poisoning"
}
```

## 2. WorldState

表示当前客观世界事实。

```json
{
  "timeline_id": "timeline_001",
  "version": 128,
  "world_time": "庆元十二年冬月初八亥时",
  "current_scene_id": "palace_banquet",
  "characters": {},
  "items": {},
  "relations": {},
  "plot": {},
  "active_rules": [],
  "pending_events": []
}
```

## 3. StatePatch

表示本轮允许发生的状态变化。

```json
{
  "operations": [
    {
      "op": "set_flag",
      "path": "plot.poisoning_prevented",
      "value": true
    },
    {
      "op": "increment_relation",
      "source_id": "maid",
      "target_id": "player",
      "dimension": "suspicion",
      "value": 0.2
    }
  ]
}
```

## 4. WorldEvent

表示已经正式发生的事实。

```json
{
  "event_id": "event_129",
  "event_type": "cup_swapped",
  "actor_ids": ["player"],
  "target_ids": ["wine_cup_01"],
  "preconditions": [
    "player_near_cup",
    "cup_accessible"
  ],
  "state_patch": {},
  "random_seed": 981273,
  "previous_version": 128,
  "new_version": 129
}
```

## 5. CharacterBelief

表示角色认为什么是真的。

```json
{
  "character_id": "maid",
  "fact_id": "fact_player_swapped_cup",
  "belief": "suspected_true",
  "confidence": 0.62,
  "source_type": "observation",
  "source_event_id": "event_129"
}
```

## 6. RenderInstruction

表示Unity该如何呈现已经发生的事实。

```json
{
  "scene_id": "palace_banquet",
  "camera": {
    "shot": "medium_closeup",
    "target_id": "maid"
  },
  "animations": [
    {
      "character_id": "maid",
      "animation": "look_suspicious"
    }
  ],
  "dialogues": [],
  "audio": {
    "bgm": "banquet_tension",
    "sfx": ["cup_collision"]
  }
}
```

这些Schema建议放到独立目录：

```text
packages/world-schema/
├── action.schema.json
├── state.schema.json
├── state_patch.schema.json
├── event.schema.json
├── belief.schema.json
├── render_instruction.schema.json
└── world_package.schema.json
```

Python侧使用Pydantic，Unity侧生成对应C# DTO。

---

# 四、第二步：手工制作一个测试世界包

不要立即处理整本小说。

先手工创建一个极小但完整的世界：

```text
1个宫宴场景
4个角色
5个物品
8条世界规则
1个原著剧情锚点
10—20个原著事件
1个状态快照
```

例如：

```text
角色：
- 用户宿主
- 女主
- 侍女
- 反派

目标：
- 阻止女主中毒

可发生结果：
- 成功且无人发现
- 成功但侍女怀疑
- 失败并暴露身份
- 失败但获得新线索
```

手工世界包目录：

```text
examples/palace-banquet/
├── manifest.json
├── characters.json
├── locations.json
├── items.json
├── relations.json
├── rules.yaml
├── canonical_events.jsonl
├── beliefs.jsonl
├── plot_graph.json
├── anchors.json
└── snapshots/
    └── banquet_before.json
```

### 本阶段验收标准

不使用LLM也能够：

1. 加载世界快照；
2. 提交结构化Action；
3. 检查前置条件；
4. 生成StatePatch；
5. 提交WorldEvent；
6. 更新世界状态；
7. 回放并恢复相同结果；
8. 从旧版本创建世界线分支。

只有这一步完成，后续AI才有可靠地基。

---

# 五、第三步：搭建后端基础工程

推荐仓库结构：

```text
ai-transmigration/
├── apps/
│   ├── unity-client/
│   └── admin-web/
├── services/
│   ├── api/
│   ├── world-compiler/
│   ├── ai-worker/
│   └── media-worker/
├── engine/
│   ├── action/
│   ├── rules/
│   ├── runtime/
│   ├── agents/
│   ├── narrative/
│   ├── consistency/
│   ├── memory/
│   └── event_store/
├── packages/
│   ├── world-schema/
│   ├── prompts/
│   └── model-gateway/
├── worlds/
│   └── palace-banquet/
├── tests/
│   ├── unit/
│   ├── scenarios/
│   ├── trajectories/
│   └── replay/
├── infra/
│   ├── docker-compose.yml
│   ├── nginx/
│   └── migrations/
└── docs/
```

## Docker Compose先启动这些服务

```text
PostgreSQL
Redis
MinIO
FastAPI
AI Worker
Admin Web
```

暂时不要先上Kubernetes。

## 后端模块结构

```text
services/api/app/
├── main.py
├── api/
│   ├── worlds.py
│   ├── timelines.py
│   ├── actions.py
│   ├── characters.py
│   └── compiler.py
├── models/
├── schemas/
├── repositories/
├── services/
└── dependencies/
```

FastAPI适合这里的原因是Python模型生态集成方便、基于类型标注生成API文档，并原生支持WebSocket；剧情文字流可以使用SSE，实时多人或Unity状态同步再使用WebSocket。([FastAPI][2])

---

# 六、第四步：实现权威状态与事件存储

先建核心数据表。

```text
worlds
world_versions
canonical_events
canonical_snapshots

runtime_timelines
runtime_events
runtime_snapshots

characters
character_beliefs
character_relations
locations
items
world_rules
plot_arcs
tasks
```

## 状态保存策略

固定、需要查询的字段放普通列：

```text
character_id
timeline_id
location_id
is_alive
world_time
version
```

不同小说差异很大的属性放JSONB：

```json
{
  "cultivation_level": "筑基后期",
  "bloodline": "凤凰血脉",
  "curse": null
}
```

PostgreSQL的JSONB支持字段访问、修改和索引，适合“固定核心字段+不同小说自定义字段”的状态结构。([PostgreSQL][3])

## 每次提交事务

```text
读取当前版本
→ 检查Action
→ 生成StatePatch
→ 验证StatePatch
→ 插入WorldEvent
→ 更新Snapshot
→ version + 1
→ 提交事务
```

必须使用乐观锁：

```sql
UPDATE runtime_snapshots
SET state = :new_state,
    version = version + 1
WHERE timeline_id = :timeline_id
  AND version = :expected_version;
```

更新行数为0时，说明发生并发冲突，必须重新计算。

---

# 七、第五步：实现确定性规则引擎

一开始不要用LLM决定成功还是失败。

规则引擎需要实现：

```text
时空约束
物品约束
身份权限
能力约束
状态不变量
剧情前置条件
角色知识边界
任务完成条件
```

规则定义：

```yaml
id: swap_poisoned_cup
priority: 50

preconditions:
  - eq: [actor.location_id, target.location_id]
  - contains: [actor.inventory, antidote]
  - lt: [world.banquet_stage, toast_started]
  - eq: [target.accessible, true]

effects:
  - set:
      path: plot.poisoning_prevented
      value: true
  - increment:
      path: relations.maid.player.suspicion
      value: 0.2
```

## 规则引擎接口

```python
class RuleEngine:
    def validate(
        self,
        state: WorldState,
        action: Action,
    ) -> RuleCheckResult:
        ...

    def apply(
        self,
        state: WorldState,
        patch: StatePatch,
    ) -> WorldState:
        ...
```

### 本阶段验收标准

自动测试必须验证：

* 死亡人物不能正常行动；
* 不在同一场景不能交换物品；
* 用户没有解药时不能使用解药；
* 角色不能知道未见事件；
* 同一唯一物品不能同时属于两个人；
* 回放相同事件得到相同状态。

---

# 八、第六步：实现一次完整Turn Pipeline

完成以下最小闭环：

```text
用户输入
   ↓
Action Parser
   ↓
Entity Linking
   ↓
Rule Check
   ↓
State Transition
   ↓
Result Settlement
   ↓
NPC Reaction
   ↓
Director
   ↓
Consistency Check
   ↓
Event Commit
   ↓
Render Instruction
```

伪代码：

```python
async def process_turn(
    timeline_id: str,
    user_text: str,
) -> TurnResult:
    state = await state_repository.load(timeline_id)

    action = await action_parser.parse(
        user_text=user_text,
        state=state,
    )

    rule_result = rule_engine.validate(state, action)
    if not rule_result.allowed:
        return build_rejected_result(rule_result)

    candidate_patch = await transition_model.propose(
        state=state,
        action=action,
        constraints=rule_result.constraints,
    )

    validated_patch = patch_validator.validate(
        state=state,
        patch=candidate_patch,
    )

    settled_patch = result_settler.settle(
        patch=validated_patch,
        seed=create_seed(timeline_id, state.version),
    )

    character_actions = await character_scheduler.react(
        state=state,
        trigger_patch=settled_patch,
    )

    combined_patch = patch_merger.merge(
        settled_patch,
        character_actions,
    )

    narrative_plan = await director.plan(
        state=state,
        patch=combined_patch,
    )

    consistency_checker.check(
        state=state,
        patch=combined_patch,
        narrative_plan=narrative_plan,
    )

    event, new_state = await event_store.commit(
        state=state,
        action=action,
        patch=combined_patch,
    )

    render_instruction = await renderer.plan(
        event=event,
        state=new_state,
        narrative_plan=narrative_plan,
    )

    return TurnResult(
        event=event,
        state=new_state,
        render=render_instruction,
    )
```

---

# 九、第七步：接入LLM，但只开放受控权限

LLM首先用于三个任务。

## 1. Action Parser

```text
自然语言
→ Action JSON
```

## 2. State Transition Proposal

```text
当前状态 + Action + 规则
→ 候选StatePatch
```

## 3. Narrative Generation

```text
已提交事件 + 角色状态
→ 剧情文字和对白
```

必须坚持：

```text
LLM可以提出Action
LLM可以提出StatePatch
LLM可以写表现文本

LLM不能直接执行SQL
LLM不能绕过规则
LLM不能直接修改权威状态
```

建立统一的Model Gateway：

```python
class ModelGateway:
    async def generate_structured(
        self,
        task: str,
        messages: list[dict],
        output_schema: type,
    ):
        ...

    async def embed(self, texts: list[str]):
        ...

    async def rerank(self, query: str, documents: list[str]):
        ...
```

记录每次调用的：

```text
任务类型
模型名称
Prompt版本
输入输出Token
延迟
费用
成功状态
Schema错误
```

---

# 十、第八步：实现角色Agent

不要让所有NPC一直运行。

使用事件驱动调度：

```text
当前场景中的角色
+ 被事件直接影响的角色
+ 有未完成计划的角色
+ 被延迟事件唤醒的角色
```

每个角色需要：

```text
身份 Profile
人格 Traits
目标 Goals
信念 Beliefs
情绪 Emotion
关系 Relations
记忆 Memory
当前计划 Plan
```

角色决策顺序：

```text
读取角色可见事实
→ 检索角色相关记忆
→ 生成候选动作
→ 规则过滤
→ Utility评分
→ 选择动作
→ 生成对白
→ 更新角色认知
```

推荐先自己写普通Python流程，复杂后再接LangGraph。LangGraph适合有分支、循环、持久化、流式输出和人工介入的Agent工作流，并可以使用PostgreSQL检查点持久化。([Docs by LangChain][4])

---

# 十一、第九步：实现长期记忆

记忆分成四类：

| 类型       | 存储             |
| -------- | -------------- |
| 世界客观事实   | PostgreSQL     |
| 角色认知事实   | PostgreSQL     |
| 原著和历史片段  | pgvector + FTS |
| 当前场景工作记忆 | Redis          |

检索流程：

```text
角色权限过滤
→ 人名、事件、时间元数据过滤
→ PostgreSQL全文检索
→ pgvector向量召回
→ RRF融合
→ Reranker重排
→ 返回有限记忆
```

pgvector支持HNSW和IVFFlat；对这一项目优先使用HNSW，它通常具有更好的速度—召回权衡，但构建更慢、占用内存更多。([GitHub][5])

记住：

> “角色是否死亡”不能只存在向量库；向量库只负责找回描述，PostgreSQL才保存权威事实。

---

# 十二、第十步：实现小说世界初始化器

运行引擎稳定后，再开始自动编译TXT。

## 初始化流水线

```text
TXT导入
→ 文本清洗
→ 章节切分
→ 场景切分
→ 实体抽取
→ 实体消歧
→ 关系抽取
→ 事件抽取
→ StatePatch抽取
→ 时间线构建
→ 因果图构建
→ 角色认知构建
→ 世界规则抽取
→ 剧情锚点生成
→ 原著事件回放
→ 状态快照生成
→ 自动验证
→ 人工审核
→ 发布WorldPackage
```

## 正确的开发顺序

不要一开始就实现全书编译，按以下顺序：

### A. 单场景编译

输入：

```text
一个场景的2—5千字
```

输出：

```text
人物
地点
物品
关系
事件
StatePatch
角色知识变化
```

### B. 单章节编译

解决：

```text
多个场景
实体别名
事件顺序
章节内状态变化
```

### C. 单卷编译

解决：

```text
跨章关系
角色目标演化
时间跳跃
伏笔
```

### D. 全书编译

解决：

```text
全局实体消歧
倒叙
多时间线
因果图
关键锚点
状态快照
```

每条抽取结果必须绑定原文段落ID和置信度。

---

# 十三、第十一步：实现世界初始化工作流

整本小说初始化可能持续数十分钟甚至数小时，必须是异步、可恢复工作流。

分工建议：

```text
Temporal
负责完整世界编译工作流、失败恢复、阶段状态

Celery
负责章节级、场景级并行抽取任务

LangGraph
负责需要多轮AI推理和人工确认的局部任务
```

Temporal通过持久化事件历史保持工作流进度，服务崩溃或网络失败后可以从已记录位置恢复，适合世界编译、批量媒体生成和跨时间剧情任务。([Temporal 文档][6])

---

# 十四、第十二步：开发Unity客户端

Unity客户端第一条完整流程只需要一个3D场景。

## Unity目录

```text
Assets/
├── Scripts/
│   ├── Core/
│   ├── Network/
│   ├── World/
│   ├── Characters/
│   ├── Interaction/
│   ├── Dialogue/
│   ├── Tasks/
│   └── Rendering/
├── Prefabs/
├── Scenes/
├── Animations/
├── Addressables/
└── ScriptableObjects/
```

## 关键Unity组件

| 组件                        | 功能         |
| ------------------------- | ---------- |
| WorldSessionManager       | 当前世界线和版本   |
| ApiClient                 | 与FastAPI通信 |
| ActionController          | 收集玩家操作     |
| InteractionDetector       | 检测可交互对象    |
| CharacterView             | 表现角色状态     |
| DialogueController        | 展示对白       |
| RenderInstructionExecutor | 执行服务器表现指令  |
| TimelineSaveUI            | 存档、回档、分支   |
| SceneLoader               | 场景切换       |
| AssetLoader               | 按需加载资源     |

## Unity与服务器通信

玩家点击酒杯：

```text
Unity本地检测点击
→ 展示“正在行动”
→ 请求服务器提交Action
→ 接收阶段进度
→ 接收最终WorldEvent
→ 执行RenderInstruction
→ 更新本地只读状态镜像
```

Unity本地状态只是显示缓存，服务器状态才是权威。

---

# 十五、第十三步：开发创作者后台

小说世界初始化不可能完全自动化，因此必须做编辑后台。

需要至少六个页面：

```text
1. 小说导入与编译任务
2. 角色与别名审核
3. 事件时间线编辑器
4. 因果图与剧情图编辑器
5. 世界规则编辑器
6. 锚点与状态快照审核
```

使用：

```text
Next.js
React Flow
JSON Schema Form
Monaco Editor
```

后台必须能够点击某条事件直接跳回原文证据。

---

# 十六、第十四步：添加多模态能力

按顺序接入：

```text
文字
→ TTS
→ 静态立绘和CG
→ 动态角色
→ 动画短片
→ 状态条件视频世界模型
```

视频世界模型必须位于表现层：

```text
已提交WorldState
→ 场景描述与状态条件
→ 视频世界模型
→ 用户看到的连续画面
```

它不能替代权威状态引擎。

---

# 十七、第十五步：建立测试体系

你的项目最重要的不是单轮文本是否好看，而是长轨迹是否稳定。

## 必须建立的测试

### 规则测试

```text
给定状态 + Action
→ 是否允许
→ StatePatch是否正确
```

### 回放测试

```text
Snapshot + Events
→ 是否恢复相同State Hash
```

### 角色知识测试

```text
角色未见事件
→ 不得在对白中提及
```

### 长轨迹测试

```text
第1轮：救下原著必死角色
第10轮：相关角色调整计划
第30轮：反派改变策略
第60轮：该角色仍然存活
```

### 世界编译回归测试

```text
同一TXT + 同一流水线版本
→ 核心人物和锚点不能无故变化
```

核心指标：

```text
规则违反率
事实保持率
角色知识越权率
事件回放一致率
人设一致率
关键事件召回率
世界编译人工修正率
单轮交互延迟
单轮模型成本
```

---

# 十八、推荐开发时间顺序

按个人项目估算，完整产品级系统通常需要长期迭代；你可以先用约16周做出一条高质量端到端主线，再持续扩展。

| 时间      | 开发重点                                   | 可交付结果    |
| ------- | -------------------------------------- | -------- |
| 第1—2周   | Schema、数据库、仓库、Docker                   | 世界数据标准   |
| 第3—4周   | 事件存储、规则引擎、状态回放                         | 无LLM世界引擎 |
| 第5—6周   | Action Parser、StatePatch、Turn Pipeline | AI交互闭环   |
| 第7—8周   | 角色Agent、认知、关系、长期记忆                     | 多角色演化    |
| 第9—10周  | 单场景和章节世界编译                             | TXT局部初始化 |
| 第11—12周 | Unity 3D客户端、服务通信                       | 可游玩3D场景  |
| 第13—14周 | 全书时间线、锚点、快照、审核后台                       | 小说世界包    |
| 第15—16周 | 轨迹测试、部署、演示、文档                          | 完整作品展示   |

其中最容易低估的是：

```text
实体消歧
角色知识边界
时间线还原
因果图
状态快照
长轨迹测试
```

这些应当比“让多个Agent对话”优先。

---

# 十九、你现在应该立即做的第一批任务

按照顺序创建以下内容：

```text
1. 新建Git仓库和目录结构
2. 编写WorldPackage v1数据规范
3. 编写Action、StatePatch、Event、Belief、Snapshot Schema
4. 启动PostgreSQL、Redis、MinIO和FastAPI
5. 创建palace-banquet手工测试世界
6. 实现WorldState加载
7. 实现规则检查
8. 实现StatePatch白名单
9. 实现事件事务提交
10. 实现状态回放测试
11. 实现POST /timelines/{id}/actions
12. 再接入第一个LLM Action Parser
```

第一个阶段的完成标志不是“AI生成了一段小说”，而是：

> 用户提交一个行动后，系统能够根据规则产生可验证的世界变化，保存事件，重新加载后状态完全一致，并且角色只知道自己应该知道的信息。

这条闭环完成后，后续的小说编译、多Agent、Unity 3D和视频世界模型才有稳定的承载基础。

[1]: https://unity.com/releases/unity-6/support?utm_source=chatgpt.com "Unity 6 Releases & Support: LTS & Updates Releases"
[2]: https://fastapi.tiangolo.com/advanced/websockets/?utm_source=chatgpt.com "WebSockets"
[3]: https://www.postgresql.org/docs/current/datatype-json.html?utm_source=chatgpt.com "Documentation: 18: 8.14. JSON Types"
[4]: https://docs.langchain.com/oss/python/langgraph/overview?utm_source=chatgpt.com "LangGraph overview - Docs by LangChain"
[5]: https://github.com/pgvector/pgvector?utm_source=chatgpt.com "pgvector/pgvector: Open-source vector similarity search for ..."
[6]: https://docs.temporal.io/temporal?utm_source=chatgpt.com "What is Temporal? | Temporal Platform Documentation"
