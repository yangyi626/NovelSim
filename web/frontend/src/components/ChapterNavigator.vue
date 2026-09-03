<script setup>
import { computed } from 'vue'

const props = defineProps({
  worldMeta: { type: Object, default: null },
  state: { type: Object, default: null },
  selectedIndex: { type: Number, default: 0 },
  progressChapter: { type: Number, default: 0 },
})

const emit = defineEmits(['select'])

function chapterLabel(chapter, index) {
  if (typeof chapter === 'object' && chapter !== null) {
    return chapter.title || chapter.name || chapter.label || `第 ${index + 1} 章`
  }
  if (typeof chapter === 'number') return `第 ${chapter} 章`
  return String(chapter || `第 ${index + 1} 章`)
}

const chapters = computed(() => {
  const source = props.worldMeta?.source_chapters || []
  const normalized = source.map((chapter, index) => ({
    index,
    number: typeof chapter === 'number' ? chapter : index + 1,
    label: chapterLabel(chapter, index),
  }))
  return normalized.length ? normalized : [{ index: 0, number: 1, label: '当前剧情片段' }]
})

const currentIndex = computed(() => {
  const flags = props.state?.flags || {}
  const raw = props.progressChapter || flags['canonical.checkpoint_chapter'] || flags.current_chapter
  const number = Number(raw)
  if (!Number.isFinite(number)) return 0
  const exact = chapters.value.findIndex((chapter) => chapter.number === number)
  return exact >= 0 ? exact : Math.max(0, Math.min(number - 1, chapters.value.length - 1))
})
</script>

<template>
  <nav class="chapter-nav" aria-label="小说章节">
    <div class="novel-block">
      <span class="nav-kicker">NOVEL WORLD</span>
      <h2>{{ worldMeta?.novel || '未命名小说' }}</h2>
      <p>{{ worldMeta?.scenario || '世界推演' }}</p>
    </div>

    <div class="section-label">
      <span>章节</span>
      <b>{{ chapters.length }}</b>
    </div>

    <div class="chapter-list">
      <button
        v-for="chapter in chapters"
        :key="`${chapter.index}-${chapter.label}`"
        class="chapter-item"
        :class="{ selected: chapter.index === selectedIndex, current: chapter.index === currentIndex }"
        type="button"
        @click="emit('select', chapter.index)"
      >
        <span class="chapter-number">{{ String(chapter.index + 1).padStart(2, '0') }}</span>
        <span class="chapter-copy">
          <strong>{{ chapter.label }}</strong>
          <small>{{ chapter.index === currentIndex ? '世界当前进度' : '原著时间线' }}</small>
        </span>
        <i v-if="chapter.index === currentIndex" title="当前章节"></i>
      </button>
    </div>

    <div class="anchor-card">
      <span>剧情锚点</span>
      <p>{{ worldMeta?.anchor || '等待载入世界设定' }}</p>
      <div class="anchor-meta">
        <b>v{{ state?.version ?? 0 }}</b>
        <span>{{ Object.keys(state?.characters || {}).length }} 位角色</span>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.chapter-nav {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  padding: 18px 12px 12px;
  color: var(--text);
}
.novel-block { padding: 0 8px 16px; border-bottom: 1px solid var(--border-soft); }
.nav-kicker { color: var(--text-faint); font: 600 9px/1.2 ui-monospace, monospace; letter-spacing: 1.3px; }
.novel-block h2 { margin-top: 7px; font-size: 17px; line-height: 1.35; }
.novel-block p { margin-top: 3px; color: var(--text-dim); font-size: 12px; }
.section-label {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 17px 8px 8px;
  color: var(--text-faint);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
}
.section-label b { font: 500 10px/1 ui-monospace, monospace; }
.chapter-list { min-height: 0; flex: 1; overflow-y: auto; }
.chapter-item {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  margin-bottom: 3px;
  padding: 9px 10px;
  border: 1px solid transparent;
  border-radius: 8px;
  background: transparent;
  color: var(--text-dim);
  text-align: left;
}
.chapter-item:hover { background: var(--bg-card); color: var(--text); }
.chapter-item.selected { border-color: var(--border); background: var(--selection); color: var(--text); }
.chapter-number { color: var(--text-faint); font: 500 10px/1 ui-monospace, monospace; }
.chapter-copy { min-width: 0; flex: 1; }
.chapter-copy strong, .chapter-copy small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chapter-copy strong { font-size: 12px; font-weight: 550; }
.chapter-copy small { margin-top: 2px; color: var(--text-faint); font-size: 9px; }
.chapter-item i { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: var(--system); box-shadow: 0 0 0 3px rgba(112, 181, 126, .1); }
.anchor-card { padding: 12px; border: 1px solid var(--border-soft); border-radius: 9px; background: rgba(255,255,255,.025); }
.anchor-card > span { color: var(--text-faint); font-size: 9px; letter-spacing: .08em; }
.anchor-card p { margin-top: 4px; color: var(--text-dim); font-size: 11px; line-height: 1.5; }
.anchor-meta { display: flex; justify-content: space-between; margin-top: 10px; color: var(--text-faint); font-size: 9px; }
.anchor-meta b { color: var(--text-dim); font: 600 10px/1 ui-monospace, monospace; }
.chapter-nav {
  padding: 16px 14px 12px;
}
.novel-block {
  padding-inline: 6px;
  border-bottom-color: #30343b;
}
.novel-block h2 { color: #e2e5ea; font-size: 16px; }
.section-label { padding: 14px 6px 8px; }
.chapter-item {
  position: relative;
  gap: 11px;
  margin-bottom: 2px;
  padding: 10px 9px;
  border-radius: 9px;
}
.chapter-item::before {
  width: 1px;
  height: 100%;
  position: absolute;
  top: 0;
  left: 17px;
  background: #30343b;
  content: '';
}
.chapter-item.selected::before { background: #8ab4f8; }
.chapter-number { position: relative; z-index: 1; min-width: 18px; text-align: center; }
.chapter-item.current .chapter-number { color: var(--system); }
.chapter-copy strong { font-size: 11px; }
.anchor-card {
  margin-top: 8px;
  border-color: #30343b;
  background: rgba(255,255,255,.035);
}
</style>
