<script setup>
import { ref, onMounted } from 'vue'
import { startSession, submitTurn } from './api.js'
import StoryFeed from './components/StoryFeed.vue'
import StatePanel from './components/StatePanel.vue'
import TurnInput from './components/TurnInput.vue'

// ---- 全局响应式状态 ----
const sessionId = ref('')
const defaultActor = ref('')
const state = ref(null)          // 当前世界状态 (WorldState.dict())
const worldMeta = ref(null)      // 世界元信息 (标题/锚点)
const turns = ref([])            // 回合历史卡片
const loading = ref(false)       // 推演中
const bootError = ref('')        // 启动错误

// ---- 启动新会话 ----
async function start() {
  loading.value = true
  bootError.value = ''
  const data = await startSession()
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  sessionId.value = data.session_id
  defaultActor.value = data.default_actor
  state.value = data.state
  worldMeta.value = data.world_meta
  turns.value = []  // 新开局清空剧情流
}

// ---- 提交一回合 ----
async function submitTurnHandler(text, useNpcAgents) {
  if (!sessionId.value || !text.trim() || loading.value) return
  loading.value = true
  const data = await submitTurn(sessionId.value, text.trim(), useNpcAgents)
  loading.value = false

  // 玩家输入先入流 (让剧情流能看到玩家说了什么)
  turns.value.push({ player_input: text.trim() })

  if (data.status === 'error') {
    turns.value.push({ status: 'error', error: data.error })
    return
  }

  // 正常回合产物入流 + 更新世界状态
  turns.value.push({
    status: data.status,
    error: data.error || '',
    rule_reason: data.rule_reason || '',
    action: data.action,
    narrative: data.narrative,
    npc_reactions: data.npc_reactions || [],
  })
  if (data.state) state.value = data.state
}

onMounted(start)
</script>

<template>
  <div class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="title">
        <span class="title-main">{{ worldMeta?.novel || '第一狂妃' }}</span>
        <span class="title-sub">{{ worldMeta?.scenario || '华容巷' }}</span>
      </div>
      <div class="topbar-actions">
        <span v-if="state" class="version-tag">世界线 v{{ state.version }}</span>
        <button class="btn-restart" @click="start" :disabled="loading">重新开局</button>
      </div>
    </header>

    <!-- 启动错误 -->
    <div v-if="bootError" class="boot-error">
      <strong>后端启动失败：</strong>{{ bootError }}
      <div class="hint">请确认已运行后端 (<code>python web/run.py</code>) 且 <code>.env</code> 配置了 LLM_API_KEY。</div>
    </div>

    <!-- 主体两栏 -->
    <main class="main">
      <section class="col-feed">
        <StoryFeed :turns="turns" :loading="loading" :default-actor="defaultActor" :state="state" />
        <TurnInput :loading="loading" @submit="submitTurnHandler" />
      </section>
      <aside class="col-panel">
        <StatePanel :state="state" :default-actor="defaultActor" />
      </aside>
    </main>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.title-main {
  font-size: 18px;
  color: var(--accent);
  font-weight: 600;
  letter-spacing: 1px;
}
.title-sub {
  margin-left: 10px;
  color: var(--text-dim);
  font-size: 14px;
}
.topbar-actions {
  display: flex;
  align-items: center;
  gap: 14px;
}
.version-tag {
  color: var(--text-faint);
  font-size: 13px;
  border: 1px solid var(--border-soft);
  padding: 2px 8px;
  border-radius: 3px;
}
.btn-restart {
  background: var(--bg-card);
  color: var(--text-dim);
  border: 1px solid var(--border);
}
.btn-restart:hover:not(:disabled) {
  color: var(--accent);
  border-color: var(--accent-dim);
}
.main {
  flex: 1;
  display: flex;
  overflow: hidden;
}
.col-feed {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-right: 1px solid var(--border);
}
.col-panel {
  width: 360px;
  flex-shrink: 0;
  overflow-y: auto;
}
.boot-error {
  margin: 16px 20px;
  padding: 14px 18px;
  background: rgba(201, 90, 90, 0.12);
  border: 1px solid var(--danger);
  border-radius: 6px;
  color: var(--danger);
}
.boot-error .hint {
  margin-top: 6px;
  color: var(--text-dim);
  font-size: 13px;
}
.boot-error code {
  background: var(--bg-input);
  padding: 1px 5px;
  border-radius: 3px;
  color: var(--accent);
}
</style>
