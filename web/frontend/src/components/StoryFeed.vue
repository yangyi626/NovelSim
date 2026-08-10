<script setup>
import { ref, watch, nextTick } from 'vue'

const props = defineProps({
  turns: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  defaultActor: { type: String, default: '' },
  state: { type: Object, default: null },
})

const feedEl = ref(null)

// 新回合自动滚到底
watch(() => props.turns.length, async () => {
  await nextTick()
  if (feedEl.value) feedEl.value.scrollTop = feedEl.value.scrollHeight
})
watch(() => props.loading, async () => {
  await nextTick()
  if (feedEl.value) feedEl.value.scrollTop = feedEl.value.scrollHeight
})

// 角色显示名查表 (id -> display_name)
function charName(id) {
  const c = props.state?.characters?.[id]
  return c?.display_name || id
}

// 状态文案
function statusBadge(turn) {
  switch (turn.status) {
    case 'committed': return null
    case 'rejected': return { text: '⛔ 规则拒绝', cls: 'badge-reject' }
    case 'parse_failed': return { text: '❓ 无法理解', cls: 'badge-warn' }
    case 'propose_failed': return { text: '⚠️ 推演失败', cls: 'badge-warn' }
    case 'narrate_failed': return { text: '⚠️ 叙事失败', cls: 'badge-warn' }
    case 'error': return { text: '❌ 错误', cls: 'badge-reject' }
    default: return null
  }
}

// NPC 反应徽章：把 id 列表转成名字
function npcNames(turn) {
  return (turn.npc_reactions || []).map(charName)
}
</script>

<template>
  <div class="feed" ref="feedEl">
    <!-- 空状态 -->
    <div v-if="turns.length === 0 && !loading" class="empty">
      <div class="empty-title">华容巷 · 风波将起</div>
      <div class="empty-desc">
        你以快穿者身份接管了夜轻歌的身体——北月国闻名的废柴三小姐。<br />
        她正被诬陷通奸、当众受辱。下一步，由你决定。<br />
        在下方输入你想做的事，世界会因你而改变。
      </div>
    </div>

    <template v-for="(turn, i) in turns" :key="i">
      <!-- 玩家输入 -->
      <div v-if="turn.player_input" class="player-input">
        <span class="pi-label">你说</span>
        <span class="pi-text">{{ turn.player_input }}</span>
      </div>

      <!-- 回合产物卡片 -->
      <div v-else class="turn-card" :class="{ 'turn-error': ['error','rejected','parse_failed','propose_failed','narrate_failed'].includes(turn.status) }">
        <!-- 状态徽章 (非 committed) -->
        <div v-if="statusBadge(turn)" class="status-badge" :class="statusBadge(turn).cls">
          {{ statusBadge(turn).text }}
          <span v-if="turn.rule_reason" class="badge-reason"> · {{ turn.rule_reason }}</span>
        </div>
        <div v-if="turn.error && turn.status === 'error'" class="error-text">{{ turn.error }}</div>

        <div v-if="turn.status === 'rejected' && (turn.rejection_code || turn.rejection_message)" class="rejection-trace">
          <span v-if="turn.rejection_code" class="trace-code">{{ turn.rejection_code }}</span>
          <p>{{ turn.rejection_message || turn.rule_reason }}</p>
          <div v-if="Object.keys(turn.rejection_details || {}).length" class="trace-details">
            <span v-for="(value, key) in turn.rejection_details" :key="key">
              <b>{{ key }}</b>{{ Array.isArray(value) ? value.join('、') : value }}
            </span>
          </div>
          <small>本次行动未写入权威世界状态</small>
        </div>

        <!-- 旁白 -->
        <p v-if="turn.narrative?.narration" class="narration">{{ turn.narrative.narration }}</p>

        <!-- 对白 -->
        <div v-for="(d, j) in (turn.narrative?.dialogues || [])" :key="j" class="dialogue" :class="{ 'dlg-player': d.speaker_id === defaultActor }">
          <span class="dlg-speaker">{{ charName(d.speaker_id) }}</span>
          <span v-if="d.tone" class="dlg-tone">（{{ d.tone }}）</span>
          <span class="dlg-line">「{{ d.line }}」</span>
        </div>

        <!-- 系统提示 -->
        <div v-if="turn.narrative?.system_hints?.length" class="system-hints">
          <div v-for="(h, j) in turn.narrative.system_hints" :key="j" class="hint-line">▸ {{ h }}</div>
        </div>

        <!-- NPC 自主反应标记 -->
        <div v-if="npcNames(turn).length" class="npc-reactions">
          <span class="npc-badge">⚡ NPC 自主行动</span>
          <span v-for="name in npcNames(turn)" :key="name" class="npc-name">{{ name }}</span>
        </div>
        <div v-if="turn.memory_warning" class="memory-warning">{{ turn.memory_warning }}</div>
      </div>
    </template>

    <!-- 推演中 -->
    <div v-if="loading" class="thinking">
      <span class="dot"></span><span class="dot"></span><span class="dot"></span>
      <span class="thinking-text">世界正在推演…</span>
    </div>
  </div>
</template>

<style scoped>
.feed {
  flex: 1;
  overflow-y: auto;
  padding: 18px 22px;
}
.empty {
  text-align: center;
  padding: 60px 30px;
  color: var(--text-dim);
}
.empty-title {
  font-size: 22px;
  color: var(--accent);
  margin-bottom: 16px;
  letter-spacing: 2px;
}
.empty-desc {
  line-height: 2;
  color: var(--text-faint);
}

/* 玩家输入 */
.player-input {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-bottom: 14px;
}
.pi-label {
  flex-shrink: 0;
  color: var(--player);
  font-size: 13px;
  padding-top: 2px;
}
.pi-text {
  color: var(--text);
  font-style: italic;
  border-left: 2px solid var(--player);
  padding-left: 10px;
}

/* 回合卡片 */
.turn-card {
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 6px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.turn-card.turn-error {
  border-color: var(--danger);
  background: rgba(201, 90, 90, 0.08);
}

.status-badge {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.badge-reject { color: var(--danger); }
.badge-warn { color: var(--warn); }
.badge-reason { color: var(--text-dim); font-weight: 400; }
.error-text {
  color: var(--danger);
  font-size: 14px;
  margin-top: 4px;
}
.rejection-trace {
  margin: 9px 0 12px;
  padding: 10px 12px;
  border-left: 3px solid var(--danger);
  border-radius: 4px;
  background: rgba(201, 90, 90, 0.08);
}
.trace-code {
  display: inline-block;
  margin-bottom: 5px;
  padding: 1px 6px;
  border-radius: 3px;
  background: rgba(201, 90, 90, 0.16);
  color: var(--danger);
  font: 10px/1.5 ui-monospace, monospace;
}
.rejection-trace p { color: var(--text-dim); font-size: 13px; }
.trace-details { display: grid; gap: 3px; margin-top: 6px; }
.trace-details span { color: var(--text-dim); font-size: 11px; overflow-wrap: anywhere; }
.trace-details b { margin-right: 6px; color: var(--text-faint); font-weight: 500; }
.rejection-trace small { display: block; margin-top: 7px; color: var(--system); font-size: 10px; }
.memory-warning { margin-top: 8px; color: var(--warn); font-size: 11px; }

.narration {
  margin-bottom: 12px;
  text-indent: 2em;
  color: var(--text);
}

.dialogue {
  margin: 6px 0;
  padding-left: 12px;
}
.dialogue.dlg-player .dlg-speaker { color: var(--player); }
.dlg-speaker {
  color: var(--npc);
  font-weight: 600;
}
.dlg-tone {
  color: var(--text-faint);
  font-size: 13px;
  margin: 0 4px;
}
.dlg-line {
  color: var(--text);
}

.system-hints {
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border-soft);
}
.hint-line {
  color: var(--system);
  font-size: 13px;
  margin: 2px 0;
}

.npc-reactions {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}
.npc-badge {
  background: rgba(201, 169, 106, 0.15);
  color: var(--accent);
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 3px;
  border: 1px solid var(--accent-dim);
}
.npc-name {
  color: var(--npc);
  font-size: 13px;
}

/* 推演中动画 */
.thinking {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-dim);
  padding: 8px 0;
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: blink 1.2s infinite;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
.thinking-text { margin-left: 6px; font-size: 14px; }
@keyframes blink {
  0%, 60%, 100% { opacity: 0.2; }
  30% { opacity: 1; }
}
</style>
