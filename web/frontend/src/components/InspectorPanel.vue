<script setup>
import { computed, ref, watch } from 'vue'
import StatePanel from './StatePanel.vue'

const props = defineProps({
  state: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
  latestTurn: { type: Object, default: null },
  worldMeta: { type: Object, default: null },
})

const activeTab = ref('status')

watch(() => props.latestTurn, (turn) => {
  if (turn?.status === 'rejected') activeTab.value = 'rules'
})

const locations = computed(() => Object.values(props.state?.locations || {}))
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
</script>

<template>
  <div class="inspector">
    <div class="tabs" role="tablist" aria-label="世界检查器">
      <button :class="{ active: activeTab === 'status' }" @click="activeTab = 'status'">状态</button>
      <button :class="{ active: activeTab === 'rules' }" @click="activeTab = 'rules'">
        规则判定<span v-if="latestTurn?.status === 'rejected'" class="alert-dot"></span>
      </button>
      <button :class="{ active: activeTab === 'scenes' }" @click="activeTab = 'scenes'">场景</button>
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

      <div v-else class="scenes-panel">
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
    </div>
  </div>
</template>

<style scoped>
.inspector { display: flex; height: 100%; min-height: 0; flex-direction: column; }
.tabs {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
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
.rules-panel, .scenes-panel { padding: 14px; }
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
</style>
