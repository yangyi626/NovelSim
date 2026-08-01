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

当前命令只导出 `secret_letter_v1` 确定性基准，用于验证合同，不是正式训练集。正式训练数据应使用下文已冻结的 Expert v4 → SFT/GRPO v5 流水线，不从单个演示场景直接拼接样本。

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

## 正式确定性专家数据 v4

正式 manifest 使用 12 variants × 20 seeds × 3 families，共 720 个参数化场景。每个场景采集三条轨迹：

- `scripted_expert`：标准专家路线；
- `safe_heuristic`：语义不同但合法的替代路线；
- `controlled_recovery`：一次被 Gate 拒绝的提议，随后读取结构化 feedback 并完成恢复。

复现命令：

```powershell
.\.venv\Scripts\python.exe -m training.collect_dataset `
  --manifest training\manifests\scenario-split-v1.json `
  --output-dir data\trajectories\novelsim-planner-expert-v4 `
  --report-dir training\reports\novelsim-planner-expert-v4 `
  --code-commit 00b97cf553ce8923cc1bb4e2d6eee974c863c4bc
```

实测结果：2,160 episodes、9,120 decision steps；受控恢复 720 episodes（33.33%）；目标成功与回放一致均为 2,160/2,160；illegal proposal 720（全部来自预期的首步拒绝），illegal commit 0。Train/Dev/Test-ID/Test-OOD 分别为 1,080/120/240/720 episodes，其中 Test-ID 与 Test-OOD 在数据卡中显式封存。

完整 JSONL/Parquet 默认写入 `data/trajectories/` 并由 Git 忽略，仓库只提交 manifest、泄漏审计、数据卡与文件 SHA-256。PromptedLLM 来源尚未并入这份确定性专家数据，必须单独运行、单独标记真实模型与 Token，并通过同一 verifier 后才能合并。

## PromptedLLM Train/Dev 限量 smoke

Prompted 与 SFT 共享 `engine/planner_prompt.py` 中同一个 `novelsim_planner_prompt.v4`：角色可见 observation、精简 Tool Schema、当前权威计划步骤、相同 system prompt。Prompted 请求固定关闭 Qwen thinking、限制 completion、设置 provider timeout，并通过现有 telemetry 记录响应中的真实 Token 与模型名。Runtime 只在声明的状态谓词由假变真后原子推进计划，不允许模型直接提交 `advance_plan`。

默认命令只生成/核对计划，不调用模型：

```powershell
.\.venv\Scripts\python.exe -m training.prompted_smoke `
  --config training\configs\prompted_smoke_v1.json `
  --write-plan training\manifests\prompted-smoke-v1.json
```

冻结计划只从 Train/Dev 各取 `resource_negotiation` 与 `secret_transport` 一个场景，共 4 个；Test-ID、Test-OOD、adversarial 不会进入候选。上限为 24 次模型决策、100,000 个实际 Token，每次最多 512 completion tokens；采集器还按 prompt UTF-8 字节数保守预留预算，余额不足时在调用前停止。

真实调用必须显式执行，并要求 `.env`/环境中的 `LLM_MODEL` 与已审计 config 完全一致：

```powershell
.\.venv\Scripts\python.exe -m training.prompted_smoke `
  --config training\configs\prompted_smoke_v1.json `
  --execute `
  --code-commit <当前提交SHA>
```

真实报告会分别给出 provider call/failed call、模型 Schema 通过、scripted fallback、Gate 通过、illegal proposal、illegal commit、目标完成、回放一致和 Token。Fallback 只用于让 smoke 继续，不计为模型成功；即使轨迹满足目标，也只标记 `eligible_for_sft_review`，不会自动并入 SFT。

2026-08-02 已用真实 `qwen3.7-plus-2026-05-26` 执行冻结的 Train/Dev 四场景：17 次 provider call、22,581 tokens，Schema 与 Gate 均为 `17/17`，fallback、illegal proposal、illegal commit 均为 `0`，目标成功与回放一致均为 `4/4`。报告位于 `training/reports/prompted-smoke-v1/`。该结果证明 Prompt v4 与 Runtime 闭环可工作，不代表本地 SFT/GRPO 模型已经训练完成。

## 两类安全指标

- `illegal_proposal`：Planner 提议被 Schema、实体、能力、Affordance、知识或 Patch Gate 拒绝；
- `illegal_commit`：非法效果实际形成已提交事件；完整 Runtime 中必须始终为 `0`。

合法失败步骤没有 `committed_event`，且 `previous_state_hash == next_state_hash`。成功步骤必须有匹配的 `ToolResult.committed_event_id` 和 `WorldEvent`。

## 内容哈希

`content_hash` 用于数据去重与跨 split 泄漏审计。它覆盖世界、观察、决策语义、工具结果、事件、奖励和失败标签，但排除 run ID、trace 时间、延迟、随机 call/decision ID 等易变遥测，因此相同语义轨迹重复采集仍得到相同哈希。

## SFT prompt-completion 数据 v5

SFT 数据只读取正式轨迹的 `train.jsonl` 和 `dev.jsonl`，命令行没有 Test 输入参数。构建器会先用专家数据卡核对源文件 SHA-256，再执行以下规则：

- 只把成功执行且形成 `committed_event` 的动作作为正监督目标；
- 被 Gate 拒绝的 illegal proposal 不进入 completion；
- 拒绝后的结构化 `feedback` 保留在下一次合法恢复动作的 observation 中；
- prompt 只包含角色可见事实和精简 Tool Schema，不包含权威 `WorldState`、状态 hash 或运行期 ID；
- completion 去除 `policy_id`、decision/call/trace ID 和采集策略名称，只保留规范化 `PlannerDecision`；
- 内容哈希只覆盖真实 prompt、completion 与 prompt 版本，不用 split/source 元数据掩盖重复；相同训练语义只保留一次，语义不同的多条合法路线仍分别保留；
- 输出采用 TRL 原生 conversational prompt-completion 格式，训练时显式启用 completion-only loss。

复现命令：

```powershell
.\.venv\Scripts\python.exe -m training.build_sft_dataset `
  --train-input data\trajectories\novelsim-planner-expert-v4\train.jsonl `
  --dev-input data\trajectories\novelsim-planner-expert-v4\dev.jsonl `
  --source-card training\reports\novelsim-planner-expert-v4\dataset-card.json `
  --output-dir data\sft\novelsim-planner-v5 `
  --report-dir training\reports\novelsim-planner-sft-v5
```

正式源步骤为 Train `4,680`、Dev `520`；分别剔除 `360/40` 个非法首步和 `1,260/140` 个相同训练语义重复项，得到 Train `3,060`、Dev `340` 个唯一监督样本，并完整保留 `360/40` 个恢复反馈上下文；Train/Dev content hash 重叠为 `0`。JSONL 在 `data/sft/` 下由 Git 忽略，仓库提交数据卡和文件哈希。

## 单卡 4090 QLoRA SFT

训练环境与 Python 3.8 游戏 Runtime 隔离。服务器建议使用 Python 3.11/3.12，并先按服务器 CUDA 版本安装 PyTorch，再安装冻结的后训练栈：

### 可校验的跨机器交付

训练 JSONL 不进入 Git，因此不能只在服务器 `git pull` 后直接开训。本地先构建只含 SFT Train/Dev、数据卡、配置、Dev manifest 和依赖锁的哈希包；Test-ID/Test-OOD 不会进入归档：

```powershell
.\.venv\Scripts\python.exe -m training.server_handoff build `
  --config training\configs\server_sft_qwen3_0.6b_smoke.json
.\.venv\Scripts\python.exe -m training.server_handoff verify `
  --config training\configs\server_sft_qwen3_0.6b_smoke.json
```

将 `tmp/server-handoff/novelsim-qwen3-0.6b-sft-smoke-v5.tar.gz` 传到服务器。服务器必须先检出归档 manifest 记录的精确 commit，再安装；相同文件会复用，任何同路径不同哈希文件都会被拒绝覆盖。安装成功会写入 handoff receipt，真实训练入口缺少该 receipt 时会在加载 CUDA 模型前停止：

```bash
git fetch origin codex/trainable-planner-v2
git checkout codex/trainable-planner-v2
git pull --ff-only
python3.11 -m venv .venv-sft
source .venv-sft/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m training.server_handoff install \
  --config training/configs/server_sft_qwen3_0.6b_smoke.json \
  --archive /path/to/novelsim-qwen3-0.6b-sft-smoke-v5.tar.gz
```

不加载 CUDA 模型的端到端 dry-run：

```bash
python -m training.server_handoff run \
  --config training/configs/server_sft_qwen3_0.6b_smoke.json
```

dry-run 只证明数据/配置/Dev 场景可用，报告必须是 `executes_training=false`。真实执行命令为：

```bash
python -m training.server_handoff run \
  --config training/configs/server_sft_qwen3_0.6b_smoke.json \
  --execute
```

统一入口依次执行数据哈希预检、100-step SFT、adapter 逐文件审计和 Dev inference→Gate→replay。中断后会从输出目录中最大编号的 `checkpoint-*` 恢复；已有完整且哈希一致的 adapter 会复用。最终生成 `run-manifest.json`、服务器 pipeline `report.json` 和 `model-card.md`，记录有效配置哈希、真实 tokenizer 长度、训练指标、环境包版本、GPU 型号、峰值显存、adapter 哈希和 Runtime smoke 结果。

```bash
source .venv-sft/bin/activate
python -m pip install --upgrade pip
# 先从 pytorch.org 选择与服务器 CUDA 匹配的 torch 安装命令
python -m pip install -r training/requirements-sft.txt
```

在不加载模型、不要求 CUDA 的情况下，可先核对配置、数据边界、样本数和所有哈希：

```bash
python -m training.train_sft \
  --config training/configs/sft_qwen3_0.6b_smoke.json \
  --validate-only
```

4090 上必须先完成 0.6B 的 100-step smoke：

```bash
python -m training.train_sft \
  --config training/configs/sft_qwen3_0.6b_smoke.json
```

只有 0.6B smoke 的加载、token 长度审计、训练、Dev eval、adapter 保存与 Runtime 回放均通过，才进入 1.7B reward/debug 档：

```bash
python -m training.train_sft \
  --config training/configs/sft_qwen3_1.7b_debug.json
python -m training.checkpoint_smoke \
  --config training/configs/checkpoint_smoke_qwen3_1.7b.json \
  --execute
```

1.7B debug 通过后再运行 4B SFT 主训练：

```bash
python -m training.train_sft \
  --config training/configs/sft_qwen3_4b_qlora.json
```

两份配置固定使用 4-bit NF4、double quant、bf16、gradient checkpointing、micro batch `1`、`all-linear` LoRA 和 completion-only loss。训练入口会在模型加载后对全部 3,400 个唯一样本做 tokenizer 长度审计；任何样本超过 `max_length=2048` 都会停止，而不是静默截断 prompt 或 completion。训练产物保存在 `training/outputs/` 并由 Git 忽略，`run-manifest.json` 记录数据哈希、config、commit、GPU/CUDA、包版本、token 长度和指标。

### Adapter 加载与 Runtime smoke

`SFTPolicy`/`GRPOPolicy` 使用同一 `AdapterPlannerPolicy`：懒加载 Transformers + PEFT，不会让 Python 3.8 游戏 Runtime 在 import 时依赖 CUDA 包。加载前必须同时通过：

- run manifest 为完成态，模型、Prompt 版本和 SFT 数据集一致；
- `adapter_config.json` 的 base model、LoRA 和 `CAUSAL_LM` 类型一致；
- tokenizer、adapter 权重存在；
- adapter 目录中每个文件的 SHA-256 与训练结束时写入 run manifest 的清单一致。

本地无 checkpoint 时可做零模型预检；当前预期结果是 `ready=false`，并明确报告 `run_manifest_missing` 和 `adapter_directory_missing`：

```bash
python -m training.checkpoint_smoke \
  --config training/configs/checkpoint_smoke_qwen3_0.6b.json
```

服务器完成 0.6B 训练后，用同一配置显式执行 Dev-only smoke：

```bash
python -m training.checkpoint_smoke \
  --config training/configs/checkpoint_smoke_qwen3_0.6b.json \
  --execute
```

该 smoke 必须实际加载 adapter，在冻结 Dev 场景逐步生成 `PlannerDecision`，经过权威 Runtime Gate，保存可回放 trajectory，并同时满足：无生成错误、Schema 全通过、illegal commit 为 0、目标完成、回放一致。报告和 trajectory 默认写入 Git 忽略目录，不能用单元测试的 fake backend 代替服务器报告。

## GRPO 可重建环境与数据 v5

GRPO 不把某一条专家动作当作唯一答案。`training.build_grpo_dataset` 从 Train/Dev 轨迹为每个决策步保存：

- 与 SFT/Prompted 完全相同的 `novelsim_planner_prompt.v4`；
- 场景 family、variant、seed 和场景 content hash；
- 当前步之前的已提交 `WorldEvent` 前缀；
- 当前 actor、结构化 failure feedback 和起始状态 hash。

每个候选 completion 都由 `NovelSimEnv` 独立 reset 到相同 hash，再解析为 `PlannerDecision` 并经过 `ToolRegistry → FSM → PatchValidator → WorldEvent`。模型输出不能携带 `StatePatch`，被拒绝的动作不改变状态。复现数据：

```powershell
.\.venv\Scripts\python.exe -m training.build_grpo_dataset `
  --train-input data\trajectories\novelsim-planner-expert-v4\train.jsonl `
  --dev-input data\trajectories\novelsim-planner-expert-v4\dev.jsonl `
  --source-card training\reports\novelsim-planner-expert-v4\dataset-card.json `
  --output-dir data\grpo\novelsim-planner-v5 `
  --report-dir training\reports\novelsim-planner-grpo-v5
```

正式结果为 Train `3,600`、Dev `400` 个唯一环境 prompt；分别去除 `1,080/120` 个相同 prompt+权威初态重复项，保留恢复 feedback `360/40` 个，Train/Dev content hash 重叠为 `0`。Test-ID/Test-OOD 没有读取。

## 结果型 reward 与 hacking audit

混合 reward 使用计划文档冻结的八个分量：目标进度、工具执行、因果落地、角色目标、信息完整性、失败恢复、行动效率和终局结果。Schema、实体、能力/知识、Patch、伪造 evidence/goal、重复动作和真正 no-op 分别记录 penalty 与 failure label。`objective_only` 只返回可验证成功条件比例的增量，用于稀疏奖励对照；它不会因为 Gate 拒绝获得正分。

冻结 Dev 只含两个 ID 世界族，因此正式审计对每族固定一个状态、每状态 7 种 probe，共 14 个：合法参考动作、非法 JSON、未知 aircraft、wait、门禁接受但与目标无关的闲聊、伪造 evidence、伪造 goal；另对重复动作做二次执行检查。复现：

```powershell
.\.venv\Scripts\python.exe -m training.reward_audit `
  --grpo-file data\grpo\novelsim-planner-v5\dev.jsonl `
  --trajectory-file data\trajectories\novelsim-planner-expert-v4\dev.jsonl `
  --split dev --group-size 7 --max-samples 2 `
  --output training\reports\novelsim-planner-grpo-v5\reward-audit.json
```

当前确定性审计 `reward-audit-031dcac357ce4494` 对两个 Dev 样本执行 14 个 probe，并通过 11 项不变量：同组初态一致、格式/未知实体为负、合法参考动作优于 wait、门禁接受但与目标无关的闲聊不获正分、伪造 evidence/goal 不获益、重复动作受罚、objective-only 拒绝不获正分、`illegal_commit_count=0`。这是 reward/Runtime 设计证据，不是模型效果报告。

## 单卡 4090 GRPO

训练入口锁定 TRL `1.8.0` 的 `GRPOTrainer + custom async reward_funcs`；额外数据列保留 `environment_spec`，组大小由 `num_generations=4` 固定。QLoRA 从逐文件 hash 审计通过的 SFT adapter 开始，默认使用 `dr_grpo` 长度归一化、Dev reward 选 checkpoint、Qwen thinking 关闭和 completion 截断 mask。TRL 1.8.0 已不接收 `max_prompt_length` Trainer 参数，因此项目在训练前用真实 tokenizer 对全部 Train/Dev prompt 做独立硬审计，超限即停止，绝不依赖静默截断。

预检不会加载模型；当前因为真实 SFT adapter 尚不存在而应诚实返回 `ready=false`：

```bash
python -m training.train_grpo \
  --config training/configs/grpo_qwen3_0.6b_objective_smoke.json \
  --inspect
```

SFT checkpoint 和 Runtime smoke 通过后，固定顺序为：

```bash
# 20-step 稀疏 objective-only 对照
python -m training.train_grpo \
  --config training/configs/grpo_qwen3_0.6b_objective_smoke.json

# 50-step 混合 reward smoke
python -m training.train_grpo \
  --config training/configs/grpo_qwen3_0.6b_mixed_smoke.json
python -m training.checkpoint_smoke \
  --config training/configs/checkpoint_smoke_grpo_qwen3_0.6b.json \
  --execute

# 1.7B reward/debug 主档；先完成对应 SFT 与 checkpoint smoke
python -m training.train_grpo \
  --config training/configs/grpo_qwen3_1.7b_mixed_debug.json
python -m training.checkpoint_smoke \
  --config training/configs/checkpoint_smoke_grpo_qwen3_1.7b.json \
  --execute

# 4B 仅在 24GB 显存 smoke 稳定后执行；需要 Linux/CUDA vLLM 可选依赖
python -m pip install -r training/requirements-grpo-vllm.txt
python -m training.train_grpo \
  --config training/configs/grpo_qwen3_4b_mixed_qlora.json
python -m training.checkpoint_smoke \
  --config training/configs/checkpoint_smoke_grpo_qwen3_4b.json \
  --execute
```

4B 配置使用单卡 colocate vLLM、`gpu_memory_utilization=0.2` 和 sleep mode。若 OOM，按计划退回 1.7B GRPO 主实验，不反复试参，也不把代码预检或 fake model 当作训练完成。
