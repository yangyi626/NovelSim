# Web 规划审批与自动推演

## 目标

把真实 LLM 规划器接入 BookWorld 风格 Web，同时保持世界引擎的权威边界：LLM 和
前端只能编辑 `JointPlan -> ToolCall`，不能提交 `WorldState` 或任意 `StatePatch`。

## Manual 闭环

```text
选择角色与剧情目标
  -> POST /api/joint-plans/generate
  -> 真实 LLM 按角色私有 GameObservation 生成动作链
  -> draft 持久化（此时绝不执行）
  -> Web 修改结构化 JSON
  -> PUT /api/joint-plans/{id} 重新执行 Schema/工具/实体校验
  -> POST /approve 绑定当前世界版本
  -> POST /execute 单步或运行到本轮结束
  -> ToolRegistry/FSM/规则门禁/原子 WorldEvent
  -> 刷新世界状态、剧情流与计划指针
```

`draft` 直接调用执行接口会得到 409；世界版本在草案生成后发生变化时，审批也会
得到 409，必须重新规划。该约束防止用户批准一份基于旧世界状态的计划。

## Auto 闭环

Auto 由前端按轮驱动同一组后端权威接口：

1. 调用真实 LLM 生成计划并显式设置 `auto_approve=true`；
2. 执行到本轮 `completed`，动作失败或计划失效时按上限调用真实 LLM 局部重规划；
3. 恢复最新权威会话并生成下一轮；
4. 达到用户设置的 1--10 轮后停止；用户点击停止时，在当前请求结束后停止下一轮。

Auto 不是绕过审批门禁：后端仍写入 `approved` 状态，之后才允许执行。设置有限轮数
是为了防止浏览器误操作造成无限 API Token 消耗。

## 数据与恢复

- `joint_plan_runtime.plan_json` 保存计划及修订；
- `runtime_json` 保存各角色步骤指针、已完成步骤、等待、失效与重规划状态；
- 每个成功 ToolCall 独立提交 `WorldEvent` 和新 `WorldState`；
- 崩溃恢复时以事件中的 `call_id` 对齐运行时，避免重复执行已提交动作；
- Web 剧情流会显示计划工具的事件摘要，角色记忆仍从已提交事件派生。

## 当前边界

- 当前正式开发存储是 SQLite；PostgreSQL 后端尚未补齐联合计划运行时表契约；
- 规划编辑器当前提供完整结构化 JSON，后续可以增加逐步骤表单编辑；
- Auto 的暂停粒度是“当前 HTTP/LLM 请求结束后”，尚未实现服务端后台任务与立即取消；
- 自动选择角色默认取当前场景中最多三个存活角色，也可以在 Web 中手动选择最多四个。
