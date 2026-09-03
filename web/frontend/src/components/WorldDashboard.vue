<script setup>
import { computed } from 'vue'
import MissionProgress from './MissionProgress.vue'

const props = defineProps({
  dashboard: { type: Object, default: null },
  state: { type: Object, default: null },
  worldMeta: { type: Object, default: null },
  playerView: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
  latestTurn: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  settlement: { type: Object, default: null },
})

const emit = defineEmits(['suggest', 'settle'])

const suggestions = computed(() => {
  const source = props.dashboard?.context_choices
    || props.dashboard?.suggested_actions
    || props.dashboard?.choices
    || []
  return source.map((item) => typeof item === 'string' ? { label: item, action: item } : {
    label: item.label || item.title || item.text || item.action,
    action: item.action || item.prompt || item.text || item.label,
  }).filter((item) => item.label && item.action).slice(0, 4)
})

const echoes = computed(() => (
  props.dashboard?.npc_memory_echoes
  || props.dashboard?.memory_echoes
  || []
).map((item) => typeof item === 'string' ? { text: item } : item).slice(0, 4))

const stage = computed(() => {
  if (props.loading) return { label: '故事回应中', detail: '世界正在判断你的行动，并推动在场角色作出反应。', cls: 'running' }
  if (props.latestTurn?.status === 'rejected') return { label: '行动未能发生', detail: props.latestTurn.rejection_message || props.latestTurn.rule_reason || '换一种符合当前世界条件的做法。', cls: 'blocked' }
  if (props.latestTurn?.status && props.latestTurn.status !== 'committed') return { label: '本轮未完成', detail: props.latestTurn.error || '你的输入仍保留，可以修改后再次尝试。', cls: 'blocked' }
  return { label: '等待你的行动', detail: '观察局势、与角色交谈，或直接尝试改变眼前的命运。', cls: 'ready' }
})

const canSettle = computed(() => Boolean(
  props.dashboard?.can_settle
  || props.dashboard?.settlement?.can_settle
  || props.settlement?.can_settle
  || ['available', 'ready', 'in_progress', 'processing'].includes(String(props.settlement?.status || '').toLowerCase()),
))
const settlementDone = computed(() => ['settled', 'completed', 'done'].includes(String(
  props.settlement?.status || props.dashboard?.settlement?.status || '',
).toLowerCase()))
</script>

<template>
  <aside class="player-dashboard" aria-label="旅程指引">
    <MissionProgress
      :dashboard="dashboard"
      :state="state"
      :world-meta="worldMeta"
      :player-view="playerView"
      :default-actor="defaultActor"
    />

    <section class="dashboard-section turn-progress" :class="stage.cls">
      <span class="section-label">当前状态</span>
      <strong><i></i>{{ stage.label }}</strong>
      <p>{{ stage.detail }}</p>
    </section>

    <section v-if="canSettle" class="dashboard-section settlement-entry">
      <div>
        <span class="section-label">世界线已抵达终点</span>
        <strong>{{ settlementDone ? '结算已完成' : '可以结算这条世界线' }}</strong>
        <p>{{ settlementDone ? '结局、改变与角色记忆已经保存。' : '查看这段旅程的结局、改变、NPC 记忆与积分。' }}</p>
      </div>
      <button type="button" :disabled="loading" @click="emit('settle')">{{ settlementDone ? '查看结算' : '进入结算' }}</button>
    </section>

    <section class="dashboard-section choices">
      <div class="section-title"><span>可以尝试</span><small>点击填入行动</small></div>
      <button
        v-for="choice in suggestions"
        :key="choice.action"
        type="button"
        :disabled="loading"
        @click="emit('suggest', choice.action)"
      ><span>{{ choice.label }}</span><b>＋</b></button>
      <p v-if="!suggestions.length" class="empty">你可以自由描述想做的事，故事会结合当前情境回应。</p>
    </section>

    <section v-if="props.latestTurn?.outcome || props.latestTurn?.world_progress" class="dashboard-section latest-impact" aria-live="polite">
      <span class="section-label">最近一次选择</span>
      <strong>{{ props.latestTurn?.outcome?.label || '世界线已更新' }}</strong>
      <p>{{ props.latestTurn?.outcome?.message || '你的行动已经留下了影响。' }}</p>
      <small v-if="props.latestTurn?.world_progress?.advanced">
        世界从 v{{ props.latestTurn.world_progress.from_version }} 推进到 v{{ props.latestTurn.world_progress.to_version }}
      </small>
    </section>

    <section v-if="echoes.length" class="dashboard-section echoes">
      <span class="section-label">角色记得</span>
      <article v-for="(echo, index) in echoes" :key="echo.id || index">
        <strong>{{ echo.npc_name || echo.character_name || echo.name || '某位角色' }}</strong>
        <p>{{ echo.text || echo.memory || echo.summary }}</p>
      </article>
    </section>
  </aside>
</template>

<style scoped>
.player-dashboard {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  overflow-y: auto;
  background: #171a20;
}
.dashboard-section { padding: 13px 16px; }
.turn-progress strong { color: #e5e8ed; }
.choices button { border-radius: 9px; background: rgba(255,255,255,.035); }
.choices button:hover:not(:disabled) { background: rgba(138,180,248,.09); border-color: #53617a; }
.latest-impact { background: rgba(138,180,248,.04); }
.echoes article { border-left-color: #657086; border-radius: 0 8px 8px 0; }
.settlement-entry { background: linear-gradient(100deg, rgba(112,181,126,.14), rgba(255,255,255,.02)); }

.dashboard-section { padding: 14px; border-bottom: 1px solid var(--border-soft); }.section-label, .section-title span { color: var(--text-faint); font-size: 9px; letter-spacing: .08em; }.turn-progress strong { display: flex; align-items: center; gap: 7px; margin-top: 7px; font-size: 12px; }.turn-progress strong i { width: 7px; height: 7px; border-radius: 50%; background: var(--system); }.turn-progress.running strong i { background: var(--player); box-shadow: 0 0 0 4px rgba(138,180,248,.1); animation: pulse 1s infinite; }.turn-progress.blocked strong i { background: var(--warn); }.turn-progress p, .echoes p, .empty { margin-top: 5px; color: var(--text-faint); font-size: 10px; line-height: 1.6; }@keyframes pulse { 50% { opacity: .3; } }
.section-title { display: flex; justify-content: space-between; gap: 8px; margin-bottom: 9px; }.section-title small { color: var(--text-faint); font-size: 8px; }.choices button { display: flex; width: 100%; align-items: center; justify-content: space-between; gap: 10px; margin-top: 6px; padding: 9px 10px; border: 1px solid var(--border-soft); background: rgba(255,255,255,.025); color: var(--text-dim); text-align: left; font-size: 10px; line-height: 1.45; }.choices button:hover:not(:disabled) { border-color: #535965; background: rgba(255,255,255,.05); color: var(--text); }.choices button b { color: #8eb0e8; font-size: 14px; font-weight: 400; }
.latest-impact strong { display: block; margin-top: 7px; color: var(--text); font-size: 11px; }.latest-impact p { margin-top: 4px; color: var(--text-dim); font-size: 10px; line-height: 1.5; }.latest-impact small { display: block; margin-top: 7px; color: var(--player); font-size: 9px; }
.echoes article { margin-top: 8px; padding: 9px; border-left: 2px solid #515965; background: rgba(255,255,255,.02); }.echoes strong { color: var(--text-dim); font-size: 10px; }
.settlement-entry { display: flex; align-items: center; justify-content: space-between; gap: 10px; background: linear-gradient(100deg, rgba(112,181,126,.1), rgba(255,255,255,.02)); }.settlement-entry strong { display: block; margin-top: 5px; color: var(--text); font-size: 11px; }.settlement-entry p { margin-top: 4px; color: var(--text-faint); font-size: 9px; line-height: 1.5; }.settlement-entry button { flex: 0 0 auto; padding: 7px 9px; border: 1px solid rgba(112,181,126,.45); background: rgba(112,181,126,.13); color: #a8d8ae; font-size: 10px; }
</style>
