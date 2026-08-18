<script setup>
import { computed } from 'vue'

const props = defineProps({
  playerView: { type: Object, default: null },
  state: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  storyMode: { type: String, default: 'replay' },
})

const emit = defineEmits(['intervene'])

const beats = computed(() => props.playerView?.story_beats || [])
const latestVersion = computed(() => props.state?.version ?? 0)

function sourceLabel(source) {
  return {
    player: '玩家干预',
    agent: '角色行动',
    environment: '世界事件',
  }[source] || '世界事件'
}
</script>

<template>
  <section class="novel-reader" aria-label="小说演化正文">
    <div v-if="!beats.length" class="opening-state">
      <span class="opening-chapter">原著起点 · 第 {{ playerView?.checkpoint_chapter || 1 }} 章</span>
      <h2>世界已经初始化，故事正在等待第一次行动。</h2>
      <p v-if="storyMode === 'replay'">
        启动自动演化后，真实 LLM 将依据角色目标、已知事实与世界规则生成联合计划；只有通过校验的动作才会写入这里。
      </p>
      <p v-else>
        输入你想做的事，系统会从当前检查点创建新的穿越世界线，并保留每一步产生的状态变化。
      </p>
      <div class="opening-rule">
        <span></span>
        <b>WORLD v{{ latestVersion }}</b>
        <span></span>
      </div>
    </div>

    <article v-for="(beat, index) in beats" :key="beat.event_id" class="story-beat">
      <div v-if="index === 0 || beats[index - 1]?.chapter !== beat.chapter" class="chapter-break">
        <span>CHAPTER {{ String(beat.chapter).padStart(2, '0') }}</span>
        <strong>第 {{ beat.chapter }} 章 · 模拟世界线</strong>
      </div>

      <header class="beat-heading">
        <div>
          <span class="beat-index">{{ String(index + 1).padStart(2, '0') }}</span>
          <h2>{{ beat.title }}</h2>
        </div>
        <div class="beat-badges">
          <span :class="['source-badge', beat.source]">{{ sourceLabel(beat.source) }}</span>
          <span :class="['align-badge', beat.alignment_status]">
            {{ beat.alignment_status === 'matched' ? '原著事件已对齐' : '新增剧情' }}
          </span>
        </div>
      </header>

      <p class="narrative-copy">{{ beat.narrative }}</p>

      <blockquote v-for="dialogue in beat.dialogues" :key="`${beat.event_id}-${dialogue.speaker_id}-${dialogue.line}`">
        <strong>{{ dialogue.speaker || '未知角色' }}</strong>
        <p>“{{ dialogue.line }}”</p>
        <small v-if="dialogue.to">对 {{ dialogue.to }}</small>
      </blockquote>

      <p v-for="hint in beat.system_hints" :key="hint" class="system-hint">{{ hint }}</p>

      <details class="evidence-fold">
        <summary>查看这段剧情的执行证据</summary>
        <dl>
          <div><dt>世界版本</dt><dd>v{{ beat.world_version }}</dd></div>
          <div><dt>受限工具</dt><dd>{{ beat.tool_name || 'world_event' }}</dd></div>
          <div><dt>行动角色</dt><dd>{{ beat.actor_names?.join('、') || '系统' }}</dd></div>
          <div><dt>影响对象</dt><dd>{{ beat.target_names?.join('、') || '无' }}</dd></div>
        </dl>
      </details>
    </article>

    <div v-if="beats.length" class="timeline-tail">
      <span></span>
      <div>
        <b>已推演至世界 v{{ latestVersion }}</b>
        <small>{{ storyMode === 'replay' ? '等待下一幕原著复现规划' : '等待你的下一次干预' }}</small>
      </div>
      <span></span>
    </div>

    <button v-if="storyMode === 'replay'" class="intervene-entry" type="button" @click="emit('intervene')">
      <span>从当前进度进入世界</span>
      <small>切换到穿越干预模式，改变接下来的剧情</small>
    </button>

    <div v-if="loading" class="reader-loading"><i></i> 世界正在演化，已提交的事件稍后会出现在正文中…</div>
  </section>
</template>

<style scoped>
.novel-reader {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  scroll-behavior: smooth;
  padding: clamp(28px, 4vw, 58px) clamp(24px, 7vw, 92px) 80px;
  background:
    linear-gradient(90deg, transparent, rgba(255,255,255,.012) 48%, transparent),
    #17191c;
}
.opening-state { max-width: 700px; margin: 8vh auto 0; text-align: center; }
.opening-chapter, .chapter-break span { color: var(--text-faint); font: 600 10px/1.4 ui-monospace, monospace; letter-spacing: .16em; }
.opening-state h2 { margin: 18px auto 12px; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(23px, 3vw, 34px); line-height: 1.45; font-weight: 550; }
.opening-state p { max-width: 590px; margin: 0 auto; color: var(--text-dim); font-size: 13px; line-height: 1.9; }
.opening-rule, .timeline-tail { display: flex; align-items: center; justify-content: center; gap: 14px; margin: 34px auto; }
.opening-rule span, .timeline-tail > span { width: 70px; height: 1px; background: var(--border); }
.opening-rule b { color: var(--text-faint); font: 600 9px/1 ui-monospace, monospace; letter-spacing: .12em; }
.story-beat { position: relative; max-width: 760px; margin: 0 auto; padding: 22px 0 34px; border-bottom: 1px solid var(--border-soft); }
.chapter-break { display: flex; align-items: baseline; gap: 12px; margin: 8px 0 34px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
.chapter-break strong { color: var(--text-dim); font-family: Georgia, 'Noto Serif SC', serif; font-size: 14px; font-weight: 550; }
.beat-heading, .beat-heading > div, .beat-badges { display: flex; align-items: center; }
.beat-heading { justify-content: space-between; gap: 12px; }
.beat-heading > div:first-child { min-width: 0; gap: 10px; }
.beat-index { color: var(--text-faint); font: 500 10px/1 ui-monospace, monospace; }
.beat-heading h2 { overflow: hidden; margin: 0; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: 18px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.beat-badges { flex: 0 0 auto; gap: 5px; }
.source-badge, .align-badge { padding: 3px 7px; border: 1px solid var(--border-soft); border-radius: 999px; color: var(--text-faint); font-size: 9px; }
.source-badge.player { border-color: rgba(138,180,248,.35); color: var(--player); }
.align-badge.matched { border-color: rgba(112,181,126,.28); color: var(--system); }
.align-badge.new { border-color: rgba(207,151,88,.25); color: #cf9758; }
.narrative-copy { margin: 18px 0 0; color: #d8d8d6; font-family: Georgia, 'Noto Serif SC', serif; font-size: 15px; line-height: 2.05; letter-spacing: .02em; }
blockquote { margin: 18px 0 0 18px; padding: 5px 0 5px 16px; border-left: 2px solid #50545d; }
blockquote strong { color: var(--text-dim); font-size: 11px; }
blockquote p { margin: 5px 0 0; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: 14px; line-height: 1.7; }
blockquote small { display: block; margin-top: 4px; color: var(--text-faint); font-size: 9px; }
.system-hint { margin: 12px 0 0; color: var(--text-faint); font-size: 10px; }
.evidence-fold { margin-top: 20px; color: var(--text-faint); font-size: 10px; }
.evidence-fold summary { width: fit-content; cursor: pointer; user-select: none; }
.evidence-fold dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 18px; margin: 12px 0 0; padding: 12px; border: 1px solid var(--border-soft); border-radius: 8px; background: rgba(255,255,255,.018); }
.evidence-fold dl div { display: flex; justify-content: space-between; gap: 8px; }
.evidence-fold dt { color: var(--text-faint); }
.evidence-fold dd { overflow: hidden; margin: 0; color: var(--text-dim); text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.timeline-tail { max-width: 760px; margin-top: 38px; }
.timeline-tail > span { flex: 1; width: auto; }
.timeline-tail div { text-align: center; }
.timeline-tail b, .timeline-tail small { display: block; }
.timeline-tail b { color: var(--text-dim); font-size: 11px; }
.timeline-tail small { margin-top: 3px; color: var(--text-faint); font-size: 9px; }
.intervene-entry { display: block; width: min(460px, 100%); margin: 24px auto 0; padding: 12px 16px; border: 1px solid #454952; border-radius: 9px; background: #202328; color: var(--text); text-align: left; }
.intervene-entry:hover { border-color: #606570; background: #272a30; }
.intervene-entry span, .intervene-entry small { display: block; }
.intervene-entry span { font-size: 12px; font-weight: 600; }
.intervene-entry small { margin-top: 3px; color: var(--text-faint); font-size: 9px; }
.reader-loading { position: sticky; bottom: 12px; width: fit-content; margin: 28px auto 0; padding: 7px 11px; border: 1px solid var(--border); border-radius: 999px; background: rgba(31,34,39,.94); color: var(--text-dim); font-size: 10px; box-shadow: 0 6px 20px rgba(0,0,0,.3); }
.reader-loading i { display: inline-block; width: 6px; height: 6px; margin-right: 7px; border-radius: 50%; background: var(--system); animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: .25; } }
@media (max-width: 760px) {
  .novel-reader { padding: 24px 18px 60px; }
  .opening-state { margin-top: 5vh; }
  .beat-heading { align-items: flex-start; flex-direction: column; }
  .narrative-copy { font-size: 14px; }
  .evidence-fold dl { grid-template-columns: 1fr; }
}
</style>
