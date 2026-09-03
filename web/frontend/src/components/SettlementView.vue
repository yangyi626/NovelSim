<script setup>
import { computed } from 'vue'

const props = defineProps({
  settlement: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  transitionLoading: { type: Boolean, default: false },
  transitionError: { type: String, default: '' },
  transitionResult: { type: Object, default: null },
})

const emit = defineEmits(['settle', 'transition', 'close', 'system-space'])

const result = computed(() => props.settlement || {})
const settlementData = computed(() => result.value.settlement || result.value.result || result.value)
const dashboardData = computed(() => result.value.dashboard || {})
const status = computed(() => String(
  settlementData.value.status
    || result.value.settlement_status
    || (result.value.settled ? 'settled' : '')
    || 'available',
).toLowerCase())
const isSettled = computed(() => ['settled', 'completed', 'done'].includes(status.value))
const isUnavailable = computed(() => ['unavailable', 'locked', 'inactive'].includes(status.value))
const isInProgress = computed(() => ['in_progress', 'processing', 'running'].includes(status.value))
const ending = computed(() => result.value.ending || result.value.ending_result || settlementData.value.ending || {})
const endingTitle = computed(() => (
  ending.value.title
    || ending.value.name
    || result.value.ending_title
    || settlementData.value.ending_title
    || settlementData.value.title
    || result.value.title
    || '这条世界线的结局'
))
const endingText = computed(() => (
  ending.value.description
    || ending.value.summary
    || ending.value.text
    || result.value.ending_summary
    || settlementData.value.summary
    || settlementData.value.description
    || result.value.description
    || '你的选择已经为这段故事留下了最终答案。'
))
const changes = computed(() => normalizeList(
  result.value.changes
    || result.value.world_changes
    || result.value.canonical_changes
    || result.value.change_summary
    || dashboardData.value.canonical_changes
    || dashboardData.value.recent_world_changes,
))
const memories = computed(() => normalizeList(
  result.value.npc_memories
    || result.value.npc_memory_echoes
    || result.value.memories
    || result.value.memory_echoes
    || dashboardData.value.npc_memory_echoes,
))
const summary = computed(() => (
  result.value.story_summary
    || result.value.plot_summary
    || result.value.narrative_summary
    || result.value.summary
    || settlementData.value.summary
    || ''
))
const points = computed(() => (
  result.value.points
    ?? result.value.score
    ?? result.value.reward_points
    ?? settlementData.value.reward_points
    ?? result.value.total_points
    ?? 0
))
const nextChapter = computed(() => (
  settlementData.value.next_chapter
  || result.value.next_chapter
  || dashboardData.value.settlement?.next_chapter
  || dashboardData.value.next_chapter
  || null
))
const nextChapterStatus = computed(() => String(nextChapter.value?.status || '').toLowerCase())
const nextChapterCreated = computed(() => (
  ['created', 'reused'].includes(nextChapterStatus.value)
  || Boolean(nextChapter.value?.child_session_id)
))
const nextChapterUnlocked = computed(() => (
  ['available', 'unlocked', 'ready'].includes(nextChapterStatus.value)
  || (Boolean(nextChapter.value?.package_id) && !['locked', 'unavailable', 'blocked'].includes(nextChapterStatus.value))
))
const nextChapterLocked = computed(() => Boolean(nextChapter.value) && !nextChapterCreated.value && !nextChapterUnlocked.value)
const inheritancePreview = computed(() => normalizeList(
  settlementData.value.inheritance_preview
  || result.value.inheritance_preview
  || dashboardData.value.settlement?.inheritance_preview,
))
const inheritanceSummary = computed(() => normalizeList(
  props.transitionResult?.inheritance_summary
  || nextChapter.value?.inheritance_summary,
))
const chapterRange = computed(() => {
  const start = nextChapter.value?.chapter_start
  const end = nextChapter.value?.chapter_end
  if (start == null && end == null) return ''
  if (start != null && end != null && String(start) !== String(end)) return `第 ${start}—${end} 章`
  return `第 ${start ?? end} 章`
})

function normalizeList(value) {
  if (!value) return []
  if (!Array.isArray(value) && typeof value === 'object') {
    return Object.entries(value).map(([title, item]) => {
      if (typeof item === 'string' || typeof item === 'number') return { title, text: String(item) }
      return normalizeItem(item, title)
    }).filter((item) => item.text || item.title)
  }
  const list = Array.isArray(value) ? value : [value]
  return list.map((item) => normalizeItem(item)).filter((item) => item.text || item.title)
}

function normalizeItem(item, fallbackTitle = '') {
  if (typeof item === 'string' || typeof item === 'number') return { title: fallbackTitle, text: String(item) }
  return {
    title: item?.title || item?.label || item?.name || item?.npc_name || item?.character_name || fallbackTitle,
    text: item?.text || item?.summary || item?.description || item?.memory || item?.change || item?.message || item?.value || '',
  }
}

const statusLabel = computed(() => {
  if (isSettled.value) return '已结算'
  if (isUnavailable.value) return '尚未达到结算条件'
  if (isInProgress.value) return '结算处理中'
  return '可以结算'
})

const nextChapterLabel = computed(() => {
  if (nextChapterCreated.value) return '下一章世界线已创建'
  if (nextChapterUnlocked.value) return '下一章已解锁'
  return '下一章尚未解锁'
})

function requestTransition() {
  if (nextChapter.value?.package_id) emit('transition', nextChapter.value.package_id)
}
</script>

<template>
  <div class="settlement-overlay" role="dialog" aria-modal="true" aria-label="世界线结算">
    <main class="settlement-view">
      <header class="settlement-header">
        <div>
          <span class="eyebrow">WORLD SETTLEMENT</span>
          <h1>世界线结算</h1>
          <p>{{ statusLabel }}<span v-if="isSettled"> · 结果已保存，可放心重复打开</span></p>
        </div>
        <button class="close-button" type="button" aria-label="关闭结算" @click="emit('close')">×</button>
      </header>

      <div v-if="error" class="settlement-error" role="alert">{{ error }}</div>
      <div v-if="loading && !settlement" class="settlement-loading">正在整理这条世界线的结局…</div>

      <div v-else class="settlement-content">
        <section class="ending-card">
          <span class="section-label">最终结局</span>
          <h2>{{ endingTitle }}</h2>
          <p>{{ endingText }}</p>
        </section>

        <section v-if="summary" class="settlement-section">
          <div class="section-heading"><span>剧情摘要</span></div>
          <p class="summary">{{ summary }}</p>
        </section>

        <div class="settlement-grid">
          <section class="settlement-section">
            <div class="section-heading"><span>这段旅程改变了什么</span><small>{{ changes.length }} 项</small></div>
            <ul v-if="changes.length" class="settlement-list">
              <li v-for="(change, index) in changes" :key="index">
                <strong v-if="change.title">{{ change.title }}</strong>
                <span>{{ change.text }}</span>
              </li>
            </ul>
            <p v-else class="empty">这条世界线没有记录到额外改变。</p>
          </section>

          <section class="settlement-section">
            <div class="section-heading"><span>NPC 记忆</span><small>{{ memories.length }} 条</small></div>
            <ul v-if="memories.length" class="settlement-list memories">
              <li v-for="(memory, index) in memories" :key="index">
                <strong v-if="memory.title">{{ memory.title }}</strong>
                <span>{{ memory.text }}</span>
              </li>
            </ul>
            <p v-else class="empty">角色会带着这段经历继续生活。</p>
          </section>
        </div>

        <section class="points-card">
          <div><span class="section-label">{{ isSettled ? '本次获得积分' : '预计结算积分' }}</span><strong>{{ points }}</strong></div>
          <span>{{ isSettled ? '积分已计入系统空间' : '完成结算后计入系统空间' }}</span>
        </section>

        <section v-if="isSettled && nextChapter" class="next-chapter-card" :class="{ locked: nextChapterLocked, created: nextChapterCreated }">
          <div class="next-chapter-heading">
            <div>
              <span class="section-label">NEXT CHAPTER</span>
              <h2>{{ nextChapter.title || '下一段旅程' }}</h2>
              <p v-if="chapterRange">{{ chapterRange }}</p>
            </div>
            <span class="chapter-state">{{ nextChapterLabel }}</span>
          </div>

          <p v-if="nextChapter.reason" class="next-reason">{{ nextChapter.reason }}</p>

          <div v-if="inheritancePreview.length || inheritanceSummary.length" class="inheritance-block">
            <div class="section-heading">
              <span>{{ inheritanceSummary.length ? '已继承到下一章' : '进入下一章将继承' }}</span>
              <small>{{ (inheritanceSummary.length || inheritancePreview.length) }} 项</small>
            </div>
            <ul class="settlement-list inheritance-list">
              <li v-for="(item, index) in (inheritanceSummary.length ? inheritanceSummary : inheritancePreview)" :key="index">
                <strong v-if="item.title">{{ item.title }}</strong>
                <span>{{ item.text }}</span>
              </li>
            </ul>
          </div>

          <div v-if="transitionError" class="settlement-error transition-error" role="alert">{{ transitionError }}</div>
          <div class="next-chapter-actions">
            <button
              v-if="nextChapterUnlocked || nextChapterCreated"
              class="primary-button"
              type="button"
              :disabled="transitionLoading || !nextChapter.package_id"
              @click="requestTransition"
            >
              {{ transitionLoading ? '正在连接下一章…' : (nextChapterCreated ? '继续下一章' : '进入下一章') }}
            </button>
            <span v-else>{{ nextChapter.reason || '完成当前章节目标后即可解锁。' }}</span>
          </div>
        </section>
      </div>

      <footer class="settlement-actions">
        <button v-if="isSettled" class="primary-button" type="button" @click="emit('system-space')">返回系统空间</button>
        <button v-else-if="!isUnavailable" class="primary-button" type="button" :disabled="loading || isInProgress" @click="emit('settle')">
          {{ isInProgress ? '结算处理中…' : '确认结算' }}
        </button>
        <button v-else class="primary-button" type="button" @click="emit('close')">回到世界线</button>
        <button v-if="!isSettled" class="secondary-button" type="button" @click="emit('close')">稍后再说</button>
        <button v-else class="secondary-button" type="button" @click="emit('close')">回到世界线</button>
      </footer>
    </main>
  </div>
</template>

<style scoped>
.settlement-overlay { position: fixed; z-index: 120; inset: 0; display: flex; align-items: center; justify-content: center; padding: 22px; overflow-y: auto; background: rgba(6, 8, 12, .9); backdrop-filter: blur(10px); }
.settlement-view { width: min(940px, 100%); max-height: calc(100vh - 44px); overflow-y: auto; border: 1px solid #3e4652; border-radius: 16px; background: linear-gradient(145deg, #171b24, #101319); box-shadow: 0 28px 90px rgba(0,0,0,.55); }
.settlement-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; padding: 28px 32px 22px; border-bottom: 1px solid var(--border-soft); }.settlement-header h1 { margin-top: 7px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 30px; font-weight: 550; }.settlement-header p { margin-top: 5px; color: var(--text-faint); font-size: 11px; }.close-button { padding: 0 8px; background: transparent; color: var(--text-dim); font-size: 28px; line-height: 1; }.settlement-content { padding: 24px 32px; }.settlement-error { margin: 16px 32px 0; padding: 10px 12px; border: 1px solid rgba(224,108,117,.4); border-radius: 7px; background: rgba(224,108,117,.1); color: #e9a0a7; font-size: 11px; }.settlement-loading, .empty { padding: 42px 20px; color: var(--text-faint); text-align: center; font-size: 12px; }
.ending-card { padding: 24px; border: 1px solid rgba(142,174,226,.35); border-radius: 12px; background: radial-gradient(circle at 85% 15%, rgba(107,145,216,.18), transparent 35%), rgba(255,255,255,.025); }.section-label, .section-heading span { color: var(--text-faint); font-size: 10px; letter-spacing: .1em; }.ending-card h2 { margin-top: 9px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 25px; font-weight: 550; }.ending-card p, .summary { margin-top: 10px; color: var(--text-dim); font-size: 13px; line-height: 1.85; }.settlement-section { min-width: 0; padding-top: 22px; }.settlement-section + .settlement-section { border-top: 1px solid var(--border-soft); }.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }.section-heading small { color: var(--text-faint); font-size: 10px; }.settlement-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 28px; }.settlement-grid .settlement-section { border-top: 0; }.settlement-list { display: grid; gap: 8px; margin-top: 11px; list-style: none; }.settlement-list li { display: grid; gap: 3px; padding: 10px 12px; border-left: 2px solid #687991; background: rgba(255,255,255,.025); }.settlement-list strong { color: var(--text); font-size: 11px; }.settlement-list span { color: var(--text-dim); font-size: 11px; line-height: 1.6; }.memories li { border-left-color: #a96f79; }.points-card { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-top: 24px; padding: 17px 20px; border: 1px solid rgba(112,181,126,.35); border-radius: 10px; background: rgba(112,181,126,.08); }.points-card strong { display: block; margin-top: 3px; color: #a8d8ae; font: 700 27px/1.1 ui-monospace, monospace; }.points-card > span { color: var(--text-faint); font-size: 11px; }
.next-chapter-card { margin-top: 22px; padding: 20px; border: 1px solid rgba(142,174,226,.42); border-radius: 12px; background: linear-gradient(130deg, rgba(75,105,158,.18), rgba(255,255,255,.025)); }.next-chapter-card.locked { border-color: var(--border); background: rgba(255,255,255,.02); }.next-chapter-card.created { border-color: rgba(112,181,126,.42); background: linear-gradient(130deg, rgba(69,125,81,.15), rgba(255,255,255,.025)); }.next-chapter-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }.next-chapter-heading h2 { margin-top: 6px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 20px; font-weight: 550; }.next-chapter-heading p, .next-reason { color: var(--text-faint); font-size: 10px; }.chapter-state { flex: 0 0 auto; padding: 4px 8px; border: 1px solid currentColor; border-radius: 999px; color: #9bb7e8; font-size: 9px; }.locked .chapter-state { color: var(--text-faint); }.created .chapter-state { color: #a8d8ae; }.next-reason { margin-top: 10px; line-height: 1.65; }.inheritance-block { margin-top: 17px; padding-top: 16px; border-top: 1px solid var(--border-soft); }.inheritance-list li { border-left-color: #8b78ad; }.next-chapter-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin-top: 17px; }.next-chapter-actions > span { color: var(--text-faint); font-size: 10px; }.transition-error { margin: 14px 0 0; }
.settlement-actions { display: flex; justify-content: flex-end; gap: 9px; padding: 18px 32px 24px; border-top: 1px solid var(--border-soft); }.primary-button, .secondary-button { padding: 9px 17px; border: 1px solid #596579; font-size: 11px; }.primary-button { background: #e7edf7; color: #151a22; font-weight: 650; }.secondary-button { background: rgba(255,255,255,.04); color: var(--text-dim); }
@media (max-width: 680px) { .settlement-overlay { align-items: flex-start; padding: 8px; }.settlement-view { max-height: calc(100vh - 16px); border-radius: 11px; }.settlement-header, .settlement-content, .settlement-actions { padding-left: 17px; padding-right: 17px; }.settlement-grid { grid-template-columns: 1fr; gap: 0; }.settlement-grid .settlement-section + .settlement-section { border-top: 1px solid var(--border-soft); }.points-card, .next-chapter-heading { align-items: flex-start; flex-direction: column; }.settlement-actions { align-items: stretch; flex-direction: column-reverse; }.settlement-actions button, .next-chapter-actions button { width: 100%; }.next-chapter-actions { align-items: stretch; flex-direction: column; } }
</style>
