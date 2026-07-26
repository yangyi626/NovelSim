# 编译器 C 阶段与创作者发布流

## 编译器 C：跨章节演化

单场景 A、单章节 B 负责抽取实体、关系、事件和规则。C 阶段在多个章节之间继续维护三类生命周期。

### 角色状态

`character_states` 记录会延续到后续章节的状态，例如身份变化、伤势、处境和主导情绪。

编译结果写入：

- `Character.attrs.compiled_state`：最新跨章状态；
- `Character.attrs.chapter_states`：带章节、证据和置信度的历史；
- `CharacterPsyche.emotion`：最新主导情绪；
- `identity_tags`：新增且去重的身份标签。

### 伏笔

`foreshadows` 使用稳定标题识别同一伏笔，生命周期为：

```text
planted → reinforced → resolved
```

伏笔编译为 `PlotArc(kind="foreshadow")`，保存首次出现章节、强化章节、回收章节、相关实体、证据和 payoff hint。

### 目标演化

`goal_evolutions` 使用 `character_id + goal_key` 生成稳定目标 ID，状态为：

```text
active / achieved / abandoned / superseded
```

同一目标跨章节幂等更新，并在 `AgentGoal.evolution` 中保留章节级证据历史。

### 编译入口

`compile_novel` 已统一使用 `VolumeCompiler`。编译多个章节时，WorldPackage manifest 会包含：

```json
{
  "compiler": {
    "stage": "C",
    "source_chapters": [1, 2, 3],
    "chapter_summaries": [],
    "character_state_updates": 0,
    "foreshadow_updates": 0,
    "goal_updates": 0,
    "warnings": []
  }
}
```

## 创作者审核发布流

可编辑世界包具有以下状态机：

```text
draft → pending_review → approved → published
             └────────→ rejected
rejected → draft / pending_review
published → draft
```

每次保存内容或变更审核状态都会创建新修订。修改已经批准或发布的世界包会自动回到 `draft`，避免未经复审的内容继续显示为已发布。

修订文件存放在 `worlds/.history/`，属于运行数据，不进入 Git。

创作者 API：

- `GET /api/creator/packages/{id}/revisions`
- `GET /api/creator/packages/{id}/diff?from_revision=1&to_revision=2`
- `POST /api/creator/packages/{id}/review`

创作台已经提供：

- SVG 人物关系图；
- 最近修订列表；
- 前后修订结构化差异；
- 提交审核、批准、驳回、发布和退回修改操作。

当前审核流是单机工作流状态机，尚未加入账户、角色权限和审核人身份。生产多用户版本需要再增加 RBAC 和审计主体。
