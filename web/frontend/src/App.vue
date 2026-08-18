<script setup>
import { computed, ref, onMounted } from 'vue'
import {
  abortActiveJointPlans,
  approveJointPlan,
  deleteSave,
  executeJointPlan,
  exportSave,
  generateJointPlan,
  getJointPlans,
  getPlayerView,
  importSave,
  listSaves,
  listPlayableWorlds,
  renameSave,
  resumeSession,
  runDemoCase,
  startSession,
  submitTurn,
  updateJointPlan,
} from './api.js'
import StoryFeed from './components/StoryFeed.vue'
import CreatorStudio from './components/CreatorStudio.vue'
import SaveManager from './components/SaveManager.vue'
import TurnInput from './components/TurnInput.vue'
import WorldMap from './components/WorldMap.vue'
import CharacterProfiles from './components/CharacterProfiles.vue'
import ChapterNavigator from './components/ChapterNavigator.vue'
import InspectorPanel from './components/InspectorPanel.vue'
import DemoLauncher from './components/DemoLauncher.vue'
import WorldSelector from './components/WorldSelector.vue'
import PlayerNovelView from './components/PlayerNovelView.vue'
import CanonComparisonPanel from './components/CanonComparisonPanel.vue'

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
const demoLauncherOpen = ref(false)
const worldSelectorOpen = ref(false)
const worldPackages = ref([])
const worldSelectionError = ref('')
const activeDemo = ref(null)
const saves = ref([])
const jointPlans = ref([])
const planBusy = ref(false)
const planError = ref('')
const autoRunning = ref(false)
const selectedChapterIndex = ref(0)
const utilityDrawer = ref('')
const mobileChaptersOpen = ref(false)
const inspectorOpen = ref(false)
const interfaceMode = ref('player')
const storyMode = ref('replay')
const playerView = ref(null)
const playerViewError = ref('')
const pendingAutoRequest = ref(null)
let autoRunToken = 0
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

const selectedChapterLabel = computed(() => {
  const chapters = worldMeta.value?.source_chapters || []
  const chapter = chapters[selectedChapterIndex.value]
  if (typeof chapter === 'object' && chapter !== null) {
    return chapter.title || chapter.name || chapter.label || `第 ${selectedChapterIndex.value + 1} 章`
  }
  if (typeof chapter === 'number') return `第 ${chapter} 章`
  return String(chapter || '当前剧情片段')
})

const planningActorIds = computed(() => {
  const characters = Object.values(state.value?.characters || {})
  const currentSceneId = state.value?.current_scene_id
  const available = characters.filter((character) => (
    character.is_alive !== false
    && (!currentSceneId || character.location_id === currentSceneId)
  ))
  const ids = available.map((character) => character.character_id).filter(Boolean)
  if (defaultActor.value && !ids.includes(defaultActor.value)) ids.unshift(defaultActor.value)
  return [...new Set(ids)].slice(0, 4)
})

const blockingPlans = computed(() => jointPlans.value.filter((plan) => (
  !['completed', 'aborted'].includes(plan.status)
)))

function selectChapter(index) {
  selectedChapterIndex.value = index
  mobileChaptersOpen.value = false
}

function toggleUtility(name) {
  utilityDrawer.value = utilityDrawer.value === name ? '' : name
  mobileChaptersOpen.value = false
  inspectorOpen.value = false
}

function closeResponsivePanels() {
  mobileChaptersOpen.value = false
  inspectorOpen.value = false
}

function applySession(data, { resumed = false } = {}) {
  const changedSession = sessionId.value !== data.session_id
  sessionId.value = data.session_id
  defaultActor.value = data.default_actor
  state.value = data.state
  worldMeta.value = data.world_meta
  const chapterNumber = Number(
    data.state?.flags?.['canonical.checkpoint_chapter']
    ?? data.state?.flags?.current_chapter
    ?? 1,
  )
  const chapterCount = data.world_meta?.source_chapters?.length || 1
  selectedChapterIndex.value = Number.isFinite(chapterNumber)
    ? Math.max(0, Math.min(chapterNumber - 1, chapterCount - 1))
    : 0
  currentSaveName.value = data.save?.name || '华容巷世界线'
  currentPackageId.value = data.save?.world_package_id || 'huarong_lane'
  if (changedSession) {
    storyMode.value = currentPackageId.value === 'first_crazy_ch1_checkpoint'
      ? 'replay'
      : 'intervention'
  }
  activeDemo.value = data.demo || null
  localStorage.setItem(SESSION_STORAGE_KEY, data.session_id)
  turns.value = resumed
    ? (data.turns?.length ? data.turns : [{
        status: 'committed',
        narrative: {
          system_hints: [`已恢复世界线 v${data.state?.version ?? 0}。`],
        },
      }])
    : []
  refreshJointPlans()
  refreshPlayerView()
}

async function refreshPlayerView() {
  if (!sessionId.value) {
    playerView.value = null
    return
  }
  playerViewError.value = ''
  const data = await getPlayerView(sessionId.value)
  if (data.status !== 'ok') {
    playerViewError.value = data.error || '玩家剧情读取失败'
    return
  }
  playerView.value = data
}

async function refreshJointPlans() {
  if (!sessionId.value) {
    jointPlans.value = []
    return
  }
  const data = await getJointPlans(sessionId.value)
  jointPlans.value = data.status === 'ok' ? (data.plans || []) : []
}

async function refreshWorldAfterPlan() {
  const data = await resumeSession(sessionId.value)
  if (data.status === 'error') {
    planError.value = data.error
    return false
  }
  applySession(data, { resumed: true })
  await refreshJointPlans()
  return true
}

async function generatePlanHandler({ goal, actorIds }, { autoApprove = false } = {}) {
  if (!sessionId.value || planBusy.value) return null
  planBusy.value = true
  planError.value = ''
  const data = await generateJointPlan(
    sessionId.value,
    goal,
    actorIds,
    autoApprove,
  )
  planBusy.value = false
  if (data.status !== 'ok') {
    planError.value = data.error || '规划生成失败'
    await refreshJointPlans()
    return null
  }
  await refreshJointPlans()
  return data.plan
}

async function savePlanHandler({ planId, plan }) {
  if (planBusy.value) return
  planBusy.value = true
  planError.value = ''
  const data = await updateJointPlan(sessionId.value, planId, plan)
  planBusy.value = false
  if (data.status !== 'ok') planError.value = data.error || '保存规划失败'
  await refreshJointPlans()
}

async function approvePlanHandler(planId) {
  if (planBusy.value) return
  planBusy.value = true
  planError.value = ''
  const data = await approveJointPlan(sessionId.value, planId)
  planBusy.value = false
  if (data.status !== 'ok') planError.value = data.error || '规划审批失败'
  await refreshJointPlans()
}

async function executePlanHandler({ planId, complete }) {
  if (planBusy.value) return null
  planBusy.value = true
  planError.value = ''
  const data = await executeJointPlan(sessionId.value, planId, {
    runToCompletion: complete,
    autoReplan: true,
    maxTicks: 25,
  })
  planBusy.value = false
  if (data.status !== 'ok') {
    planError.value = data.error || '规划执行失败'
    await refreshJointPlans()
    return null
  }
  await refreshWorldAfterPlan()
  if (data.memory_warning) planError.value = data.memory_warning
  return data.plan
}

async function toggleAutoHandler({ enabled, goal, actorIds, cycles }) {
  if (!enabled) {
    autoRunning.value = false
    autoRunToken += 1
    return
  }
  if (autoRunning.value || planBusy.value) return
  if (blockingPlans.value.length) {
    pendingAutoRequest.value = { enabled: true, goal, actorIds, cycles }
    planError.value = `检测到 ${blockingPlans.value.length} 个未结束规划。可以保留已提交的世界 v${state.value?.version ?? 0}，终止旧规划后从当前状态继续。`
    return
  }
  pendingAutoRequest.value = null
  await startAutoLoop({ goal, actorIds, cycles })
}

async function startAutoLoop({ goal, actorIds, cycles }) {
  autoRunning.value = true
  planError.value = ''
  const token = ++autoRunToken
  for (let index = 0; index < cycles; index += 1) {
    if (!autoRunning.value || token !== autoRunToken) break
    const plan = await generatePlanHandler(
      { goal, actorIds },
      { autoApprove: true },
    )
    if (!plan || !autoRunning.value || token !== autoRunToken) break
    const result = await executePlanHandler({ planId: plan.plan_id, complete: true })
    if (!result || result.status !== 'completed') {
      planError.value = planError.value || describePlanStop(result)
      break
    }
  }
  if (token === autoRunToken) autoRunning.value = false
}

function describePlanStop(plan) {
  if (!plan) return 'Auto 未得到可执行规划，已安全停止。'
  const reasons = plan.stale_reasons || []
  const message = reasons.find((reason) => String(reason).startsWith('message:'))
  let detail = message ? String(message).slice('message:'.length) : reasons[0]
  if (detail?.startsWith('actor is already at ')) {
    detail = `角色已经位于 ${detail.slice('actor is already at '.length)}，重复移动没有产生状态变化`
  } else if (detail?.startsWith('location entry condition is not satisfied: ')) {
    detail = `目标地点 ${detail.slice('location entry condition is not satisfied: '.length)} 的进入条件尚未满足`
  }
  return `Auto 在规划 ${plan.status || 'blocked'} 状态停止（已重规划 ${plan.replan_count || 0} 次）${detail ? `：${detail}` : '。'}。`
}

async function abortBlockingPlansHandler({ continueAuto = false } = {}) {
  if (!sessionId.value || planBusy.value) return false
  planBusy.value = true
  const data = await abortActiveJointPlans(sessionId.value)
  planBusy.value = false
  if (data.status !== 'ok') {
    planError.value = data.error || '终止旧规划失败'
    return false
  }
  jointPlans.value = data.plans || []
  const request = pendingAutoRequest.value || (
    continueAuto ? buildPlayerAutoRequest() : null
  )
  pendingAutoRequest.value = null
  planError.value = data.aborted_plan_ids?.length
    ? `已终止 ${data.aborted_plan_ids.length} 个旧规划，权威世界仍为 v${data.world_version}。`
    : '当前没有需要终止的规划。'
  if (continueAuto && request) {
    await toggleAutoHandler(request)
  }
  return true
}

function buildPlayerAutoRequest() {
  const goal = storyMode.value === 'replay'
    ? '依据角色目标、已知事实和世界规则，推进下一段符合原著人物逻辑的剧情。'
    : '依据玩家已经造成的变化、角色目标和世界规则，推进穿越世界线的下一段合理剧情。'
  return {
    enabled: true,
    goal,
    // Auto leaves actor selection to the backend plot-driver scheduler. The
    // visible scene can lag behind the protagonist after a committed move.
    actorIds: [],
    cycles: 3,
  }
}

async function runPlayerEvolution() {
  if (autoRunning.value) {
    await toggleAutoHandler({ enabled: false, goal: '', actorIds: [], cycles: 0 })
    return
  }
  await toggleAutoHandler(buildPlayerAutoRequest())
}

function enterInterventionMode() {
  storyMode.value = 'intervention'
}

// ---- 启动与恢复会话 ----
async function startNewSession(packageId = currentPackageId.value) {
  loading.value = true
  bootError.value = ''
  const data = await startSession(packageId)
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return false
  }
  applySession(data)
  if (saveManagerOpen.value) await refreshSaves()
  return true
}

async function refreshWorldPackages() {
  worldSelectionError.value = ''
  const data = await listPlayableWorlds()
  if (data.status !== 'ok') {
    worldSelectionError.value = data.error || '世界列表读取失败'
    return
  }
  worldPackages.value = data.worlds || []
}

async function openWorldSelector() {
  worldSelectorOpen.value = true
  await refreshWorldPackages()
}

async function selectWorld(packageId) {
  worldSelectionError.value = ''
  const started = await startNewSession(packageId)
  if (!started) {
    worldSelectionError.value = bootError.value || '世界初始化失败'
    return
  }
  worldSelectorOpen.value = false
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

async function runDemoCaseHandler(caseId) {
  loading.value = true
  bootError.value = ''
  const data = await runDemoCase(caseId)
  loading.value = false
  if (data.status === 'error' || data.status === 'invalid') {
    bootError.value = data.error || '演示运行失败'
    return
  }
  applySession(data, { resumed: true })
  demoLauncherOpen.value = false
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
  await refreshJointPlans()
  await refreshPlayerView()
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
      <div class="header-left">
        <button class="icon-btn chapters-trigger" type="button" title="章节" @click="mobileChaptersOpen = !mobileChaptersOpen">
          <span class="hamburger-icon" aria-hidden="true"></span>
          <span>章节</span>
        </button>
        <div class="brand">
          <span class="brand-mark">N</span>
          <div>
            <div class="brand-name">NovelSim</div>
            <div class="brand-context">
              {{ worldMeta?.novel || '第一狂妃' }} / {{ worldMeta?.scenario || '华容巷' }}
            </div>
          </div>
        </div>
      </div>
      <nav class="top-tools" aria-label="世界工具">
        <button class="top-tool" type="button" aria-label="世界选择" title="世界选择" @click="openWorldSelector">
          <span class="tool-icon world-icon" aria-hidden="true"></span>
          <span>世界</span>
        </button>
        <button class="top-tool" :class="{ active: utilityDrawer === 'map' }" type="button" aria-label="地图" title="地图" @click="toggleUtility('map')">
          <span class="tool-icon map-icon" aria-hidden="true"></span>
          <span>地图</span>
          <small>{{ Object.keys(state?.locations || {}).length }}</small>
        </button>
        <button class="top-tool" :class="{ active: utilityDrawer === 'characters' }" type="button" aria-label="角色档案" title="角色档案" @click="toggleUtility('characters')">
          <span class="tool-icon people-icon" aria-hidden="true"></span>
          <span>角色档案</span>
          <small>{{ Object.keys(state?.characters || {}).length }}</small>
        </button>
        <button class="top-tool mode-tool" type="button" :title="interfaceMode === 'player' ? '切换到开发者模式' : '返回玩家模式'" @click="interfaceMode = interfaceMode === 'player' ? 'developer' : 'player'">
          <span class="tool-icon mode-icon" aria-hidden="true"></span>
          <span>{{ interfaceMode === 'player' ? '开发者模式' : '玩家模式' }}</span>
        </button>
        <button class="top-tool inspector-trigger" type="button" :aria-label="interfaceMode === 'player' ? '原著对照' : '规划检查器'" :title="interfaceMode === 'player' ? '原著对照' : '规划检查器'" @click="inspectorOpen = !inspectorOpen">
          <span class="tool-icon inspector-icon" aria-hidden="true"></span>
          <span>{{ interfaceMode === 'player' ? '原著对照' : '规划检查器' }}</span>
        </button>
      </nav>
      <div class="runtime-meta">
        <span class="runtime-pill" :class="runtimeStatus.cls">
          <i></i>{{ runtimeStatus.label }}
        </span>
        <span v-if="state" class="version-tag">{{ currentSaveName }} · v{{ state.version }}</span>
        <button class="header-btn demo-btn wide-action" @click="demoLauncherOpen = true" :disabled="loading">演示</button>
        <button class="header-btn wide-action" @click="openCreator" :disabled="loading">创作台</button>
        <button class="header-btn" @click="openSaveManager" :disabled="loading">存档</button>
        <button class="header-btn primary" @click="startNewSession(currentPackageId)" :disabled="loading">重新开局</button>
      </div>
    </header>

    <!-- 启动错误 -->
    <div v-if="bootError" class="boot-error">
      <strong>后端启动失败：</strong>{{ bootError }}
      <div class="hint">请确认已运行后端 (<code>python web/run.py</code>) 且 <code>.env</code> 配置了 LLM_API_KEY。</div>
    </div>

    <div v-if="mobileChaptersOpen || inspectorOpen" class="responsive-scrim" @click="closeResponsivePanels"></div>

    <main class="sim-workspace">
      <aside class="left-rail" :class="{ open: mobileChaptersOpen }">
        <ChapterNavigator
          :world-meta="worldMeta"
          :state="state"
          :selected-index="selectedChapterIndex"
          :progress-chapter="playerView?.current_story_chapter || 0"
          @select="selectChapter"
        />
      </aside>

      <section class="story-stage">
        <div class="story-toolbar">
          <div>
            <span class="eyebrow">{{ interfaceMode === 'player' ? 'PLAYABLE NOVEL' : 'LIVE WORLD' }} / {{ selectedChapterLabel }}</span>
            <h1>{{ interfaceMode === 'player' ? '小说演化' : '世界事件流' }}</h1>
          </div>
          <div v-if="interfaceMode === 'player'" class="experience-controls">
            <div class="story-mode-switch" role="group" aria-label="世界线模式">
              <button type="button" :class="{ active: storyMode === 'replay' }" :disabled="!playerView?.canonical_baseline_available" @click="storyMode = 'replay'">原著复现</button>
              <button type="button" :class="{ active: storyMode === 'intervention' }" @click="storyMode = 'intervention'">穿越干预</button>
            </div>
            <button class="evolve-btn" :class="{ stop: autoRunning }" type="button" :disabled="planBusy && !autoRunning" @click="runPlayerEvolution">
              {{ autoRunning ? '停止演化' : '自动演化 3 幕' }}
            </button>
          </div>
          <div v-else class="scene-now">
            <span>当前场景</span>
            <strong>{{ state?.locations?.[state?.current_scene_id]?.display_name || state?.current_scene_id || '加载中' }}</strong>
          </div>
        </div>
        <div v-if="activeDemo" class="demo-evidence-banner">
          <div>
            <span class="eyebrow">NO API KEY SHOWCASE</span>
            <strong>{{ activeDemo.title }}</strong>
            <p>{{ activeDemo.description }}</p>
          </div>
          <div class="demo-metrics">
            <span><b>v{{ activeDemo.evidence?.world_version ?? 0 }}</b>世界版本</span>
            <span><b>{{ activeDemo.evidence?.tool_calls ?? 0 }}</b>工具调用</span>
            <span><b>{{ activeDemo.evidence?.propagation_count ?? 0 }}</b>传播记录</span>
            <span><b>{{ activeDemo.evidence?.alliance_count ?? 0 }}</b>联盟</span>
          </div>
        </div>
        <div v-if="playerViewError || planError || blockingPlans.length" class="player-warning">
          <strong>{{ playerViewError ? '剧情读取失败' : '本轮演化已暂停' }}</strong>
          <span>{{ playerViewError || planError || `检测到 ${blockingPlans.length} 个未结束规划；可保留当前世界 v${state?.version ?? 0} 并从这里重新规划。` }}</span>
          <div v-if="blockingPlans.length" class="warning-actions">
            <button type="button" class="resolve" :disabled="planBusy" @click="abortBlockingPlansHandler({ continueAuto: true })">终止旧规划并继续</button>
            <button type="button" :disabled="planBusy" @click="interfaceMode = 'developer'">查看规划</button>
          </div>
        </div>
        <template v-if="interfaceMode === 'player'">
          <PlayerNovelView
            :player-view="playerView"
            :state="state"
            :loading="loading || planBusy"
            :story-mode="storyMode"
            @intervene="enterInterventionMode"
          />
          <TurnInput v-if="storyMode === 'intervention'" :loading="loading" @submit="submitTurnHandler" />
          <div v-else class="replay-controls">
            <div>
              <strong>原著自动复现</strong>
              <span>每幕先由真实 LLM 生成计划，通过规则校验后才写入小说正文。</span>
            </div>
            <button type="button" :disabled="planBusy && !autoRunning" @click="runPlayerEvolution">{{ autoRunning ? '停止' : '推进下一组剧情' }}</button>
          </div>
        </template>
        <template v-else>
          <StoryFeed :turns="turns" :loading="loading" :default-actor="defaultActor" :state="state" />
          <TurnInput :loading="loading" @submit="submitTurnHandler" />
        </template>
      </section>

      <aside class="inspector-rail" :class="{ open: inspectorOpen }">
        <div class="responsive-panel-heading">
          <strong>{{ interfaceMode === 'player' ? '原著对照' : '规划检查器' }}</strong>
          <button type="button" :aria-label="interfaceMode === 'player' ? '关闭原著对照' : '关闭规划检查器'" @click="inspectorOpen = false">×</button>
        </div>
        <CanonComparisonPanel
          v-if="interfaceMode === 'player'"
          :player-view="playerView"
          :selected-chapter-index="selectedChapterIndex"
          :story-mode="storyMode"
        />
        <InspectorPanel
          v-else
          :state="state"
          :default-actor="defaultActor"
          :latest-turn="latestDecision"
          :world-meta="worldMeta"
          :joint-plans="jointPlans"
          :plan-busy="planBusy"
          :plan-error="planError"
          :auto-running="autoRunning"
          @generate-plan="generatePlanHandler"
          @save-plan="savePlanHandler"
          @approve-plan="approvePlanHandler"
          @execute-plan="executePlanHandler"
          @toggle-auto="toggleAutoHandler"
          @abort-plans="abortBlockingPlansHandler"
        />
      </aside>
    </main>

    <div v-if="utilityDrawer" class="utility-overlay" @click.self="utilityDrawer = ''">
      <aside class="utility-drawer" role="dialog" aria-modal="true" :aria-label="utilityDrawer === 'map' ? '地点地图' : '角色档案'">
        <div class="drawer-heading">
          <div>
            <span class="eyebrow">{{ utilityDrawer === 'map' ? 'WORLD GRAPH' : 'AGENT FILES' }}</span>
            <h2>{{ utilityDrawer === 'map' ? '地点地图' : '角色档案' }}</h2>
          </div>
          <div class="drawer-actions">
            <span>{{ utilityDrawer === 'map' ? `${Object.keys(state?.locations || {}).length} 地点` : `${Object.keys(state?.characters || {}).length} 角色` }}</span>
            <button type="button" aria-label="关闭" @click="utilityDrawer = ''">×</button>
          </div>
        </div>
        <div class="drawer-body">
          <WorldMap v-if="utilityDrawer === 'map'" :state="state" />
          <CharacterProfiles v-else :state="state" :default-actor="defaultActor" />
        </div>
      </aside>
    </div>
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
    <DemoLauncher
      :open="demoLauncherOpen"
      :loading="loading"
      @close="demoLauncherOpen = false"
      @run="runDemoCaseHandler"
    />
    <WorldSelector
      :open="worldSelectorOpen"
      :packages="worldPackages"
      :current-package-id="currentPackageId"
      :loading="loading"
      :error="worldSelectionError"
      @close="worldSelectorOpen = false"
      @refresh="refreshWorldPackages"
      @select="selectWorld"
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
.header-btn.demo-btn {
  border-color: rgba(122, 162, 201, 0.5);
  background: rgba(122, 162, 201, 0.1);
  color: var(--player);
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
.demo-evidence-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
  border-bottom: 1px solid rgba(122, 162, 201, 0.25);
  background: linear-gradient(90deg, rgba(122, 162, 201, 0.12), rgba(44, 36, 28, 0.7));
}
.demo-evidence-banner strong { display: block; color: var(--player); font-size: 12px; }
.demo-evidence-banner p { margin-top: 2px; color: var(--text-faint); font-size: 10px; }
.demo-metrics { display: flex; flex: 0 0 auto; gap: 12px; }
.demo-metrics span { color: var(--text-faint); font-size: 9px; text-align: center; }
.demo-metrics b { display: block; color: var(--text); font: 700 13px/1.3 ui-monospace, monospace; }
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

<style scoped>
.bookworld-shell {
  min-width: 0;
  background: var(--bg);
}
.sim-header {
  position: relative;
  z-index: 40;
  min-height: 64px;
  gap: 16px;
  padding: 8px 14px;
  background: #17191c;
  border-color: var(--border-soft);
}
.header-left, .top-tools, .runtime-meta { display: flex; align-items: center; min-width: 0; }
.header-left { flex: 0 1 260px; }
.brand { min-width: 0; gap: 10px; }
.brand-mark {
  width: 34px;
  height: 34px;
  border-color: #42454c;
  border-radius: 8px;
  background: #22252a;
  color: var(--text);
  box-shadow: inset 0 1px rgba(255,255,255,.04);
}
.brand-name { font-size: 14px; letter-spacing: .2px; }
.brand-context { overflow: hidden; max-width: 220px; color: var(--text-faint); text-overflow: ellipsis; white-space: nowrap; }
.chapters-trigger { display: none; }
.icon-btn, .top-tool {
  align-items: center;
  gap: 7px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-dim);
}
.top-tools { justify-content: center; flex: 1 1 auto; gap: 4px; }
.top-tool { display: flex; padding: 7px 10px; font-size: 12px; }
.top-tool:hover, .top-tool.active { border-color: var(--border); background: var(--bg-card); color: var(--text); }
.top-tool small { min-width: 18px; padding: 1px 5px; border-radius: 999px; background: rgba(255,255,255,.06); color: var(--text-faint); font: 500 9px/1.4 ui-monospace, monospace; }
.tool-icon { position: relative; display: inline-block; width: 15px; height: 15px; flex: 0 0 15px; opacity: .8; }
.world-icon { border: 1px solid currentColor; border-radius: 50%; }
.world-icon::before { position: absolute; top: 2px; bottom: 2px; left: 3px; right: 3px; border: 1px solid currentColor; border-top: 0; border-bottom: 0; border-radius: 50%; content: ''; }
.world-icon::after { position: absolute; top: 6px; right: 1px; left: 1px; height: 1px; background: currentColor; content: ''; }
.map-icon { border: 1px solid currentColor; border-radius: 3px; transform: skewY(-8deg); }
.map-icon::before, .map-icon::after { position: absolute; top: 1px; bottom: 1px; width: 1px; background: currentColor; content: ''; }
.map-icon::before { left: 5px; } .map-icon::after { right: 4px; }
.people-icon::before, .people-icon::after { position: absolute; border: 1px solid currentColor; border-radius: 50%; content: ''; }
.people-icon::before { top: 1px; left: 5px; width: 5px; height: 5px; }
.people-icon::after { left: 2px; bottom: 0; width: 11px; height: 6px; border-radius: 7px 7px 3px 3px; }
.inspector-icon { border: 1px solid currentColor; border-radius: 3px; }
.inspector-icon::before { position: absolute; top: 2px; bottom: 2px; left: 4px; width: 1px; background: currentColor; content: ''; }
.mode-icon { border: 1px solid currentColor; border-radius: 3px; }
.mode-icon::before { position: absolute; top: 3px; left: 3px; width: 3px; height: 3px; border: 1px solid currentColor; border-radius: 50%; content: ''; }
.mode-icon::after { position: absolute; right: 2px; bottom: 2px; width: 6px; height: 1px; background: currentColor; box-shadow: 0 -3px currentColor; content: ''; }
.inspector-trigger { display: none; }
.runtime-meta { flex: 0 0 auto; gap: 6px; }
.runtime-pill, .version-tag { border-color: var(--border-soft); background: rgba(255,255,255,.02); }
.header-btn { padding: 7px 10px; border-color: var(--border); background: #202226; color: var(--text-dim); }
.header-btn:hover:not(:disabled) { border-color: #50535b; background: #292c31; color: var(--text); }
.header-btn.primary { border-color: #4b4e55; background: #2a2d32; color: var(--text); }
.header-btn.demo-btn { border-color: var(--border); background: #202226; color: var(--text-dim); }
.sim-workspace {
  grid-template-columns: 242px minmax(0, 1fr) minmax(330px, 360px);
  gap: 8px;
  padding: 8px;
  background: #0d0e10;
}
.left-rail, .story-stage, .inspector-rail {
  border: 1px solid var(--border-soft);
  border-radius: 12px;
  background: var(--bg-panel);
  box-shadow: 0 1px 0 rgba(255,255,255,.025) inset;
}
.left-rail {
  display: block;
  border-right: 1px solid var(--border-soft);
  background: linear-gradient(180deg, #191e2c, #171b26);
  overflow: hidden;
}
.story-stage { border-right: 1px solid var(--border-soft); overflow: hidden; }
.story-toolbar { min-height: 58px; background: #1b1d20; }
.eyebrow { color: var(--text-faint); }
.scene-now strong { color: var(--text-dim); }
.experience-controls, .story-mode-switch { display: flex; align-items: center; }
.experience-controls { gap: 8px; }
.story-mode-switch { padding: 2px; border: 1px solid var(--border-soft); border-radius: 7px; background: #15171a; }
.story-mode-switch button { padding: 5px 8px; border: 0; background: transparent; color: var(--text-faint); font-size: 10px; }
.story-mode-switch button.active { background: #2b2e34; color: var(--text); box-shadow: 0 1px 3px rgba(0,0,0,.25); }
.story-mode-switch button:disabled { opacity: .35; }
.evolve-btn { padding: 7px 10px; border: 1px solid #4b5059; background: #282b31; color: var(--text); font-size: 10px; }
.evolve-btn:hover:not(:disabled) { border-color: #656b76; background: #30343b; }
.evolve-btn.stop { border-color: rgba(201,90,90,.5); color: #df9696; }
.player-warning { display: flex; align-items: center; gap: 9px; padding: 8px 14px; border-bottom: 1px solid rgba(207,151,88,.25); background: rgba(207,151,88,.07); color: #cf9758; font-size: 10px; }
.player-warning strong { flex: 0 0 auto; }
.player-warning span { overflow: hidden; color: var(--text-dim); text-overflow: ellipsis; white-space: nowrap; }
.warning-actions { display: flex; flex: 0 0 auto; gap: 6px; margin-left: auto; }
.warning-actions button { padding: 5px 8px; border: 1px solid var(--border); background: #24272c; color: var(--text-dim); font-size: 9px; }
.warning-actions button.resolve { border-color: rgba(207,151,88,.45); color: #e0ac70; }
.warning-actions button:hover:not(:disabled) { background: #2d3036; color: var(--text); }
.replay-controls { display: flex; min-height: 64px; align-items: center; justify-content: space-between; gap: 14px; padding: 10px 14px; border-top: 1px solid var(--border-soft); background: #1b1d20; }
.replay-controls strong, .replay-controls span { display: block; }
.replay-controls strong { color: var(--text); font-size: 11px; }
.replay-controls span { margin-top: 3px; color: var(--text-faint); font-size: 9px; }
.replay-controls button { flex: 0 0 auto; padding: 8px 11px; border: 1px solid #4b5059; background: #292c32; color: var(--text); font-size: 10px; }
.inspector-rail { background: #181a1d; }
.responsive-panel-heading { display: none; }
.demo-evidence-banner { border-color: rgba(138,180,248,.2); background: linear-gradient(90deg, rgba(138,180,248,.08), rgba(255,255,255,.02)); }
.responsive-scrim { display: none; }
.utility-overlay {
  position: fixed;
  z-index: 80;
  inset: 64px 0 0;
  display: flex;
  justify-content: flex-end;
  background: rgba(4,5,7,.62);
  backdrop-filter: blur(3px);
}
.utility-drawer {
  display: flex;
  width: min(560px, 46vw);
  min-width: 390px;
  height: 100%;
  flex-direction: column;
  border-left: 1px solid var(--border);
  background: #181a1d;
  box-shadow: -18px 0 50px rgba(0,0,0,.35);
  animation: drawer-in .18s ease-out;
}
@keyframes drawer-in { from { transform: translateX(20px); opacity: .5; } }
.drawer-heading { display: flex; align-items: center; justify-content: space-between; min-height: 66px; padding: 12px 16px; border-bottom: 1px solid var(--border-soft); }
.drawer-heading h2 { font-size: 15px; }
.drawer-actions { display: flex; align-items: center; gap: 12px; color: var(--text-faint); font-size: 10px; }
.drawer-actions button, .responsive-panel-heading button { display: grid; width: 30px; height: 30px; place-items: center; padding: 0; border: 1px solid var(--border); background: var(--bg-card); color: var(--text-dim); font-size: 20px; line-height: 1; }
.drawer-body { display: flex; min-height: 0; flex: 1; flex-direction: column; overflow: hidden; padding: 10px; }

@media (max-width: 1300px) {
  .sim-workspace { grid-template-columns: 220px minmax(0, 1fr) 320px; }
  .brand-context, .version-tag, .wide-action { display: none; }
  .header-left { flex-basis: 170px; }
  .mode-tool span:not(.tool-icon) { display: none; }
}

@media (max-width: 1120px) {
  .sim-workspace { grid-template-columns: 226px minmax(0, 1fr); }
  .inspector-trigger { display: flex; }
  .inspector-rail {
    position: fixed;
    z-index: 70;
    top: 72px;
    right: 8px;
    bottom: 8px;
    width: min(410px, calc(100vw - 32px));
    transform: translateX(calc(100% + 24px));
    transition: transform .2s ease;
    box-shadow: -18px 0 50px rgba(0,0,0,.4);
  }
  .inspector-rail.open { transform: translateX(0); }
  .responsive-panel-heading { display: flex; align-items: center; justify-content: space-between; min-height: 48px; padding: 8px 10px 8px 14px; border-bottom: 1px solid var(--border-soft); }
  .responsive-panel-heading strong { font-size: 12px; }
  .responsive-scrim { position: fixed; z-index: 60; inset: 64px 0 0; display: block; background: rgba(4,5,7,.58); }
  .top-tool { padding-inline: 8px; }
}

@media (max-width: 760px) {
  .sim-header { min-height: 58px; gap: 6px; padding: 7px 8px; }
  .header-left { flex: 0 0 auto; }
  .chapters-trigger { display: flex; padding: 7px 8px; font-size: 11px; }
  .hamburger-icon { width: 14px; height: 10px; border-top: 1px solid currentColor; border-bottom: 1px solid currentColor; box-shadow: 0 -4px transparent; }
  .brand { display: none; }
  .top-tools { justify-content: flex-start; gap: 1px; }
  .top-tool { gap: 5px; padding: 7px; }
  .top-tool small, .inspector-trigger span:last-child { display: none; }
  .runtime-meta { gap: 4px; }
  .runtime-pill, .wide-action { display: none; }
  .header-btn { padding: 7px 8px; font-size: 11px; }
  .sim-workspace { grid-template-columns: minmax(0, 1fr); gap: 0; padding: 6px; }
  .left-rail {
    position: fixed;
    z-index: 70;
    top: 64px;
    bottom: 6px;
    left: 6px;
    width: min(300px, calc(100vw - 36px));
    transform: translateX(calc(-100% - 18px));
    transition: transform .2s ease;
    box-shadow: 18px 0 50px rgba(0,0,0,.4);
  }
  .left-rail.open { transform: translateX(0); }
  .story-stage { border-radius: 10px; }
  .story-toolbar { min-height: 52px; padding: 9px 11px; }
  .story-toolbar h1 { font-size: 14px; }
  .scene-now { display: none; }
  .experience-controls { gap: 4px; }
  .story-mode-switch button { padding: 5px 6px; font-size: 9px; }
  .evolve-btn { padding: 6px 7px; font-size: 9px; }
  .inspector-rail { top: 64px; bottom: 6px; right: 6px; }
  .responsive-scrim { inset: 58px 0 0; }
  .utility-overlay { inset: 58px 0 0; }
  .utility-drawer { width: 100%; min-width: 0; }
  .demo-evidence-banner { align-items: flex-start; flex-direction: column; }
  .demo-metrics { width: 100%; justify-content: space-between; }
}

@media (max-width: 480px) {
  .header-btn.primary { display: none; }
  .top-tool span:not(.tool-icon) { display: none; }
  .top-tool { padding: 8px; }
  .chapters-trigger span:last-child { display: none; }
  .drawer-heading { min-height: 58px; }
  .story-toolbar { align-items: flex-start; flex-direction: column; gap: 7px; }
  .experience-controls { width: 100%; justify-content: space-between; }
  .replay-controls span { display: none; }
  .player-warning { align-items: flex-start; flex-wrap: wrap; }
  .player-warning span { width: 100%; white-space: normal; }
  .warning-actions { width: 100%; margin-left: 0; }
}
</style>
