# NovelSim 结构化评测：novelsim-secret-letter-objective-v1

- Run ID：`evaluation_824921617298808b`
- 生成时间：`2026-07-29T17:03:06.242028+00:00`
- 固定案例：9
- 实际运行：9
- 确定性运行：是
- 套件门禁：PASS

## 核心指标

| 指标 | 结果 |
|---|---:|
| 案例期望通过率 | 100.00% |
| 目标完成率（全部路线） | 33.33% |
| 核心事件完成率 | 100.00% |
| 工具执行成功率 | 100.00% |
| 规则预期拒绝率 | 100.00% |
| 事件回放一致率 | 100.00% |
| 信息传播准确率 | 100.00% |
| 证据链完整率 | 100.00% |
| 结构化角色一致率 | 100.00% |
| 联盟形成率 | 100.00% |

## 安全与失败

| 指标 | 数量 |
|---|---:|
| 非法 Patch 提交 | 0 |
| 未知实体被接受 | 0 |
| 因果越权提交 | 0 |
| 认知泄漏提交 | 0 |
| 非预期拒绝 | 0 |
| 无效循环 | 0 |

失败类型：`cognitive_boundary`=1、`precondition_failed`=1、`target_not_found`=1

## 延迟与模型用量

| 指标 | 结果 |
|---|---:|
| 运行延迟 P50 | 17.712 ms |
| 运行延迟 P95 | 47.622 ms |
| 模型调用 | 0 |
| 失败模型调用 | 0 |
| 输入 Token | 0 |
| 输出 Token | 0 |
| 总 Token | 0 |
| 估算成本 | 未配置单价 |

## 案例明细

| Case | Mode | 结局/状态 | 工具 | 版本 | 结果 |
|---|---|---|---:|---:|---|
| `free_defenders_allied` | free | `defenders_allied` | 5 | 0→5 | PASS |
| `script_defenders_allied` | script | `defenders_allied` | 5 | 0→5 | PASS |
| `player_destroys_letter` | free | `letter_destroyed` | 2 | 0→2 | PASS |
| `player_intercepts_letter` | free | `player_intercepted` | 2 | 0→2 | PASS |
| `player_exposes_truth` | free | `truth_exposed` | 5 | 0→5 | PASS |
| `bounded_turn_limit` | free | `turn_limit` | 2 | 0→2 | PASS |
| `reject_alliance_without_evidence` | free | `tool_failed` | 1 | 0→0 | PASS |
| `reject_unknown_entity` | free | `tool_failed` | 1 | 0→0 | PASS |
| `reject_knowledge_leak` | free | `tool_failed` | 1 | 0→0 | PASS |

## Free / Script 配对基线

| 比较 | 配对数 | 终态相同 | 工具链相同 | Free P50 | Script P50 |
|---|---:|---:|---:|---:|---:|
| `free_vs_script_canonical` | 1 | 100.00% | 100.00% | 32.517 ms | 51.540 ms |

## 消融结果

所有禁用门禁的结果均为隔离反事实，不写入权威存档。

| 世界门禁组 | 启用门禁数 | 违规拒绝率 | 违规被接受 |
|---|---:|---:|---:|
| G0：仅提示词 | 0 | 0.00% | 6 |
| G1：结构化世界规则 | 2 | 33.33% | 4 |
| G2：执行门禁 | 5 | 83.33% | 1 |
| G3：完整闭环 | 6 | 100.00% | 0 |

| 记忆组 | Hit@4 | MRR | nDCG@4 | P95 |
|---|---:|---:|---:|---:|
| `no_memory` | 0.00% | 0.000 | 0.000 | 0.001 ms |
| `sqlite_fts5` | 84.00% | 0.805 | 0.814 | 6.016 ms |

## 验收检查

| 检查 | 实测 | 门槛 | 本套件要求 | 结果 |
|---|---:|---:|---|---|
| `illegal_patch_commits` | 0 | 0 | 是 | PASS |
| `unknown_entity_accepts` | 0 | 0 | 是 | PASS |
| `causal_violation_commits` | 0 | 0 | 是 | PASS |
| `knowledge_leaks` | 0 | 0 | 是 | PASS |
| `replay_consistency` | 100.00% | >=100% | 是 | PASS |
| `benchmark_expectations` | 100.00% | >=100% | 是 | PASS |
| `core_event_completion` | 100.00% | >=80% | 是 | PASS |
| `tool_execution_success` | 100.00% | >=95% | 是 | PASS |
| `expected_rejections` | 100.00% | >=100% | 是 | PASS |
| `propagation_accuracy` | 100.00% | >=100% | 是 | PASS |
| `evidence_chain_completeness` | 100.00% | >=100% | 是 | PASS |
| `narrative_event_grounding` | not measured | 100% | 否 | 未测 |
| `real_llm_regression_runs` | 0 | >=20 | 否 | 未测 |
| `free_vs_script_canonical_authoritative_equivalence` | state=100.00%, chain=100.00% | state=100%, chain=100% | 是 | PASS |
| `ablation_isolation` | temporary/in-memory | no authoritative store | 是 | PASS |
| `full_guard_violation_accepts` | 0 | 0 | 是 | PASS |
| `memory_ablation_effect` | Hit 0.000->0.840, MRR 0.000->0.805 | enabled > no_memory | 是 | PASS |

> 未测项目不会被确定性套件伪装为通过；真实 LLM 局数与叙事依据率由后续真实模型/Pairwise 套件补充。
