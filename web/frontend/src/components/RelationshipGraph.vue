<script setup>
import { computed } from 'vue'

const props = defineProps({
  snapshot: {
    type: Object,
    required: true,
  },
  defaultActorId: {
    type: String,
    default: '',
  },
})

const width = 820
const height = 420
const centerX = width / 2
const centerY = height / 2
const radius = 155

const nodes = computed(() => {
  const characters = Object.values(props.snapshot?.characters || {})
  return characters.map((character, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(1, characters.length) - Math.PI / 2
    return {
      ...character,
      x: centerX + Math.cos(angle) * radius,
      y: centerY + Math.sin(angle) * radius,
    }
  })
})

const nodeMap = computed(
  () => Object.fromEntries(nodes.value.map((node) => [node.character_id, node])),
)

const edges = computed(() => (
  (props.snapshot?.relations || [])
    .map((relation) => ({
      ...relation,
      source: nodeMap.value[relation.source_id],
      target: nodeMap.value[relation.target_id],
    }))
    .filter((edge) => edge.source && edge.target)
))

function edgeColor(edge) {
  const dimensions = edge.dimensions || {}
  if ((dimensions.hostility || 0) >= 0.5) return '#c95a5a'
  if ((dimensions.trust || 0) >= 0.4 || (dimensions.affection || 0) >= 0.4) return '#8aa86b'
  if ((dimensions.fear || 0) >= 0.4) return '#c9a45a'
  return '#8f8173'
}
</script>

<template>
  <div class="graph-shell">
    <svg
      class="relationship-graph"
      :viewBox="`0 0 ${width} ${height}`"
      role="img"
      aria-label="人物关系图"
    >
      <defs>
        <marker id="relation-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="context-stroke" />
        </marker>
      </defs>
      <g v-for="(edge, index) in edges" :key="`${edge.source_id}-${edge.target_id}-${index}`">
        <line
          :x1="edge.source.x"
          :y1="edge.source.y"
          :x2="edge.target.x"
          :y2="edge.target.y"
          :stroke="edgeColor(edge)"
          stroke-width="2"
          stroke-opacity="0.82"
          marker-end="url(#relation-arrow)"
        />
        <text
          :x="(edge.source.x + edge.target.x) / 2"
          :y="(edge.source.y + edge.target.y) / 2 - 7"
          text-anchor="middle"
          class="edge-label"
        >{{ edge.public_relation || edge.private_relation || '关系' }}</text>
      </g>
      <g
        v-for="node in nodes"
        :key="node.character_id"
        :transform="`translate(${node.x}, ${node.y})`"
      >
        <circle
          r="34"
          :class="{ player: node.character_id === defaultActorId }"
        />
        <text text-anchor="middle" dy="4" class="node-label">{{ node.display_name }}</text>
        <text text-anchor="middle" dy="54" class="node-id">{{ node.character_id }}</text>
        <title>{{ node.display_name }} · {{ node.identity_tags?.join('、') }}</title>
      </g>
    </svg>
    <div v-if="!nodes.length" class="graph-empty">暂无角色可绘制</div>
    <div class="graph-legend">
      <span><i class="friendly"></i>信任/亲近</span>
      <span><i class="hostile"></i>敌意</span>
      <span><i class="fear"></i>畏惧</span>
      <span><i class="neutral"></i>其他</span>
    </div>
  </div>
</template>

<style scoped>
.graph-shell {
  position: relative;
  margin-bottom: 18px;
  padding: 10px;
  overflow: hidden;
  background: radial-gradient(circle at center, rgba(201, 169, 106, 0.08), transparent 60%);
  border: 1px solid var(--border-soft);
}
.relationship-graph {
  display: block;
  width: 100%;
  min-height: 320px;
}
circle {
  fill: #3d342b;
  stroke: #8f8173;
  stroke-width: 2;
}
circle.player {
  fill: #51432f;
  stroke: var(--accent);
  stroke-width: 3;
}
.node-label {
  fill: var(--text);
  font-size: 13px;
}
.node-id, .edge-label {
  fill: var(--text-faint);
  font: 9px ui-monospace, monospace;
  paint-order: stroke;
  stroke: var(--bg);
  stroke-width: 3px;
}
.edge-label {
  font-size: 10px;
}
.graph-empty {
  padding: 60px;
  text-align: center;
  color: var(--text-faint);
}
.graph-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  color: var(--text-faint);
  font-size: 11px;
}
.graph-legend span {
  display: flex;
  align-items: center;
  gap: 5px;
}
.graph-legend i {
  width: 18px;
  height: 2px;
  background: #8f8173;
}
.graph-legend .friendly { background: #8aa86b; }
.graph-legend .hostile { background: #c95a5a; }
.graph-legend .fear { background: #c9a45a; }
.graph-legend .neutral { background: #8f8173; }
</style>
