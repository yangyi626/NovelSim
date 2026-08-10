<script setup>
import { computed, ref, onMounted } from 'vue'
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
import TurnInput from './components/TurnInput.vue'
import WorldMap from './components/WorldMap.vue'
import CharacterProfiles from './components/CharacterProfiles.vue'
import InspectorPanel from './components/InspectorPanel.vue'

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

const latestDecision = computed(() => {
  for (let i = turns.value.length - 1; i >= 0; i -= 1) {
    const turn = turns.value[i]
    const isDecision = (
      turn.action
      || turn.rule_reason
      || turn.rejection_code
      || turn.error
      || (turn.status && turn.status !== 'committed')
    )
    if (!turn.player_input && isDecision) return turn
  }
  return null
})

const runtimeStatus = computed(() => {
  if (loading.value) return { label: '推演中', cls: 'running' }
  if (latestDecision.value?.status === 'rejected') return { label: '规则已拦截', cls: 'rejected' }
  if (bootError.value) return { label: '连接异常', cls: 'error' }
  return { label: '世界在线', cls: 'online' }
})

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
    rejection_code: data.rejection_code || '',
    rejection_message: data.rejection_message || '',
    rejection_details: data.rejection_details || {},
    action: data.action,
    narrative: data.narrative,
    npc_reactions: data.npc_reactions || [],
    memory_warning: data.memory_warning || '',
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
  <div v-else class="bookworld-shell">
    <header class="sim-header">
      <div class="brand">
        <span class="brand-mark">NS</span>
        <div>
          <div class="brand-name">NovelSim World Console</div>
          <div class="brand-context">
            {{ worldMeta?.novel || '第一狂妃' }} · {{ worldMeta?.scenario || '华容巷' }}
          </div>
        </div>
      </div>
      <div class="runtime-meta">
        <span class="runtime-pill" :class="runtimeStatus.cls">
          <i></i>{{ runtimeStatus.label }}
        </span>
        <span v-if="state" class="version-tag">{{ currentSaveName }} · v{{ state.version }}</span>
        <button class="header-btn" @click="openCreator" :disabled="loading">世界创作台</button>
        <button class="header-btn" @click="openSaveManager" :disabled="loading">世界线存档</button>
        <button class="header-btn primary" @click="startNewSession(currentPackageId)" :disabled="loading">重新开局</button>
      </div>
    </header>

    <!-- 启动错误 -->
    <div v-if="bootError" class="boot-error">
      <strong>后端启动失败：</strong>{{ bootError }}
      <div class="hint">请确认已运行后端 (<code>python web/run.py</code>) 且 <code>.env</code> 配置了 LLM_API_KEY。</div>
    </div>

    <main class="sim-workspace">
      <aside class="left-rail">
        <section class="shell-panel map-shell">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">WORLD GRAPH</span>
              <h2>地点地图</h2>
            </div>
            <span class="panel-count">{{ Object.keys(state?.locations || {}).length }} 地点</span>
          </div>
          <WorldMap :state="state" />
        </section>
        <section class="shell-panel profiles-shell">
          <div class="panel-heading">
            <div>
              <span class="eyebrow">AGENTS</span>
              <h2>角色档案</h2>
            </div>
            <span class="panel-count">{{ Object.keys(state?.characters || {}).length }} 角色</span>
          </div>
          <CharacterProfiles :state="state" :default-actor="defaultActor" />
        </section>
      </aside>

      <section class="story-stage">
        <div class="story-toolbar">
          <div>
            <span class="eyebrow">LIVE SIMULATION</span>
            <h1>世界事件流</h1>
          </div>
          <div class="scene-now">
            <span>当前场景</span>
            <strong>{{ state?.locations?.[state?.current_scene_id]?.display_name || state?.current_scene_id || '加载中' }}</strong>
          </div>
        </div>
        <StoryFeed :turns="turns" :loading="loading" :default-actor="defaultActor" :state="state" />
        <TurnInput :loading="loading" @submit="submitTurnHandler" />
      </section>

      <aside class="inspector-rail">
        <InspectorPanel
          :state="state"
          :default-actor="defaultActor"
          :latest-turn="latestDecision"
          :world-meta="worldMeta"
        />
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
.bookworld-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-width: 1120px;
  background:
    radial-gradient(circle at 15% 0%, rgba(201, 169, 106, 0.08), transparent 24%),
    var(--bg);
}
.sim-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 68px;
  padding: 10px 18px;
  background: rgba(36, 30, 24, 0.96);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-mark {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid var(--accent-dim);
  border-radius: 10px;
  background: linear-gradient(145deg, rgba(201, 169, 106, 0.22), rgba(201, 169, 106, 0.04));
  color: var(--accent);
  font: 700 13px/1 ui-monospace, monospace;
  letter-spacing: 1.5px;
}
.brand-name {
  color: var(--text);
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.8px;
}
.brand-context {
  margin-top: 1px;
  color: var(--text-dim);
  font-size: 12px;
}
.runtime-meta {
  display: flex;
  align-items: center;
  gap: 9px;
}
.runtime-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 9px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  color: var(--text-dim);
  font-size: 11px;
}
.runtime-pill i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--system);
  box-shadow: 0 0 8px rgba(138, 168, 107, 0.65);
}
.runtime-pill.running i { background: var(--accent); animation: pulse 1s infinite; }
.runtime-pill.rejected i, .runtime-pill.error i { background: var(--danger); }
@keyframes pulse { 50% { opacity: 0.35; } }
.header-btn {
  padding: 7px 11px;
  border: 1px solid var(--border);
  background: var(--bg-card);
  color: var(--text-dim);
  font-size: 12px;
}
.header-btn:hover:not(:disabled) {
  border-color: var(--accent-dim);
  color: var(--accent);
}
.header-btn.primary {
  border-color: var(--accent-dim);
  background: rgba(201, 169, 106, 0.13);
  color: var(--accent);
}
.version-tag {
  color: var(--text-faint);
  font-size: 11px;
  border: 1px solid var(--border-soft);
  padding: 4px 8px;
  border-radius: 999px;
}
.sim-workspace {
  flex: 1;
  display: grid;
  grid-template-columns: minmax(270px, 21vw) minmax(480px, 1fr) minmax(320px, 25vw);
  min-height: 0;
  overflow: hidden;
}
.left-rail {
  display: grid;
  grid-template-rows: minmax(250px, 42%) minmax(280px, 58%);
  min-width: 0;
  border-right: 1px solid var(--border);
  background: rgba(29, 24, 19, 0.72);
}
.shell-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.shell-panel + .shell-panel { border-top: 1px solid var(--border); }
.panel-heading, .story-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 9px;
  flex-shrink: 0;
}
.panel-heading h2, .story-toolbar h1 {
  margin: 0;
  color: var(--text);
  font-size: 14px;
  line-height: 1.3;
}
.eyebrow {
  display: block;
  margin-bottom: 2px;
  color: var(--accent-dim);
  font: 9px/1.2 ui-monospace, monospace;
  letter-spacing: 1.4px;
}
.panel-count {
  color: var(--text-faint);
  font-size: 10px;
}
.story-stage {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  border-right: 1px solid var(--border);
}
.story-toolbar {
  min-height: 58px;
  border-bottom: 1px solid var(--border-soft);
  background: rgba(36, 30, 24, 0.64);
}
.story-toolbar h1 { font-size: 16px; }
.scene-now {
  display: flex;
  align-items: baseline;
  gap: 7px;
  font-size: 11px;
}
.scene-now span { color: var(--text-faint); }
.scene-now strong { color: var(--accent); font-weight: 500; }
.inspector-rail {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: rgba(36, 30, 24, 0.56);
}
.boot-error {
  margin: 0;
  padding: 9px 18px;
  background: rgba(201, 90, 90, 0.12);
  border-bottom: 1px solid var(--danger);
  color: var(--danger);
  font-size: 13px;
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

@media (max-width: 1280px) {
  .sim-workspace { grid-template-columns: 270px minmax(460px, 1fr) 330px; }
  .brand-context, .version-tag { display: none; }
}
</style>
