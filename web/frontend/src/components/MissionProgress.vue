<script setup>
import { computed } from 'vue'

const props = defineProps({
  dashboard: { type: Object, default: null },
  state: { type: Object, default: null },
  worldMeta: { type: Object, default: null },
  playerView: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
})

const actor = computed(() => props.state?.characters?.[props.defaultActor] || null)
const mission = computed(() => (
  props.dashboard?.mission
  || props.dashboard?.current_mission
  || props.dashboard?.objective
  || props.worldMeta?.anchor
  || '在当前世界中活下来，并找到改变命运的机会。'
))
const identity = computed(() => (
  props.dashboard?.identity
  || props.dashboard?.player_identity
  || [actor.value?.display_name, ...(actor.value?.identity_tags || []).slice(0, 2)].filter(Boolean).join(' · ')
  || '命运介入者'
))
const progress = computed(() => {
  const raw = props.dashboard?.mission_progress?.percent
    ?? props.dashboard?.mission_progress
    ?? props.dashboard?.progress_percent
  if (Number.isFinite(Number(raw))) return Math.max(0, Math.min(100, Number(raw)))
  const chapter = Number(props.playerView?.current_story_chapter || 1)
  const total = props.worldMeta?.source_chapters?.length || 5
  return Math.max(8, Math.min(100, Math.round((chapter / total) * 100)))
})
</script>

<template>
  <section class="mission-card" aria-label="当前使命">
    <div class="mission-heading"><span>你的身份</span><strong>{{ identity }}</strong></div>
    <p>{{ mission }}</p>
    <div class="progress-row"><div><i :style="{ width: `${progress}%` }"></i></div><b>{{ progress }}%</b></div>
  </section>
</template>

<style scoped>
.mission-card {
  padding: 15px 16px;
  border-bottom: 1px solid #30343b;
  background: linear-gradient(135deg, rgba(74,103,151,.2), rgba(255,255,255,.02));
}
.mission-heading { gap: 10px; }
.mission-heading strong { color: #e3e9f4; }
.mission-card p { line-height: 1.65; }
.progress-row > div { height: 5px; }

.mission-heading { display: flex; align-items: baseline; gap: 8px; }.mission-heading span { color: var(--text-faint); font-size: 9px; }.mission-heading strong { color: #dce5f5; font-size: 11px; }.mission-card p { margin-top: 5px; color: var(--text-dim); font-size: 11px; line-height: 1.55; }
.progress-row { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 8px; margin-top: 9px; }.progress-row > div { overflow: hidden; height: 4px; border-radius: 99px; background: rgba(255,255,255,.08); }.progress-row i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, #7099dd, #a9c2ee); }.progress-row b { color: var(--text-faint); font: 600 9px/1 ui-monospace, monospace; }
</style>
