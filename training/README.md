# NovelSim V2 轨迹数据流水线

Phase 1B 将现有权威 Runtime 的每个决策步记录为：

```text
GameObservation
→ PlannerDecision
→ Schema / World Gate
→ ToolResult + AgentTrace
→ committed WorldEvent（失败时为空）
→ next state hash
→ RewardBreakdown + FailureAttribution
```

JSONL 是自包含、可回放的 episode 权威格式；Parquet 是每个决策步一行的训练/分析格式。两种格式写入前都会重放全部 WorldEvent 并核对最终状态 hash。

## 导出当前确定性基准

```powershell
.\.venv\Scripts\python.exe -m training.export_trajectories `
  --output tmp\v2-phase1b\secret-letter-v1.jsonl `
  --parquet-output tmp\v2-phase1b\secret-letter-v1.parquet
```

安装 Parquet 可选依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[training]"
```

当前命令只导出已完成的 `secret_letter_v1` 确定性基准，用于验证合同，不是正式训练集。Phase 2 冻结 scenario-family split 和泄漏审计前，不允许批量生成 SFT 数据。

## 两类安全指标

- `illegal_proposal`：Planner 提议被 Schema、实体、能力、Affordance、知识或 Patch Gate 拒绝；
- `illegal_commit`：非法效果实际形成已提交事件；完整 Runtime 中必须始终为 `0`。

合法失败步骤没有 `committed_event`，且 `previous_state_hash == next_state_hash`。成功步骤必须有匹配的 `ToolResult.committed_event_id` 和 `WorldEvent`。

## 内容哈希

`content_hash` 用于数据去重与跨 split 泄漏审计。它覆盖世界、观察、决策语义、工具结果、事件、奖励和失败标签，但排除 run ID、trace 时间、延迟、随机 call/decision ID 等易变遥测，因此相同语义轨迹重复采集仍得到相同哈希。

