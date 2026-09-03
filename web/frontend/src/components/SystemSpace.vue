<script setup>
import { computed } from 'vue'

const props = defineProps({
  worlds: { type: Array, default: () => [] },
  books: { type: Array, default: () => [] },
  history: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
})

const emit = defineEmits(['enter-world', 'continue-session', 'refresh', 'open-library', 'open-menu'])

const featuredBooks = computed(() => props.books.slice(0, 3))
const featuredWorlds = computed(() => props.worlds.slice(0, 3))
const latestSession = computed(() => props.history[0] || null)
const campaigns = computed(() => {
  const grouped = new Map()
  props.history
    .filter((save) => Boolean(String(save.campaign_id || '').trim()))
    .forEach((save) => {
      const key = String(save.campaign_id).trim()
      if (!grouped.has(key)) grouped.set(key, [])
      grouped.get(key).push(save)
    })
  return [...grouped.entries()]
    .map(([campaignId, saves]) => ({
      campaignId,
      saves: [...saves].sort((a, b) => {
        const depthDiff = Number(a.depth || 0) - Number(b.depth || 0)
        if (depthDiff) return depthDiff
        return String(a.created_at || '').localeCompare(String(b.created_at || ''))
      }),
    }))
    .filter(({ saves }) => saves.some((save) => (
      Boolean(String(save.parent_session_id || '').trim())
      || Number(save.depth || 0) > 0
    )))
})

function isCanonical(world) {
  return world.manifest?.entry_kind === 'canonical_checkpoint'
}

function identityLabel(world) {
  if (world.player_identity) return world.player_identity
  if (isCanonical(world)) return '快穿者 · 夜轻歌'
  if (String(world.novel || '').includes('密信')) return '守卫 · 真相追查者'
  return '命运介入者'
}

function missionLabel(world) {
  if (world.mission || world.objective) return world.mission || world.objective
  if (isCanonical(world)) return '在受辱的起点夺回主动权，改写夜轻歌的命运。'
  return world.anchor || '进入故事，在局势失控前完成你的使命。'
}

function chapterLabel(world) {
  const chapters = world.source_chapters || []
  const start = world.manifest?.checkpoint_chapter || 1
  if (!chapters.length) return '序章'
  return isCanonical(world) ? `第 ${start} 章起` : `${chapters.length} 章剧情`
}

function saveChapterLabel(save) {
  return save.chapter_label || save.scenario || save.name || `第 ${Number(save.depth || 0) + 1} 程`
}

function formatTime(value) {
  if (!value) return '最近游玩'
  return new Date(value).toLocaleString('zh-CN', {
    month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}

function settlementInfo(save) {
  const settlement = save?.settlement || {}
  const status = String(settlement.status || save?.settlement_status || '').toLowerCase()
  if (['settled', 'completed', 'done'].includes(status)) return { label: '已结算', cls: 'settled' }
  if (['available', 'ready', 'can_settle'].includes(status) || settlement.can_settle) return { label: '可结算', cls: 'available' }
  return { label: '进行中', cls: 'progress' }
}
</script>

<template>
  <main class="system-space" aria-label="SystemSpace 世界中转站">
    <header class="space-header">
      <div class="space-brand">
        <span class="space-mark">N</span>
        <div><strong>NovelSim</strong><small>系统空间</small></div>
      </div>
      <button class="menu-button" type="button" aria-label="打开更多入口" @click="emit('open-menu')">更多</button>
    </header>

    <section class="hero">
      <div class="hero-copy">
        <span class="kicker">SYSTEM SPACE</span>
        <h1>选择一段人生，<br />从故事里醒来。</h1>
        <p>每次进入都会开启一条独立世界线。你拥有身份、任务与选择，故事会记住你留下的改变。</p>
        <div class="hero-actions">
          <button class="primary" type="button" :disabled="loading" @click="emit('open-library')">选择世界</button>
          <button
            v-if="latestSession"
            class="secondary"
            type="button"
            :disabled="loading"
            @click="emit('continue-session', latestSession.session_id)"
          >继续上次世界线</button>
        </div>
      </div>
      <div class="portal" aria-hidden="true"><i></i><span>世界入口已就绪</span></div>
    </section>

    <div v-if="error" class="space-error">
      <span>{{ error }}</span>
      <button type="button" @click="emit('refresh')">重试</button>
    </div>

    <section class="space-section">
      <div class="section-heading">
        <div><span>可进入世界</span><h2>从这里开始新的命运</h2></div>
        <button type="button" @click="emit('open-library')">查看全部</button>
      </div>
      <div v-if="loading && !featuredBooks.length && !featuredWorlds.length" class="loading-card">正在连接世界书库…</div>
      <div v-else class="world-entries">
        <article v-for="book in featuredBooks" :key="book.book_id" class="entry-card">
          <div class="entry-art"><span>{{ book.chapter_count }} 章已缓存</span></div>
          <div class="entry-copy">
            <span class="entry-kind">原著小说</span>
            <h3>{{ book.novel }}</h3>
            <p class="scenario">整本小说 · revision {{ book.revision }}</p>
            <dl><div><dt>进入方式</dt><dd>选择任意章节，开启独立世界线</dd></div><div><dt>世界状态</dt><dd>已缓存章节目录与正文</dd></div></dl>
            <button type="button" :disabled="loading" @click="emit('open-library')">选择章节</button>
          </div>
        </article>
        <article v-if="!featuredBooks.length" v-for="world in featuredWorlds" :key="world.package_id" class="entry-card">
          <div class="entry-art"><span>{{ chapterLabel(world) }}</span></div>
          <div class="entry-copy"><span class="entry-kind">{{ isCanonical(world) ? '原著世界' : '开放世界' }}</span><h3>{{ world.novel || world.scenario }}</h3><p class="scenario">{{ world.scenario }}</p><dl><div><dt>你的身份</dt><dd>{{ identityLabel(world) }}</dd></div><div><dt>当前任务</dt><dd>{{ missionLabel(world) }}</dd></div></dl><button type="button" :disabled="loading" @click="emit('enter-world', world.package_id)">进入世界</button></div>
        </article>
        <div v-if="!featuredBooks.length && !featuredWorlds.length && !loading" class="loading-card">暂时没有可进入的世界</div>
      </div>
    </section>

    <section v-if="latestSession" class="continue-card">
      <div>
        <span class="kicker">LAST TIMELINE</span>
        <h2>继续上次世界线</h2>
        <p>{{ saveChapterLabel(latestSession) }} · 世界线 v{{ latestSession.version ?? 0 }}</p>
        <small v-if="latestSession.parent_session_id">继承自上一章 · 第 {{ Number(latestSession.depth || 0) + 1 }} 程</small>
      </div>
      <div class="continue-actions">
        <small>{{ formatTime(latestSession.updated_at) }}</small>
        <span class="settlement-status" :class="settlementInfo(latestSession).cls">{{ settlementInfo(latestSession).label }}</span>
        <button type="button" :disabled="loading" @click="emit('continue-session', latestSession.session_id)">继续旅程</button>
      </div>
    </section>

    <section v-if="campaigns.length" class="history-section">
      <div class="section-heading">
        <div><span>WORLD LINES</span><h2>你的跨章旅程</h2></div>
      </div>
      <div class="campaign-list">
        <article v-for="campaign in campaigns" :key="campaign.campaignId" class="campaign-card">
          <div v-if="campaign.saves.length > 1" class="campaign-heading">
            <span>连续旅程</span>
            <small>{{ campaign.saves.length }} 个章节世界线</small>
          </div>
          <div class="history-list">
            <article v-for="(save, index) in campaign.saves" :key="save.session_id" class="history-item" :class="{ child: save.parent_session_id }">
              <span v-if="campaign.saves.length > 1" class="lineage-marker" aria-hidden="true">{{ index + 1 }}</span>
              <div class="history-copy">
                <strong>{{ saveChapterLabel(save) }}</strong>
                <p>{{ save.name || '未命名世界线' }} · 世界线 v{{ save.version ?? 0 }} · {{ formatTime(save.updated_at) }}</p>
                <small v-if="save.parent_session_id">承接上一章 · 深度 {{ Number(save.depth || 0) }}</small>
                <small v-else-if="campaign.saves.length > 1">旅程起点</small>
              </div>
              <div class="history-actions">
                <span class="settlement-status" :class="settlementInfo(save).cls">{{ settlementInfo(save).label }}</span>
                <button type="button" :disabled="loading" @click="emit('continue-session', save.session_id)">进入</button>
              </div>
            </article>
          </div>
        </article>
      </div>
    </section>
  </main>
</template>

<style scoped>
.system-space {
  width: 100%;
  min-height: 100%;
  overflow-y: auto;
  background: radial-gradient(circle at 72% 23%, rgba(86,116,165,.2), transparent 25%), linear-gradient(160deg, #10131a, #080a0e 60%);
  background-repeat: no-repeat;
  background-size: cover;
  color: var(--text);
}
.space-header { display: flex; min-height: 64px; align-items: center; justify-content: space-between; padding: 10px clamp(16px, 4vw, 52px); border-bottom: 1px solid rgba(255,255,255,.08); background: rgba(10,12,16,.72); backdrop-filter: blur(12px); }
.space-brand { display: flex; align-items: center; gap: 10px; }
.space-mark { display: grid; width: 34px; height: 34px; place-items: center; border: 1px solid #555d6d; border-radius: 9px; background: #202631; font: 700 12px/1 ui-monospace, monospace; }
.space-brand strong, .space-brand small { display: block; }
.space-brand strong { font-size: 13px; }.space-brand small { color: var(--text-faint); font-size: 10px; }
.menu-button, .section-heading button { border: 1px solid var(--border); background: rgba(255,255,255,.04); color: var(--text-dim); font-size: 11px; }
.hero { display: grid; min-height: 430px; grid-template-columns: minmax(0, 1.05fr) minmax(280px, .95fr); align-items: center; gap: clamp(30px, 7vw, 100px); width: min(1180px, calc(100% - 40px)); margin: 0 auto; padding: 66px 0 44px; }
.kicker, .entry-kind { color: #91aee0; font: 650 9px/1.4 ui-monospace, monospace; letter-spacing: .16em; }
.hero h1 { margin-top: 16px; font-family: Georgia, 'Noto Serif SC', serif; font-size: clamp(36px, 5.6vw, 72px); line-height: 1.13; font-weight: 540; letter-spacing: -.04em; }
.hero-copy > p { max-width: 580px; margin-top: 20px; color: var(--text-dim); font-size: 14px; line-height: 1.9; }
.hero-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 28px; }.hero-actions button { padding: 11px 18px; font-size: 12px; }
.hero-actions .primary { background: #eef1f6; color: #12151b; font-weight: 700; }.hero-actions .secondary { border: 1px solid #4b5362; background: rgba(255,255,255,.04); color: var(--text); }
.portal { position: relative; display: grid; min-height: 310px; place-items: center; }
.portal::before, .portal::after, .portal i { position: absolute; border-radius: 50%; content: ''; }
.portal::before { width: min(30vw, 330px); aspect-ratio: 1; border: 1px solid rgba(142,174,226,.45); box-shadow: 0 0 90px rgba(78,117,181,.2), inset 0 0 60px rgba(100,140,210,.12); }
.portal::after { width: min(23vw, 245px); aspect-ratio: 1; border: 1px solid rgba(255,255,255,.22); box-shadow: 0 0 28px rgba(154,191,246,.28); }
.portal i { width: min(14vw, 145px); aspect-ratio: 1; background: radial-gradient(circle, rgba(222,235,255,.82), rgba(99,143,216,.16) 36%, transparent 68%); filter: blur(1px); }
.portal span { z-index: 1; margin-top: 235px; color: #aab7cd; font-size: 10px; letter-spacing: .12em; }
.space-error { display: flex; width: min(1180px, calc(100% - 40px)); align-items: center; justify-content: space-between; gap: 12px; margin: 0 auto 20px; padding: 11px 14px; border: 1px solid rgba(224,108,117,.35); border-radius: 9px; background: rgba(224,108,117,.08); color: #e9a0a7; font-size: 12px; }.space-error button { background: transparent; color: inherit; font-size: 11px; }
.space-section, .continue-card, .history-section { width: min(1180px, calc(100% - 40px)); margin: 0 auto; }
.space-section { padding: 24px 0 50px; }.section-heading { display: flex; align-items: end; justify-content: space-between; gap: 18px; }.section-heading span { color: var(--text-faint); font-size: 10px; }.section-heading h2 { margin-top: 4px; font-size: 22px; }.section-heading button { padding: 7px 11px; }
.history-section { padding: 0 0 56px; }.campaign-list { display: grid; gap: 14px; margin-top: 16px; }.campaign-card { overflow: hidden; border: 1px solid rgba(255,255,255,.08); border-radius: 12px; background: rgba(18,22,29,.58); }.campaign-heading { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-bottom: 1px solid rgba(255,255,255,.07); color: #91aee0; font-size: 10px; }.campaign-heading small { color: var(--text-faint); }.history-list { display: grid; }.history-item { position: relative; display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 14px 16px; background: rgba(25,29,37,.7); }.history-item + .history-item { border-top: 1px solid rgba(255,255,255,.07); }.history-item.child { padding-left: 21px; }.history-copy { min-width: 0; flex: 1; }.history-item strong { font-size: 12px; }.history-item p { margin-top: 3px; color: var(--text-faint); font-size: 10px; }.history-item small { display: block; margin-top: 4px; color: #8198bd; font-size: 9px; }.lineage-marker { display: grid; width: 21px; height: 21px; flex: 0 0 21px; place-items: center; border: 1px solid #56637a; border-radius: 50%; background: #232b38; color: #c3d2ec; font: 700 9px/1 ui-monospace, monospace; }.history-actions { display: flex; align-items: center; gap: 12px; }.history-actions button { padding: 7px 12px; border: 1px solid #596579; background: #e7edf7; color: #151a22; font-size: 10px; font-weight: 650; }
.world-entries { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 14px; margin-top: 20px; }.entry-card { overflow: hidden; border: 1px solid rgba(255,255,255,.09); border-radius: 14px; background: rgba(25,29,37,.86); }.entry-art { display: flex; min-height: 92px; align-items: flex-end; padding: 13px; background: linear-gradient(145deg, rgba(98,133,190,.34), rgba(40,45,55,.35)), radial-gradient(circle at 80% 20%, rgba(255,255,255,.15), transparent 22%); }.entry-art span { padding: 3px 7px; border-radius: 999px; background: rgba(5,8,12,.55); color: #dce6f7; font-size: 9px; }.entry-copy { padding: 16px; }.entry-copy h3 { margin-top: 8px; font-family: Georgia, 'Noto Serif SC', serif; font-size: 18px; }.scenario { margin-top: 2px; color: var(--text-faint); font-size: 11px; }.entry-copy dl { display: grid; gap: 9px; margin-top: 16px; }.entry-copy dl div { display: grid; gap: 2px; }.entry-copy dt { color: var(--text-faint); font-size: 9px; }.entry-copy dd { margin: 0; color: var(--text-dim); font-size: 11px; line-height: 1.55; }.entry-copy button { width: 100%; margin-top: 17px; border: 1px solid #515a6b; background: #252c37; color: var(--text); font-size: 11px; }
.loading-card { grid-column: 1 / -1; padding: 42px 20px; border: 1px dashed var(--border); border-radius: 12px; color: var(--text-faint); text-align: center; }
.continue-card { display: flex; align-items: center; justify-content: space-between; gap: 22px; margin-bottom: 60px; padding: 22px; border: 1px solid rgba(141,170,220,.25); border-radius: 14px; background: linear-gradient(100deg, rgba(66,89,126,.18), rgba(255,255,255,.025)); }.continue-card h2 { margin-top: 5px; font-size: 18px; }.continue-card p, .continue-card > div > small, .continue-actions small { margin-top: 4px; color: var(--text-faint); font-size: 11px; }.continue-card > div > small { display: block; color: #8198bd; }.continue-actions { display: flex; align-items: center; gap: 14px; }.continue-actions button { border: 1px solid #596579; background: #e7edf7; color: #151a22; font-size: 11px; font-weight: 650; }.settlement-status { display: inline-flex; align-items: center; padding: 2px 7px; border: 1px solid currentColor; border-radius: 999px; font-size: 9px; white-space: nowrap; }.settlement-status.progress { color: var(--text-faint); }.settlement-status.available { color: #a8d8ae; }.settlement-status.settled { color: #91aee0; }
@media (max-width: 1024px) { .world-entries { grid-template-columns: repeat(2, minmax(0,1fr)); }.portal::before { width: 290px; }.portal::after { width: 215px; }.portal i { width: 130px; } }
@media (max-width: 768px) { .hero { min-height: 0; grid-template-columns: 1fr; padding-top: 48px; }.portal { min-height: 240px; order: -1; }.portal::before { width: 230px; }.portal::after { width: 170px; }.portal i { width: 100px; }.portal span { margin-top: 190px; }.world-entries { grid-template-columns: 1fr; }.continue-card { align-items: flex-start; flex-direction: column; }.continue-actions, .history-actions { width: 100%; justify-content: space-between; }.history-item { align-items: flex-start; flex-wrap: wrap; }.history-actions { padding-left: 33px; } }
@media (max-width: 430px) { .space-header { min-height: 56px; }.hero, .space-section, .continue-card, .history-section, .space-error { width: calc(100% - 24px); }.hero { padding: 34px 0 28px; }.hero h1 { font-size: 38px; }.hero-actions { align-items: stretch; flex-direction: column; }.hero-actions button { width: 100%; }.portal { min-height: 200px; }.portal::before { width: 190px; }.portal::after { width: 142px; }.portal i { width: 82px; }.portal span { margin-top: 160px; }.section-heading { align-items: flex-start; }.entry-art { min-height: 76px; }.continue-actions, .history-actions { align-items: stretch; flex-direction: column; }.continue-actions button, .history-actions button { width: 100%; }.history-actions { padding-left: 0; }.lineage-marker { display: none; } }
</style>
