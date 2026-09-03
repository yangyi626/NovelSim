<script setup>
import { computed, nextTick, ref } from 'vue'

const props = defineProps({
  activityItems: { type: [Array, Object], default: () => [] },
  storyBeats: { type: Array, default: () => [] },
})

const tabs = [
  { id: 'npc', label: 'NPC 行动' },
  { id: 'world', label: '世界变化' },
  { id: 'system', label: '系统记录' },
]
const activeTab = ref('npc')
const tabRefs = ref([])

function itemCategory(item, fallback = '') {
  const value = String(
    item?.category || item?.activity_type || item?.type || item?.kind || item?.channel || fallback || item?.source || '',
  ).toLowerCase()
  if (['npc', 'npc_action', 'npc-actions', 'agent', 'character', 'character_action'].includes(value)) return 'npc'
  if (['system', 'system_record', 'system-records', 'log', 'record', 'tool', 'evidence'].includes(value)) return 'system'
  if (['world', 'world_change', 'world-changes', 'environment', 'change', 'player'].includes(value)) return 'world'
  if (String(item?.source || '').toLowerCase() === 'agent') return 'npc'
  if (String(item?.source || '').toLowerCase() === 'system') return 'system'
  return 'world'
}

function normalizeItem(item, category, index) {
  if (typeof item === 'string') {
    return { id: `${category}-${index}`, category, title: '', text: item, meta: '' }
  }
  const actor = item?.actor_name || item?.npc_name || item?.character_name || item?.actor || ''
  const title = item?.title || item?.label || actor || ''
  const text = item?.text || item?.summary || item?.description || item?.message || item?.narrative || item?.detail || ''
  const version = item?.world_version ?? item?.version
  const chapter = item?.chapter ?? item?.chapter_number
  return {
    ...item,
    id: item?.activity_id || item?.id || item?.event_id || `${category}-${index}`,
    category: itemCategory(item, category),
    title,
    text,
    meta: [chapter != null ? `第 ${chapter} 章` : '', version != null ? `v${version}` : ''].filter(Boolean).join(' · '),
  }
}

const normalizedItems = computed(() => {
  const source = props.activityItems
  const items = []
  if (Array.isArray(source)) {
    source.forEach((item, index) => items.push(normalizeItem(item, '', index)))
  } else if (source && typeof source === 'object') {
    const groups = [
      ['npc', source.npc_actions || source.npc || source.agent_actions],
      ['world', source.world_changes || source.world || source.changes],
      ['system', source.system_records || source.system || source.records || source.logs],
    ]
    groups.forEach(([category, group]) => {
      if (!Array.isArray(group)) return
      group.forEach((item, index) => items.push(normalizeItem(item, category, index)))
    })
  }

  if (items.length) return items

  const fallback = []
  props.storyBeats.forEach((beat, index) => {
    const category = itemCategory(beat)
    fallback.push(normalizeItem({
      ...beat,
      text: beat.narrative,
      category,
    }, category, index))
    ;(beat.system_hints || []).forEach((hint, hintIndex) => fallback.push(normalizeItem({
      id: `${beat.event_id || index}-hint-${hintIndex}`,
      category: 'system',
      title: '系统提示',
      text: hint,
      world_version: beat.world_version,
      chapter: beat.chapter,
    }, 'system', hintIndex)))
  })
  return fallback
})

const visibleItems = computed(() => normalizedItems.value.filter((item) => item.category === activeTab.value))
const counts = computed(() => Object.fromEntries(tabs.map((tab) => [
  tab.id,
  normalizedItems.value.filter((item) => item.category === tab.id).length,
])))

async function selectTab(tabId, focus = false) {
  activeTab.value = tabId
  if (!focus) return
  await nextTick()
  const index = tabs.findIndex((tab) => tab.id === tabId)
  tabRefs.value[index]?.focus()
}

function handleTabKeydown(event, index) {
  let nextIndex = index
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % tabs.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + tabs.length) % tabs.length
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = tabs.length - 1
  else return
  event.preventDefault()
  selectTab(tabs[nextIndex].id, true)
}
</script>

<template>
  <section class="activity-panel" aria-label="世界动态内容">
    <div class="activity-tabs" role="tablist" aria-label="世界动态分类">
      <button
        v-for="(tab, index) in tabs"
        :id="`activity-tab-${tab.id}`"
        :key="tab.id"
        :ref="(element) => { if (element) tabRefs[index] = element }"
        type="button"
        role="tab"
        :class="{ active: activeTab === tab.id }"
        :aria-selected="activeTab === tab.id"
        :aria-controls="`activity-panel-${tab.id}`"
        :tabindex="activeTab === tab.id ? 0 : -1"
        @click="selectTab(tab.id)"
        @keydown="handleTabKeydown($event, index)"
      >
        <span>{{ tab.label }}</span>
        <small>{{ counts[tab.id] }}</small>
      </button>
    </div>

    <div
      :id="`activity-panel-${activeTab}`"
      class="activity-list"
      role="tabpanel"
      :aria-labelledby="`activity-tab-${activeTab}`"
      tabindex="0"
    >
      <article v-for="item in visibleItems" :key="item.id" class="activity-item">
        <header v-if="item.title || item.meta">
          <strong>{{ item.title || tabs.find((tab) => tab.id === activeTab)?.label }}</strong>
          <span v-if="item.meta">{{ item.meta }}</span>
        </header>
        <p>{{ item.text || '该动态没有附带文字说明。' }}</p>
      </article>
      <div v-if="!visibleItems.length" class="activity-empty">
        <strong>暂无{{ tabs.find((tab) => tab.id === activeTab)?.label }}</strong>
        <p>世界继续推进后，相关动态会记录在这里。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.activity-panel { display: flex; min-height: 0; flex: 1; flex-direction: column; }
.activity-tabs { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 5px; padding: 4px; border: 1px solid var(--border-soft); border-radius: 10px; background: #121417; }
.activity-tabs button { display: flex; min-width: 0; align-items: center; justify-content: center; gap: 6px; padding: 8px 7px; border: 1px solid transparent; background: transparent; color: var(--text-faint); font-size: 11px; }
.activity-tabs button:hover, .activity-tabs button.active { border-color: #444a54; background: #282c32; color: var(--text); }
.activity-tabs small { min-width: 18px; padding: 1px 5px; border-radius: 999px; background: rgba(255,255,255,.06); color: var(--text-faint); font: 500 9px/1.4 ui-monospace, monospace; }
.activity-list { min-height: 0; flex: 1; margin-top: 10px; overflow-y: auto; padding: 2px 2px 22px; }
.activity-list:focus { outline: none; }
.activity-list:focus-visible { outline: 2px solid rgba(138,180,248,.75); outline-offset: -2px; }
.activity-item { padding: 14px 15px; border: 1px solid var(--border-soft); border-radius: 10px; background: linear-gradient(145deg, rgba(35,39,46,.9), rgba(25,28,33,.9)); }
.activity-item + .activity-item { margin-top: 8px; }
.activity-item header { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; }
.activity-item strong { color: var(--text); font-size: 12px; font-weight: 600; }
.activity-item header span { flex: 0 0 auto; color: var(--text-faint); font: 500 9px/1.4 ui-monospace, monospace; }
.activity-item p { margin-top: 7px; color: var(--text-dim); font-size: 11px; line-height: 1.75; white-space: pre-wrap; }
.activity-empty { margin: 12vh auto 0; padding: 24px; color: var(--text-faint); text-align: center; }
.activity-empty strong { color: var(--text-dim); font-size: 13px; }
.activity-empty p { margin-top: 6px; font-size: 10px; }
@media (max-width: 480px) {
  .activity-tabs button { gap: 4px; padding-inline: 4px; font-size: 10px; }
  .activity-tabs small { display: none; }
}
</style>
