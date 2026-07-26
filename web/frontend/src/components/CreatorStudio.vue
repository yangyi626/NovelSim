<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  cloneWorldPackage,
  getWorldPackage,
  listWorldPackages,
  saveWorldPackage,
  validateWorldPackage,
} from '../api.js'

const emit = defineEmits(['back', 'play'])

const packages = ref([])
const selectedId = ref('')
const draft = ref(null)
const loading = ref(false)
const notice = ref('')
const errors = ref([])
const activeTab = ref('overview')
const selectedCharacterId = ref('')
const selectedLocationId = ref('')
const selectedItemId = ref('')
const selectedPsycheId = ref('')
const selectedBeliefCharacterId = ref('')
const rawSnapshot = ref('')

const tabs = [
  ['overview', '总览'],
  ['characters', '角色'],
  ['psyches', '角色心理'],
  ['beliefs', '角色认知'],
  ['locations', '地点'],
  ['items', '物品'],
  ['relations', '关系'],
  ['rules', '规则'],
  ['plot', '剧情线'],
  ['json', '高级 JSON'],
]

const currentSummary = computed(
  () => packages.value.find((item) => item.package_id === selectedId.value),
)
const editable = computed(() => draft.value?.source === 'custom')
const snapshot = computed(() => draft.value?.snapshot || null)
const characterList = computed(
  () => Object.values(snapshot.value?.characters || {}),
)
const selectedCharacter = computed(
  () => snapshot.value?.characters?.[selectedCharacterId.value] || null,
)
const locationList = computed(
  () => Object.values(snapshot.value?.locations || {}),
)
const selectedLocation = computed(
  () => snapshot.value?.locations?.[selectedLocationId.value] || null,
)
const itemList = computed(
  () => Object.values(snapshot.value?.items || {}),
)
const selectedItem = computed(
  () => snapshot.value?.items?.[selectedItemId.value] || null,
)
const selectedPsyche = computed(
  () => snapshot.value?.character_psyches?.[selectedPsycheId.value] || null,
)
const selectedBeliefs = computed(
  () => snapshot.value?.beliefs?.[selectedBeliefCharacterId.value] || [],
)
const plotEntries = computed(
  () => Object.entries(snapshot.value?.plot || {}),
)
const stats = computed(() => ({
  characters: characterList.value.length,
  relations: snapshot.value?.relations?.length || 0,
  rules: snapshot.value?.world_rules?.length || 0,
  locations: Object.keys(snapshot.value?.locations || {}).length,
  items: Object.keys(snapshot.value?.items || {}).length,
  beliefs: Object.values(snapshot.value?.beliefs || {}).reduce(
    (count, items) => count + items.length,
    0,
  ),
  plots: plotEntries.value.length,
}))

function clearFeedback() {
  notice.value = ''
  errors.value = []
}

function splitList(value) {
  return value
    .split(/[,，、\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function splitLines(value) {
  return value
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean)
}

function formatJson(value) {
  return JSON.stringify(value ?? {}, null, 2)
}

function updateJsonObject(target, key, value, label) {
  clearFeedback()
  try {
    target[key] = JSON.parse(value || '{}')
  } catch (_) {
    errors.value = [`${label}必须是有效 JSON`]
  }
}

async function refreshPackages(preferredId = selectedId.value) {
  const data = await listWorldPackages()
  if (data.status === 'error') {
    errors.value = [data.error]
    return
  }
  packages.value = data.packages || []
  const nextId = packages.value.some((item) => item.package_id === preferredId)
    ? preferredId
    : packages.value[0]?.package_id
  if (nextId) await loadPackage(nextId)
}

async function loadPackage(packageId) {
  loading.value = true
  clearFeedback()
  const data = await getWorldPackage(packageId)
  loading.value = false
  if (data.status === 'error') {
    errors.value = [data.error]
    return
  }
  selectedId.value = packageId
  draft.value = data.package
  initializeSelections()
  syncRawSnapshot()
}

function initializeSelections() {
  selectedCharacterId.value = Object.keys(snapshot.value?.characters || {})[0] || ''
  selectedLocationId.value = Object.keys(snapshot.value?.locations || {})[0] || ''
  selectedItemId.value = Object.keys(snapshot.value?.items || {})[0] || ''
  selectedPsycheId.value = selectedCharacterId.value
  selectedBeliefCharacterId.value = selectedCharacterId.value
}

async function clonePackage() {
  if (!selectedId.value || loading.value) return
  loading.value = true
  clearFeedback()
  const data = await cloneWorldPackage(selectedId.value)
  loading.value = false
  if (data.status === 'error') {
    errors.value = [data.error]
    return
  }
  await refreshPackages(data.package.package_id)
  notice.value = `已创建可编辑版本 ${data.package.package_id}`
}

async function validateDraft() {
  if (!draft.value || loading.value) return false
  loading.value = true
  clearFeedback()
  const data = await validateWorldPackage(draft.value)
  loading.value = false
  if (data.status !== 'ok') {
    errors.value = data.errors?.length ? data.errors : [data.error]
    return false
  }
  notice.value = `校验通过：${data.manifest.character_count} 个角色，${data.manifest.relation_count} 条关系`
  return true
}

async function saveDraft() {
  if (!draft.value || !editable.value || loading.value) return false
  loading.value = true
  clearFeedback()
  const data = await saveWorldPackage(
    draft.value.package_id,
    draft.value,
    draft.value.revision,
  )
  loading.value = false
  if (data.status !== 'ok') {
    errors.value = data.errors?.length ? data.errors : [data.error]
    return false
  }
  draft.value = data.package
  notice.value = `世界包已保存为修订 v${data.package.revision}`
  await refreshPackageListOnly()
  return true
}

async function playCurrent() {
  if (!draft.value || loading.value) return
  if (editable.value && !(await saveDraft())) return
  emit('play', draft.value.package_id)
}

async function refreshPackageListOnly() {
  const data = await listWorldPackages()
  if (data.status === 'ok') packages.value = data.packages || []
}

function addRelation() {
  const ids = Object.keys(snapshot.value.characters)
  if (ids.length < 2) {
    errors.value = ['至少需要两个角色才能建立关系']
    return
  }
  snapshot.value.relations.push({
    source_id: ids[0],
    target_id: ids[1],
    public_relation: '',
    private_relation: '',
    dimensions: {
      affection: 0,
      trust: 0,
      fear: 0,
      hostility: 0,
      respect: 0,
      debt: 0,
    },
  })
}

function addWorldRule() {
  const rules = snapshot.value.world_rules
  let index = rules.length + 1
  while (rules.some((rule) => rule.rule_id === `rule_creator_${index}`)) index += 1
  rules.push({
    rule_id: `rule_creator_${index}`,
    category: 'identity',
    statement: '填写这条世界规则',
  })
}

function addPlotArc() {
  let index = plotEntries.value.length + 1
  let id = `arc_creator_${index}`
  while (snapshot.value.plot[id]) {
    index += 1
    id = `arc_creator_${index}`
  }
  snapshot.value.plot[id] = {
    arc_id: id,
    title: '新的剧情线',
    kind: 'side',
    stage: 'not_started',
    completed: false,
    attrs: {},
  }
}

function nextEntityId(prefix, bucket) {
  let index = Object.keys(bucket).length + 1
  let id = `${prefix}_${index}`
  while (bucket[id]) {
    index += 1
    id = `${prefix}_${index}`
  }
  return id
}

function addLocation() {
  const id = nextEntityId('loc_creator', snapshot.value.locations)
  snapshot.value.locations[id] = {
    location_id: id,
    display_name: '新的地点',
    parent_id: null,
    accessible: true,
    requires_permission: [],
    attrs: {},
  }
  selectedLocationId.value = id
}

function removeLocation(locationId) {
  clearFeedback()
  const references = []
  if (snapshot.value.current_scene_id === locationId) references.push('初始场景')
  for (const character of characterList.value) {
    if (character.location_id === locationId) references.push(`角色 ${character.display_name}`)
  }
  for (const item of itemList.value) {
    if (item.location_id === locationId) references.push(`物品 ${item.display_name}`)
  }
  for (const location of locationList.value) {
    if (location.parent_id === locationId) references.push(`子地点 ${location.display_name}`)
  }
  if (references.length) {
    errors.value = [`无法删除地点，仍被引用：${references.join('、')}`]
    return
  }
  delete snapshot.value.locations[locationId]
  selectedLocationId.value = Object.keys(snapshot.value.locations)[0] || ''
}

function addItem() {
  const id = nextEntityId('item_creator', snapshot.value.items)
  snapshot.value.items[id] = {
    item_id: id,
    display_name: '新的物品',
    owner_id: null,
    location_id: snapshot.value.current_scene_id || null,
    quantity: 1,
    unique: true,
    accessible: true,
    attrs: {},
  }
  selectedItemId.value = id
}

function removeItem(itemId) {
  clearFeedback()
  const holders = characterList.value.filter(
    (character) => character.inventory?.includes(itemId),
  )
  if (holders.length) {
    errors.value = [`无法删除物品，仍在角色背包中：${holders.map((item) => item.display_name).join('、')}`]
    return
  }
  delete snapshot.value.items[itemId]
  selectedItemId.value = Object.keys(snapshot.value.items)[0] || ''
}

function enablePsyche(characterId) {
  snapshot.value.character_psyches[characterId] = {
    character_id: characterId,
    traits: [],
    emotion: '平静',
    emotion_intensity: 0.5,
    goals: [],
    plans: [],
    recent_perceptions: [],
    is_player: characterId === draft.value.default_actor_id,
  }
}

function removePsyche(characterId) {
  delete snapshot.value.character_psyches[characterId]
}

function addGoal(psyche) {
  let index = psyche.goals.length + 1
  let id = `goal_creator_${index}`
  while (psyche.goals.some((goal) => goal.goal_id === id)) {
    index += 1
    id = `goal_creator_${index}`
  }
  psyche.goals.push({
    goal_id: id,
    description: '填写角色目标',
    priority: 0.5,
    target_ids: [],
    achieved: false,
  })
}

function removeGoal(psyche, index) {
  const goalId = psyche.goals[index].goal_id
  if (psyche.plans.some((plan) => plan.goal_id === goalId)) {
    errors.value = [`目标 ${goalId} 仍被计划引用，请先修改或删除对应计划`]
    return
  }
  psyche.goals.splice(index, 1)
}

function addPlan(psyche) {
  if (!psyche.goals.length) {
    errors.value = ['请先为角色添加至少一个目标']
    return
  }
  let index = psyche.plans.length + 1
  let id = `plan_creator_${index}`
  while (psyche.plans.some((plan) => plan.plan_id === id)) {
    index += 1
    id = `plan_creator_${index}`
  }
  psyche.plans.push({
    plan_id: id,
    goal_id: psyche.goals[0].goal_id,
    steps: ['填写第一步行动'],
    current_step: 0,
    status: 'active',
  })
}

function addBelief(characterId) {
  if (!snapshot.value.beliefs[characterId]) {
    snapshot.value.beliefs[characterId] = []
  }
  const beliefs = snapshot.value.beliefs[characterId]
  let index = beliefs.length + 1
  let factId = `fact_creator_${index}`
  while (beliefs.some((belief) => belief.fact_id === factId)) {
    index += 1
    factId = `fact_creator_${index}`
  }
  beliefs.push({
    fact_id: factId,
    belief: 'unknown',
    confidence: 0,
    source_type: 'unknown',
    source_event_id: null,
    keywords: [],
  })
}

function removeBelief(characterId, index) {
  snapshot.value.beliefs[characterId].splice(index, 1)
}

function syncRawSnapshot() {
  rawSnapshot.value = formatJson(draft.value?.snapshot)
}

function applyRawSnapshot() {
  clearFeedback()
  try {
    draft.value.snapshot = JSON.parse(rawSnapshot.value)
    initializeSelections()
    notice.value = 'JSON 已应用到当前草稿，保存前请先校验'
  } catch (_) {
    errors.value = ['世界状态 JSON 格式无效']
  }
}

function selectTab(tabId) {
  activeTab.value = tabId
  if (tabId === 'json') syncRawSnapshot()
}

onMounted(refreshPackages)
</script>

<template>
  <div class="studio">
    <header class="studio-bar">
      <div class="brand">
        <button class="back-button" @click="emit('back')">← 返回试玩</button>
        <div>
          <strong>世界创作台</strong>
          <span>World Package Studio</span>
        </div>
      </div>
      <div class="toolbar">
        <button class="ghost" :disabled="loading || !draft" @click="validateDraft">校验</button>
        <button class="ghost" :disabled="loading || !draft" @click="clonePackage">另存为新版本</button>
        <button class="primary" :disabled="loading || !editable" @click="saveDraft">保存世界包</button>
        <button class="play" :disabled="loading || !draft" @click="playCurrent">保存并试玩</button>
      </div>
    </header>

    <div class="studio-body">
      <aside class="package-rail">
        <div class="rail-title">
          <span>世界包</span>
          <small>{{ packages.length }}</small>
        </div>
        <button
          v-for="pkg in packages"
          :key="pkg.package_id"
          class="package-card"
          :class="{ active: pkg.package_id === selectedId }"
          @click="loadPackage(pkg.package_id)"
        >
          <span class="source-mark" :class="pkg.source">{{ pkg.editable ? '创作' : '内置' }}</span>
          <strong>{{ pkg.scenario }}</strong>
          <span>{{ pkg.novel }}</span>
          <small>{{ pkg.package_id }} · r{{ pkg.revision }}</small>
        </button>
        <p class="rail-hint">内置包保持只读。点击“另存为新版本”后即可自由编辑。</p>
      </aside>

      <main v-if="draft" class="workspace">
        <section class="package-heading">
          <div>
            <div class="eyebrow">{{ editable ? '可编辑版本' : '只读基准包' }} · {{ draft.package_id }}</div>
            <h1>{{ draft.scenario }}</h1>
            <p>{{ draft.anchor }}</p>
          </div>
          <div class="revision">修订 r{{ draft.revision }}</div>
        </section>

        <div v-if="notice" class="notice success">{{ notice }}</div>
        <div v-if="errors.length" class="notice error">
          <strong>需要处理 {{ errors.length }} 项：</strong>
          <ul>
            <li v-for="error in errors" :key="error">{{ error }}</li>
          </ul>
        </div>

        <nav class="tabs" aria-label="世界包编辑区域">
          <button
            v-for="[id, label] in tabs"
            :key="id"
            :class="{ active: activeTab === id }"
            @click="selectTab(id)"
          >{{ label }}</button>
        </nav>

        <fieldset :disabled="!editable" class="editor-fieldset">
          <section v-if="activeTab === 'overview'" class="panel">
            <div class="stat-grid">
              <article><strong>{{ stats.characters }}</strong><span>角色</span></article>
              <article><strong>{{ stats.relations }}</strong><span>关系</span></article>
              <article><strong>{{ stats.rules }}</strong><span>规则</span></article>
              <article><strong>{{ stats.locations }}</strong><span>地点</span></article>
              <article><strong>{{ stats.items }}</strong><span>物品</span></article>
              <article><strong>{{ stats.beliefs }}</strong><span>认知事实</span></article>
              <article><strong>{{ stats.plots }}</strong><span>剧情线</span></article>
            </div>
            <div class="form-grid">
              <label>小说名称<input v-model="draft.novel" /></label>
              <label>场景名称<input v-model="draft.scenario" /></label>
              <label class="wide">介入锚点<textarea v-model="draft.anchor" rows="3"></textarea></label>
              <label>
                默认玩家角色
                <select v-model="draft.default_actor_id">
                  <option v-for="character in characterList" :key="character.character_id" :value="character.character_id">
                    {{ character.display_name }} · {{ character.character_id }}
                  </option>
                </select>
              </label>
              <label>
                世界时间
                <input v-model="draft.snapshot.world_time" />
              </label>
              <label>
                初始场景
                <select v-model="draft.snapshot.current_scene_id">
                  <option value="">未指定</option>
                  <option v-for="location in Object.values(draft.snapshot.locations)" :key="location.location_id" :value="location.location_id">
                    {{ location.display_name }} · {{ location.location_id }}
                  </option>
                </select>
              </label>
            </div>
          </section>

          <section v-else-if="activeTab === 'characters'" class="panel split-editor">
            <div class="entity-list">
              <button
                v-for="character in characterList"
                :key="character.character_id"
                :class="{ active: selectedCharacterId === character.character_id }"
                @click.prevent="selectedCharacterId = character.character_id"
              >
                <strong>{{ character.display_name }}</strong>
                <span>{{ character.character_id }}</span>
              </button>
            </div>
            <div v-if="selectedCharacter" class="detail-form">
              <div class="section-title">角色档案</div>
              <div class="form-grid">
                <label>显示名<input v-model="selectedCharacter.display_name" /></label>
                <label>
                  所在地点
                  <select v-model="selectedCharacter.location_id">
                    <option :value="null">未指定</option>
                    <option v-for="location in Object.values(snapshot.locations)" :key="location.location_id" :value="location.location_id">
                      {{ location.display_name }}
                    </option>
                  </select>
                </label>
                <label class="wide">
                  别名
                  <input
                    :value="selectedCharacter.aliases.join('、')"
                    @input="selectedCharacter.aliases = splitList($event.target.value)"
                  />
                </label>
                <label class="wide">
                  身份标签
                  <input
                    :value="selectedCharacter.identity_tags.join('、')"
                    @input="selectedCharacter.identity_tags = splitList($event.target.value)"
                  />
                </label>
                <label class="wide">
                  初始背包物品
                  <input
                    :value="selectedCharacter.inventory.join('、')"
                    placeholder="填写物品 ID，以逗号或顿号分隔"
                    @input="selectedCharacter.inventory = splitList($event.target.value)"
                  />
                </label>
                <label class="check"><input v-model="selectedCharacter.is_alive" type="checkbox" /> 初始存活</label>
                <label class="wide">
                  小说特有属性（JSON）
                  <textarea
                    :value="formatJson(selectedCharacter.attrs)"
                    rows="9"
                    spellcheck="false"
                    @change="updateJsonObject(selectedCharacter, 'attrs', $event.target.value, '角色属性')"
                  ></textarea>
                </label>
              </div>
            </div>
          </section>

          <section v-else-if="activeTab === 'psyches'" class="panel split-editor">
            <div class="entity-list">
              <button
                v-for="character in characterList"
                :key="character.character_id"
                :class="{ active: selectedPsycheId === character.character_id }"
                @click.prevent="selectedPsycheId = character.character_id"
              >
                <strong>{{ character.display_name }}</strong>
                <span>
                  {{ snapshot.character_psyches[character.character_id] ? '已启用 Agent' : '未配置心理' }}
                </span>
              </button>
            </div>
            <div class="detail-form">
              <template v-if="selectedPsyche">
                <div class="section-head">
                  <div>
                    <div class="section-title">{{ snapshot.characters[selectedPsycheId]?.display_name }} · 心理模型</div>
                    <p>人格、情绪、目标和计划会直接参与 NPC 自主决策。</p>
                  </div>
                  <button class="icon-danger" @click.prevent="removePsyche(selectedPsycheId)">停用心理</button>
                </div>
                <div class="form-grid">
                  <label class="wide">
                    人格特征
                    <input
                      :value="selectedPsyche.traits.join('、')"
                      @input="selectedPsyche.traits = splitList($event.target.value)"
                    />
                  </label>
                  <label>当前情绪<input v-model="selectedPsyche.emotion" /></label>
                  <label>
                    情绪强度（0–1）
                    <input v-model.number="selectedPsyche.emotion_intensity" type="number" min="0" max="1" step="0.1" />
                  </label>
                  <label class="check"><input v-model="selectedPsyche.is_player" type="checkbox" /> 玩家宿主（不自动行动）</label>
                  <label class="wide">
                    初始短期记忆（每行一条）
                    <textarea
                      :value="selectedPsyche.recent_perceptions.join('\n')"
                      rows="3"
                      @input="selectedPsyche.recent_perceptions = splitLines($event.target.value)"
                    ></textarea>
                  </label>
                </div>

                <div class="subsection-head">
                  <h3>目标</h3>
                  <button class="small-action" @click.prevent="addGoal(selectedPsyche)">＋ 添加目标</button>
                </div>
                <div class="goal-list">
                  <article v-for="(goal, index) in selectedPsyche.goals" :key="`${goal.goal_id}-${index}`">
                    <div class="card-grid">
                      <label>目标 ID<input v-model="goal.goal_id" class="mono" /></label>
                      <label>
                        优先级
                        <input v-model.number="goal.priority" type="number" min="0" max="1" step="0.1" />
                      </label>
                      <label class="wide">目标描述<textarea v-model="goal.description" rows="2"></textarea></label>
                      <label class="wide">
                        针对角色 ID
                        <input
                          :value="goal.target_ids.join('、')"
                          @input="goal.target_ids = splitList($event.target.value)"
                        />
                      </label>
                      <label class="check"><input v-model="goal.achieved" type="checkbox" /> 开局时已达成</label>
                    </div>
                    <button class="icon-danger" @click.prevent="removeGoal(selectedPsyche, index)">删除目标</button>
                  </article>
                </div>

                <div class="subsection-head">
                  <h3>计划</h3>
                  <button class="small-action" @click.prevent="addPlan(selectedPsyche)">＋ 添加计划</button>
                </div>
                <div class="goal-list">
                  <article v-for="(plan, index) in selectedPsyche.plans" :key="`${plan.plan_id}-${index}`">
                    <div class="card-grid">
                      <label>计划 ID<input v-model="plan.plan_id" class="mono" /></label>
                      <label>
                        服务目标
                        <select v-model="plan.goal_id">
                          <option v-for="goal in selectedPsyche.goals" :key="goal.goal_id" :value="goal.goal_id">{{ goal.goal_id }}</option>
                        </select>
                      </label>
                      <label>
                        当前步骤
                        <input v-model.number="plan.current_step" type="number" min="0" :max="plan.steps.length" />
                      </label>
                      <label>
                        状态
                        <select v-model="plan.status">
                          <option value="active">进行中</option>
                          <option value="paused">暂停</option>
                          <option value="completed">已完成</option>
                          <option value="abandoned">已放弃</option>
                        </select>
                      </label>
                      <label class="wide">
                        行动步骤（每行一步）
                        <textarea
                          :value="plan.steps.join('\n')"
                          rows="4"
                          @input="plan.steps = splitLines($event.target.value)"
                        ></textarea>
                      </label>
                    </div>
                    <button class="icon-danger" @click.prevent="selectedPsyche.plans.splice(index, 1)">删除计划</button>
                  </article>
                </div>
              </template>
              <div v-else class="configure-empty">
                <p>该角色尚未配置自主决策心理。</p>
                <button class="small-action" @click.prevent="enablePsyche(selectedPsycheId)">启用角色心理</button>
              </div>
            </div>
          </section>

          <section v-else-if="activeTab === 'beliefs'" class="panel split-editor">
            <div class="entity-list">
              <button
                v-for="character in characterList"
                :key="character.character_id"
                :class="{ active: selectedBeliefCharacterId === character.character_id }"
                @click.prevent="selectedBeliefCharacterId = character.character_id"
              >
                <strong>{{ character.display_name }}</strong>
                <span>{{ snapshot.beliefs[character.character_id]?.length || 0 }} 条认知</span>
              </button>
            </div>
            <div class="detail-form">
              <div class="section-head">
                <div>
                  <div class="section-title">{{ snapshot.characters[selectedBeliefCharacterId]?.display_name }} · 认知边界</div>
                  <p>同一事实可被不同角色相信、怀疑或完全不知道。</p>
                </div>
                <button class="small-action" @click.prevent="addBelief(selectedBeliefCharacterId)">＋ 添加认知</button>
              </div>

              <div v-if="selectedBeliefs.length" class="belief-list">
                <article v-for="(belief, index) in selectedBeliefs" :key="`${belief.fact_id}-${index}`">
                  <div class="belief-card-head">
                    <span :class="['belief-badge', belief.belief]">{{ belief.belief }}</span>
                    <span v-if="belief.source_type === 'secret'" class="secret-badge">秘密</span>
                    <button class="icon-danger" @click.prevent="removeBelief(selectedBeliefCharacterId, index)">删除</button>
                  </div>
                  <div class="card-grid">
                    <label class="wide">事实 ID<input v-model="belief.fact_id" class="mono" /></label>
                    <label>
                      认知判断
                      <select v-model="belief.belief">
                        <option value="believed_true">确信为真</option>
                        <option value="suspected_true">怀疑为真</option>
                        <option value="unknown">不知道</option>
                        <option value="suspected_false">怀疑为假</option>
                        <option value="believed_false">确信为假</option>
                      </select>
                    </label>
                    <label>
                      可信度（0–1）
                      <input v-model.number="belief.confidence" type="number" min="0" max="1" step="0.1" />
                    </label>
                    <label>
                      信息来源
                      <select v-model="belief.source_type">
                        <option value="unknown">未知</option>
                        <option value="observation">亲眼观察</option>
                        <option value="hearsay">听闻</option>
                        <option value="inference">推断</option>
                        <option value="secret">自身秘密</option>
                      </select>
                    </label>
                    <label>
                      来源事件 ID
                      <input v-model="belief.source_event_id" placeholder="可留空" />
                    </label>
                    <label class="wide">
                      中文关键词
                      <input
                        :value="belief.keywords.join('、')"
                        placeholder="用于检查对白是否泄漏未知事实"
                        @input="belief.keywords = splitList($event.target.value)"
                      />
                    </label>
                  </div>
                </article>
              </div>
              <div v-else class="configure-empty">
                <p>该角色尚未记录任何事实认知。</p>
                <button class="small-action" @click.prevent="addBelief(selectedBeliefCharacterId)">添加第一条认知</button>
              </div>
            </div>
          </section>

          <section v-else-if="activeTab === 'locations'" class="panel split-editor">
            <div class="entity-list">
              <button class="add-entity" @click.prevent="addLocation">＋ 新增地点</button>
              <button
                v-for="location in locationList"
                :key="location.location_id"
                :class="{ active: selectedLocationId === location.location_id }"
                @click.prevent="selectedLocationId = location.location_id"
              >
                <strong>{{ location.display_name }}</strong>
                <span>{{ location.location_id }}</span>
              </button>
            </div>
            <div v-if="selectedLocation" class="detail-form">
              <div class="section-head">
                <div><div class="section-title">地点档案</div><p>{{ selectedLocation.location_id }}</p></div>
                <button class="icon-danger" @click.prevent="removeLocation(selectedLocation.location_id)">删除地点</button>
              </div>
              <div class="form-grid">
                <label>显示名<input v-model="selectedLocation.display_name" /></label>
                <label>
                  父地点
                  <select v-model="selectedLocation.parent_id">
                    <option :value="null">无</option>
                    <option
                      v-for="location in locationList.filter((item) => item.location_id !== selectedLocation.location_id)"
                      :key="location.location_id"
                      :value="location.location_id"
                    >{{ location.display_name }}</option>
                  </select>
                </label>
                <label class="wide">
                  所需身份标签
                  <input
                    :value="selectedLocation.requires_permission.join('、')"
                    @input="selectedLocation.requires_permission = splitList($event.target.value)"
                  />
                </label>
                <label class="check"><input v-model="selectedLocation.accessible" type="checkbox" /> 开局可访问</label>
                <label class="wide">
                  地点扩展属性（JSON）
                  <textarea
                    :value="formatJson(selectedLocation.attrs)"
                    rows="8"
                    spellcheck="false"
                    @change="updateJsonObject(selectedLocation, 'attrs', $event.target.value, '地点属性')"
                  ></textarea>
                </label>
              </div>
            </div>
            <div v-else class="detail-form configure-empty">暂无地点，点击左侧新增。</div>
          </section>

          <section v-else-if="activeTab === 'items'" class="panel split-editor">
            <div class="entity-list">
              <button class="add-entity" @click.prevent="addItem">＋ 新增物品</button>
              <button
                v-for="item in itemList"
                :key="item.item_id"
                :class="{ active: selectedItemId === item.item_id }"
                @click.prevent="selectedItemId = item.item_id"
              >
                <strong>{{ item.display_name }}</strong>
                <span>{{ item.item_id }}</span>
              </button>
            </div>
            <div v-if="selectedItem" class="detail-form">
              <div class="section-head">
                <div><div class="section-title">物品档案</div><p>{{ selectedItem.item_id }}</p></div>
                <button class="icon-danger" @click.prevent="removeItem(selectedItem.item_id)">删除物品</button>
              </div>
              <div class="form-grid">
                <label>显示名<input v-model="selectedItem.display_name" /></label>
                <label>
                  数量
                  <input v-model.number="selectedItem.quantity" type="number" min="1" step="1" />
                </label>
                <label>
                  持有角色
                  <select v-model="selectedItem.owner_id" @change="selectedItem.owner_id && (selectedItem.location_id = null)">
                    <option :value="null">无</option>
                    <option v-for="character in characterList" :key="character.character_id" :value="character.character_id">{{ character.display_name }}</option>
                  </select>
                </label>
                <label>
                  放置地点
                  <select v-model="selectedItem.location_id" @change="selectedItem.location_id && (selectedItem.owner_id = null)">
                    <option :value="null">无</option>
                    <option v-for="location in locationList" :key="location.location_id" :value="location.location_id">{{ location.display_name }}</option>
                  </select>
                </label>
                <label class="check"><input v-model="selectedItem.unique" type="checkbox" /> 唯一物品</label>
                <label class="check"><input v-model="selectedItem.accessible" type="checkbox" /> 可被交互</label>
                <label class="wide">
                  物品扩展属性（JSON）
                  <textarea
                    :value="formatJson(selectedItem.attrs)"
                    rows="8"
                    spellcheck="false"
                    @change="updateJsonObject(selectedItem, 'attrs', $event.target.value, '物品属性')"
                  ></textarea>
                </label>
              </div>
            </div>
            <div v-else class="detail-form configure-empty">暂无物品，点击左侧新增。</div>
          </section>

          <section v-else-if="activeTab === 'relations'" class="panel">
            <div class="section-head">
              <div><h2>人物关系</h2><p>关系是有方向的；夜轻歌 → 夜清清与反向关系可不同。</p></div>
              <button class="small-action" @click.prevent="addRelation">＋ 添加关系</button>
            </div>
            <div class="relation-list">
              <article v-for="(relation, index) in snapshot.relations" :key="`${relation.source_id}-${relation.target_id}-${index}`">
                <div class="relation-route">
                  <select v-model="relation.source_id">
                    <option v-for="character in characterList" :key="character.character_id" :value="character.character_id">{{ character.display_name }}</option>
                  </select>
                  <span>→</span>
                  <select v-model="relation.target_id">
                    <option v-for="character in characterList" :key="character.character_id" :value="character.character_id">{{ character.display_name }}</option>
                  </select>
                  <button class="icon-danger" @click.prevent="snapshot.relations.splice(index, 1)">删除</button>
                </div>
                <div class="form-grid compact">
                  <label>公开关系<input v-model="relation.public_relation" /></label>
                  <label>私下关系<input v-model="relation.private_relation" /></label>
                </div>
                <div class="dimension-grid">
                  <label v-for="dim in ['affection','trust','fear','hostility','respect','debt']" :key="dim">
                    {{ dim }}
                    <input v-model.number="relation.dimensions[dim]" type="number" min="-1" max="1" step="0.1" />
                  </label>
                </div>
              </article>
            </div>
          </section>

          <section v-else-if="activeTab === 'rules'" class="panel">
            <div class="section-head">
              <div><h2>世界规则</h2><p>规则会进入推演和叙事一致性上下文。</p></div>
              <button class="small-action" @click.prevent="addWorldRule">＋ 添加规则</button>
            </div>
            <div class="rule-list">
              <article v-for="(rule, index) in snapshot.world_rules" :key="rule.rule_id">
                <input v-model="rule.rule_id" class="mono" />
                <select v-model="rule.category">
                  <option v-for="category in ['magic','death','identity','politics','time']" :key="category">{{ category }}</option>
                </select>
                <textarea v-model="rule.statement" rows="2"></textarea>
                <button class="icon-danger" @click.prevent="snapshot.world_rules.splice(index, 1)">删除</button>
              </article>
            </div>
          </section>

          <section v-else-if="activeTab === 'plot'" class="panel">
            <div class="section-head">
              <div><h2>剧情线与伏笔</h2><p>定义新世界线开局时正在推进的主线、支线与伏笔。</p></div>
              <button class="small-action" @click.prevent="addPlotArc">＋ 添加剧情线</button>
            </div>
            <div class="plot-grid">
              <article v-for="[plotId, arc] in plotEntries" :key="plotId">
                <div class="plot-id">{{ plotId }}</div>
                <label>标题<input v-model="arc.title" /></label>
                <label>类型
                  <select v-model="arc.kind">
                    <option value="main">主线</option>
                    <option value="side">支线</option>
                    <option value="foreshadow">伏笔</option>
                  </select>
                </label>
                <label>阶段<input v-model="arc.stage" /></label>
                <label class="check"><input v-model="arc.completed" type="checkbox" /> 已完成</label>
                <button class="icon-danger" @click.prevent="delete snapshot.plot[plotId]">删除</button>
              </article>
            </div>
          </section>

          <section v-else class="panel raw-panel">
            <div class="section-head">
              <div><h2>高级世界状态 JSON</h2><p>用于编辑地点、物品、认知、角色心理和确定性规则等全部字段。</p></div>
              <button class="small-action" @click.prevent="applyRawSnapshot">应用 JSON</button>
            </div>
            <textarea v-model="rawSnapshot" spellcheck="false"></textarea>
          </section>
        </fieldset>
      </main>

      <main v-else class="workspace empty-state">
        <div>{{ loading ? '正在载入世界包…' : '暂无可用世界包' }}</div>
      </main>
    </div>
  </div>
</template>

<style scoped>
.studio {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(circle at 80% -20%, rgba(201, 169, 106, 0.12), transparent 38%),
    var(--bg);
}
.studio-bar {
  min-height: 66px;
  padding: 10px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  background: rgba(36, 30, 24, 0.96);
  border-bottom: 1px solid var(--border);
}
.brand, .toolbar, .brand > div {
  display: flex;
  align-items: center;
}
.brand {
  gap: 15px;
}
.brand > div {
  align-items: flex-start;
  flex-direction: column;
  line-height: 1.25;
}
.brand strong {
  color: var(--accent);
  font-size: 18px;
  letter-spacing: 2px;
}
.brand span {
  color: var(--text-faint);
  font: 10px/1.4 ui-monospace, monospace;
  letter-spacing: 1.5px;
  text-transform: uppercase;
}
.toolbar {
  gap: 8px;
}
.studio button {
  padding: 7px 13px;
  font-size: 13px;
}
.back-button, .ghost, .small-action {
  color: var(--text-dim);
  background: var(--bg-card);
  border: 1px solid var(--border);
}
.primary {
  color: #211a12;
  background: var(--accent);
}
.play {
  color: #f0eadf;
  background: #536b42;
  border: 1px solid #718b5c;
}
.studio-body {
  min-height: 0;
  flex: 1;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
}
.package-rail {
  padding: 18px 14px;
  overflow-y: auto;
  background: rgba(30, 25, 20, 0.92);
  border-right: 1px solid var(--border);
}
.rail-title {
  margin: 0 7px 12px;
  display: flex;
  justify-content: space-between;
  color: var(--text-dim);
  font-size: 13px;
  letter-spacing: 2px;
}
.rail-title small {
  padding: 0 7px;
  border: 1px solid var(--border);
  border-radius: 10px;
}
.package-card {
  width: 100%;
  margin-bottom: 9px;
  padding: 13px;
  display: flex;
  align-items: flex-start;
  flex-direction: column;
  text-align: left;
  color: var(--text);
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
}
.package-card.active {
  border-color: var(--accent-dim);
  box-shadow: inset 3px 0 var(--accent);
}
.package-card strong {
  margin-top: 6px;
}
.package-card > span:not(.source-mark) {
  color: var(--text-dim);
  font-size: 12px;
}
.package-card small {
  margin-top: 7px;
  color: var(--text-faint);
  font: 10px ui-monospace, monospace;
}
.source-mark {
  padding: 1px 6px;
  border-radius: 8px;
  color: var(--accent);
  background: rgba(201, 169, 106, 0.1);
  font-size: 10px;
}
.source-mark.custom {
  color: var(--system);
  background: rgba(138, 168, 107, 0.1);
}
.rail-hint {
  margin: 16px 7px;
  color: var(--text-faint);
  font-size: 12px;
  line-height: 1.6;
}
.workspace {
  padding: 24px 30px 50px;
  overflow-y: auto;
}
.package-heading {
  display: flex;
  justify-content: space-between;
  gap: 20px;
}
.eyebrow, .revision {
  color: var(--accent-dim);
  font: 11px ui-monospace, monospace;
  letter-spacing: 1px;
}
.package-heading h1 {
  margin-top: 4px;
  color: var(--text);
  font-size: 28px;
  line-height: 1.3;
}
.package-heading p {
  margin-top: 5px;
  color: var(--text-dim);
}
.revision {
  height: fit-content;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 3px;
}
.notice {
  margin-top: 18px;
  padding: 10px 14px;
  border-radius: 4px;
  font-size: 13px;
}
.notice.success {
  color: var(--system);
  background: rgba(138, 168, 107, 0.1);
  border: 1px solid rgba(138, 168, 107, 0.35);
}
.notice.error {
  color: #df8b8b;
  background: rgba(201, 90, 90, 0.1);
  border: 1px solid rgba(201, 90, 90, 0.35);
}
.notice ul {
  margin: 5px 0 0 20px;
}
.tabs {
  margin-top: 22px;
  display: flex;
  overflow-x: auto;
  gap: 3px;
  border-bottom: 1px solid var(--border);
}
.tabs button {
  flex-shrink: 0;
  padding: 9px 16px;
  color: var(--text-faint);
  background: transparent;
  border-bottom: 2px solid transparent;
  border-radius: 0;
}
.tabs button.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.editor-fieldset {
  min-width: 0;
  border: 0;
}
.editor-fieldset:disabled {
  opacity: 0.72;
}
.panel {
  margin-top: 20px;
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(90px, 1fr));
  gap: 10px;
}
.stat-grid article {
  padding: 15px 18px;
  background: linear-gradient(145deg, var(--bg-card), rgba(44, 36, 28, 0.55));
  border: 1px solid var(--border-soft);
}
.stat-grid strong, .stat-grid span {
  display: block;
}
.stat-grid strong {
  color: var(--accent);
  font: 24px ui-monospace, monospace;
}
.stat-grid span {
  color: var(--text-faint);
  font-size: 12px;
}
.form-grid {
  margin-top: 18px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.form-grid.compact {
  margin-top: 10px;
}
.form-grid label, .detail-form label, .plot-grid label {
  color: var(--text-dim);
  font-size: 12px;
}
.form-grid .wide {
  grid-column: 1 / -1;
}
.studio input, .studio select, .studio textarea {
  width: 100%;
  margin-top: 5px;
  padding: 9px 10px;
  color: var(--text);
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 3px;
  outline: none;
  font: inherit;
}
.studio input:focus, .studio select:focus, .studio textarea:focus {
  border-color: var(--accent-dim);
}
.studio textarea {
  resize: vertical;
  line-height: 1.55;
}
.split-editor {
  min-height: 470px;
  display: grid;
  grid-template-columns: 220px minmax(0, 1fr);
  background: var(--bg-panel);
  border: 1px solid var(--border-soft);
}
.entity-list {
  padding: 10px;
  border-right: 1px solid var(--border-soft);
}
.entity-list button {
  width: 100%;
  margin-bottom: 5px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  color: var(--text-dim);
  background: transparent;
}
.entity-list button.active {
  color: var(--accent);
  background: var(--bg-card);
}
.entity-list .add-entity {
  margin-bottom: 12px;
  color: var(--accent);
  background: rgba(201, 169, 106, 0.08);
  border: 1px dashed var(--accent-dim);
}
.entity-list span {
  color: var(--text-faint);
  font: 10px ui-monospace, monospace;
}
.detail-form {
  padding: 22px;
}
.section-title, .section-head h2 {
  color: var(--accent);
  font-size: 17px;
}
.section-head p {
  color: var(--text-faint);
  font-size: 12px;
}
.subsection-head {
  margin-top: 26px;
  padding-top: 16px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-top: 1px solid var(--border-soft);
}
.subsection-head h3 {
  color: var(--text-dim);
  font-size: 15px;
}
.goal-list article {
  position: relative;
  margin-top: 10px;
  padding: 14px 60px 14px 14px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
}
.belief-list article {
  margin-top: 12px;
  padding: 14px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
}
.belief-card-head {
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 7px;
}
.belief-card-head .icon-danger {
  margin-left: auto;
}
.belief-badge, .secret-badge {
  padding: 2px 8px;
  border-radius: 10px;
  color: var(--text-dim);
  background: var(--bg-input);
  border: 1px solid var(--border);
  font: 10px ui-monospace, monospace;
}
.belief-badge.believed_true {
  color: var(--system);
  border-color: rgba(138, 168, 107, 0.45);
}
.belief-badge.believed_false {
  color: var(--danger);
  border-color: rgba(201, 90, 90, 0.45);
}
.belief-badge.suspected_true, .belief-badge.suspected_false {
  color: var(--warn);
  border-color: rgba(201, 164, 90, 0.45);
}
.secret-badge {
  color: #c78fc9;
  border-color: rgba(199, 143, 201, 0.45);
}
.goal-list article > .icon-danger {
  position: absolute;
  top: 9px;
  right: 7px;
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
.card-grid label {
  color: var(--text-dim);
  font-size: 12px;
}
.card-grid .wide {
  grid-column: 1 / -1;
}
.configure-empty {
  min-height: 260px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 12px;
  color: var(--text-faint);
}
.check {
  display: flex;
  align-items: center;
  gap: 8px;
}
.check input {
  width: auto;
  margin: 0;
}
.section-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
}
.relation-list article, .rule-list article, .plot-grid article {
  margin-top: 12px;
  padding: 15px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
}
.relation-route {
  display: grid;
  grid-template-columns: minmax(120px, 1fr) 28px minmax(120px, 1fr) auto;
  align-items: center;
  gap: 8px;
}
.relation-route span {
  text-align: center;
  color: var(--accent);
}
.dimension-grid {
  margin-top: 12px;
  display: grid;
  grid-template-columns: repeat(6, minmax(70px, 1fr));
  gap: 8px;
}
.dimension-grid label {
  color: var(--text-faint);
  font: 10px ui-monospace, monospace;
}
.icon-danger {
  padding: 5px 8px !important;
  color: var(--danger);
  background: transparent;
}
.rule-list article {
  display: grid;
  grid-template-columns: 180px 120px minmax(240px, 1fr) auto;
  align-items: start;
  gap: 9px;
}
.mono, .raw-panel textarea, .plot-id {
  font-family: ui-monospace, "Cascadia Code", monospace !important;
}
.plot-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.plot-id {
  margin-bottom: 9px;
  color: var(--accent-dim);
  font-size: 11px;
}
.raw-panel textarea {
  min-height: 560px;
  font-size: 12px;
  white-space: pre;
}
.empty-state {
  display: grid;
  place-items: center;
  color: var(--text-faint);
}
@media (max-width: 980px) {
  .studio-bar {
    align-items: flex-start;
    flex-direction: column;
  }
  .studio-body {
    grid-template-columns: 210px minmax(0, 1fr);
  }
  .workspace {
    padding: 20px;
  }
  .stat-grid, .dimension-grid {
    grid-template-columns: repeat(3, 1fr);
  }
  .card-grid {
    grid-template-columns: 1fr;
  }
  .card-grid .wide {
    grid-column: auto;
  }
  .rule-list article, .plot-grid {
    grid-template-columns: 1fr;
  }
}
</style>
