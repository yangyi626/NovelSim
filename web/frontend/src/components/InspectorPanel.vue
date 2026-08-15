<script setup>
import { computed, ref, watch } from 'vue'
import StatePanel from './StatePanel.vue'

const props = defineProps({
  state: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
  latestTurn: { type: Object, default: null },
  worldMeta: { type: Object, default: null },
  jointPlans: { type: Array, default: () => [] },
  planBusy: { type: Boolean, default: false },
  planError: { type: String, default: '' },
  autoRunning: { type: Boolean, default: false },
})

const emit = defineEmits([
  'generate-plan',
  'save-plan',
  'approve-plan',
  'execute-plan',
  'toggle-auto',
])

const activeTab = ref('status')
const planningGoal = ref('依据角色目标、已知事实和世界规则，推动下一段合理剧情。')
const selectedActorIds = ref([])
const planEditor = ref('')
const editorError = ref('')
const autoCycles = ref(3)

watch(() => props.latestTurn, (turn) => {
  if (turn?.status === 'rejected') activeTab.value = 'rules'
})

const locations = computed(() => Object.values(props.state?.locations || {}))
const activePlan = computed(() => props.jointPlans[0] || null)
const planningCharacters = computed(() => {
  const characters = Object.values(props.state?.characters || {}).filter((item) => item.is_alive)
  const local = characters.filter((item) => (
    !props.state?.current_scene_id || item.location_id === props.state.current_scene_id
  ))
  return local.length ? local : characters
})
const constraints = computed(() => props.state?.world_constraints || [])
const highLevelRules = computed(() => props.state?.world_rules || [])
const deterministicRules = computed(() => props.state?.rules || [])
const ruleCount = computed(() => (
  constraints.value.length + highLevelRules.value.length + deterministicRules.value.length
))

const decision = computed(() => {
  const turn = props.latestTurn
  if (!turn) return { label: '等待行动', cls: 'idle', summary: '输入一个行动后，这里会展示规则门禁结论。' }
  if (turn.status === 'committed') return { label: 'ACCEPTED', cls: 'accepted', summary: '行动已通过校验并原子写入权威世界状态。' }
  if (turn.status === 'rejected') return { label: 'REJECTED', cls: 'rejected', summary: turn.rejection_message || turn.rule_reason || '行动违反世界规则，未写入状态。' }
  return { label: String(turn.status || 'UNKNOWN').toUpperCase(), cls: 'warning', summary: turn.error || '本回合未提交到权威状态。' }
})

function ruleText(rule) {
  return rule.statement || rule.description || rule.rule_id || rule.constraint_id || '未命名规则'
}

function charactersAt(locationId) {
  return Object.values(props.state?.characters || {}).filter((character) => character.location_id === locationId)
}

watch(planningCharacters, (characters) => {
  const available = new Set(characters.map((item) => item.character_id))
  selectedActorIds.value = selectedActorIds.value.filter((actorId) => available.has(actorId))
  if (!selectedActorIds.value.length) {
    const ordered = [...characters].sort((a, b) => (
      Number(b.character_id === props.defaultActor) - Number(a.character_id === props.defaultActor)
    ))
    selectedActorIds.value = ordered.slice(0, 3).map((item) => item.character_id)
  }
}, { immediate: true })

watch(activePlan, (plan) => {
  if (plan?.raw_plan) {
    planEditor.value = JSON.stringify(plan.raw_plan, null, 2)
    planningGoal.value = plan.goal || planningGoal.value
  } else {
    planEditor.value = ''
  }
  editorError.value = ''
}, { immediate: true })

function toggleActor(actorId) {
  if (selectedActorIds.value.includes(actorId)) {
    selectedActorIds.value = selectedActorIds.value.filter((item) => item !== actorId)
  } else if (selectedActorIds.value.length < 4) {
    selectedActorIds.value = [...selectedActorIds.value, actorId]
  }
}

function requestPlan() {
  if (!planningGoal.value.trim() || !selectedActorIds.value.length) return
  emit('generate-plan', {
    goal: planningGoal.value.trim(),
    actorIds: [...selectedActorIds.value],
  })
}

function saveEditedPlan() {
  editorError.value = ''
  try {
    const parsed = JSON.parse(planEditor.value)
    emit('save-plan', { planId: activePlan.value.plan_id, plan: parsed })
  } catch (error) {
    editorError.value = `JSON 格式错误：${error.message}`
  }
}

function toggleAuto() {
  emit('toggle-auto', {
    enabled: !props.autoRunning,
    goal: planningGoal.value.trim(),
    actorIds: [...selectedActorIds.value],
    cycles: Math.max(1, Math.min(10, Number(autoCycles.value) || 1)),
  })
}
</script>

<template>
  <div class="inspector">
    <div class="tabs" role="tablist" aria-label="世界检查器">
      <button :class="{ active: activeTab === 'status' }" @click="activeTab = 'status'">状态</button>
      <button :class="{ active: activeTab === 'rules' }" @click="activeTab = 'rules'">
        规则判定<span v-if="latestTurn?.status === 'rejected'" class="alert-dot"></span>
      </button>
      <button :class="{ active: activeTab === 'scenes' }" @click="activeTab = 'scenes'">场景</button>
      <button :class="{ active: activeTab === 'plans' }" @click="activeTab = 'plans'">协作计划</button>
    </div>

    <div class="tab-content">
      <StatePanel v-if="activeTab === 'status'" :state="state" :default-actor="defaultActor" />

      <div v-else-if="activeTab === 'rules'" class="rules-panel">
        <section class="decision-card" :class="decision.cls">
          <span class="eyebrow">LATEST DECISION</span>
          <div class="decision-head">
            <strong>{{ decision.label }}</strong>
            <span>v{{ state?.version ?? 0 }}</span>
          </div>
          <p>{{ decision.summary }}</p>
          <dl v-if="latestTurn?.action" class="action-grid">
            <div><dt>行动</dt><dd>{{ latestTurn.action.type }}</dd></div>
            <div><dt>执行者</dt><dd>{{ state?.characters?.[latestTurn.action.actor]?.display_name || latestTurn.action.actor }}</dd></div>
            <div v-if="latestTurn.action.targets?.length"><dt>目标</dt><dd>{{ latestTurn.action.targets.join('、') }}</dd></div>
            <div v-if="latestTurn.action.goal"><dt>意图</dt><dd>{{ latestTurn.action.goal }}</dd></div>
          </dl>
          <div v-if="latestTurn?.rejection_code" class="reason-code">{{ latestTurn.rejection_code }}</div>
          <div v-if="Object.keys(latestTurn?.rejection_details || {}).length" class="detail-list">
            <div v-for="(value, key) in latestTurn.rejection_details" :key="key">
              <span>{{ key }}</span><code>{{ Array.isArray(value) ? value.join('、') : value }}</code>
            </div>
          </div>
        </section>

        <section class="rule-section">
          <div class="section-heading"><span>世界规则</span><b>{{ ruleCount }}</b></div>
          <article v-for="rule in constraints" :key="rule.constraint_id" class="rule-item">
            <span>{{ rule.category || 'general' }}</span>
            <p>{{ ruleText(rule) }}</p>
          </article>
          <article v-for="rule in highLevelRules" :key="rule.rule_id" class="rule-item">
            <span>{{ rule.category || 'world' }}</span>
            <p>{{ ruleText(rule) }}</p>
          </article>
          <article v-for="rule in deterministicRules" :key="rule.rule_id" class="rule-item deterministic">
            <span>deterministic · P{{ rule.priority }}</span>
            <p>{{ ruleText(rule) }}</p>
          </article>
          <div v-if="!constraints.length && !highLevelRules.length && !deterministicRules.length" class="empty-mini">当前世界包没有声明规则。</div>
        </section>
      </div>

      <div v-else-if="activeTab === 'scenes'" class="scenes-panel">
        <div class="world-summary">
          <span class="eyebrow">WORLD PACKAGE</span>
          <strong>{{ worldMeta?.scenario || '当前世界' }}</strong>
          <p>{{ worldMeta?.anchor || '从当前介入锚点继续推演。' }}</p>
        </div>
        <article
          v-for="location in locations"
          :key="location.location_id"
          class="scene-card"
          :class="{ current: location.location_id === state?.current_scene_id }"
        >
          <div class="scene-head">
            <strong>{{ location.display_name }}</strong>
            <span v-if="location.location_id === state?.current_scene_id">当前</span>
          </div>
          <div class="scene-id">{{ location.location_id }}</div>
          <div class="scene-characters">
            <span v-for="character in charactersAt(location.location_id)" :key="character.character_id">
              {{ character.display_name }}
            </span>
            <em v-if="!charactersAt(location.location_id).length">暂无角色</em>
          </div>
        </article>
      </div>

      <div v-else class="plans-panel">
        <section class="plan-console">
          <div class="console-heading">
            <div>
              <span class="eyebrow">REAL LLM WORLD DRIVER</span>
              <strong>规划审批台</strong>
            </div>
            <span :class="['mode-chip', autoRunning ? 'auto' : 'manual']">
              {{ autoRunning ? 'AUTO RUNNING' : 'MANUAL' }}
            </span>
          </div>
          <label class="plan-field">
            <span>本轮剧情目标</span>
            <textarea v-model="planningGoal" rows="3" :disabled="planBusy || autoRunning"></textarea>
          </label>
          <div class="actor-picker">
            <span>参与规划角色（最多 4 个）</span>
            <div>
              <button
                v-for="character in planningCharacters"
                :key="character.character_id"
                type="button"
                :class="{ selected: selectedActorIds.includes(character.character_id) }"
                :disabled="planBusy || autoRunning"
                @click="toggleActor(character.character_id)"
              >
                {{ character.display_name }}
              </button>
            </div>
          </div>
          <div class="plan-actions primary-actions">
            <button
              type="button"
              class="primary"
              :disabled="planBusy || autoRunning || !!activePlan && !['completed', 'aborted'].includes(activePlan.status) || !selectedActorIds.length"
              @click="requestPlan"
            >
              {{ planBusy ? '处理中…' : '生成规划草案' }}
            </button>
            <label class="cycle-input">
              <span>连续轮数</span>
              <input v-model.number="autoCycles" type="number" min="1" max="10" :disabled="autoRunning" />
            </label>
            <button
              type="button"
              :class="autoRunning ? 'danger' : 'auto-button'"
              :disabled="planBusy && !autoRunning"
              @click="toggleAuto"
            >
              {{ autoRunning ? '停止 Auto' : '启动 Auto' }}
            </button>
          </div>
          <p v-if="planError" class="console-error">{{ planError }}</p>
          <p class="console-hint">Manual：生成后可修改并审批；Auto：每轮自动生成、批准、执行，达到设置轮数后停止。</p>
        </section>

        <article v-for="plan in jointPlans" :key="plan.plan_id" class="plan-card">
          <div class="plan-head">
            <div><span class="eyebrow">JOINT PLAN · R{{ plan.revision }}</span><strong>{{ plan.goal_id }}</strong></div>
            <span class="plan-status" :class="plan.status">{{ plan.status }}</span>
          </div>
          <div class="plan-meta">v{{ plan.base_world_version }} → v{{ plan.observed_world_version }} · 重规划 {{ plan.replan_count }} 次</div>
          <div v-if="plan.deadlock_cycle?.length" class="plan-warning">死锁：{{ plan.deadlock_cycle.join(' → ') }}</div>
          <div v-if="plan.stale_reasons?.length" class="plan-warning">失效：{{ plan.stale_reasons.join('；') }}</div>
          <section v-for="chain in plan.actor_chains" :key="chain.actor_id" class="chain-card">
            <div class="chain-title">
              <strong>{{ state?.characters?.[chain.actor_id]?.display_name || chain.actor_id }}</strong>
              <span v-if="chain.blocked_reason">{{ chain.blocked_reason }}</span>
            </div>
            <ol>
              <li v-for="step in chain.steps" :key="step.step_id" :class="step.status">
                <i></i>
                <div>
                  <b>{{ step.kind }}</b>
                  <span>{{ step.tool_call?.tool_name || step.target_step_id || step.condition?.kind }}</span>
                </div>
              </li>
            </ol>
          </section>
          <section v-if="plan.editable" class="plan-editor">
            <label>
              <span>结构化规划 JSON（修改后仍会重新校验）</span>
              <textarea v-model="planEditor" rows="13" :disabled="planBusy"></textarea>
            </label>
            <p v-if="editorError" class="console-error">{{ editorError }}</p>
            <div class="plan-actions">
              <button type="button" :disabled="planBusy" @click="saveEditedPlan">保存修改</button>
              <button type="button" class="primary" :disabled="planBusy" @click="emit('approve-plan', plan.plan_id)">批准规划</button>
            </div>
          </section>
          <div v-else-if="['approved', 'active', 'stale', 'deadlocked'].includes(plan.status)" class="plan-actions execution-actions">
            <button type="button" :disabled="planBusy || autoRunning" @click="emit('execute-plan', { planId: plan.plan_id, complete: false })">执行一步</button>
            <button type="button" class="primary" :disabled="planBusy || autoRunning" @click="emit('execute-plan', { planId: plan.plan_id, complete: true })">执行本轮规划</button>
          </div>
        </article>
        <div v-if="!jointPlans.length" class="empty-mini">当前存档还没有运行中的联合计划。</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.inspector { display: flex; height: 100%; min-height: 0; flex-direction: column; }
.tabs {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  flex-shrink: 0;
  border-bottom: 1px solid var(--border);
  background: var(--bg-panel);
}
.tabs button {
  position: relative;
  padding: 18px 6px 13px;
  border-radius: 0;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: var(--text-faint);
  font-size: 12px;
}
.tabs button.active { border-bottom-color: var(--accent); color: var(--accent); }
.alert-dot { position: absolute; width: 6px; height: 6px; margin: 1px 0 0 4px; border-radius: 50%; background: var(--danger); }
.tab-content { flex: 1; min-height: 0; overflow-y: auto; }
.rules-panel, .scenes-panel, .plans-panel { padding: 14px; }
.decision-card {
  padding: 14px;
  border: 1px solid var(--border);
  border-left: 4px solid var(--text-faint);
  border-radius: 8px;
  background: var(--bg-card);
}
.decision-card.accepted { border-left-color: var(--system); }
.decision-card.rejected { border-left-color: var(--danger); background: rgba(201, 90, 90, 0.08); }
.decision-card.warning { border-left-color: var(--warn); }
.eyebrow { display: block; color: var(--accent-dim); font: 9px/1.2 ui-monospace, monospace; letter-spacing: 1.4px; }
.decision-head { display: flex; justify-content: space-between; align-items: center; margin-top: 7px; }
.decision-head strong { color: var(--text); font: 700 17px/1.2 ui-monospace, monospace; }
.decision-card.accepted .decision-head strong { color: var(--system); }
.decision-card.rejected .decision-head strong { color: var(--danger); }
.decision-head span { color: var(--text-faint); font-size: 11px; }
.decision-card > p { margin-top: 9px; color: var(--text-dim); font-size: 12px; line-height: 1.65; }
.action-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 7px; margin-top: 12px; }
.action-grid div { padding: 7px; border: 1px solid var(--border-soft); border-radius: 5px; }
.action-grid dt { color: var(--text-faint); font-size: 9px; }
.action-grid dd { overflow: hidden; margin: 2px 0 0; color: var(--text); font-size: 11px; text-overflow: ellipsis; }
.reason-code { display: inline-block; margin-top: 10px; padding: 2px 7px; border-radius: 3px; background: rgba(201, 90, 90, 0.16); color: var(--danger); font: 10px/1.5 ui-monospace, monospace; }
.detail-list { display: grid; gap: 5px; margin-top: 9px; }
.detail-list div { display: grid; grid-template-columns: 90px 1fr; gap: 6px; font-size: 10px; }
.detail-list span { color: var(--text-faint); }
.detail-list code { overflow-wrap: anywhere; color: var(--text-dim); }
.rule-section { margin-top: 18px; }
.section-heading { display: flex; justify-content: space-between; margin-bottom: 8px; color: var(--text); font-size: 12px; }
.section-heading b { color: var(--accent); font-weight: 500; }
.rule-item { margin-bottom: 7px; padding: 9px 10px; border: 1px solid var(--border-soft); border-radius: 6px; background: rgba(44, 36, 28, 0.5); }
.rule-item > span { color: var(--accent-dim); font: 9px/1.2 ui-monospace, monospace; text-transform: uppercase; }
.rule-item p { margin-top: 4px; color: var(--text-dim); font-size: 11px; line-height: 1.55; }
.rule-item.deterministic > span { color: var(--player); }
.empty-mini { padding: 24px; color: var(--text-faint); text-align: center; font-size: 11px; }
.world-summary { margin-bottom: 14px; padding: 14px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); }
.world-summary strong { display: block; margin-top: 6px; color: var(--accent); font-size: 15px; }
.world-summary p { margin-top: 5px; color: var(--text-dim); font-size: 11px; line-height: 1.6; }
.scene-card { margin-bottom: 8px; padding: 11px; border: 1px solid var(--border-soft); border-left: 3px solid transparent; border-radius: 6px; background: rgba(44, 36, 28, 0.6); }
.scene-card.current { border-left-color: var(--accent); }
.scene-head { display: flex; justify-content: space-between; align-items: center; }
.scene-head strong { color: var(--text); font-size: 12px; }
.scene-head span { padding: 1px 5px; border-radius: 3px; background: rgba(201, 169, 106, 0.14); color: var(--accent); font-size: 9px; }
.scene-id { margin-top: 2px; color: var(--text-faint); font: 9px/1.4 ui-monospace, monospace; }
.scene-characters { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.scene-characters span { padding: 2px 6px; border-radius: 999px; background: var(--bg-input); color: var(--text-dim); font-size: 9px; }
.scene-characters em { color: var(--text-faint); font-size: 10px; font-style: normal; }
.plan-card { margin-bottom: 10px; padding: 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); }
.plan-head { display: flex; justify-content: space-between; gap: 8px; }
.plan-head strong { display: block; margin-top: 4px; color: var(--text); font-size: 12px; }
.plan-status { align-self: flex-start; padding: 2px 6px; border-radius: 999px; background: var(--bg-input); color: var(--text-dim); font: 9px/1.5 ui-monospace, monospace; }
.plan-status.active { color: var(--player); }
.plan-status.draft { color: var(--warn); }
.plan-status.approved { color: var(--accent); }
.plan-status.completed { color: var(--system); }
.plan-status.stale, .plan-status.deadlocked, .plan-status.aborted { color: var(--danger); }
.plan-meta { margin-top: 7px; color: var(--text-faint); font: 9px/1.4 ui-monospace, monospace; }
.plan-warning { margin-top: 7px; padding: 6px; border-radius: 4px; background: rgba(201, 90, 90, 0.12); color: var(--danger); font-size: 10px; }
.chain-card { margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--border-soft); }
.chain-title { display: flex; justify-content: space-between; gap: 6px; color: var(--text); font-size: 10px; }
.chain-title span { color: var(--warn); font: 8px/1.4 ui-monospace, monospace; }
.chain-card ol { display: grid; gap: 4px; margin-top: 7px; padding: 0; list-style: none; }
.chain-card li { display: grid; grid-template-columns: 7px 1fr; gap: 6px; align-items: center; color: var(--text-faint); }
.chain-card li i { width: 6px; height: 6px; border-radius: 50%; background: var(--border); }
.chain-card li div { display: flex; justify-content: space-between; gap: 6px; font-size: 9px; }
.chain-card li b { color: inherit; font-weight: 600; }
.chain-card li.completed { color: var(--system); }
.chain-card li.ready { color: var(--player); }
.chain-card li.blocked { color: var(--warn); }
.chain-card li.completed i { background: var(--system); }
.chain-card li.ready i { background: var(--player); }
.chain-card li.blocked i { background: var(--warn); }
.plan-console { margin-bottom: 12px; padding: 13px; border: 1px solid var(--accent-dim); border-radius: 8px; background: rgba(201, 169, 106, 0.06); }
.console-heading { display: flex; justify-content: space-between; gap: 10px; align-items: flex-start; }
.console-heading strong { display: block; margin-top: 5px; color: var(--text); font-size: 14px; }
.mode-chip { padding: 3px 7px; border-radius: 999px; font: 8px/1.5 ui-monospace, monospace; }
.mode-chip.manual { background: var(--bg-input); color: var(--text-dim); }
.mode-chip.auto { background: rgba(82, 166, 125, 0.15); color: var(--system); }
.plan-field { display: grid; gap: 5px; margin-top: 12px; }
.plan-field > span, .actor-picker > span, .plan-editor label > span { color: var(--text-faint); font-size: 9px; }
.plan-field textarea, .plan-editor textarea { width: 100%; resize: vertical; border: 1px solid var(--border); border-radius: 5px; background: var(--bg-input); color: var(--text); font: 10px/1.55 ui-monospace, monospace; }
.actor-picker { margin-top: 10px; }
.actor-picker > div { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 6px; }
.actor-picker button { padding: 4px 7px; border: 1px solid var(--border); border-radius: 999px; background: transparent; color: var(--text-faint); font-size: 9px; }
.actor-picker button.selected { border-color: var(--accent); background: rgba(201, 169, 106, 0.12); color: var(--accent); }
.plan-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.plan-actions button { padding: 6px 9px; border: 1px solid var(--border); background: var(--bg-input); color: var(--text-dim); font-size: 9px; }
.plan-actions button.primary { border-color: var(--accent-dim); background: rgba(201, 169, 106, 0.14); color: var(--accent); }
.plan-actions button.auto-button { border-color: var(--system); color: var(--system); }
.plan-actions button.danger { border-color: var(--danger); color: var(--danger); }
.plan-actions button:disabled { cursor: not-allowed; opacity: 0.45; }
.primary-actions { align-items: flex-end; }
.cycle-input { display: grid; gap: 3px; color: var(--text-faint); font-size: 8px; }
.cycle-input input { width: 52px; padding: 5px; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-input); color: var(--text); }
.console-hint { margin-top: 9px; color: var(--text-faint); font-size: 9px; line-height: 1.5; }
.console-error { margin-top: 8px; color: var(--danger); font-size: 9px; line-height: 1.5; }
.plan-editor { margin-top: 11px; padding-top: 10px; border-top: 1px solid var(--border-soft); }
.plan-editor label { display: grid; gap: 5px; }
.execution-actions { padding-top: 2px; }
</style>
