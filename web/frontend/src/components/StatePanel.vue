<script setup>
import { computed } from 'vue'

const props = defineProps({
  state: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
})

// 在场角色 = location_id 等于当前场景的角色 (优先)，否则全部列出
const sceneChars = computed(() => {
  if (!props.state) return []
  const scene = props.state.current_scene_id
  const all = Object.values(props.state.characters || {})
  if (scene) {
    const inScene = all.filter(c => c.location_id === scene)
    return inScene.length ? inScene : all
  }
  return all
})

// 场景显示名
const sceneName = computed(() => {
  const id = props.state?.current_scene_id
  if (!id) return '—'
  return props.state.locations?.[id]?.display_name || id
})

// 关系列表 (源 -> 目标)
const relations = computed(() => {
  return props.state?.relations || []
})

// 拿角色的 psyche (若有)
function psycheOf(charId) {
  return props.state?.character_psyches?.[charId] || null
}

// 拿角色名 (关系里是 id)
function charName(id) {
  return props.state?.characters?.[id]?.display_name || id
}

// 关系维度条：红条 (hostility/fear) 与绿条 (affection/trust/respect)
// 值域：hostility/fear ∈ [0,1]；其余 ∈ [-1,1]
const POS_DIMS = ['affection', 'trust', 'respect', 'debt']
const NEG_DIMS = ['hostility', 'fear']
const DIM_CN = {
  affection: '好感', trust: '信任', fear: '恐惧',
  hostility: '敌意', respect: '敬意', debt: '恩怨',
}

function dimEntries(dims) {
  if (!dims) return []
  const out = []
  for (const k of POS_DIMS.concat(NEG_DIMS)) {
    const v = dims[k]
    if (v !== undefined && v !== null && v !== 0) {
      out.push({ key: k, cn: DIM_CN[k] || k, value: v, positive: POS_DIMS.includes(k) })
    }
  }
  return out
}

// 进度条宽度 (0-100%)
function barWidth(entry) {
  // 正向维度 [−1,1] -> 绝对值*50% (从中线向左/右)
  // 负向维度 [0,1] -> *100%
  if (entry.positive) {
    return Math.min(50, Math.abs(entry.value) * 50)
  }
  return Math.min(100, entry.value * 100)
}

// 计划当前步骤文本
function currentStep(psyche) {
  if (!psyche?.plans?.length) return ''
  const plan = psyche.plans.find(p => p.status === 'active') || psyche.plans[0]
  if (!plan.steps?.length) return ''
  const idx = Math.min(plan.current_step, plan.steps.length - 1)
  return plan.steps[idx]
}

// 主要目标
function mainGoal(psyche) {
  if (!psyche?.goals?.length) return ''
  const g = psyche.goals.find(x => !x.achieved)
  return (g || psyche.goals[0]).description
}

// 身份标签
function tags(char) {
  return (char.identity_tags || []).join(' · ')
}
</script>

<template>
  <div class="panel">
    <template v-if="state">
      <!-- 世界概览 -->
      <div class="section">
        <div class="sec-title">世界</div>
        <div class="kv"><span class="k">时间</span><span class="v">{{ state.world_time || '—' }}</span></div>
        <div class="kv"><span class="k">场景</span><span class="v">{{ sceneName }}</span></div>
      </div>

      <!-- 在场角色 -->
      <div class="section">
        <div class="sec-title">在场角色</div>
        <div v-for="c in sceneChars" :key="c.character_id" class="char-block" :class="{ dead: !c.is_alive, 'is-player': c.character_id === defaultActor }">
          <div class="char-head">
            <span class="char-name">{{ c.display_name }}</span>
            <span v-if="c.character_id === defaultActor" class="tag tag-player">你</span>
            <span v-if="!c.is_alive" class="tag tag-dead">亡</span>
          </div>
          <div v-if="tags(c)" class="char-tags">{{ tags(c) }}</div>

          <!-- NPC 内在状态 (有 psyche 的) -->
          <div v-if="psycheOf(c.character_id)" class="psyche">
            <div v-if="psycheOf(c.character_id).emotion" class="psyche-row">
              <span class="psyche-k">情绪</span>
              <span class="psyche-v">{{ psycheOf(c.character_id).emotion }}
                <span class="dim-num">({{ (psycheOf(c.character_id).emotion_intensity ?? 0).toFixed(2) }})</span>
              </span>
            </div>
            <div v-if="mainGoal(psycheOf(c.character_id))" class="psyche-row">
              <span class="psyche-k">目标</span>
              <span class="psyche-v">{{ mainGoal(psycheOf(c.character_id)) }}</span>
            </div>
            <div v-if="currentStep(psycheOf(c.character_id))" class="psyche-row">
              <span class="psyche-k">计划</span>
              <span class="psyche-v">{{ currentStep(psycheOf(c.character_id)) }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 关系 -->
      <div class="section">
        <div class="sec-title">关系</div>
        <div v-for="(r, i) in relations" :key="i" class="rel-block">
          <div class="rel-head">
            <span>{{ charName(r.source_id) }}</span>
            <span class="rel-arrow">→</span>
            <span>{{ charName(r.target_id) }}</span>
            <span v-if="r.public_relation" class="rel-pub">{{ r.public_relation }}</span>
          </div>
          <div v-for="e in dimEntries(r.dimensions)" :key="e.key" class="dim-bar">
            <span class="dim-label">{{ e.cn }}</span>
            <div class="bar-track" :class="{ 'track-pos': e.positive, 'track-neg': !e.positive }">
              <div class="bar-fill" :class="{ 'fill-pos': e.positive && e.value >= 0, 'fill-neg': e.positive && e.value < 0, 'fill-red': !e.positive }"
                   :style="{ width: barWidth(e) + '%' }"></div>
            </div>
            <span class="dim-num">{{ e.value.toFixed(2) }}</span>
          </div>
        </div>
        <div v-if="!relations.length" class="empty-mini">无关系数据</div>
      </div>
    </template>

    <div v-else class="panel-empty">加载世界中…</div>
  </div>
</template>

<style scoped>
.panel {
  padding: 16px 18px;
}
.section {
  margin-bottom: 22px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-soft);
}
.section:last-child { border-bottom: none; }
.sec-title {
  color: var(--accent);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 1px;
  margin-bottom: 10px;
  padding-bottom: 4px;
  border-bottom: 1px dashed var(--border-soft);
}
.kv {
  display: flex;
  font-size: 14px;
  margin: 4px 0;
}
.k { color: var(--text-faint); width: 40px; flex-shrink: 0; }
.v { color: var(--text-dim); }

/* 角色 */
.char-block {
  padding: 8px 10px;
  margin-bottom: 8px;
  background: var(--bg-card);
  border-radius: 4px;
  border-left: 3px solid transparent;
}
.char-block.is-player { border-left-color: var(--player); }
.char-block.dead { opacity: 0.5; }
.char-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.char-name {
  font-weight: 600;
  color: var(--text);
}
.char-tags {
  color: var(--text-faint);
  font-size: 12px;
  margin-top: 2px;
}
.tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 3px;
}
.tag-player { background: var(--player); color: #1a1612; }
.tag-dead { background: var(--danger); color: #fff; }

/* psyche */
.psyche {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px dashed var(--border-soft);
}
.psyche-row {
  display: flex;
  font-size: 13px;
  margin: 3px 0;
}
.psyche-k { color: var(--accent-dim); width: 36px; flex-shrink: 0; }
.psyche-v { color: var(--text-dim); }
.dim-num { color: var(--text-faint); font-size: 12px; margin-left: 4px; }

/* 关系 */
.rel-block {
  margin-bottom: 12px;
  padding: 8px 10px;
  background: var(--bg-card);
  border-radius: 4px;
}
.rel-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  margin-bottom: 6px;
  color: var(--text);
}
.rel-arrow { color: var(--text-faint); }
.rel-pub {
  margin-left: 6px;
  font-size: 12px;
  color: var(--accent-dim);
  border: 1px solid var(--border-soft);
  padding: 0 5px;
  border-radius: 3px;
}

/* 维度条 */
.dim-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 3px 0;
  font-size: 12px;
}
.dim-label { width: 32px; color: var(--text-faint); flex-shrink: 0; }
.bar-track {
  flex: 1;
  height: 6px;
  background: var(--bg-input);
  border-radius: 3px;
  overflow: hidden;
  position: relative;
}
/* 正向维度：中线在中间，可左可右 */
.track-pos {
  background: linear-gradient(to right, var(--bg-input) 50%, var(--bg-input) 50%);
}
.track-neg { /* 负向维度：从左填满 */ }
.bar-fill {
  height: 100%;
  border-radius: 3px;
}
.fill-pos { background: var(--system); }      /* 绿 (好感类正向) */
.fill-neg { background: var(--danger); }      /* 红 (好感类负向) */
.fill-red { background: var(--npc); }         /* 红橙 (敌意/恐惧) */
.dim-num { width: 36px; text-align: right; color: var(--text-faint); }

.empty-mini, .panel-empty {
  color: var(--text-faint);
  font-size: 13px;
  text-align: center;
  padding: 10px;
}
</style>
