<script setup>
import { computed } from 'vue'

const props = defineProps({
  playerView: { type: Object, default: null },
  state: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  storyMode: { type: String, default: 'replay' },
  showEvidence: { type: Boolean, default: false },
  manuscriptOnly: { type: Boolean, default: false },
  readerOnly: { type: Boolean, default: false },
  ariaLabel: { type: String, default: '小说演化正文' },
  selectedPassageId: { type: String, default: '' },
  rewriteBusyPassageId: { type: String, default: '' },
  rewriteError: { type: String, default: '' },
  revisionHistoryPassageId: { type: String, default: '' },
  revisionHistory: { type: Array, default: () => [] },
  revisionHistoryLoading: { type: Boolean, default: false },
  revisionHistoryError: { type: String, default: '' },
  revisionSelectBusyPassageId: { type: String, default: '' },
})

const emit = defineEmits(['intervene', 'rewrite', 'revision-history', 'select-revision'])

const beats = computed(() => props.playerView?.story_beats || [])
const allPassages = computed(() => Array.isArray(props.playerView?.novel_passages)
  ? props.playerView.novel_passages
  : [])
const passages = computed(() => allPassages.value
  .map((passage, index) => ({ passage, index }))
  .sort((left, right) => {
    const leftOrder = Number(left.passage?.order ?? left.passage?.manuscript_sequence)
    const rightOrder = Number(right.passage?.order ?? right.passage?.manuscript_sequence)
    if (Number.isFinite(leftOrder) && Number.isFinite(rightOrder) && leftOrder !== rightOrder) return leftOrder - rightOrder
    return left.index - right.index
  })
  .map(({ passage }) => passage))
const unavailablePassages = computed(() => passages.value.filter(
  (passage) => passage?.generation_status && passage.generation_status !== 'ready',
))
const readyPassages = computed(() => passages.value.filter(
  (passage) => passage?.generation_status === 'ready' && Array.isArray(passage?.paragraphs) && passage.paragraphs.length,
))
const passageGroups = computed(() => {
  const groups = []
  const byKey = new Map()
  for (const passage of passages.value) {
    const chapter = chapterNumber(passage)
    const entryId = String(passage?.entry_id || '')
    const entryRevision = Number(passage?.entry_revision || 0)
    const key = entryId
      ? `entry:${entryId}:${entryRevision}:chapter:${chapter ?? 'unknown'}`
      : 'unarchived-worldline-continuation'
    let group = byKey.get(key)
    if (!group) {
      group = {
        key,
        entryId,
        entryRevision,
        chapter: entryId ? chapter : null,
        title: entryId ? (passage?.chapter_title || passage?.title || '') : '世界线续篇',
        passages: [],
      }
      byKey.set(key, group)
      groups.push(group)
    }
    group.passages.push(passage)
  }
  return groups
})
const usePassages = computed(() => readyPassages.value.length > 0)
const latestVersion = computed(() => props.state?.version ?? 0)
const hasAnyManuscript = computed(() => allPassages.value.length > 0)
const hasStory = computed(() => (props.manuscriptOnly ? hasAnyManuscript.value : (usePassages.value || beats.value.length > 0)))

function sourceLabel(source) {
  return {
    player: '玩家干预',
    agent: '角色行动',
    environment: '世界事件',
    system: '系统记录',
  }[source] || '世界事件'
}

function chapterNumber(item) {
  return item?.chapter ?? item?.chapter_number ?? item?.story_chapter ?? null
}

function passageKey(passage, index) {
  return passage.passage_id || passage.id || `passage-${chapterNumber(passage) || 'current'}-${index}`
}

function paragraphText(paragraph) {
  if (typeof paragraph === 'string') return paragraph
  return paragraph?.text || paragraph?.content || paragraph?.narrative || paragraph?.paragraph || ''
}

function paragraphKey(paragraph, index) {
  if (typeof paragraph === 'string') return `${index}-${paragraph.slice(0, 24)}`
  return paragraph?.paragraph_id || paragraph?.id || `${index}-${paragraphText(paragraph).slice(0, 24)}`
}

function passageCanRewrite(passage) {
  return passage?.generation_status === 'ready'
    && Boolean(passage?.passage_id)
    && (Boolean(passage?.quality_issues?.length) || passage.passage_id === props.selectedPassageId)
}

function requestRewrite(passage) {
  emit('rewrite', {
    passage_id: passage.passage_id,
    revision: Number(passage.revision ?? passage.current_revision ?? 0),
  })
}

function evidenceRows(item) {
  const evidence = item?.evidence || item?.provenance || {}
  const sources = evidence.sources || item?.source_event_ids || item?.event_ids || item?.evidence_event_ids || []
  const tools = evidence.tool_names || item?.tool_names || (item?.tool_name ? [item.tool_name] : [])
  const actors = evidence.actor_names || item?.actor_names || []
  const targets = evidence.target_names || item?.target_names || []
  const fromVersion = evidence.from_world_version ?? item?.from_world_version
  const toVersion = evidence.to_world_version ?? item?.to_world_version
  const version = evidence.world_version ?? item?.world_version ?? item?.version
  const versionLabel = fromVersion != null && toVersion != null
    ? (fromVersion === toVersion ? `v${toVersion}` : `v${fromVersion}–v${toVersion}`)
    : (version != null ? `v${version}` : '')
  return [
    { label: '世界版本', value: versionLabel },
    { label: '事件来源', value: Array.isArray(sources) ? sources.join('、') : sources },
    { label: '执行动作', value: Array.isArray(tools) ? tools.join('、') : tools },
    { label: '行动角色', value: Array.isArray(actors) ? actors.join('、') : actors },
    { label: '影响对象', value: Array.isArray(targets) ? targets.join('、') : targets },
  ].filter((row) => row.value)
}

function requestRevisionHistory(passage) {
  emit('revision-history', { passage_id: passage.passage_id })
}

function requestSelectRevision(passage, revision) {
  emit('select-revision', {
    passage_id: passage.passage_id,
    revision_number: Number(revision.revision_number),
    expected_revision: Number(passage.revision ?? passage.current_revision ?? 0),
  })
}

function revisionParagraphs(revision) {
  if (Array.isArray(revision?.paragraphs)) return revision.paragraphs
  return revision?.text ? [revision.text] : []
}

function revisionSourceLabel(source) {
  return {
    deterministic: '安全写作器',
    narrative_output: '叙事输出',
    llm: '模型写作器',
    legacy: '历史旧稿',
  }[source] || source || '未知来源'
}
</script>

<template>
  <section class="novel-reader" :aria-label="ariaLabel">
    <div
      v-if="!hasStory"
      class="opening-state"
      :class="{ 'manuscript-empty': manuscriptOnly }"
      :role="manuscriptOnly && hasAnyManuscript ? 'status' : undefined"
    >
      <span class="opening-chapter">
        {{ manuscriptOnly
          ? 'PERSONAL MANUSCRIPT'
          : (playerView?.checkpoint_chapter != null ? `原著起点 · 第 ${playerView.checkpoint_chapter} 章` : '世界线起点') }}
      </span>
      <template v-if="manuscriptOnly">
        <h2>{{ hasAnyManuscript ? '这一段世界已经发生，但小说正文尚未就绪。' : '你的个人小说将在世界发生改变后写入这里。' }}</h2>
        <p>
          {{ unavailablePassages.some((passage) => passage.generation_status === 'failed')
            ? '正文生成暂时失败，但权威世界事件已经保存。你可以回到“世界演化”继续行动，系统稍后可重新生成正文。'
            : '采取行动或启动自动演化后，已提交事件会被整理成连续场景、对白与情绪描写。' }}
        </p>
      </template>
      <template v-else>
        <h2>世界已经初始化，故事正在等待第一次行动。</h2>
        <p v-if="storyMode === 'replay'">
          推进故事后，角色会依据各自的目标、所知线索与世界规则行动；真正发生的事件会成为这条世界线的新篇章。
        </p>
        <p v-else>
          输入你想做的事，故事会从当前章节分岔，并记住你带来的每一次改变。
        </p>
      </template>
      <div class="opening-rule">
        <span></span>
        <b>{{ manuscriptOnly ? `已记录世界版本 v${latestVersion}` : `世界线 · 第 ${latestVersion + 1} 幕` }}</b>
        <span></span>
      </div>
    </div>

    <div v-if="hasAnyManuscript" class="continuous-manuscript">
      <section v-for="group in passageGroups" :key="group.key" class="manuscript-chapter">
        <header class="passage-heading">
          <span v-if="group.chapter != null">CHAPTER {{ String(group.chapter).padStart(2, '0') }}</span>
          <span v-else>WORLDLINE CONTINUATION</span>
          <h2>{{ group.title || `第 ${group.chapter} 章` }}</h2>
        </header>

        <article
          v-for="(passage, index) in group.passages"
          :key="passageKey(passage, index)"
          class="novel-passage"
          :class="`passage-${passage.generation_status || 'ready'}`"
        >
          <template v-if="passage.generation_status === 'ready'">
            <template v-for="(paragraph, paragraphIndex) in passage.paragraphs || []" :key="paragraphKey(paragraph, paragraphIndex)">
              <p v-if="paragraphText(paragraph)" class="manuscript-paragraph">{{ paragraphText(paragraph) }}</p>
            </template>

            <aside v-if="passage.quality_issues?.length" class="quality-warning" role="status">
              <div>
                <strong>这段旧稿已使用安全读者版</strong>
                <p>{{ passage.quality_issues.join('；') }}</p>
              </div>
              <button
                v-if="passageCanRewrite(passage)"
                type="button"
                class="rewrite-passage"
                :disabled="rewriteBusyPassageId === passage.passage_id"
                :aria-label="`重写旧稿：${passage.title || `第 ${group.chapter || '续篇'} 章`}`"
                @click="requestRewrite(passage)"
              >
                {{ rewriteBusyPassageId === passage.passage_id ? '正在重写…' : '重写旧稿' }}
              </button>
            </aside>
            <p v-if="rewriteError && passage.passage_id === selectedPassageId" class="rewrite-error" role="alert">{{ rewriteError }}</p>

            <div class="revision-toolbar">
              <button
                type="button"
                class="revision-history-toggle"
                :aria-expanded="revisionHistoryPassageId === passage.passage_id"
                @click="requestRevisionHistory(passage)"
              >
                {{ revisionHistoryPassageId === passage.passage_id ? '收起版本' : '版本记录' }}
              </button>
              <span>当前版本 v{{ passage.revision || 0 }}</span>
            </div>

            <div
              v-if="revisionHistoryPassageId === passage.passage_id"
              class="revision-history"
              :aria-busy="revisionHistoryLoading"
            >
              <p v-if="revisionHistoryLoading" class="revision-history-state" role="status">正在读取版本记录…</p>
              <p v-else-if="revisionHistoryError" class="revision-history-error" role="alert">{{ revisionHistoryError }}</p>
              <template v-else>
                <article
                  v-for="revision in revisionHistory"
                  :key="`${passage.passage_id}-${revision.revision_number}`"
                  class="revision-card"
                  :class="{ selected: revision.selected }"
                >
                  <header>
                    <div>
                      <strong>版本 {{ revision.revision_number }}</strong>
                      <span>{{ revisionSourceLabel(revision.source) }}</span>
                    </div>
                    <span v-if="revision.selected" class="revision-current">当前使用</span>
                    <button
                      v-else
                      type="button"
                      class="revision-select"
                      :disabled="revisionSelectBusyPassageId === passage.passage_id"
                      @click="requestSelectRevision(passage, revision)"
                    >
                      {{ revisionSelectBusyPassageId === passage.passage_id ? '正在切换…' : '切换到此版本' }}
                    </button>
                  </header>
                  <div class="revision-preview">
                    <p v-for="(paragraph, paragraphIndex) in revisionParagraphs(revision)" :key="`${revision.revision_number}-${paragraphIndex}`">{{ paragraph }}</p>
                  </div>
                </article>
                <p v-if="!revisionHistory.length" class="revision-history-state">暂无可用版本记录。</p>
              </template>
            </div>

            <p v-for="hint in passage.system_hints || []" :key="hint" class="system-hint passage-hint">{{ hint }}</p>

            <details v-if="showEvidence && evidenceRows(passage).length" class="evidence-fold passage-evidence">
              <summary>溯源信息</summary>
              <dl>
                <div v-for="row in evidenceRows(passage)" :key="row.label"><dt>{{ row.label }}</dt><dd>{{ row.value }}</dd></div>
              </dl>
            </details>
          </template>
          <div v-else class="passage-generation-state" role="status">
            <strong>{{ passage.generation_status === 'failed' ? '这一段正文生成失败' : '这一段正文正在整理' }}</strong>
            <p>{{ passage.generation_status === 'failed' ? '已提交的世界事件仍然保留，可稍后重试生成。' : '它在阅读顺序中的位置已经保留，完成后会原位出现。' }}</p>
          </div>
        </article>
      </section>
    </div>

    <div v-if="unavailablePassages.length && usePassages" class="manuscript-status" role="status">
      <strong>部分小说正文尚未就绪</strong>
      <p>
        {{ unavailablePassages.some((passage) => passage.generation_status === 'failed')
          ? '已生成的正文会继续保留；失败部分不会影响已经提交的世界事件。'
          : '已有正文可以继续阅读，其余已提交事件正在整理为连续小说。' }}
      </p>
    </div>

    <template v-if="!manuscriptOnly && !usePassages">
      <article v-for="(beat, index) in beats" :key="beat.event_id || index" class="story-beat">
        <div v-if="index === 0 || beats[index - 1]?.chapter !== beat.chapter" class="chapter-break">
          <span v-if="beat.chapter != null">CHAPTER {{ String(beat.chapter).padStart(2, '0') }}</span>
          <span v-else>WORLDLINE</span>
          <strong>{{ beat.chapter != null ? `第 ${beat.chapter} 章 · 模拟世界线` : '世界线续篇' }}</strong>
        </div>


        <header class="beat-heading">
          <div>
            <span class="beat-index">{{ String(index + 1).padStart(2, '0') }}</span>
            <h2>{{ beat.title }}</h2>
          </div>
          <div v-if="showEvidence" class="beat-badges">
            <span :class="['source-badge', beat.source]">{{ sourceLabel(beat.source) }}</span>
            <span :class="['align-badge', beat.alignment_status]">
              {{ beat.alignment_status === 'matched' ? '与原著一致' : (beat.source === 'player' ? '因你改变' : '新的分支') }}
            </span>
          </div>
        </header>

        <template v-for="(paragraph, paragraphIndex) in beat.paragraphs || []" :key="paragraphKey(paragraph, paragraphIndex)">
          <p v-if="paragraphText(paragraph)" class="narrative-copy">{{ paragraphText(paragraph) }}</p>
        </template>

        <p v-for="hint in beat.system_hints" :key="hint" class="system-hint">{{ hint }}</p>

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
    </template>

    <div v-if="hasStory" class="timeline-tail">
      <span></span>
      <div>
        <b>{{ manuscriptOnly ? `已收录 ${passages.length} 段小说正文` : `故事已推进 ${latestVersion} 幕` }}</b>
        <small>{{ manuscriptOnly ? '新的世界事件提交后，会继续写入这条个人世界线' : (storyMode === 'replay' ? '等待角色继续他们的命运' : '等待你的下一次行动') }}</small>
      </div>
      <span></span>
    </div>

    <button v-if="!readerOnly && storyMode === 'replay'" class="intervene-entry" type="button" @click="emit('intervene')">
      <span>从当前进度进入世界</span>
      <small>切换到穿越干预模式，改变接下来的剧情</small>
    </button>

    <div v-if="loading" class="reader-loading"><i></i> {{ manuscriptOnly ? '世界正在演化，新正文会在事件提交后写入这里…' : '世界正在演化，已提交的事件稍后会出现在正文中…' }}</div>
  </section>
</template>

<style scoped>
.novel-reader {
  min-height: 0;
  flex: 1;
  overflow-y: auto;
  scroll-behavior: smooth;
  scrollbar-gutter: stable;
  padding: clamp(28px, 4vw, 58px) clamp(24px, 7vw, 92px) 80px;
  background: radial-gradient(circle at 50% 0%, rgba(229,197,139,.045), transparent 34%), #15181c;
}
.opening-state { max-width: 760px; margin: 3vh auto 0; padding: 34px 30px 38px; border: 1px solid rgba(229,197,139,.18); border-radius: 18px; background: linear-gradient(145deg, rgba(44,40,34,.82), rgba(27,31,37,.9)); text-align: center; box-shadow: 0 16px 34px rgba(0,0,0,.16), 0 1px 0 rgba(255,255,255,.04) inset; }
.opening-chapter, .chapter-break span, .passage-heading span { color: var(--text-faint); font: 600 10px/1.4 ui-monospace, monospace; letter-spacing: .16em; }
.opening-state h2 { max-width: 650px; margin: 18px auto 12px; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(23px, 3vw, 34px); line-height: 1.45; font-weight: 550; }
.opening-state p { max-width: 590px; margin: 0 auto; color: var(--text-dim); font-size: 13px; line-height: 1.9; }
.opening-rule, .timeline-tail { display: flex; align-items: center; justify-content: center; gap: 14px; margin: 34px auto; }
.opening-rule span, .timeline-tail > span { width: 70px; height: 1px; background: var(--border); }
.opening-rule b { color: var(--text-faint); font: 600 9px/1 ui-monospace, monospace; letter-spacing: .12em; }
.continuous-manuscript { max-width: 780px; margin: 0 auto; padding: clamp(28px, 5vw, 58px) clamp(22px, 7vw, 72px) clamp(42px, 7vw, 78px); border: 1px solid rgba(229,197,139,.14); border-radius: 18px; background: linear-gradient(155deg, rgba(31,32,32,.96), rgba(24,26,29,.94)); box-shadow: 0 20px 55px rgba(0,0,0,.22), 0 1px 0 rgba(255,255,255,.035) inset; }
.manuscript-chapter + .manuscript-chapter { margin-top: 58px; }
.novel-passage + .novel-passage { margin-top: 1.15em; }
.passage-heading { margin-bottom: 30px; padding-bottom: 16px; border-bottom: 1px solid rgba(229,197,139,.18); text-align: center; }
.passage-heading span { display: block; color: #b9a173; }
.passage-heading h2, .passage-heading strong { display: block; margin-top: 8px; color: #eeece5; font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(20px, 3vw, 27px); font-weight: 550; }
.manuscript-paragraph { margin: 0; color: #e1e0dc; font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(15px, 1.6vw, 17px); line-height: 1.95; text-align: start; text-indent: 2em; white-space: pre-wrap; }
.manuscript-paragraph + .manuscript-paragraph { margin-top: .85em; }
.passage-hint { margin-top: 18px; }
.passage-generation-state { margin: 14px 0; padding: 14px 16px; border: 1px dashed rgba(207,151,88,.28); border-radius: 10px; background: rgba(207,151,88,.045); }
.passage-generation-state strong { color: #ddc496; font-size: 12px; }
.passage-generation-state p { margin: 5px 0 0; color: var(--text-faint); font-size: 11px; line-height: 1.7; }
.quality-warning { display: flex; align-items: center; justify-content: space-between; gap: 18px; margin-top: 22px; padding: 13px 14px; border: 1px solid rgba(207,151,88,.3); border-radius: 10px; background: rgba(207,151,88,.07); }
.quality-warning strong { color: #e3cca1; font-size: 12px; }
.quality-warning p { margin: 4px 0 0; color: var(--text-faint); font-size: 10px; line-height: 1.65; }
.rewrite-passage { flex: 0 0 auto; padding: 7px 11px; border: 1px solid #806d49; border-radius: 7px; background: #312c24; color: #eee2c7; font-size: 11px; }
.rewrite-passage:disabled { cursor: wait; opacity: .6; }
.rewrite-error { margin: 8px 0 0; color: #e9a29a; font-size: 11px; }
.revision-toolbar { display: flex; align-items: center; gap: 10px; margin-top: 14px; color: var(--text-faint); font-size: 10px; }
.revision-history-toggle, .revision-select { border: 1px solid rgba(229,197,139,.28); border-radius: 7px; background: rgba(229,197,139,.06); color: #d9c59e; font-size: 10px; padding: 6px 9px; }
.revision-history-toggle:hover, .revision-select:hover { background: rgba(229,197,139,.14); }
.revision-select:disabled { cursor: wait; opacity: .6; }
.revision-history { display: grid; gap: 9px; margin-top: 12px; padding: 12px; border: 1px solid rgba(229,197,139,.14); border-radius: 10px; background: rgba(8,10,12,.2); }
.revision-history-state, .revision-history-error { margin: 0; color: var(--text-faint); font-size: 10px; line-height: 1.7; }
.revision-history-error { color: #e9a29a; }
.revision-card { padding: 11px 12px; border: 1px solid rgba(255,255,255,.08); border-radius: 8px; background: rgba(255,255,255,.018); }
.revision-card.selected { border-color: rgba(229,197,139,.34); background: rgba(229,197,139,.055); }
.revision-card > header { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.revision-card > header > div { display: flex; align-items: baseline; gap: 8px; }
.revision-card strong { color: #e5d2aa; font-size: 11px; }
.revision-card header span { color: var(--text-faint); font-size: 9px; }
.revision-current { color: #d8bd87 !important; }
.revision-preview { margin-top: 8px; color: #c9c8c3; font-family: Georgia, 'Noto Serif SC', serif; font-size: 12px; line-height: 1.75; }
.revision-preview p { margin: 0; }
.revision-preview p + p { margin-top: 5px; }
.manuscript-status { width: min(820px, 100%); margin: 18px auto; padding: 14px 16px; border: 1px solid rgba(207,151,88,.28); border-radius: 10px; background: rgba(207,151,88,.07); }
.manuscript-status strong { color: #ddc496; font-size: 12px; }
.manuscript-status p { margin: 5px 0 0; color: var(--text-faint); font-size: 10px; line-height: 1.65; }
.story-beat { position: relative; max-width: 900px; margin: 0 auto; padding: 30px 42px 36px; border: 1px solid rgba(255,255,255,.085); border-radius: 16px; background: linear-gradient(145deg, rgba(31,35,42,.92), rgba(24,28,34,.84)); box-shadow: 0 14px 34px rgba(0,0,0,.16), 0 1px 0 rgba(255,255,255,.03) inset; }
.story-beat + .story-beat { margin-top: 14px; }
.chapter-break { display: flex; align-items: baseline; gap: 12px; margin: 0 0 34px; padding: 0 0 14px; border-bottom: 1px solid #3d424b; }
.chapter-break span { color: #c3a979; }
.chapter-break strong { color: #e0e2e5; font-family: Georgia, 'Noto Serif SC', serif; font-size: 14px; font-weight: 550; }
.beat-heading, .beat-heading > div, .beat-badges { display: flex; align-items: center; }
.beat-heading { align-items: flex-start; justify-content: space-between; gap: 12px; }
.beat-heading > div:first-child { min-width: 0; gap: 10px; }
.beat-index { min-width: 24px; padding-top: 5px; color: #7b8490; font: 500 10px/1 ui-monospace, monospace; }
.beat-heading h2 { overflow: hidden; margin: 0; color: var(--text); font-family: Georgia, 'Noto Serif SC', serif; font-size: 20px; font-weight: 550; text-overflow: ellipsis; white-space: nowrap; }
.beat-badges { flex: 0 0 auto; gap: 5px; padding-top: 2px; }
.source-badge, .align-badge { padding: 3px 7px; border: 1px solid var(--border-soft); border-radius: 999px; color: var(--text-faint); font-size: 9px; }
.source-badge.player { border-color: rgba(138,180,248,.35); color: var(--player); }
.align-badge.matched { border-color: rgba(112,181,126,.28); color: var(--system); }
.align-badge.new { border-color: rgba(207,151,88,.25); color: #cf9758; }
.narrative-copy { max-width: 740px; margin: 18px 0 0; color: #e4e4e1; font-family: Georgia, 'Noto Serif SC', serif; font-size: 16px; line-height: 1.95; text-align: start; }
blockquote { margin: 18px 0 0 30px; padding: 11px 16px 11px 18px; border-left: 2px solid #9b8254; border-radius: 0 10px 10px 0; background: linear-gradient(90deg, rgba(229,197,139,.085), rgba(229,197,139,.015) 72%, transparent); }
blockquote strong { color: var(--text-dim); font-size: 11px; }
blockquote p { margin: 5px 0 0; color: #f2f1ed; font-family: Georgia, 'Noto Serif SC', serif; font-size: 14px; line-height: 1.7; }
blockquote small { display: block; margin-top: 4px; color: var(--text-faint); font-size: 9px; }
.system-hint { margin: 12px 0 0; padding: 8px 11px; border-left: 2px solid rgba(131,189,140,.62); border-radius: 0 6px 6px 0; background: rgba(131,189,140,.07); color: var(--text-faint); font-size: 10px; }
.evidence-fold { margin-top: 20px; color: var(--text-faint); font-size: 10px; }
.passage-evidence { padding-top: 14px; border-top: 1px solid var(--border-soft); }
.evidence-fold summary { width: fit-content; cursor: pointer; user-select: none; }
.evidence-fold dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px 18px; margin: 12px 0 0; padding: 12px; border: 1px solid var(--border-soft); border-radius: 8px; background: rgba(255,255,255,.018); }
.evidence-fold dl div { display: flex; justify-content: space-between; gap: 8px; }
.evidence-fold dt { color: var(--text-faint); }
.evidence-fold dd { overflow: hidden; margin: 0; color: var(--text-dim); text-align: right; text-overflow: ellipsis; white-space: nowrap; }
.timeline-tail { max-width: 900px; margin: 32px auto 26px; }
.timeline-tail > span { flex: 1; width: auto; }
.timeline-tail div { text-align: center; }
.timeline-tail b, .timeline-tail small { display: block; }
.timeline-tail b { color: var(--text-dim); font-size: 11px; }
.timeline-tail small { margin-top: 3px; color: var(--text-faint); font-size: 9px; }
.intervene-entry { display: block; width: min(900px, 100%); margin: 24px auto 0; padding: 12px 16px; border: 1px solid #756340; border-radius: 9px; background: linear-gradient(135deg, #302b23, #252a32); color: var(--text); text-align: left; }
.intervene-entry:hover { border-color: #8a754d; background: linear-gradient(135deg, #383126, #2b3038); }
.intervene-entry span, .intervene-entry small { display: block; }
.intervene-entry span { color: #eee2c7; font-size: 12px; font-weight: 600; }
.intervene-entry small { margin-top: 3px; color: var(--text-faint); font-size: 9px; }
.reader-loading { position: sticky; bottom: 12px; width: fit-content; margin: 28px auto 0; padding: 7px 11px; border: 1px solid #59606b; border-radius: 999px; background: rgba(31,34,39,.94); color: var(--text-dim); font-size: 10px; box-shadow: 0 6px 20px rgba(0,0,0,.3); }
.reader-loading i { display: inline-block; width: 6px; height: 6px; margin-right: 7px; border-radius: 50%; background: var(--system); animation: pulse 1s infinite; }
@keyframes pulse { 50% { opacity: .25; } }
@media (max-width: 760px) {
  .novel-reader { padding: 20px 16px 60px; }
  .opening-state { margin-top: 1vh; padding: 24px 18px 28px; }
  .continuous-manuscript { padding: 30px 22px 44px; border-radius: 14px; }
  .story-beat { padding: 24px 20px 28px; }
  .beat-heading { align-items: flex-start; flex-direction: column; }
  .narrative-copy { font-size: 14px; }
  .quality-warning { align-items: flex-start; flex-direction: column; }
  .evidence-fold dl { grid-template-columns: 1fr; }
  blockquote { margin-left: 10px; }
}
@media (max-width: 480px) {
  .continuous-manuscript { padding-inline: 18px; }
  .manuscript-paragraph { font-size: 15px; line-height: 2.05; }
  .story-beat { padding-inline: 16px; }
  .narrative-copy { font-size: 15px; }
}
</style>
