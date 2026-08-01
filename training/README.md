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

## 参数化场景与 split smoke

三个原创世界族：

- `secret_transport`：密信、证据传播与联盟；
- `resource_negotiation`：稀缺资源、沟通与交付；
- `rescue_escort`：跨地点取药、移动与救援交付。

生成 10 variants × 5 seeds × 3 families 的小规模 split，并将整个 `rescue_escort` 世界族封存为 Test-OOD：

```powershell
.\.venv\Scripts\python.exe -m training.build_split `
  --output training\manifests\scenario-split-smoke-v1.json `
  --audit-output training\manifests\scenario-split-smoke-v1.audit.json
```

当前 smoke manifest 共 150 个场景：Train 70、Dev 10、Test-ID 20、Test-OOD 50。content hash、variant 和 world package 跨 split 重叠必须为 0；实体和规则 ID 重叠作为诊断矩阵报告，因为同一世界族会有意复用公共游戏本体。

## 正式确定性专家数据 v1

正式 manifest 使用 12 variants × 20 seeds × 3 families，共 720 个参数化场景。每个场景采集三条轨迹：

- `scripted_expert`：标准专家路线；
- `safe_heuristic`：语义不同但合法的替代路线；
- `controlled_recovery`：一次被 Gate 拒绝的提议，随后读取结构化 feedback 并完成恢复。

复现命令：

```powershell
.\.venv\Scripts\python.exe -m training.collect_dataset `
  --manifest training\manifests\scenario-split-v1.json `
  --output-dir data\trajectories\novelsim-planner-expert-v1 `
  --report-dir training\reports\novelsim-planner-expert-v1 `
  --code-commit f6f9f20
```

实测结果：2,160 episodes、9,120 decision steps；受控恢复 720 episodes（33.33%）；目标成功与回放一致均为 2,160/2,160；illegal proposal 720（全部来自预期的首步拒绝），illegal commit 0。Train/Dev/Test-ID/Test-OOD 分别为 1,080/120/240/720 episodes，其中 Test-ID 与 Test-OOD 在数据卡中显式封存。

完整 JSONL/Parquet 默认写入 `data/trajectories/` 并由 Git 忽略，仓库只提交 manifest、泄漏审计、数据卡与文件 SHA-256。PromptedLLM 来源尚未并入这份确定性专家数据，必须单独运行、单独标记真实模型与 Token，并通过同一 verifier 后才能合并。

## 两类安全指标

- `illegal_proposal`：Planner 提议被 Schema、实体、能力、Affordance、知识或 Patch Gate 拒绝；
- `illegal_commit`：非法效果实际形成已提交事件；完整 Runtime 中必须始终为 `0`。

合法失败步骤没有 `committed_event`，且 `previous_state_hash == next_state_hash`。成功步骤必须有匹配的 `ToolResult.committed_event_id` 和 `WorldEvent`。

## 内容哈希

`content_hash` 用于数据去重与跨 split 泄漏审计。它覆盖世界、观察、决策语义、工具结果、事件、奖励和失败标签，但排除 run ID、trace 时间、延迟、随机 call/decision ID 等易变遥测，因此相同语义轨迹重复采集仍得到相同哈希。
