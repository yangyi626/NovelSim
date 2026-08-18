<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  packages: { type: Array, default: () => [] },
  currentPackageId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['close', 'select', 'refresh'])
const selectedId = ref('')

const orderedPackages = computed(() => [...props.packages].sort((a, b) => {
  const aCanonical = a.manifest?.entry_kind === 'canonical_checkpoint' ? 1 : 0
  const bCanonical = b.manifest?.entry_kind === 'canonical_checkpoint' ? 1 : 0
  if (aCanonical !== bCanonical) return bCanonical - aCanonical
  if (a.package_id === props.currentPackageId) return -1
  if (b.package_id === props.currentPackageId) return 1
  return String(a.scenario).localeCompare(String(b.scenario), 'zh-CN')
}))

watch([() => props.open, () => props.packages], ([open]) => {
  if (!open) return
  selectedId.value = (
    orderedPackages.value.find((item) => item.manifest?.entry_kind === 'canonical_checkpoint')?.package_id
    || props.currentPackageId
    || orderedPackages.value[0]?.package_id
    || ''
  )
}, { immediate: true })

const selectedPackage = computed(() => (
  orderedPackages.value.find((item) => item.package_id === selectedId.value) || null
))

function isCanonical(pkg) {
  return pkg.manifest?.entry_kind === 'canonical_checkpoint'
}

function chapterRange(pkg) {
  const chapters = pkg.source_chapters || []
  if (!chapters.length) return '未标注章节'
  if (isCanonical(pkg)) {
    const start = pkg.manifest?.checkpoint_chapter || 1
    const targets = pkg.manifest?.target_chapters || []
    return `第 ${start} 章检查点 · 推演至第 ${targets.at(-1) || chapters.length} 章`
  }
  return `${chapters.length} 个来源章节`
}
</script>

<template>
  <div v-if="open" class="world-select-page" role="dialog" aria-modal="true" aria-labelledby="world-select-title">
    <header class="selector-header">
      <div class="selector-brand">
        <span class="brand-glyph">N</span>
        <div>
          <span class="eyebrow">NOVELSIM / WORLD LIBRARY</span>
          <h1 id="world-select-title">选择世界起点</h1>
        </div>
      </div>
      <div class="selector-actions">
        <button type="button" class="ghost-btn" :disabled="loading" @click="emit('refresh')">刷新</button>
        <button type="button" class="close-btn" aria-label="关闭世界选择" @click="emit('close')">×</button>
      </div>
    </header>

    <main class="selector-content">
      <section class="selector-intro">
        <span>WORLD CHECKPOINTS</span>
        <h2>从可信检查点创建一条新世界线</h2>
        <p>每次进入都会复制一份独立初始状态。原著检查点可用于自动复现，也可以作为玩家穿越干预的分叉起点。</p>
      </section>

      <div v-if="error" class="selector-error">{{ error }}</div>
      <div v-if="!orderedPackages.length && loading" class="selector-empty">正在读取世界包…</div>
      <div v-else-if="!orderedPackages.length" class="selector-empty">暂无可启动世界</div>

      <section v-else class="world-grid" aria-label="可用世界">
        <button
          v-for="pkg in orderedPackages"
          :key="pkg.package_id"
          type="button"
          class="world-card"
          :class="{ selected: selectedId === pkg.package_id, canonical: isCanonical(pkg) }"
          @click="selectedId = pkg.package_id"
        >
          <div class="card-heading">
            <span class="world-type" :class="{ canonical: isCanonical(pkg) }">
              {{ isCanonical(pkg) ? '原著复现' : (pkg.source === 'builtin' ? '内置世界' : '创作者世界') }}
            </span>
            <span v-if="pkg.package_id === currentPackageId" class="current-world">当前</span>
          </div>
          <h3>{{ pkg.scenario }}</h3>
          <p class="novel-title">{{ pkg.novel }}</p>
          <p class="world-anchor">{{ pkg.anchor }}</p>
          <div class="checkpoint-row">
            <span>{{ chapterRange(pkg) }}</span>
            <span>{{ pkg.manifest?.character_count || 0 }} 角色</span>
            <span>{{ pkg.manifest?.location_count || 0 }} 地点</span>
          </div>
          <div v-if="isCanonical(pkg)" class="canonical-proof">
            <span>未来事件不进入 Planner</span>
            <span>支持真实 LLM 自动推演</span>
          </div>
        </button>
      </section>
    </main>

    <footer class="selector-footer">
      <div v-if="selectedPackage" class="selection-summary">
        <span>将创建新存档</span>
        <strong>{{ selectedPackage.scenario }}</strong>
        <small>{{ selectedPackage.package_id }}</small>
      </div>
      <button
        type="button"
        class="enter-btn"
        :disabled="loading || !selectedPackage"
        @click="emit('select', selectedPackage.package_id)"
      >
        {{ loading ? '正在初始化…' : (isCanonical(selectedPackage || {}) ? '从第 1 章检查点开始' : '进入所选世界') }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.world-select-page {
  position: fixed;
  z-index: 1100;
  inset: 0;
  display: flex;
  min-width: 0;
  flex-direction: column;
  background: #0d0f12;
  color: var(--text);
}
.selector-header {
  display: flex;
  min-height: 68px;
  align-items: center;
  justify-content: space-between;
  padding: 10px 20px;
  border-bottom: 1px solid var(--border-soft);
  background: #17191c;
}
.selector-brand, .selector-actions { display: flex; align-items: center; gap: 12px; }
.brand-glyph { display: grid; width: 36px; height: 36px; place-items: center; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); font: 700 13px/1 ui-monospace, monospace; }
.eyebrow { color: var(--text-faint); font: 600 9px/1.2 ui-monospace, monospace; letter-spacing: 1.2px; }
.selector-header h1 { margin-top: 3px; font-size: 15px; }
.ghost-btn, .close-btn { border: 1px solid var(--border); background: var(--bg-card); color: var(--text-dim); }
.ghost-btn { padding: 7px 12px; font-size: 11px; }
.close-btn { display: grid; width: 34px; height: 34px; place-items: center; padding: 0; font-size: 21px; }
.selector-content { width: min(1180px, calc(100% - 40px)); min-height: 0; flex: 1; margin: 0 auto; overflow-y: auto; padding: 46px 0 36px; }
.selector-intro { max-width: 680px; }
.selector-intro > span { color: var(--player); font: 600 9px/1 ui-monospace, monospace; letter-spacing: 1.4px; }
.selector-intro h2 { margin-top: 9px; font-size: clamp(23px, 3vw, 34px); letter-spacing: -.025em; }
.selector-intro p { margin-top: 10px; color: var(--text-dim); font-size: 13px; line-height: 1.7; }
.selector-error, .selector-empty { margin-top: 28px; padding: 20px; border: 1px solid var(--border-soft); border-radius: 10px; color: var(--text-dim); background: var(--bg-panel); }
.selector-error { border-color: rgba(224,108,117,.4); color: var(--danger); }
.world-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-top: 32px; }
.world-card {
  position: relative;
  display: flex;
  min-height: 278px;
  flex-direction: column;
  align-items: stretch;
  padding: 18px;
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: #181a1d;
  color: var(--text);
  text-align: left;
}
.world-card:hover { border-color: #4b4f57; background: #1c1f23; }
.world-card.selected { border-color: #737984; box-shadow: 0 0 0 1px #737984 inset; }
.world-card.canonical { background: linear-gradient(145deg, rgba(54,67,91,.5), #181a1d 55%); }
.world-card.canonical.selected { border-color: var(--player); box-shadow: 0 0 0 1px var(--player) inset, 0 18px 45px rgba(0,0,0,.18); }
.card-heading { display: flex; align-items: center; justify-content: space-between; }
.world-type, .current-world { padding: 3px 7px; border: 1px solid var(--border); border-radius: 999px; color: var(--text-faint); font-size: 9px; }
.world-type.canonical { border-color: rgba(138,180,248,.4); background: rgba(138,180,248,.08); color: var(--player); }
.current-world { color: var(--system); }
.world-card h3 { margin-top: 20px; font-size: 17px; line-height: 1.4; }
.novel-title { margin-top: 3px; color: var(--text-faint); font-size: 11px; }
.world-anchor { margin-top: 17px; color: var(--text-dim); font-size: 12px; line-height: 1.65; }
.checkpoint-row { display: flex; flex-wrap: wrap; gap: 5px; margin-top: auto; padding-top: 20px; }
.checkpoint-row span { padding: 3px 6px; border-radius: 4px; background: rgba(255,255,255,.04); color: var(--text-faint); font-size: 9px; }
.canonical-proof { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; color: var(--system); font-size: 9px; }
.canonical-proof span::before { margin-right: 4px; content: '✓'; }
.selector-footer { display: flex; min-height: 82px; align-items: center; justify-content: flex-end; gap: 24px; padding: 12px max(20px, calc((100vw - 1180px) / 2)); border-top: 1px solid var(--border-soft); background: #15171a; }
.selection-summary { display: grid; min-width: 0; margin-right: auto; }
.selection-summary span, .selection-summary small { color: var(--text-faint); font-size: 9px; }
.selection-summary strong { margin: 2px 0; overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.selection-summary small { font-family: ui-monospace, monospace; }
.enter-btn { min-width: 220px; padding: 11px 18px; border: 1px solid #626772; background: #ececee; color: #15171a; font-size: 12px; font-weight: 650; }
.enter-btn:hover:not(:disabled) { background: #fff; }
@media (max-width: 900px) {
  .world-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 620px) {
  .selector-header { min-height: 60px; padding: 8px 12px; }
  .selector-content { width: calc(100% - 24px); padding: 26px 0 24px; }
  .world-grid { grid-template-columns: 1fr; margin-top: 22px; }
  .world-card { min-height: 240px; }
  .selector-footer { align-items: stretch; flex-direction: column; gap: 8px; padding: 10px 12px; }
  .selection-summary { display: none; }
  .enter-btn { width: 100%; min-width: 0; }
}
</style>
