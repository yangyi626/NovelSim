<script setup>
import { ref, onMounted } from 'vue'
import {
  deleteSave,
  exportSave,
  importSave,
  listSaves,
  renameSave,
  resumeSession,
  startSession,
  submitTurn,
} from './api.js'
import StoryFeed from './components/StoryFeed.vue'
import CreatorStudio from './components/CreatorStudio.vue'
import SaveManager from './components/SaveManager.vue'
import StatePanel from './components/StatePanel.vue'
import TurnInput from './components/TurnInput.vue'

// ---- 全局响应式状态 ----
const sessionId = ref('')
const defaultActor = ref('')
const state = ref(null)          // 当前世界状态 (WorldState.dict())
const worldMeta = ref(null)      // 世界元信息 (标题/锚点)
const currentSaveName = ref('')
const currentPackageId = ref('huarong_lane')
const turns = ref([])            // 回合历史卡片
const loading = ref(false)       // 推演中
const bootError = ref('')        // 启动错误
const saveManagerOpen = ref(false)
const saves = ref([])
const creatorMode = ref(window.location.hash === '#/creator')
const SESSION_STORAGE_KEY = 'ai-transmigration-session-id'

function applySession(data, { resumed = false } = {}) {
  sessionId.value = data.session_id
  defaultActor.value = data.default_actor
  state.value = data.state
  worldMeta.value = data.world_meta
  currentSaveName.value = data.save?.name || '华容巷世界线'
  currentPackageId.value = data.save?.world_package_id || 'huarong_lane'
  localStorage.setItem(SESSION_STORAGE_KEY, data.session_id)
  turns.value = resumed
    ? (data.turns?.length ? data.turns : [{
        status: 'committed',
        narrative: {
          system_hints: [`已恢复世界线 v${data.state?.version ?? 0}。`],
        },
      }])
    : []
}

// ---- 启动与恢复会话 ----
async function startNewSession(packageId = currentPackageId.value) {
  loading.value = true
  bootError.value = ''
  const data = await startSession(packageId)
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  applySession(data)
  if (saveManagerOpen.value) await refreshSaves()
}

async function boot() {
  loading.value = true
  bootError.value = ''
  const savedSessionId = localStorage.getItem(SESSION_STORAGE_KEY)
  if (savedSessionId) {
    const restored = await resumeSession(savedSessionId)
    if (restored.status !== 'error') {
      loading.value = false
      applySession(restored, { resumed: true })
      return
    }
    localStorage.removeItem(SESSION_STORAGE_KEY)
  }
  const data = await startSession()
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  applySession(data)
}

async function refreshSaves() {
  const data = await listSaves()
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  saves.value = data.saves || []
}

async function openSaveManager() {
  saveManagerOpen.value = true
  await refreshSaves()
}

async function loadSave(id) {
  loading.value = true
  const data = await resumeSession(id)
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  applySession(data, { resumed: true })
  saveManagerOpen.value = false
}

async function renameSaveHandler(id, name) {
  const data = await renameSave(id, name)
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  if (id === sessionId.value) currentSaveName.value = data.save.name
  await refreshSaves()
}

async function deleteSaveHandler(id) {
  const data = await deleteSave(id)
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  if (id === sessionId.value) {
    localStorage.removeItem(SESSION_STORAGE_KEY)
    await startNewSession()
  }
  await refreshSaves()
}

async function exportSaveHandler(id) {
  const data = await exportSave(id)
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  const url = URL.createObjectURL(data.blob)
  const link = document.createElement('a')
  link.href = url
  link.download = data.filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

async function importSaveHandler(backup) {
  loading.value = true
  const data = await importSave(backup)
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return
  }
  applySession(data, { resumed: true })
  saveManagerOpen.value = false
}

async function createSaveFromManager() {
  await startNewSession(currentPackageId.value)
  saveManagerOpen.value = false
}

function openCreator() {
  creatorMode.value = true
  window.location.hash = '/creator'
}

function closeCreator() {
  creatorMode.value = false
  window.location.hash = '/'
  if (!state.value) boot()
}

async function playPackage(packageId) {
  creatorMode.value = false
  window.location.hash = '/'
  await startNewSession(packageId)
}

// ---- 提交一回合 ----
async function submitTurnHandler(text, useNpcAgents) {
  if (!sessionId.value || !text.trim() || loading.value) return
  loading.value = true
  const data = await submitTurn(sessionId.value, text.trim(), useNpcAgents)
  loading.value = false

  // 玩家输入先入流 (让剧情流能看到玩家说了什么)
  turns.value.push({ player_input: text.trim() })

  if (data.status === 'error' || data.status === 'conflict') {
    turns.value.push({ status: 'error', error: data.error })
    if (data.state) state.value = data.state
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
  if (saveManagerOpen.value) await refreshSaves()
}

onMounted(() => {
  if (!creatorMode.value) boot()
})
</script>

<template>
  <CreatorStudio
    v-if="creatorMode"
    @back="closeCreator"
    @play="playPackage"
  />
  <div v-else class="layout">
    <!-- 顶栏 -->
    <header class="topbar">
      <div class="title">
        <span class="title-main">{{ worldMeta?.novel || '第一狂妃' }}</span>
        <span class="title-sub">{{ worldMeta?.scenario || '华容巷' }}</span>
      </div>
      <div class="topbar-actions">
        <span v-if="state" class="version-tag">{{ currentSaveName }} · v{{ state.version }}</span>
        <button class="btn-restart" @click="openCreator" :disabled="loading">创作台</button>
        <button class="btn-restart" @click="openSaveManager" :disabled="loading">存档</button>
        <button class="btn-restart" @click="startNewSession(currentPackageId)" :disabled="loading">重新开局</button>
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
    <SaveManager
      :open="saveManagerOpen"
      :saves="saves"
      :current-session-id="sessionId"
      :loading="loading"
      @close="saveManagerOpen = false"
      @create="createSaveFromManager"
      @load="loadSave"
      @rename="renameSaveHandler"
      @delete="deleteSaveHandler"
      @export="exportSaveHandler"
      @import="importSaveHandler"
      @refresh="refreshSaves"
    />
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
