<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  playerView: { type: Object, default: null },
  selectedChapterIndex: { type: Number, default: 0 },
  storyMode: { type: String, default: 'replay' },
})

const activeTab = ref('events')

const metrics = computed(() => props.playerView?.metrics || {})
const selectedChapter = computed(() => (
  props.playerView?.original_chapters?.[props.selectedChapterIndex] || null
))
const comparison = computed(() => props.playerView?.comparison || [])
const matchedCount = computed(() => comparison.value.filter((item) => item.status === 'matched').length)

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`
}

function measuredPercent(value, minimumMatches = 1) {
  return matchedCount.value >= minimumMatches ? percent(value) : '—'
}
</script>

<template>
  <section class="canon-panel">
    <div class="canon-heading">
      <div>
        <span>CANON BASELINE</span>
        <h2>原著对照</h2>
      </div>
      <b v-if="playerView?.canonical_baseline_available" :class="{ diverged: playerView?.diverged }">
        {{ playerView?.diverged ? '世界线已分岔' : '原著复现中' }}
      </b>
      <b v-else>无原著基线</b>
    </div>

    <div v-if="playerView?.canonical_baseline_available" class="canon-metrics">
      <div><strong>{{ matchedCount }}/{{ metrics.canonical_event_count || 0 }}</strong><span>事件对齐</span></div>
      <div><strong>{{ percent(metrics.weighted_event_recall) }}</strong><span>加权召回</span></div>
      <div><strong>{{ measuredPercent(metrics.event_order_accuracy, 2) }}</strong><span>顺序一致</span></div>
    </div>

    <nav class="canon-tabs" aria-label="原著对照视图">
      <button :class="{ active: activeTab === 'events' }" type="button" @click="activeTab = 'events'">事件锚点</button>
      <button :class="{ active: activeTab === 'original' }" type="button" @click="activeTab = 'original'">原著正文</button>
      <button :class="{ active: activeTab === 'diff' }" type="button" @click="activeTab = 'diff'">世界线差异</button>
    </nav>

    <div class="canon-body">
      <div v-if="activeTab === 'events'" class="anchor-list">
        <div v-if="!comparison.length" class="empty-copy">当前世界没有配置事件级原著基线。</div>
        <article v-for="item in comparison" :key="item.canonical_event_id" :class="['anchor-item', item.status]">
          <div class="anchor-line">
            <i></i>
            <span>第 {{ item.chapter }} 章</span>
            <b>{{ item.status === 'matched' ? '已对齐' : '待发生' }}</b>
          </div>
          <p>{{ item.canonical_summary }}</p>
          <small v-if="item.simulated_narrative">模拟：{{ item.simulated_narrative }}</small>
        </article>
      </div>

      <article v-else-if="activeTab === 'original'" class="original-copy">
        <div v-if="selectedChapter">
          <span>你在左侧选择的章节</span>
          <h3>{{ selectedChapter.title }}</h3>
          <p>{{ selectedChapter.excerpt }}</p>
          <small v-if="selectedChapter.truncated">此处仅展示章节开头摘录。</small>
        </div>
        <div v-else class="empty-copy">当前章节没有可用的原著正文。</div>
        <aside>原著内容只供玩家与评测器对照，不会传入 LLM Planner。</aside>
      </article>

      <div v-else class="diff-view">
        <div class="diff-summary">
          <span>{{ storyMode === 'replay' ? '复现阶段' : '穿越世界线' }}</span>
          <h3>{{ playerView?.diverged ? '检测到原著之外的新事件' : '尚未出现世界线分岔' }}</h3>
          <p v-if="storyMode === 'replay'">复现模式中的新增事件会作为偏离信号；它不一定是规则冲突，但会降低原著一致性。</p>
          <p v-else>干预模式允许剧情偏离原著，系统仍会追踪哪些变化由玩家行动造成。</p>
        </div>
        <dl>
          <div><dt>玩家干预</dt><dd>{{ playerView?.player_intervention_count || 0 }}</dd></div>
          <div><dt>新增事件</dt><dd>{{ playerView?.unmatched_beats?.length || 0 }}</dd></div>
          <div><dt>角色一致</dt><dd>{{ measuredPercent(metrics.actor_consistency) }}</dd></div>
          <div><dt>对象一致</dt><dd>{{ measuredPercent(metrics.target_consistency) }}</dd></div>
        </dl>
        <article v-for="beat in playerView?.unmatched_beats || []" :key="beat.event_id" class="diff-item">
          <span>v{{ beat.world_version }} · {{ beat.source === 'player' ? '玩家触发' : '模拟新增' }}</span>
          <p>{{ beat.narrative }}</p>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.canon-panel { display: flex; height: 100%; min-height: 0; flex-direction: column; color: var(--text); }
.canon-heading { display: flex; min-height: 66px; align-items: center; justify-content: space-between; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--border-soft); }
.canon-heading span { color: var(--text-faint); font: 600 9px/1.2 ui-monospace, monospace; letter-spacing: .14em; }
.canon-heading h2 { margin: 4px 0 0; font-size: 14px; }
.canon-heading > b { padding: 4px 7px; border: 1px solid rgba(112,181,126,.25); border-radius: 999px; color: var(--system); font-size: 9px; font-weight: 550; }
.canon-heading > b.diverged { border-color: rgba(207,151,88,.3); color: #cf9758; }
.canon-metrics { display: grid; grid-template-columns: repeat(3, 1fr); gap: 1px; border-bottom: 1px solid var(--border-soft); background: var(--border-soft); }
.canon-metrics div { padding: 11px 7px; background: #181a1d; text-align: center; }
.canon-metrics strong, .canon-metrics span { display: block; }
.canon-metrics strong { color: var(--text); font: 650 14px/1.2 ui-monospace, monospace; }
.canon-metrics span { margin-top: 3px; color: var(--text-faint); font-size: 8px; }
.canon-tabs { display: grid; grid-template-columns: repeat(3, 1fr); padding: 8px 8px 0; border-bottom: 1px solid var(--border-soft); }
.canon-tabs button { padding: 8px 4px; border: 0; border-bottom: 2px solid transparent; border-radius: 0; background: transparent; color: var(--text-faint); font-size: 10px; }
.canon-tabs button.active { border-bottom-color: #737883; color: var(--text); }
.canon-body { min-height: 0; flex: 1; overflow-y: auto; padding: 12px; }
.anchor-list { position: relative; }
.anchor-item { position: relative; margin: 0 0 8px; padding: 10px 10px 10px 20px; border: 1px solid var(--border-soft); border-radius: 8px; background: rgba(255,255,255,.018); }
.anchor-item.pending { opacity: .62; }
.anchor-item::before { position: absolute; top: 0; bottom: -9px; left: 9px; width: 1px; background: var(--border); content: ''; }
.anchor-item:last-child::before { bottom: 50%; }
.anchor-line { display: flex; align-items: center; gap: 7px; }
.anchor-line i { position: absolute; left: 6px; width: 7px; height: 7px; border: 2px solid #25282d; border-radius: 50%; background: #666b75; z-index: 1; }
.anchor-item.matched .anchor-line i { background: var(--system); }
.anchor-line span { flex: 1; color: var(--text-faint); font: 500 9px/1 ui-monospace, monospace; }
.anchor-line b { color: var(--text-faint); font-size: 8px; font-weight: 550; }
.anchor-item.matched .anchor-line b { color: var(--system); }
.anchor-item p { margin: 7px 0 0; color: var(--text-dim); font-size: 11px; line-height: 1.55; }
.anchor-item small { display: block; margin-top: 7px; padding-top: 7px; border-top: 1px dashed var(--border-soft); color: var(--text-faint); font-size: 9px; line-height: 1.45; }
.original-copy > div > span, .diff-summary > span { color: var(--text-faint); font-size: 9px; }
.original-copy h3, .diff-summary h3 { margin: 6px 0 12px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 15px; }
.original-copy p { white-space: pre-line; color: #c9c9c6; font-family: Georgia, 'Noto Serif SC', serif; font-size: 12px; line-height: 1.9; }
.original-copy small { color: var(--text-faint); font-size: 9px; }
.original-copy aside { margin-top: 18px; padding: 10px; border-left: 2px solid #4b4f57; background: rgba(255,255,255,.02); color: var(--text-faint); font-size: 9px; line-height: 1.55; }
.diff-summary { padding: 4px 2px 14px; }
.diff-summary p { color: var(--text-faint); font-size: 10px; line-height: 1.55; }
.diff-view dl { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 0 0 14px; }
.diff-view dl div { display: flex; justify-content: space-between; padding: 8px; border: 1px solid var(--border-soft); border-radius: 6px; color: var(--text-faint); font-size: 9px; }
.diff-view dd { margin: 0; color: var(--text); font: 600 10px/1 ui-monospace, monospace; }
.diff-item { margin-top: 7px; padding: 9px; border: 1px solid rgba(207,151,88,.2); border-radius: 7px; background: rgba(207,151,88,.035); }
.diff-item span { color: #cf9758; font-size: 8px; }
.diff-item p { margin: 5px 0 0; color: var(--text-dim); font-size: 10px; line-height: 1.5; }
.empty-copy { padding: 26px 12px; color: var(--text-faint); font-size: 10px; line-height: 1.6; text-align: center; }
</style>
