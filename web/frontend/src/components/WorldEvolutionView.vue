<script setup>
import { computed } from 'vue'
import WorldDashboard from './WorldDashboard.vue'

const props = defineProps({
  playerView: { type: Object, default: null },
  state: { type: Object, default: null },
  dashboard: { type: Object, default: null },
  worldMeta: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
  latestTurn: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  settlement: { type: Object, default: null },
  showEvidence: { type: Boolean, default: false },
})

const emit = defineEmits(['suggest', 'settle', 'open-activity'])

const beats = computed(() => Array.isArray(props.playerView?.story_beats)
  ? props.playerView.story_beats
  : [])
const latestVersion = computed(() => props.state?.version ?? 0)
const activityCount = computed(() => props.playerView?.activity_items?.length || 0)

function sourceLabel(source) {
  return {
    player: '玩家干预',
    agent: '角色行动',
    environment: '世界事件',
    system: '系统记录',
  }[source] || '世界事件'
}
</script>

<template>
  <section class="world-evolution-view" aria-label="世界演化记录">
    <div class="evolution-feed">
      <div class="feed-heading">
        <div>
          <span class="section-kicker">AUTHORITATIVE WORLD</span>
          <h2>已发生的剧情</h2>
          <p>这里记录世界实际发生的行动与变化；生成后的文学正文会收录到“我的小说”。</p>
        </div>
        <button type="button" class="activity-entry" @click="emit('open-activity', $event.currentTarget)">
          <span>查看世界动态</span>
          <small>{{ activityCount }}</small>
        </button>
      </div>

      <div v-if="!beats.length" class="evolution-opening">
        <span>世界线 · v{{ latestVersion }}</span>
        <h3>世界已经就绪，等待第一次推进。</h3>
        <p>采取行动或启动自动演化后，角色决策和权威世界变化会先记录在这里。</p>
      </div>

      <div v-else class="beat-list">
        <article v-for="(beat, index) in beats" :key="beat.event_id || index" class="story-beat">
          <div v-if="index === 0 || beats[index - 1]?.chapter !== beat.chapter" class="chapter-break">
            <span>CHAPTER {{ String(beat.chapter || 1).padStart(2, '0') }}</span>
            <strong>第 {{ beat.chapter || 1 }} 章 · 世界演化记录</strong>
          </div>

          <header class="beat-heading">
            <div>
              <span class="beat-index">{{ String(index + 1).padStart(2, '0') }}</span>
              <h3>{{ beat.title || '世界发生了新的变化' }}</h3>
            </div>
            <div class="beat-badges">
              <span :class="['source-badge', beat.source]">{{ sourceLabel(beat.source) }}</span>
              <span v-if="beat.alignment_status" :class="['align-badge', beat.alignment_status]">
                {{ beat.alignment_status === 'matched' ? '与原著一致' : (beat.source === 'player' ? '因你改变' : '新的分支') }}
              </span>
            </div>
          </header>

          <p class="narrative-copy">{{ beat.narrative }}</p>

          <blockquote v-for="dialogue in beat.dialogues || []" :key="`${beat.event_id}-${dialogue.speaker_id}-${dialogue.line}`">
            <strong>{{ dialogue.speaker || dialogue.speaker_name || dialogue.speaker_id || '未知角色' }}</strong>
            <p>“{{ dialogue.line }}”</p>
            <small v-if="dialogue.to || dialogue.to_name || dialogue.to_id">对 {{ dialogue.to || dialogue.to_name || dialogue.to_id }}</small>
          </blockquote>

          <p v-for="hint in beat.system_hints || []" :key="hint" class="system-hint">{{ hint }}</p>

          <details v-if="showEvidence" class="evidence-fold">
            <summary>溯源信息</summary>
            <dl>
              <div><dt>世界版本</dt><dd>v{{ beat.world_version }}</dd></div>
              <div><dt>事件来源</dt><dd>{{ beat.event_id || '未记录' }}</dd></div>
              <div><dt>执行动作</dt><dd>{{ beat.tool_name || 'world_event' }}</dd></div>
              <div><dt>行动角色</dt><dd>{{ beat.actor_names?.join('、') || '系统' }}</dd></div>
              <div><dt>影响对象</dt><dd>{{ beat.target_names?.join('、') || '无' }}</dd></div>
            </dl>
          </details>
        </article>
      </div>
    </div>

    <WorldDashboard
      class="evolution-dashboard"
      :dashboard="dashboard"
      :state="state"
      :world-meta="worldMeta"
      :player-view="playerView"
      :default-actor="defaultActor"
      :latest-turn="latestTurn"
      :loading="loading"
      :settlement="settlement"
      @suggest="emit('suggest', $event)"
      @settle="emit('settle')"
    />
  </section>
</template>

<style scoped>
.world-evolution-view {
  display: grid;
  min-width: 0;
  min-height: 0;
  flex: 1;
  grid-template-columns: minmax(0, 1fr) minmax(280px, 330px);
  overflow: hidden;
  background: #15181c;
}
.evolution-feed {
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: clamp(22px, 3vw, 38px) clamp(20px, 4vw, 54px) 52px;
  scrollbar-gutter: stable;
}
.feed-heading {
  display: flex;
  max-width: 900px;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin: 0 auto 22px;
  padding-bottom: 18px;
  border-bottom: 1px solid #343a45;
}
.section-kicker {
  color: #8d826e;
  font: 600 9px/1.4 ui-monospace, monospace;
  letter-spacing: .15em;
}
.feed-heading h2 {
  margin-top: 6px;
  color: var(--text);
  font-family: Georgia, 'Noto Serif SC', serif;
  font-size: clamp(20px, 2.4vw, 27px);
  font-weight: 550;
}
.feed-heading p {
  max-width: 620px;
  margin-top: 7px;
  color: var(--text-faint);
  font-size: 11px;
  line-height: 1.7;
}
.activity-entry {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid #444a54;
  background: #23272d;
  color: var(--text-dim);
  font-size: 10px;
}
.activity-entry:hover { border-color: #626a77; color: var(--text); }
.activity-entry small {
  min-width: 20px;
  padding: 1px 5px;
  border-radius: 999px;
  background: rgba(255,255,255,.07);
  color: var(--text-faint);
  font: 600 9px/1.4 ui-monospace, monospace;
}
.evolution-opening {
  max-width: 760px;
  margin: 8vh auto 0;
  padding: 34px;
  border: 1px solid rgba(138,180,248,.16);
  border-radius: 16px;
  background: linear-gradient(145deg, rgba(36,42,52,.8), rgba(25,29,35,.9));
  text-align: center;
}
.evolution-opening span { color: var(--text-faint); font: 600 9px/1.4 ui-monospace, monospace; letter-spacing: .12em; }
.evolution-opening h3 { margin-top: 14px; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: 23px; font-weight: 550; }
.evolution-opening p { margin-top: 9px; color: var(--text-faint); font-size: 11px; line-height: 1.75; }
.beat-list { max-width: 900px; margin: 0 auto; }
.story-beat {
  position: relative;
  padding: 28px 36px 32px;
  border: 1px solid rgba(255,255,255,.085);
  border-radius: 15px;
  background: linear-gradient(145deg, rgba(31,35,42,.92), rgba(24,28,34,.84));
  box-shadow: 0 14px 34px rgba(0,0,0,.16), 0 1px 0 rgba(255,255,255,.03) inset;
}
.story-beat + .story-beat { margin-top: 14px; }
.chapter-break { display: flex; align-items: baseline; gap: 12px; margin-bottom: 28px; padding-bottom: 13px; border-bottom: 1px solid #3d424b; }
.chapter-break span { color: #c3a979; font: 600 10px/1.4 ui-monospace, monospace; letter-spacing: .14em; }
.chapter-break strong { color: #e0e2e5; font-family: Georgia, 'Noto Serif SC', serif; font-size: 14px; font-weight: 550; }
.beat-heading, .beat-heading > div, .beat-badges { display: flex; align-items: center; }
.beat-heading { align-items: flex-start; justify-content: space-between; gap: 12px; }
.beat-heading > div:first-child { min-width: 0; gap: 10px; }
.beat-index { min-width: 24px; padding-top: 5px; color: #7b8490; font: 500 10px/1 ui-monospace, monospace; }
.beat-heading h3 { overflow: hidden; margin: 0; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: 19px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.beat-badges { flex: 0 0 auto; gap: 5px; padding-top: 2px; }
.source-badge, .align-badge { padding: 3px 7px; border: 1px solid var(--border-soft); border-radius: 999px; color: var(--text-faint); font-size: 9px; }
.source-badge.player { border-color: rgba(138,180,248,.35); color: var(--player); }
.align-badge.matched { border-color: rgba(112,181,126,.28); color: var(--system); }
.align-badge.new { border-color: rgba(207,151,88,.25); color: #cf9758; }
.narrative-copy { margin: 18px 0 0; color: #e4e4e1; font-family: Georgia, 'Noto Serif SC', serif; font-size: 16px; line-height: 2.05; letter-spacing: .02em; }
blockquote { margin: 18px 0 0 30px; padding: 11px 16px 11px 18px; border-left: 2px solid #9b8254; border-radius: 0 10px 10px 0; background: linear-gradient(90deg, rgba(229,197,139,.085), rgba(229,197,139,.015) 72%, transparent); }
blockquote strong { color: var(--text-dim); font-size: 11px; }
blockquote p { margin: 5px 0 0; color: #f2f1ed; font-family: Georgia, 'Noto Serif SC', serif; font-size: 14px; line-height: 1.7; }
blockquote small { display: block; margin-top: 4px; color: var(--text-faint); font-size: 9px; }
.system-hint { margin: 12px 0 0; padding: 8px 11px; border-left: 2px solid rgba(131,189,140,.62); border-radius: 0 6px 6px 0; background: rgba(131,189,140,.07); color: var(--text-faint); font-size: 10px; }
.evidence-fold { margin-top: 20px; color: var(--text-faint); font-size: 10px; }
.evidence-fold summary { width: fit-content; cursor: pointer; user-select: none; }
.evidence-fold dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 18px; margin: 12px 0 0; padding: 12px; border: 1px solid var(--border-soft); border-radius: 8px; background: rgba(255,255,255,.018); }
.evidence-fold dl div { display: flex; justify-content: space-between; gap: 8px; }
.evidence-fold dt { color: var(--text-faint); }
.evidence-fold dd { overflow: hidden; margin: 0; color: var(--text-dim); text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.evolution-dashboard { border-left: 1px solid #343a45; }
@media (max-width: 980px) {
  .world-evolution-view { grid-template-columns: 1fr; overflow-y: auto; }
  .evolution-feed { overflow: visible; }
  .evolution-dashboard { height: auto; overflow: visible; border-top: 1px solid #343a45; border-left: 0; }
}
@media (max-width: 600px) {
  .evolution-feed { padding: 18px 14px 36px; }
  .feed-heading { align-items: stretch; flex-direction: column; gap: 12px; }
  .activity-entry { justify-content: center; }
  .story-beat { padding: 23px 18px 27px; }
  .beat-heading { align-items: flex-start; flex-direction: column; }
  .narrative-copy { font-size: 15px; }
  blockquote { margin-left: 8px; }
  .evidence-fold dl { grid-template-columns: 1fr; }
}
</style>
