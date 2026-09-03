<script setup>
import { computed, ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import {
  abortActiveJointPlans,
  approveJointPlan,
  clearHistoricalSaves,
  deleteSave,
  executeJointPlan,
  exportSave,
  generateJointPlan,
  getJointPlans,
  getManuscriptPassageRevisions,
  getPlayerView,
  getSettlement,
  getWorldRunDashboard,
  importSave,
  settleWorldRun,
  selectManuscriptPassageRevision,
  listSaves,
  listPlayableWorlds,
  listBooks,
  listBookChapters,
  renameSave,
  resumeSession,
  rewriteManuscriptPassage,
  runDemoCase,
  startSession,
  submitTurn,
  transitionWorldRun,
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
import SystemSpace from './components/SystemSpace.vue'
import WorldEvolutionView from './components/WorldEvolutionView.vue'
import SettlementView from './components/SettlementView.vue'
import StoryActivityPanel from './components/StoryActivityPanel.vue'

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
const books = ref([])
const chaptersByBook = ref({})
const worldSelectionError = ref('')
const activeDemo = ref(null)
const saves = ref([])
const clearHistoryBusy = ref(false)
const clearHistoryResult = ref(null)
const jointPlans = ref([])
const planBusy = ref(false)
const planError = ref('')
const autoRunning = ref(false)
const selectedChapterIndex = ref(0)
const utilityDrawer = ref('')
const mobileChaptersOpen = ref(false)
const inspectorOpen = ref(false)
const chapterTriggerRef = ref(null)
const inspectorTriggerRef = ref(null)
const utilityTriggerRef = ref(null)
const interfaceMode = ref('player')
const storyMode = ref('replay')
const primaryView = ref('world')
const primaryTabRefs = ref([])
const playerView = ref(null)
const playerViewError = ref('')
const manuscriptRewriteBusyId = ref('')
const manuscriptRewriteSelectedId = ref('')
const manuscriptRewriteError = ref('')
const manuscriptRevisionHistoryPassageId = ref('')
const manuscriptRevisionHistory = ref([])
const manuscriptRevisionHistoryLoading = ref(false)
const manuscriptRevisionHistoryError = ref('')
const manuscriptRevisionSelectBusyId = ref('')
const novelUnread = ref(false)
const manuscriptSignature = ref('')
const manuscriptSignatureInitialized = ref(false)
const manuscriptSignatureSession = ref('')
const activityCount = computed(() => playerView.value?.activity_items?.length || 0)
const readyNovelPassages = computed(() => (playerView.value?.novel_passages || []).filter(
  (passage) => passage?.generation_status === 'ready' && Array.isArray(passage?.paragraphs) && passage.paragraphs.length,
))
const readyNovelCount = computed(() => readyNovelPassages.value.length)
const utilityDrawerMeta = computed(() => ({
  map: {
    label: '地点地图',
    eyebrow: 'WORLD GRAPH',
    count: `${Object.keys(state.value?.locations || {}).length} 地点`,
  },
  characters: {
    label: '角色档案',
    eyebrow: 'AGENT FILES',
    count: `${Object.keys(state.value?.characters || {}).length} 角色`,
  },
  activity: {
    label: '世界动态',
    eyebrow: 'WORLD ACTIVITY',
    count: `${activityCount.value} 条记录`,
  },
}[utilityDrawer.value] || { label: '世界工具', eyebrow: 'WORLD TOOL', count: '' }))
const pendingAutoRequest = ref(null)
let autoRunToken = 0
const creatorMode = ref(window.location.hash === '#/creator')
const systemSpaceOpen = ref(!creatorMode.value)
const spaceLoading = ref(false)
const secondaryMenuOpen = ref(false)
const dashboard = ref(null)
const dashboardError = ref('')
const settlement = ref(null)
const settlementOpen = ref(false)
const settlementLoading = ref(false)
const settlementError = ref('')
const transitionLoading = ref(false)
const transitionError = ref('')
const transitionResult = ref(null)
const inputSuggestion = ref('')
const turnSubmitResult = ref(null)
const SESSION_STORAGE_KEY = 'ai-transmigration-session-id'
const TRANSITION_KEY_PREFIX = 'novelsim-transition-key'

const latestPlayerTurn = computed(() => {
  for (let index = turns.value.length - 1; index >= 0; index -= 1) {
    if (turns.value[index]?.player_input) continue
    return turns.value[index]
  }
  return null
})

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
  if (autoRunning.value) return { label: '自动演化中', cls: 'running' }
  if (loading.value || planBusy.value) return { label: '推演中', cls: 'running' }
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

const settlementProjection = computed(() => (
  settlement.value?.settlement
  || dashboard.value?.settlement
  || settlement.value
  || null
))

// 世界书库的章节访问状态：父存档已结算 → 子章节已解锁/已创建。
const chapterAccessMap = computed(() => {
  const map = {}
  for (const save of saves.value || []) {
    const entries = save.chapter_access || []
    if (!entries.length) continue
    const currentSettled = String(save.settlement_status || '').toLowerCase() === 'settled'
    for (const entry of entries) {
      if (!entry.package_id) continue
      const status = !currentSettled ? 'locked'
        : entry.child_session_id ? 'created' : entry.status || 'unlocked'
      const previous = map[entry.package_id]
      const rank = { locked: 0, unlocked: 1, created: 2 }
      if (!previous || (rank[status] || 0) > (rank[previous.status] || 0)) {
        map[entry.package_id] = {
          status,
          reason: entry.reason || '',
          child_session_id: entry.child_session_id || '',
        }
      }
    }
  }
  return map
})


const evolutionClosed = computed(() => ['available', 'settled'].includes(String(
  settlementProjection.value?.status || '',
).toLowerCase()))

const currentSave = computed(() => (
  saves.value.find((save) => save.session_id === sessionId.value)
  || dashboard.value?.save
  || null
))

const currentLineage = computed(() => ({
  ...currentSave.value,
  ...(dashboard.value?.lineage || {}),
  ...(settlementProjection.value?.lineage || {}),
}))

const activePanel = computed(() => {
  if (mobileChaptersOpen.value) return 'chapters'
  if (inspectorOpen.value) return 'inspector'
  return ''
})

async function selectChapter(index) {
  selectedChapterIndex.value = index
  await closeResponsivePanels('chapters')
}

function readyPassageSignature(view) {
  return (view?.novel_passages || [])
    .filter((passage) => passage?.generation_status === 'ready' && Array.isArray(passage?.paragraphs) && passage.paragraphs.length)
    .map((passage, index) => [
      passage.passage_id || passage.id || index,
      passage.revision ?? passage.current_revision ?? 0,
      passage.order ?? passage.manuscript_sequence ?? index,
    ].join(':'))
    .join('|')
}

async function selectPrimaryView(view, { focus = false } = {}) {
  primaryView.value = view
  if (view === 'novel') novelUnread.value = false
  if (!focus) return
  await nextTick()
  const index = view === 'novel' ? 1 : 0
  primaryTabRefs.value[index]?.focus()
}

function handlePrimaryTabKeydown(event, index) {
  let nextIndex = index
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') nextIndex = (index + 1) % 2
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') nextIndex = (index - 1 + 2) % 2
  else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = 1
  else return
  event.preventDefault()
  selectPrimaryView(nextIndex === 0 ? 'world' : 'novel', { focus: true })
}

async function openWorldActivity(trigger = null) {
  await toggleUtility('activity', trigger)
}

async function closeUtilityDrawer({ restoreFocus = true } = {}) {
  if (!utilityDrawer.value) return
  utilityDrawer.value = ''
  if (!restoreFocus || !utilityTriggerRef.value) return
  await nextTick()
  utilityTriggerRef.value.focus()
}

async function toggleUtility(name, trigger = null) {
  if (utilityDrawer.value === name) {
    await closeUtilityDrawer()
    return
  }
  utilityTriggerRef.value = trigger
  utilityDrawer.value = name
  mobileChaptersOpen.value = false
  inspectorOpen.value = false
}

function openResponsivePanel(name) {
  if (name === 'chapters') {
    mobileChaptersOpen.value = !mobileChaptersOpen.value
    inspectorOpen.value = false
  } else {
    inspectorOpen.value = !inspectorOpen.value
    mobileChaptersOpen.value = false
  }
}

async function closeResponsivePanels(panel = activePanel.value) {
  mobileChaptersOpen.value = false
  inspectorOpen.value = false
  const trigger = panel === 'chapters'
    ? chapterTriggerRef.value
    : panel === 'inspector'
      ? inspectorTriggerRef.value
      : null
  if (!trigger) return
  await nextTick()
  trigger.focus()
}

function handlePanelKeydown(event) {
  if (event.key !== 'Escape') return
  if (utilityDrawer.value) {
    closeUtilityDrawer()
    return
  }
  if (activePanel.value) closeResponsivePanels()
}

onMounted(() => {
  if (!creatorMode.value) boot()
  document.addEventListener('keydown', handlePanelKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('keydown', handlePanelKeydown)
})

function applySession(data, { resumed = false } = {}) {
  const changedSession = sessionId.value !== data.session_id
  if (changedSession) {
    transitionError.value = ''
    transitionResult.value = null
    primaryView.value = 'world'
    novelUnread.value = false
    manuscriptSignature.value = ''
    manuscriptSignatureInitialized.value = false
    manuscriptSignatureSession.value = data.session_id
    manuscriptRevisionHistoryPassageId.value = ''
    manuscriptRevisionHistory.value = []
    manuscriptRevisionHistoryError.value = ''
  }
  sessionId.value = data.session_id
  defaultActor.value = data.default_actor
  state.value = data.state
  worldMeta.value = data.world_meta
  if (changedSession) {
    const chapterNumber = Number(
      data.state?.flags?.['canonical.checkpoint_chapter']
      ?? data.state?.flags?.current_chapter
      ?? 1,
    )
    const chapterCount = data.world_meta?.source_chapters?.length || 1
    selectedChapterIndex.value = Number.isFinite(chapterNumber)
      ? Math.max(0, Math.min(chapterNumber - 1, chapterCount - 1))
      : 0
  }
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
  settlement.value = data.settlement || data.save?.settlement || null
  refreshJointPlans()
  refreshPlayerView()
  refreshDashboard()
}

async function refreshDashboard() {
  if (!sessionId.value) {
    dashboard.value = null
    return
  }
  dashboardError.value = ''
  const data = await getWorldRunDashboard(sessionId.value)
  if (data.status !== 'ok') {
    // Dashboard is additive and may arrive after the player UI. Existing
    // session/state projections remain the graceful fallback.
    dashboard.value = null
    if (!/^HTTP (404|405)/.test(data.error || '')) dashboardError.value = data.error || ''
    return
  }
  dashboard.value = data.dashboard || data
  if (data.dashboard?.settlement || data.dashboard?.save?.settlement) {
    settlement.value = {
      settlement: data.dashboard.settlement || data.dashboard.save.settlement,
      dashboard: data.dashboard,
    }
  }
}

async function refreshSettlement({ open = false, settle = false } = {}) {
  if (!sessionId.value || settlementLoading.value) return false
  settlementLoading.value = true
  settlementError.value = ''
  const payload = settle && state.value?.version != null
    ? { expected_version: state.value.version }
    : undefined
  const data = settle ? await settleWorldRun(sessionId.value, payload) : await getSettlement(sessionId.value)
  settlementLoading.value = false
  const acceptedStatuses = settle
    ? ['ok', 'settled']
    : ['ok', 'available', 'unavailable', 'settled']
  if (!acceptedStatuses.includes(data.status)) {
    settlementError.value = data.error || '结算信息读取失败'
    if (open) settlementOpen.value = true
    return false
  }
  settlement.value = data
  transitionError.value = ''
  settlementOpen.value = open || settle || data.settlement?.status === 'settled'
  await Promise.all([refreshSaves(), refreshDashboard()])
  return true
}

async function openSettlement({ settle = false } = {}) {
  await refreshSettlement({ open: true, settle })
}

function closeSettlement() {
  settlementOpen.value = false
  settlementError.value = ''
  transitionError.value = ''
}

function transitionStorageKey(parentSessionId, targetPackageId) {
  return `${TRANSITION_KEY_PREFIX}:${parentSessionId}:${targetPackageId}`
}

function createIdempotencyKey(parentSessionId, targetPackageId) {
  if (window.crypto?.randomUUID) return `chapter-transition:${window.crypto.randomUUID()}`
  return `chapter-transition:${parentSessionId}:${targetPackageId}:${Date.now()}`
}

function stableTransitionKey(parentSessionId, targetPackageId) {
  const storageKey = transitionStorageKey(parentSessionId, targetPackageId)
  let key = window.localStorage.getItem(storageKey)
  if (!key) {
    key = createIdempotencyKey(parentSessionId, targetPackageId)
    window.localStorage.setItem(storageKey, key)
  }
  return key
}

async function reconcileTransitionChild(childSessionId, transition) {
  if (!childSessionId) return false
  const child = await resumeSession(childSessionId)
  if (child.status === 'error') {
    transitionError.value = child.error || '下一章已创建，但读取新世界线失败。请从系统空间继续。'
    return false
  }
  applySession(child, { resumed: true })
  transitionResult.value = transition || null
  settlementOpen.value = false
  systemSpaceOpen.value = false
  await refreshSaves()
  return true
}

async function transitionToNextChapter(targetPackageId) {
  const parentSessionId = sessionId.value
  if (!parentSessionId || !targetPackageId || transitionLoading.value) return false
  transitionLoading.value = true
  transitionError.value = ''

  const key = stableTransitionKey(parentSessionId, targetPackageId)
  const data = await transitionWorldRun(parentSessionId, targetPackageId, key)
  transitionLoading.value = false

  if (!['ok', 'created', 'reused'].includes(String(data.status || '').toLowerCase())) {
    transitionError.value = data.error || data.reason || '进入下一章失败，请重试。'
    await refreshSettlement({ open: true })
    return false
  }

  const transition = data.transition || {}
  const childSessionId = transition.child_session_id
    || data.child_session_id
    || data.dashboard?.session_id
    || settlementProjection.value?.next_chapter?.child_session_id
  transitionResult.value = transition

  if (!childSessionId) {
    transitionError.value = '请求已受理，但尚未返回下一章世界线。请稍后重试。'
    await refreshSettlement({ open: true })
    return false
  }

  window.localStorage.removeItem(transitionStorageKey(parentSessionId, targetPackageId))
  return reconcileTransitionChild(childSessionId, transition)
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
  const nextSignature = readyPassageSignature(data)
  const sameSession = manuscriptSignatureSession.value === sessionId.value
  if (!sameSession) {
    manuscriptSignatureSession.value = sessionId.value
    manuscriptSignatureInitialized.value = false
    novelUnread.value = false
  }
  if (manuscriptSignatureInitialized.value && nextSignature && nextSignature !== manuscriptSignature.value && primaryView.value !== 'novel') {
    novelUnread.value = true
  }
  manuscriptSignature.value = nextSignature
  manuscriptSignatureInitialized.value = true
  playerView.value = data
}

async function rewritePassage({ passage_id: passageId, revision }) {
  if (!sessionId.value || !passageId || manuscriptRewriteBusyId.value) return
  manuscriptRewriteBusyId.value = passageId
  manuscriptRewriteSelectedId.value = passageId
  manuscriptRewriteError.value = ''
  const result = await rewriteManuscriptPassage(
    sessionId.value,
    passageId,
    Number(revision || 0),
  )
  manuscriptRewriteBusyId.value = ''
  if (result.status !== 'ok') {
    manuscriptRewriteError.value = result.error || '旧稿重写失败，原正文仍然保留。'
    return
  }
  await refreshPlayerView()
}

async function toggleRevisionHistory({ passage_id: passageId }) {
  if (!sessionId.value || !passageId || manuscriptRevisionSelectBusyId.value) return
  if (manuscriptRevisionHistoryPassageId.value === passageId) {
    manuscriptRevisionHistoryPassageId.value = ''
    manuscriptRevisionHistory.value = []
    manuscriptRevisionHistoryError.value = ''
    return
  }
  manuscriptRevisionHistoryPassageId.value = passageId
  manuscriptRevisionHistory.value = []
  manuscriptRevisionHistoryError.value = ''
  manuscriptRevisionHistoryLoading.value = true
  const result = await getManuscriptPassageRevisions(sessionId.value, passageId)
  manuscriptRevisionHistoryLoading.value = false
  if (result.status !== 'ok') {
    manuscriptRevisionHistoryError.value = result.error || '版本记录读取失败。'
    return
  }
  manuscriptRevisionHistory.value = result.revisions || []
}

async function selectRevision({ passage_id: passageId, revision_number: revisionNumber, expected_revision: expectedRevision }) {
  if (!sessionId.value || !passageId || manuscriptRevisionSelectBusyId.value) return
  manuscriptRevisionSelectBusyId.value = passageId
  manuscriptRevisionHistoryError.value = ''
  const result = await selectManuscriptPassageRevision(
    sessionId.value,
    passageId,
    Number(revisionNumber),
    Number(expectedRevision),
  )
  manuscriptRevisionSelectBusyId.value = ''
  if (result.status !== 'ok') {
    manuscriptRevisionHistoryError.value = result.error || '版本切换失败，请刷新后重试。'
    return
  }
  await refreshPlayerView()
  const history = await getManuscriptPassageRevisions(sessionId.value, passageId)
  if (history.status === 'ok') manuscriptRevisionHistory.value = history.revisions || []
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
    if (['settlement_required', 'settled'].includes(data.status)) {
      settlement.value = { settlement: data.settlement }
      autoRunning.value = false
      autoRunToken += 1
      planError.value = ''
      await openSettlement()
      return null
    }
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
    if (['settlement_required', 'settled'].includes(data.status)) {
      settlement.value = { settlement: data.settlement }
      autoRunning.value = false
      autoRunToken += 1
      planError.value = ''
      await openSettlement()
      return null
    }
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
  if (evolutionClosed.value) {
    planError.value = ''
    await openSettlement()
    return
  }
  await toggleAutoHandler(buildPlayerAutoRequest())
}

function enterInterventionMode() {
  storyMode.value = 'intervention'
}

// ---- 启动与恢复会话 ----
async function startNewSession(packageId = currentPackageId.value, options = {}) {
  loading.value = true
  bootError.value = ''
  const data = await startSession(packageId, options)
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    return false
  }
  applySession(data)
  systemSpaceOpen.value = false
  secondaryMenuOpen.value = false
  if (saveManagerOpen.value) await refreshSaves()
  return true
}

async function refreshWorldPackages() {
  worldSelectionError.value = ''
  const [worldData, bookData] = await Promise.all([
    listPlayableWorlds(),
    listBooks(),
  ])
  if (worldData.status === 'ok') worldPackages.value = worldData.worlds || []
  if (bookData.status !== 'ok') {
    worldSelectionError.value = bookData.error || '小说目录读取失败'
    return
  }
  books.value = bookData.books || []
  const firstBookId = books.value[0]?.book_id
  if (firstBookId && !chaptersByBook.value[firstBookId]) {
    await refreshBookChapters(firstBookId)
  }
}

async function refreshBookChapters(bookId) {
  if (!bookId) return
  const data = await listBookChapters(bookId)
  if (data.status !== 'ok') {
    worldSelectionError.value = data.error || '章节目录读取失败'
    return
  }
  chaptersByBook.value = { ...chaptersByBook.value, [bookId]: data.chapters || [] }
}

async function openWorldSelector() {
  worldSelectorOpen.value = true
  secondaryMenuOpen.value = false
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

async function selectChapterEntry(entry) {
  worldSelectionError.value = ''
  const started = await startNewSession(entry.package_id, {
    bookId: entry.book_id,
    entryId: entry.entry_id,
  })
  if (!started) {
    worldSelectionError.value = bootError.value || '章节世界初始化失败'
    return
  }
  worldSelectorOpen.value = false
}

async function boot() {
  spaceLoading.value = true
  bootError.value = ''
  systemSpaceOpen.value = true
  await Promise.all([refreshWorldPackages(), refreshSaves()])
  const savedSessionId = localStorage.getItem(SESSION_STORAGE_KEY)
  if (savedSessionId && !saves.value.some((save) => save.session_id === savedSessionId)) {
    localStorage.removeItem(SESSION_STORAGE_KEY)
  }
  spaceLoading.value = false
}

async function continueSession(sessionToResume) {
  if (!sessionToResume) return
  loading.value = true
  bootError.value = ''
  const data = await resumeSession(sessionToResume)
  loading.value = false
  if (data.status === 'error') {
    bootError.value = data.error
    await refreshSaves()
    return
  }
  applySession(data, { resumed: true })
  systemSpaceOpen.value = false
}

async function returnToSystemSpace() {
  systemSpaceOpen.value = true
  secondaryMenuOpen.value = false
  worldSelectorOpen.value = false
  utilityDrawer.value = ''
  inspectorOpen.value = false
  await Promise.all([refreshWorldPackages(), refreshSaves()])
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
  clearHistoryResult.value = null
  await refreshSaves()
}

async function clearHistoryHandler(confirmation) {
  clearHistoryBusy.value = true
  clearHistoryResult.value = null
  const data = await clearHistoricalSaves(sessionId.value, confirmation)
  clearHistoryBusy.value = false
  if (data.status === 'error' || data.status === 'forbidden') {
    bootError.value = data.error
    return
  }
  clearHistoryResult.value = data
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
    sessionId.value = ''
    state.value = null
    worldMeta.value = null
    dashboard.value = null
    settlement.value = null
    settlementOpen.value = false
    playerView.value = null
    manuscriptRevisionHistoryPassageId.value = ''
    manuscriptRevisionHistory.value = []
    manuscriptRevisionHistoryError.value = ''
    primaryView.value = 'world'
    novelUnread.value = false
    manuscriptSignature.value = ''
    manuscriptSignatureInitialized.value = false
    manuscriptSignatureSession.value = ''
    systemSpaceOpen.value = true
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
  else systemSpaceOpen.value = false
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
  secondaryMenuOpen.value = false
  systemSpaceOpen.value = false
}

// ---- 提交一回合 ----
async function submitTurnHandler(text, useNpcAgents) {
  if (!sessionId.value || !text.trim() || loading.value) return
  const requestText = text.trim()
  turnSubmitResult.value = null
  loading.value = true
  const data = await submitTurn(sessionId.value, requestText, useNpcAgents)
  loading.value = false

  // 玩家输入先入流 (让剧情流能看到玩家说了什么)
  turns.value.push({ player_input: requestText })

  if (data.status === 'error' || data.status === 'conflict') {
    turns.value.push({ status: 'error', error: data.error })
    turnSubmitResult.value = { success: false, requestText }
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
    action_interpretation: data.action_interpretation || '',
    outcome: data.outcome || null,
    world_progress: data.world_progress || null,
    mission_changes: data.mission_changes || [],
    relation_changes: data.relation_changes || [],
    memory_echoes: data.memory_echoes || [],
    canonical_changes: data.canonical_changes || [],
    suggested_actions: data.suggested_actions || [],
  })
  if (data.state) state.value = data.state
  if (data.settlement) settlement.value = data.settlement
  const success = data.status === 'committed'
  turnSubmitResult.value = { success, requestText }
  await refreshJointPlans()
  await Promise.all([refreshPlayerView(), refreshDashboard()])
  if (saveManagerOpen.value) await refreshSaves()
}

</script>

<template>
  <CreatorStudio
    v-if="creatorMode"
    @back="closeCreator"
    @play="playPackage"
  />
  <div v-else class="bookworld-shell">
    <SystemSpace
      v-if="systemSpaceOpen"
      :worlds="worldPackages"
      :books="books"
      :history="saves"
      :loading="spaceLoading || loading"
      :error="bootError || worldSelectionError"
      @enter-world="selectWorld"
      @continue-session="continueSession"
      @refresh="boot"
      @open-library="openWorldSelector"
      @open-menu="secondaryMenuOpen = !secondaryMenuOpen"
    />
    <template v-else>
    <header class="sim-header" :class="{ 'player-header': interfaceMode === 'player' }">
      <div class="header-left">
        <button
          ref="chapterTriggerRef"
          class="icon-btn chapters-trigger"
          type="button"
          title="章节"
          aria-label="章节"
          aria-controls="chapter-drawer"
          :aria-expanded="mobileChaptersOpen"
          @click="openResponsivePanel('chapters')"
        >
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
      <nav v-if="interfaceMode === 'player'" class="primary-nav" role="tablist" aria-label="玩家主页面">
        <button
          id="primary-tab-world"
          :ref="(element) => { if (element) primaryTabRefs[0] = element }"
          type="button"
          role="tab"
          :class="{ active: primaryView === 'world' }"
          :aria-selected="primaryView === 'world'"
          aria-controls="primary-panel-world"
          :tabindex="primaryView === 'world' ? 0 : -1"
          @click="selectPrimaryView('world')"
          @keydown="handlePrimaryTabKeydown($event, 0)"
        >
          <span>世界演化</span>
        </button>
        <button
          id="primary-tab-novel"
          :ref="(element) => { if (element) primaryTabRefs[1] = element }"
          type="button"
          role="tab"
          :class="{ active: primaryView === 'novel', unread: novelUnread }"
          :aria-selected="primaryView === 'novel'"
          aria-controls="primary-panel-novel"
          :tabindex="primaryView === 'novel' ? 0 : -1"
          @click="selectPrimaryView('novel')"
          @keydown="handlePrimaryTabKeydown($event, 1)"
        >
          <span>我的小说</span>
          <small v-if="novelUnread">新正文</small>
          <b v-else-if="readyNovelCount">{{ readyNovelCount }}</b>
        </button>
      </nav>
      <nav class="top-tools" aria-label="世界工具">
        <button class="top-tool" type="button" aria-label="世界选择" title="世界选择" @click="openWorldSelector">
          <span class="tool-icon world-icon" aria-hidden="true"></span>
          <span>世界</span>
        </button>
        <button class="top-tool" :class="{ active: utilityDrawer === 'map' }" type="button" aria-label="地图" title="地图" :aria-expanded="utilityDrawer === 'map'" @click="toggleUtility('map', $event.currentTarget)">
          <span class="tool-icon map-icon" aria-hidden="true"></span>
          <span>地图</span>
          <small>{{ Object.keys(state?.locations || {}).length }}</small>
        </button>
        <button class="top-tool" :class="{ active: utilityDrawer === 'characters' }" type="button" aria-label="角色档案" title="角色档案" :aria-expanded="utilityDrawer === 'characters'" @click="toggleUtility('characters', $event.currentTarget)">
          <span class="tool-icon people-icon" aria-hidden="true"></span>
          <span>角色档案</span>
          <small>{{ Object.keys(state?.characters || {}).length }}</small>
        </button>
        <button class="top-tool activity-shortcut" :class="{ active: utilityDrawer === 'activity' }" type="button" aria-label="世界动态" title="世界动态" :aria-expanded="utilityDrawer === 'activity'" @click="toggleUtility('activity', $event.currentTarget)">
          <span class="tool-icon activity-icon" aria-hidden="true"></span>
          <span>世界动态</span>
          <small>{{ activityCount }}</small>
        </button>
        <button v-if="interfaceMode === 'developer'" class="top-tool mode-tool" type="button" title="返回玩家模式" @click="interfaceMode = 'player'">
          <span class="tool-icon mode-icon" aria-hidden="true"></span>
          <span>玩家模式</span>
        </button>
        <button
          ref="inspectorTriggerRef"
          class="top-tool inspector-trigger"
          type="button"
          :aria-label="interfaceMode === 'player' ? '原著对照' : '规划检查器'"
          :title="interfaceMode === 'player' ? '原著对照' : '规划检查器'"
          aria-controls="inspector-drawer"
          :aria-expanded="inspectorOpen"
          @click="openResponsivePanel('inspector')"
        >
          <span class="tool-icon inspector-icon" aria-hidden="true"></span>
          <span>{{ interfaceMode === 'player' ? '原著对照' : '规划检查器' }}</span>
        </button>
        <button class="top-tool mobile-global-tool" type="button" aria-label="系统空间" title="系统空间" :disabled="loading" @click="returnToSystemSpace">
          <span class="tool-icon space-icon" aria-hidden="true"></span>
          <span>系统空间</span>
        </button>
        <button class="top-tool mobile-global-tool" type="button" aria-label="世界线" title="世界线" :disabled="loading" @click="openSaveManager">
          <span class="tool-icon save-icon" aria-hidden="true"></span>
          <span>世界线</span>
        </button>
      </nav>
      <div class="runtime-meta">
        <span class="runtime-pill" :class="runtimeStatus.cls">
          <i></i>{{ runtimeStatus.label }}
        </span>
        <span v-if="state" class="version-tag">
          {{ currentLineage.chapter_label || currentSaveName }} · v{{ state.version }}
          <template v-if="Number(currentLineage.depth || 0) > 0"> · 第 {{ Number(currentLineage.depth) + 1 }} 程</template>
        </span>
        <button v-if="autoRunning" class="header-btn stop-runtime" type="button" @click="runPlayerEvolution">停止演化</button>
        <button class="header-btn" @click="returnToSystemSpace" :disabled="loading">系统空间</button>
        <button class="header-btn" @click="openSaveManager" :disabled="loading">世界线</button>
        <button class="header-btn primary" @click="startNewSession(currentPackageId)" :disabled="loading">新的世界线</button>
      </div>
    </header>

    <!-- 启动错误 -->
    <div v-if="bootError" class="boot-error">
      <strong>后端启动失败：</strong>{{ bootError }}
      <div class="hint">请确认已运行后端 (<code>python web/run.py</code>) 且 <code>.env</code> 配置了 LLM_API_KEY。</div>
    </div>

    <div v-if="mobileChaptersOpen || inspectorOpen" class="responsive-scrim" @click="closeResponsivePanels"></div>

    <aside
      id="chapter-drawer"
      class="left-rail"
      :class="{ open: mobileChaptersOpen }"
      :aria-hidden="!mobileChaptersOpen"
      :inert="!mobileChaptersOpen"
    >
      <div class="responsive-panel-heading">
        <strong>章节与旅程</strong>
        <button type="button" aria-label="关闭章节" @click="closeResponsivePanels('chapters')">×</button>
      </div>
      <ChapterNavigator
        :world-meta="worldMeta"
        :state="state"
        :selected-index="selectedChapterIndex"
        :progress-chapter="playerView?.current_story_chapter || 0"
        @select="selectChapter"
      />
    </aside>

    <main class="sim-workspace">
      <section class="story-stage">
        <div class="story-toolbar">
          <div>
            <span class="eyebrow">
              {{ interfaceMode === 'player' ? (primaryView === 'world' ? '参与并改变世界' : '个人世界线稿件') : '世界事件流' }} / {{ selectedChapterLabel }}
            </span>
            <h1>{{ interfaceMode === 'player' ? (primaryView === 'world' ? '世界演化' : '我的小说') : '世界事件流' }}</h1>
          </div>
          <div v-if="interfaceMode === 'player' && primaryView === 'world'" class="experience-controls">
            <div class="story-mode-switch" role="group" aria-label="世界线模式">
              <button type="button" :class="{ active: storyMode === 'replay' }" :disabled="!playerView?.canonical_baseline_available" @click="storyMode = 'replay'">原著复现</button>
              <button type="button" :class="{ active: storyMode === 'intervention' }" @click="storyMode = 'intervention'">穿越干预</button>
            </div>
            <button class="evolve-btn" :class="{ stop: autoRunning }" type="button" :disabled="planBusy && !autoRunning" @click="runPlayerEvolution">
              {{ autoRunning ? '停止演化' : (evolutionClosed ? '查看世界线结算' : '自动演化 3 幕') }}
            </button>
          </div>
          <div v-else-if="interfaceMode === 'player'" class="novel-toolbar-meta">
            <span>{{ readyNovelCount }} 段已生成正文</span>
            <button type="button" @click="selectPrimaryView('world')">返回世界演化</button>
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
            <span><b>v{{ activeDemo.evidence?.world_version ?? 0 }}</b>世界进度</span>
            <span><b>{{ activeDemo.evidence?.tool_calls ?? 0 }}</b>行动记录</span>
            <span><b>{{ activeDemo.evidence?.propagation_count ?? 0 }}</b>消息传播</span>
            <span><b>{{ activeDemo.evidence?.alliance_count ?? 0 }}</b>关系变化</span>
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
          <section
            id="primary-panel-world"
            v-show="primaryView === 'world'"
            class="primary-panel world-panel"
            role="tabpanel"
            aria-labelledby="primary-tab-world"
            :aria-hidden="primaryView !== 'world'"
          >
            <WorldEvolutionView
              :player-view="playerView"
              :state="state"
              :dashboard="dashboard"
              :world-meta="worldMeta"
              :default-actor="defaultActor"
              :latest-turn="latestPlayerTurn"
              :loading="loading || planBusy"
              :settlement="settlementProjection"
              @suggest="inputSuggestion = $event"
              @settle="openSettlement()"
              @open-activity="openWorldActivity"
            />
            <TurnInput
              v-if="storyMode === 'intervention'"
              :loading="loading"
              :suggestion="inputSuggestion"
              :submit-result="turnSubmitResult"
              @suggestion-consumed="inputSuggestion = ''"
              @submit="submitTurnHandler"
            />
            <div v-else class="replay-controls">
              <div>
                <strong>原著自动复现</strong>
                <span>角色会根据已知信息和各自的处境继续行动，真正发生的变化才会写入小说正文。</span>
              </div>
              <button type="button" :disabled="planBusy && !autoRunning" @click="runPlayerEvolution">{{ autoRunning ? '停止' : '推进下一组剧情' }}</button>
            </div>
          </section>
          <section
            id="primary-panel-novel"
            v-show="primaryView === 'novel'"
            class="primary-panel novel-panel"
            role="tabpanel"
            aria-labelledby="primary-tab-novel"
            :aria-hidden="primaryView !== 'novel'"
          >
            <PlayerNovelView
              :player-view="playerView"
              :state="state"
              :loading="loading || planBusy || autoRunning"
              :story-mode="storyMode"
              :selected-passage-id="manuscriptRewriteSelectedId"
              :rewrite-busy-passage-id="manuscriptRewriteBusyId"
              :rewrite-error="manuscriptRewriteError"
              :revision-history-passage-id="manuscriptRevisionHistoryPassageId"
              :revision-history="manuscriptRevisionHistory"
              :revision-history-loading="manuscriptRevisionHistoryLoading"
              :revision-history-error="manuscriptRevisionHistoryError"
              :revision-select-busy-passage-id="manuscriptRevisionSelectBusyId"
              manuscript-only
              reader-only
              aria-label="我的小说正文"
              @rewrite="rewritePassage"
              @revision-history="toggleRevisionHistory"
              @select-revision="selectRevision"
            />
          </section>
        </template>
        <template v-else>
          <StoryFeed :turns="turns" :loading="loading" :default-actor="defaultActor" :state="state" />
          <TurnInput
            :loading="loading"
            :suggestion="inputSuggestion"
            :submit-result="turnSubmitResult"
            :developer-mode="interfaceMode === 'developer'"
            @suggestion-consumed="inputSuggestion = ''"
            @submit="submitTurnHandler"
          />
        </template>
      </section>

      <aside
        id="inspector-drawer"
        class="inspector-rail"
        :class="{ open: inspectorOpen }"
        :aria-hidden="!inspectorOpen"
        :inert="!inspectorOpen"
      >
        <div class="responsive-panel-heading">
          <strong>{{ interfaceMode === 'player' ? '原著对照' : '规划检查器' }}</strong>
          <button type="button" :aria-label="interfaceMode === 'player' ? '关闭原著对照' : '关闭规划检查器'" @click="closeResponsivePanels('inspector')">×</button>
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

    <div v-if="utilityDrawer" class="utility-overlay" @click.self="closeUtilityDrawer()">
      <aside class="utility-drawer" role="dialog" aria-modal="true" :aria-label="utilityDrawerMeta.label">
        <div class="drawer-heading">
          <div>
            <span class="eyebrow">{{ utilityDrawerMeta.eyebrow }}</span>
            <h2>{{ utilityDrawerMeta.label }}</h2>
          </div>
          <div class="drawer-actions">
            <span>{{ utilityDrawerMeta.count }}</span>
            <button type="button" :aria-label="`关闭${utilityDrawerMeta.label}`" @click="closeUtilityDrawer()">×</button>
          </div>
        </div>
        <div class="drawer-body">
          <WorldMap v-if="utilityDrawer === 'map'" :state="state" />
          <CharacterProfiles v-else-if="utilityDrawer === 'characters'" :state="state" :default-actor="defaultActor" />
          <StoryActivityPanel
            v-else-if="utilityDrawer === 'activity'"
            :activity-items="playerView?.activity_items || []"
            :story-beats="playerView?.story_beats || []"
          />
        </div>
      </aside>
    </div>
    <SaveManager
      :open="saveManagerOpen"
      :saves="saves"
      :current-session-id="sessionId"
      :loading="loading"
      :clearing="clearHistoryBusy"
      :clear-result="clearHistoryResult"
      @close="saveManagerOpen = false"
      @create="createSaveFromManager"
      @load="loadSave"
      @rename="renameSaveHandler"
      @delete="deleteSaveHandler"
      @clear-history="clearHistoryHandler"
      @export="exportSaveHandler"
      @import="importSaveHandler"
      @refresh="refreshSaves"
    />
    </template>
    <SettlementView
      v-if="settlementOpen"
      :settlement="settlement"
      :loading="settlementLoading"
      :error="settlementError"
      :transition-loading="transitionLoading"
      :transition-error="transitionError"
      :transition-result="transitionResult"
      @settle="openSettlement({ settle: true })"
      @transition="transitionToNextChapter"
      @close="closeSettlement"
      @system-space="closeSettlement(); returnToSystemSpace()"
    />
    <DemoLauncher
      :open="demoLauncherOpen"
      :loading="loading"
      @close="demoLauncherOpen = false"
      @run="runDemoCaseHandler"
    />
    <div v-if="secondaryMenuOpen" class="secondary-menu" role="menu">
      <button v-if="state" type="button" role="menuitem" @click="secondaryMenuOpen = false; systemSpaceOpen = false">返回当前世界</button>
      <button type="button" role="menuitem" @click="openCreator">创作台</button>
      <button type="button" role="menuitem" @click="demoLauncherOpen = true; secondaryMenuOpen = false">演示与技术验证</button>
      <button type="button" role="menuitem" @click="interfaceMode = 'developer'; secondaryMenuOpen = false; systemSpaceOpen = false">开发者模式</button>
    </div>
    <WorldSelector
      :open="worldSelectorOpen"
      :packages="worldPackages"
      :books="books"
      :chapters-by-book="chaptersByBook"
      :current-package-id="currentPackageId"
      :loading="loading"
      :error="worldSelectionError"
      :chapter-access="chapterAccessMap"
      @close="worldSelectorOpen = false"
      @refresh="refreshWorldPackages"
      @select="selectWorld"
      @select-entry="selectChapterEntry"
      @refresh-book="refreshBookChapters"
    />
  </div>
</template>

<style scoped>
.bookworld-shell {
  display: flex;
  flex-direction: column;
  min-width: 0;
  height: 100vh;
  background: var(--bg);
}
.sim-header {
  display: flex;
  align-items: center;
  min-height: 68px;
  gap: 20px;
  padding: 9px 24px;
  flex-shrink: 0;
  background: #111418;
  border-bottom: 1px solid #252a31;
  box-shadow: 0 8px 24px rgba(0, 0, 0, .16);
}
.story-stage {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
.demo-evidence-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 10px 14px;
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
.boot-error {
  margin: 0;
  padding: 9px 18px;
  background: rgba(201, 90, 90, 0.12);
  border-bottom: 1px solid var(--danger);
  color: var(--danger);
  font-size: 13px;
}
.boot-error .hint { margin-top: 6px; color: var(--text-dim); font-size: 13px; }
.boot-error code { background: var(--bg-input); padding: 1px 5px; border-radius: 3px; color: var(--accent); }
.sim-header {
  position: relative;
  z-index: 40;
  min-height: 64px;
  gap: 16px;
  padding: 8px 14px;
  background: #17191c;
  border-color: var(--border-soft);
}
.header-left, .primary-nav, .top-tools, .runtime-meta { display: flex; align-items: center; min-width: 0; }
.header-left { flex: 0 1 270px; min-width: 230px; gap: 12px; }
.header-left .brand { min-width: 0; }
.primary-nav { flex: 0 0 auto; gap: 3px; padding: 3px; border: 1px solid #343944; border-radius: 10px; background: #111317; }
.primary-nav button { position: relative; display: flex; min-width: 98px; align-items: center; justify-content: center; gap: 7px; padding: 8px 12px; border: 0; border-radius: 7px; background: transparent; color: #8f949e; font-size: 12px; font-weight: 600; }
.primary-nav button::after { position: absolute; right: 14px; bottom: 3px; left: 14px; height: 2px; border-radius: 2px; background: transparent; content: ''; }
.primary-nav button:hover { background: rgba(255,255,255,.035); color: var(--text); }
.primary-nav button.active { background: #292d34; color: #f0eee8; box-shadow: 0 2px 8px rgba(0,0,0,.22); }
.primary-nav button.active::after { background: #c8a971; }
.primary-nav button.unread::before { position: absolute; top: 5px; right: 6px; width: 5px; height: 5px; border-radius: 50%; background: #d6aa66; content: ''; box-shadow: 0 0 0 3px rgba(214,170,102,.12); }
.primary-nav small { padding: 2px 5px; border-radius: 999px; background: rgba(214,170,102,.14); color: #e1bc7e; font-size: 8px; font-weight: 700; }
.primary-nav b { min-width: 17px; padding: 1px 5px; border-radius: 999px; background: rgba(255,255,255,.07); color: var(--text-faint); font: 600 8px/1.5 ui-monospace, monospace; }
.top-tools { justify-content: center; flex: 1 1 auto; }
.runtime-meta { flex: 0 1 auto; justify-content: flex-end; margin-left: auto; }
.brand { display: flex; min-width: 0; gap: 10px; align-items: center; white-space: nowrap; }
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
.icon-btn, .top-tool {
  align-items: center;
  gap: 7px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-dim);
}
.top-tools { display: flex; justify-content: center; flex: 0 1 auto; gap: 4px; }
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
.activity-icon::before, .activity-icon::after { position: absolute; left: 1px; right: 1px; height: 1px; background: currentColor; content: ''; }
.activity-icon::before { top: 4px; box-shadow: 0 5px currentColor; }
.activity-icon::after { top: 1px; left: 3px; right: auto; width: 3px; height: 3px; border-radius: 50%; box-shadow: 5px 5px currentColor, 9px 0 currentColor; }
.inspector-icon { border: 1px solid currentColor; border-radius: 3px; }
.inspector-icon::before { position: absolute; top: 2px; bottom: 2px; left: 4px; width: 1px; background: currentColor; content: ''; }
.mode-icon { border: 1px solid currentColor; border-radius: 3px; }
.mode-icon::before { position: absolute; top: 3px; left: 3px; width: 3px; height: 3px; border: 1px solid currentColor; border-radius: 50%; content: ''; }
.mode-icon::after { position: absolute; right: 2px; bottom: 2px; width: 6px; height: 1px; background: currentColor; box-shadow: 0 -3px currentColor; content: ''; }
.space-icon { border: 1px solid currentColor; border-radius: 3px; }
.space-icon::before { position: absolute; top: 3px; right: 3px; bottom: 3px; left: 3px; border: 1px solid currentColor; border-radius: 50%; content: ''; }
.save-icon { border: 1px solid currentColor; border-radius: 3px; }
.save-icon::before { position: absolute; top: 3px; right: 2px; left: 2px; height: 1px; background: currentColor; box-shadow: 0 4px currentColor; content: ''; }
.mobile-global-tool { display: none; }
.runtime-meta { flex: 0 0 auto; gap: 6px; }
.runtime-pill, .version-tag { border-color: var(--border-soft); background: rgba(255,255,255,.02); }
.header-btn { padding: 7px 10px; border-color: var(--border); background: #202226; color: var(--text-dim); }
.header-btn:hover:not(:disabled) { border-color: #50535b; background: #292c31; color: var(--text); }
.header-btn.primary { border-color: #4b4e55; background: #2a2d32; color: var(--text); }
.header-btn.stop-runtime { border-color: rgba(201,90,90,.48); background: rgba(201,90,90,.1); color: #e5a0a0; }
.header-btn.stop-runtime:hover { border-color: rgba(220,115,115,.68); background: rgba(201,90,90,.16); color: #f0b1b1; }
.header-btn.demo-btn { border-color: var(--border); background: #202226; color: var(--text-dim); }
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
.novel-toolbar-meta { display: flex; align-items: center; gap: 10px; }
.novel-toolbar-meta span { color: var(--text-faint); font-size: 10px; }
.novel-toolbar-meta button { padding: 7px 9px; border: 1px solid #4b5059; background: #282b31; color: var(--text-dim); font-size: 10px; }
.novel-toolbar-meta button:hover { border-color: #656b76; color: var(--text); }
.primary-panel { display: flex; min-width: 0; min-height: 0; flex: 1; flex-direction: column; overflow: hidden; }
.primary-panel[aria-hidden="true"] { display: none; }
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
.demo-evidence-banner { border-color: rgba(138,180,248,.2); background: linear-gradient(90deg, rgba(138,180,248,.08), rgba(255,255,255,.02)); }
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

@media (max-width: 1380px) {
  .brand-context, .version-tag, .wide-action { display: none; }
  .header-left { flex-basis: auto; min-width: 170px; }
  .mode-tool span:not(.tool-icon), .top-tools .top-tool span:not(.tool-icon) { display: none; }
  .top-tool { padding-inline: 8px; }
}

@media (max-width: 1120px) {
  .inspector-trigger { display: flex; }
  .header-left { min-width: auto; }
  .brand { display: none; }
  .primary-nav button { min-width: 88px; padding-inline: 9px; }
  .runtime-meta .header-btn.primary { display: none; }
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
  .header-left { flex: 0 0 auto; min-width: auto; }
  .chapters-trigger { display: flex; min-width: auto; padding: 7px 8px; font-size: 11px; }
  .hamburger-icon { width: 14px; height: 10px; border-top: 1px solid currentColor; border-bottom: 1px solid currentColor; box-shadow: 0 -4px transparent; }
  .brand { display: none; }
  .primary-nav { flex: 1 1 auto; justify-content: center; }
  .primary-nav button { min-width: 0; flex: 1 1 0; padding: 7px 8px; font-size: 11px; }
  .primary-nav small { display: none; }
  .top-tools {
    flex: 0 1 auto;
    justify-content: flex-start;
    gap: 1px;
    overflow-x: auto;
    overscroll-behavior-inline: contain;
    scrollbar-width: none;
  }
  .top-tools::-webkit-scrollbar { display: none; }
  .top-tools .top-tool { display: flex; flex: 0 0 auto; }
  .top-tools .mobile-global-tool { display: flex; }
  .top-tool { gap: 5px; padding: 7px; }
  .top-tool small, .inspector-trigger span:last-child { display: none; }
  .runtime-meta { gap: 4px; }
  .runtime-pill, .wide-action, .runtime-meta .header-btn:not(.stop-runtime) { display: none; }
  .header-btn { padding: 7px 8px; font-size: 11px; }
  .sim-workspace { gap: 0; padding: 6px; }
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
  .primary-nav button { padding-inline: 5px; font-size: 10px; }
  .primary-nav b { display: none; }
  .top-tool span:not(.tool-icon) { display: none; }
  .top-tool { padding: 8px; }
  .chapters-trigger span:last-child { display: none; }
  .drawer-heading { min-height: 58px; }
  .story-toolbar { align-items: flex-start; flex-direction: column; gap: 7px; }
  .experience-controls, .novel-toolbar-meta { width: 100%; justify-content: space-between; }
  .replay-controls span { display: none; }
  .player-warning { align-items: flex-start; flex-wrap: wrap; }
  .player-warning span { width: 100%; white-space: normal; }
  .warning-actions { width: 100%; margin-left: 0; }
}

/* Focus mode: keep the story on stage and reveal context only on demand. */
.sim-workspace {
  display: flex;
  flex: 1;
  min-width: 0;
  min-height: 0;
  padding: 18px 24px 22px;
  background:
    radial-gradient(circle at 50% -20%, rgba(229, 197, 139, .08), transparent 36%),
    #0e1013;
}
.story-stage {
  width: min(100%, 1400px);
  margin: 0 auto;
  border: 1px solid #343944;
  border-radius: 20px;
  background: #181b20;
  box-shadow: 0 20px 60px rgba(0, 0, 0, .3), 0 1px 0 rgba(255,255,255,.045) inset;
}
.left-rail, .inspector-rail {
  position: fixed;
  z-index: 70;
  top: 76px;
  bottom: 12px;
  width: min(360px, calc(100vw - 32px));
  min-height: 0;
  overflow: hidden;
  border: 1px solid #383d46;
  border-radius: 14px;
  background: #171a20;
  box-shadow: 0 22px 60px rgba(0,0,0,.42);
  transition: transform .22s ease, opacity .22s ease;
}
.left-rail {
  left: 12px;
  transform: translateX(calc(-100% - 28px));
  flex-direction: column;
}
.left-rail.open { transform: translateX(0); }
.inspector-rail {
  right: 12px;
  transform: translateX(calc(100% + 28px));
}
.inspector-rail.open { transform: translateX(0); }
.left-rail:not(.open), .inspector-rail:not(.open) {
  pointer-events: none;
  opacity: 0;
}
.responsive-panel-heading {
  display: flex;
  min-height: 54px;
  align-items: center;
  justify-content: space-between;
  padding: 10px 12px 10px 16px;
  border-bottom: 1px solid #30343b;
  background: #1d2025;
}
.responsive-panel-heading strong { color: var(--text); font-size: 13px; letter-spacing: .02em; }
.responsive-panel-heading button {
  border-radius: 8px;
  transition: background .15s, color .15s, border-color .15s;
}
.responsive-panel-heading button:hover,
.responsive-panel-heading button:focus-visible {
  border-color: #5b6270;
  background: #2a2e35;
  color: var(--text);
}
.responsive-scrim {
  position: fixed;
  z-index: 60;
  inset: 64px 0 0;
  display: block;
  background: rgba(3, 4, 6, .68);
  backdrop-filter: blur(2px);
}
.story-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  min-height: 86px;
  padding: 18px clamp(28px, 6vw, 86px) 16px;
  background: linear-gradient(180deg, #20242a, #1b1e23);
  border-bottom: 1px solid #343a45;
}
.story-toolbar > div:first-child { min-width: 0; }
.story-toolbar .experience-controls { flex: 0 0 auto; }
.story-toolbar h1 { font-size: 18px; letter-spacing: .01em; }
.story-toolbar .eyebrow { color: #858b96; }
.chapters-trigger, .inspector-trigger { display: flex; }
.chapters-trigger {
  display: inline-flex;
  flex: 0 0 auto;
  width: auto;
  min-width: 72px;
  justify-content: center;
  padding: 7px 11px;
  border: 1px solid #30343b;
  border-radius: 8px;
  background: #202328;
  white-space: nowrap;
  writing-mode: horizontal-tb;
}
.chapters-trigger:hover, .chapters-trigger:focus-visible,
.inspector-trigger:hover, .inspector-trigger:focus-visible {
  border-color: #59616d;
  background: #2a2e35;
  color: var(--text);
}
.top-tool.active, .chapters-trigger[aria-expanded="true"] { border-color: #58616e; background: #292d34; color: var(--text); }
.novel-reader {
  padding: 34px clamp(22px, 5vw, 72px) 48px;
  background:
    radial-gradient(circle at 50% 0%, rgba(229,197,139,.045), transparent 32%),
    #15181c;
}
.story-beat {
  max-width: 900px;
  padding: 30px 42px 36px;
  border: 1px solid rgba(255,255,255,.085);
  border-radius: 16px;
  background: linear-gradient(145deg, rgba(31,35,42,.92), rgba(24,28,34,.84));
  box-shadow: 0 14px 34px rgba(0,0,0,.16), 0 1px 0 rgba(255,255,255,.03) inset;
}
.story-beat + .story-beat { margin-top: 14px; }
.narrative-copy { font-size: 16px; line-height: 2; }
.chapter-break { margin-top: 0; padding: 0 0 14px; border-bottom-color: #343b45; }
.chapter-break strong { font-size: 15px; }
.beat-heading h2 { font-size: 19px; }
.input-bar, .replay-controls { background: #1b1e22; border-color: #30343b; }
.input-bar { padding: 14px 22px 16px; }
.input { border-color: #3b4048; border-radius: 10px; background: #111316; }
.input:focus { border-color: #77808d; box-shadow: 0 0 0 3px rgba(138,180,248,.08); }
.send-btn { border-radius: 9px; background: #d6d9df; }
.timeline-tail { max-width: 860px; margin: 30px auto 24px; }
.intervene-entry { width: min(860px, 100%); }
.left-rail > :deep(.chapter-nav) { min-height: 230px; flex: 1 1 48%; }
.left-rail > :deep(.player-dashboard) { min-height: 210px; flex: 1 1 52%; }
.left-rail > :deep(.chapter-nav), .left-rail > :deep(.player-dashboard) { background: transparent; }
.inspector-rail > :deep(.canon-panel), .inspector-rail > :deep(.inspector) { background: transparent; }
button:focus-visible, textarea:focus-visible, input:focus-visible, summary:focus-visible {
  outline: 2px solid rgba(138,180,248,.75);
  outline-offset: 2px;
}
@media (max-width: 760px) {
  .sim-workspace { padding: 6px; }
  .story-stage { border-radius: 12px; }
  .left-rail, .inspector-rail { top: 64px; bottom: 6px; }
  .left-rail { left: 6px; }
  .inspector-rail { right: 6px; }
  .novel-reader { padding: 20px 16px 48px; }
  .story-beat { padding: 22px 20px 26px; }
  .story-toolbar { min-height: 64px; padding: 11px 13px; }
  .input-bar { padding: 11px 12px 13px; }
}
@media (max-width: 480px) {
  .sim-workspace { padding: 4px; }
  .novel-reader { padding-inline: 16px; }
  .left-rail, .inspector-rail { width: calc(100vw - 20px); }
}
@media (prefers-reduced-motion: reduce) {
  .left-rail, .inspector-rail, .reader-loading, .dot { transition: none; animation: none; }
}
</style>
