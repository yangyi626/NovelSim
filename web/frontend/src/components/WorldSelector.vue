<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  packages: { type: Array, default: () => [] },
  books: { type: Array, default: () => [] },
  chaptersByBook: { type: Object, default: () => ({}) },
  currentPackageId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  chapterAccess: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'select', 'select-entry', 'refresh', 'refresh-book'])
const selectedBookId = ref('')
const selectedEntryId = ref('')
const search = ref('')

const orderedBooks = computed(() => [...props.books].sort((a, b) => (
  String(a.novel || a.book_id).localeCompare(String(b.novel || b.book_id), 'zh-CN')
)))
const selectedBook = computed(() => orderedBooks.value.find((item) => item.book_id === selectedBookId.value) || null)
const chapters = computed(() => {
  const items = props.chaptersByBook?.[selectedBookId.value] || []
  const query = search.value.trim().toLowerCase()
  if (!query) return items
  return items.filter((item) => `${item.chapter_number} ${item.title} ${item.label}`.toLowerCase().includes(query))
})
const selectedEntry = computed(() => chapters.value.find((item) => item.entry_id === selectedEntryId.value) || null)

watch(() => props.open, (open) => {
  if (!open) return
  selectedBookId.value = orderedBooks.value[0]?.book_id || ''
  selectedEntryId.value = ''
  search.value = ''
}, { immediate: true })
watch(selectedBookId, async (bookId) => {
  selectedEntryId.value = ''
  if (bookId) emit('refresh-book', bookId)
})

function statusLabel(entry) {
  if (entry.published) return '可直接进入'
  if (entry.content_ready) return '章节世界正在准备'
  return '暂不可用'
}
function canEnter(entry) { return Boolean(entry?.published) }
function selectEntry(entry) {
  if (canEnter(entry)) selectedEntryId.value = entry.entry_id
}
function enterSelected() {
  if (canEnter(selectedEntry.value)) emit('select-entry', selectedEntry.value)
}
</script>

<template>
  <div v-if="open" class="world-select-page" role="dialog" aria-modal="true" aria-labelledby="world-select-title">
    <header class="selector-header">
      <div class="selector-brand"><span class="brand-glyph">N</span><div><span class="eyebrow">SYSTEM SPACE / 世界书库</span><h1 id="world-select-title">选择小说与进入章节</h1></div></div>
      <div class="selector-actions"><button type="button" class="ghost-btn" :disabled="loading" @click="emit('refresh')">刷新</button><button type="button" class="close-btn" aria-label="关闭世界选择" @click="emit('close')">×</button></div>
    </header>

    <main class="selector-content">
      <div v-if="error" class="selector-error">{{ error }}</div>
      <section class="library-layout">
        <aside class="book-list" aria-label="小说列表">
          <div class="section-label">选择小说</div>
          <button v-for="book in orderedBooks" :key="book.book_id" type="button" class="book-card" :class="{ selected: selectedBookId === book.book_id }" @click="selectedBookId = book.book_id">
            <strong>{{ book.novel }}</strong><span>{{ book.chapter_count }} 章 · revision {{ book.revision }}</span>
          </button>
          <div v-if="!orderedBooks.length" class="selector-empty">暂无已缓存小说</div>
        </aside>
        <section class="chapter-panel" aria-label="章节列表">
          <div class="chapter-heading"><div><span class="section-label">选择进入点</span><h2>{{ selectedBook?.novel || '章节目录' }}</h2><p>每章都是独立根世界线；不需要完成前置章节。</p></div><input v-if="selectedBook" v-model="search" type="search" placeholder="搜索章节" aria-label="搜索章节" /></div>
          <div v-if="selectedBook && !chapters.length" class="selector-empty">正在读取章节目录…</div>
          <div v-else class="chapter-grid">
            <button v-for="entry in chapters" :key="entry.entry_id" type="button" class="chapter-card" :class="{ selected: selectedEntryId === entry.entry_id, ready: entry.published }" :disabled="!entry.published" @click="selectEntry(entry)">
              <div class="chapter-number">第 {{ entry.chapter_number }} 章</div>
              <h3>{{ entry.title || '未命名章节' }}</h3>
              <div class="chapter-meta"><span :class="{ ready: entry.published }">{{ statusLabel(entry) }}</span><span v-if="entry.canonical">原著锚点</span></div>
            </button>
          </div>
        </section>
      </section>
    </main>

    <footer class="selector-footer">
      <div v-if="selectedEntry" class="selection-summary"><span>新的独立根世界线</span><strong>{{ selectedBook?.novel }} · 第 {{ selectedEntry.chapter_number }} 章 {{ selectedEntry.title }}</strong><small>不继承其他存档</small></div>
      <div v-else class="selection-summary"><span>章节入口</span><strong>请选择一个已发布章节</strong></div>
      <button type="button" class="enter-btn" :disabled="loading || !selectedEntry" @click="enterSelected">{{ loading ? '世界正在开启…' : '进入本章世界线' }}</button>
    </footer>
  </div>
</template>

<style scoped>
.world-select-page { position: fixed; z-index: 1100; inset: 0; display: flex; min-width: 0; flex-direction: column; background: #0d0f12; color: var(--text); }
.selector-header { display: flex; min-height: 68px; align-items: center; justify-content: space-between; padding: 10px 20px; border-bottom: 1px solid var(--border-soft); background: #17191c; }
.selector-brand, .selector-actions { display: flex; align-items: center; gap: 12px; }.brand-glyph { display: grid; width: 36px; height: 36px; place-items: center; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); font: 700 13px/1 ui-monospace, monospace; }.eyebrow, .section-label { color: var(--text-faint); font: 600 9px/1.2 ui-monospace, monospace; letter-spacing: 1.2px; }.selector-header h1 { margin-top: 3px; font-size: 15px; }.ghost-btn, .close-btn { border: 1px solid var(--border); background: var(--bg-card); color: var(--text-dim); }.ghost-btn { padding: 7px 12px; font-size: 11px; }.close-btn { display: grid; width: 34px; height: 34px; place-items: center; padding: 0; font-size: 21px; }
.selector-content { width: min(1180px, calc(100% - 40px)); min-height: 0; flex: 1; margin: 0 auto; overflow-y: auto; padding: 38px 0 30px; }.selector-error, .selector-empty { padding: 20px; border: 1px solid var(--border-soft); border-radius: 10px; color: var(--text-dim); background: var(--bg-panel); }.selector-error { margin-bottom: 20px; border-color: rgba(224,108,117,.4); color: var(--danger); }.library-layout { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 24px; }.book-list { display: grid; align-content: start; gap: 8px; }.book-card { display: grid; gap: 6px; padding: 14px; border: 1px solid var(--border-soft); border-radius: 10px; background: #181a1d; color: var(--text); text-align: left; }.book-card span, .chapter-panel p { color: var(--text-faint); font-size: 10px; }.book-card.selected { border-color: var(--player); background: rgba(79,108,157,.2); }.chapter-panel { min-width: 0; }.chapter-heading { display: flex; align-items: end; justify-content: space-between; gap: 18px; margin-bottom: 18px; }.chapter-heading h2 { margin-top: 7px; font-size: 25px; }.chapter-heading p { margin-top: 6px; }.chapter-heading input { width: 180px; padding: 9px 11px; border: 1px solid var(--border); border-radius: 7px; background: #181a1d; color: var(--text); }.chapter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }.chapter-card { min-height: 120px; padding: 14px; border: 1px solid var(--border-soft); border-radius: 10px; background: #16181b; color: var(--text); text-align: left; opacity: .58; }.chapter-card.ready { opacity: 1; }.chapter-card.selected { border-color: var(--player); box-shadow: 0 0 0 1px var(--player) inset; }.chapter-number { color: var(--player); font: 600 10px ui-monospace, monospace; }.chapter-card h3 { margin-top: 10px; font-size: 14px; }.chapter-meta { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; color: var(--text-faint); font-size: 9px; }.chapter-meta span.ready { color: var(--system); }.selector-footer { display: flex; min-height: 82px; align-items: center; justify-content: flex-end; gap: 24px; padding: 12px max(20px, calc((100vw - 1180px) / 2)); border-top: 1px solid var(--border-soft); background: #15171a; }.selection-summary { display: grid; min-width: 0; margin-right: auto; }.selection-summary span, .selection-summary small { color: var(--text-faint); font-size: 9px; }.selection-summary strong { margin: 2px 0; overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }.enter-btn { min-width: 220px; padding: 11px 18px; border: 1px solid #626772; background: #ececee; color: #15171a; font-size: 12px; font-weight: 650; }.enter-btn:disabled { opacity: .5; }
@media (max-width: 800px) { .library-layout { grid-template-columns: 1fr; }.book-list { grid-template-columns: repeat(2, minmax(0, 1fr)); }.chapter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 560px) { .selector-content { width: calc(100% - 24px); padding-top: 24px; }.chapter-heading { align-items: stretch; flex-direction: column; }.chapter-heading input { width: 100%; }.chapter-grid, .book-list { grid-template-columns: 1fr; }.selector-footer { align-items: stretch; flex-direction: column; gap: 8px; padding: 10px 12px; }.selection-summary { display: none; }.enter-btn { width: 100%; } }
</style>
