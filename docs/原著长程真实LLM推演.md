# 原著长程片段真实 LLM 推演

## 目标

验证 NovelSim 能否只从原著检查点、角色认知、人格目标和世界规则出发，使用真实
LLM 规划并执行一个长程剧情片段，再与隐藏的原著事件对齐。原著未来事件不得进入
规划 Prompt；否则测到的是答案复述，不是世界模拟。

首个案例使用《第一狂妃：废柴三小姐》第 1 章结束状态作为上下文检查点，隐藏并
评测第 2--5 章的 10 个关键事件。原文只保存在本地；仓库保存源文件指纹、场景证据
哈希、脱敏事件锚点和运行轨迹。

## 方法

```text
第1章检查点
  → 按当前场景选择剧情驱动角色
  → 为每个角色构造私有 GameObservation
  → 真实 LLM 分别生成 1--3 步受限工具链
  → 组装 JointPlan
  → Schema / 权限 / 实体 / 前置条件校验
  → 多角色执行并原子提交 WorldEvent
  → 对话写入接收者工作记忆
  → 依赖变化或执行失败触发局部重规划
  → 达到片段终态
  → 隐藏 CanonicalEvent 对齐与状态回放
```

真实规划路径禁止 Scripted Policy 回退。API、网络、JSON Schema 或工具错误都会保留
为失败；每次调用记录模型、Prompt 版本、响应 ID、原始响应 SHA-256、Token、延迟和
是否回退。

## 关键实现

- `engine/narrative_planner.py`：角色隔离的真实 LLM 剧情节拍规划器与修复器；
- `engine/joint_plan.py`：联合计划、等待、失效/死锁检测、局部替换和受控执行；
- `engine/agent_tools.py`：受限工具；新增 `take_item` 表达原著中的强制取物，要求
  同场、目标持有、物品可访问且实体 affordance 明确允许；
- `examples/huarong_lane/canonical_case.py`：第 1 章运行时检查点；
- `evaluation/canonical_cases/first_crazy_ch1_5.json`：隐藏事件和证据哈希；
- `evaluation/canonical_alignment.py`：工具/环境机制、行动者、目标和顺序的确定性
  单调对齐；
- `evaluation/canonical_novel.py`：端到端运行、真实用量、回放和报告。

## 首轮真实结果

报告：`evaluation/reports/canonical-ch1-5-real-v1.json`。

| 指标 | 结果 |
|---|---:|
| 模型 | `qwen3.7-plus` |
| 真实 LLM 调用 | 16 |
| 总 Token | 67,391 |
| 失败调用 / 脚本回退 | 0 / 0 |
| 未来原著事件泄漏 | 0 |
| 计划依赖变化 | 1 |
| 局部重规划 | 2 |
| 剧情片段终态 | 完成（夜轻歌进入夜家大堂） |
| 原著关键事件匹配 | 8/10 |
| 加权事件召回 | 78.45% |
| 已匹配事件顺序准确率 | 100% |
| 权威状态回放一致率 | 100% |
| 非法提交 | 0 |

未匹配事件保留为真实失败：纠错后夜轻歌没有继续执行“取走外衫”，并且没有完成
“警告林管家”。三生泉转移属于合法环境事件，v1 初始对齐器曾错误要求必须由
`move_to` 工具完成；增加等价环境事件类型后离线重判为匹配，没有重新调用模型。

针对第一个失败，规划 Prompt 曾升级为 `novelsim_narrative_beat.v2`：执行失败后先
修复前置条件，再继续仍有效的剩余动作意图。该历史改动没有回填或伪装成 v1 提升；
后续完整升级和真实复跑结果见下文独立报告。

## 真实性评测 v2（已完成真实模型复跑）

v2 将“原著复现能力”和“动态纠错能力”拆为两个互不混淆的协议：

- `clean`：不注入外部扰动，只测原著关键事件、状态和因果复现；
- `perturbed`：注入一次计划依赖变化，只测失效检测、重规划和最终恢复。

姬月带夜轻歌进入异空间、开放三生泉、返回风月阁不再由评测脚本直接提交。
角色规划器只能调用 `invoke_ability(ability_id)`；能力所有者、固定目标、合法来源位置、
依赖旗标和确定性状态补丁均由权威世界包配置并由引擎校验。绿荷不再由脚本强制到场，
而是通过带激活条件的角色目标进入事件驱动调度。

报告新增事件来源归因：

\[
R_{agent}=\frac{\sum w(\text{由 LLM ToolCall 复现的原著事件})}{\sum w(\text{全部原著事件})}
\]

\[
R_{environment}=\frac{\sum w(\text{由环境事件复现的原著事件})}{\sum w(\text{全部原著事件})}
\]

事件匹配也取消了顺序硬约束：先独立匹配事件，再计算相邻匹配事件的顺序准确率，
避免“匹配器强制单调，所以顺序天然为 100%”的问题。确定性回归中，clean 与 perturbed
均已完成终态且关键空间事件的环境召回贡献为 0；完整 Python 回归为
`444 passed, 15 deselected`。

### 最终真实 LLM 结果

最终版本使用 `qwen3.7-plus` 和 `novelsim_narrative_beat.v7`，没有 Scripted Policy
回退。关键节点均由角色 `ToolCall` 或 `invoke_ability` 产生，评测脚本不再直接提交
姬月空间、三生泉、返回风月阁或绿荷传唤事件。

| 指标 | clean 原著复现 | perturbed 动态纠错 |
|---|---:|---:|
| 真实 LLM 调用 | 16 | 22 |
| Token | 76,882 | 107,726 |
| 剧情片段终态 | 完成 | 完成 |
| 注入依赖变化 / 重规划 | 0 / 0 | 1 / 1 |
| 原著关键事件 | 10/10 | 10/10 |
| 加权事件召回 | 100% | 100% |
| 独立事件顺序 | 100% | 88.9% |
| Agent / 环境召回贡献 | 100% / 0% | 100% / 0% |
| 回放一致 | 100% | 100% |
| 脚本回退 / 未来事件泄漏 | 0 / 0 | 0 / 0 |

perturbed 的顺序下降是真实扰动结果：夜清清临时离场后，夜轻歌先追到夜府，再取得
外衣，因此“取衣--返回夜府”相对原著发生逆序；对齐器不再用单调匹配隐藏该差异。

最终报告：

- `evaluation/reports/canonical-ch1-5-real-v10-clean.json`；
- `evaluation/reports/canonical-ch1-5-real-v10-perturbed.json`。

## 运行

```powershell
# 纯原著复现：使用 .env 中的真实模型
.venv\Scripts\python.exe -m evaluation.canonical_novel `
  --mode clean `
  --output evaluation\reports\canonical-ch1-5-real-v10-clean.json `
  --markdown evaluation\reports\canonical-ch1-5-real-v10-clean.md

# 动态扰动纠错：应使用独立输出文件，避免覆盖 clean 报告
.venv\Scripts\python.exe -m evaluation.canonical_novel `
  --mode perturbed `
  --output evaluation\reports\canonical-ch1-5-real-v10-perturbed.json `
  --markdown evaluation\reports\canonical-ch1-5-real-v10-perturbed.md

# 修改对齐规则后，只重判已有轨迹，不产生模型调用
.venv\Scripts\python.exe -m evaluation.canonical_novel `
  --rejudge-existing evaluation\reports\canonical-ch1-5-real-v1.json `
  --output evaluation\reports\canonical-ch1-5-real-v1.json `
  --markdown evaluation\reports\canonical-ch1-5-real-v1.md

# 确定性 CI 闭环
.venv\Scripts\python.exe -m pytest -q tests\unit\test_canonical_novel.py
```
