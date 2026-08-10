<script setup>
import { computed } from 'vue'

const props = defineProps({
  state: { type: Object, default: null },
})

const width = 520
const rowHeight = 112

const locations = computed(() => Object.values(props.state?.locations || {}))

function depthOf(location, byId, visiting = new Set()) {
  if (!location?.parent_id || !byId[location.parent_id]) return 0
  if (visiting.has(location.location_id)) return 0
  const next = new Set(visiting)
  next.add(location.location_id)
  return 1 + depthOf(byId[location.parent_id], byId, next)
}

const graph = computed(() => {
  const byId = Object.fromEntries(locations.value.map((item) => [item.location_id, item]))
  const levels = new Map()
  locations.value.forEach((location) => {
    const depth = depthOf(location, byId)
    if (!levels.has(depth)) levels.set(depth, [])
    levels.get(depth).push(location)
  })

  const nodes = []
  const sortedLevels = [...levels.entries()].sort(([a], [b]) => a - b)
  sortedLevels.forEach(([depth, entries]) => {
    entries.sort((a, b) => a.display_name.localeCompare(b.display_name, 'zh-CN'))
    entries.forEach((location, index) => {
      const spacing = width / (entries.length + 1)
      nodes.push({
        ...location,
        x: spacing * (index + 1),
        y: 58 + depth * rowHeight,
      })
    })
  })
  const nodeMap = Object.fromEntries(nodes.map((node) => [node.location_id, node]))
  const edges = nodes
    .filter((node) => node.parent_id && nodeMap[node.parent_id])
    .map((node) => ({ source: nodeMap[node.parent_id], target: node }))
  const maxDepth = nodes.reduce((max, node) => Math.max(max, depthOf(node, byId)), 0)
  return { nodes, edges, height: Math.max(220, 116 + maxDepth * rowHeight) }
})

function charactersAt(locationId) {
  return Object.values(props.state?.characters || {})
    .filter((character) => character.location_id === locationId)
}
</script>

<template>
  <div class="map-wrap">
    <svg
      v-if="graph.nodes.length"
      class="world-map"
      :viewBox="`0 0 ${width} ${graph.height}`"
      role="img"
      aria-label="世界地点关系图"
    >
      <defs>
        <linearGradient id="map-node-fill" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="#3b3025" />
          <stop offset="1" stop-color="#241e18" />
        </linearGradient>
      </defs>
      <path
        v-for="edge in graph.edges"
        :key="`${edge.source.location_id}-${edge.target.location_id}`"
        :d="`M ${edge.source.x} ${edge.source.y + 25} C ${edge.source.x} ${edge.source.y + 62}, ${edge.target.x} ${edge.target.y - 62}, ${edge.target.x} ${edge.target.y - 25}`"
        class="map-edge"
      />
      <g
        v-for="node in graph.nodes"
        :key="node.location_id"
        :transform="`translate(${node.x}, ${node.y})`"
        class="map-node"
        :class="{ current: node.location_id === state?.current_scene_id, locked: !node.accessible }"
      >
        <rect x="-72" y="-25" width="144" height="50" rx="12" />
        <text class="location-name" text-anchor="middle" y="-2">{{ node.display_name }}</text>
        <text class="location-meta" text-anchor="middle" y="14">
          {{ charactersAt(node.location_id).length }} 位角色{{ node.accessible ? '' : ' · 未开放' }}
        </text>
        <circle
          v-for="(character, index) in charactersAt(node.location_id).slice(0, 5)"
          :key="character.character_id"
          :cx="-((Math.min(charactersAt(node.location_id).length, 5) - 1) * 7) + index * 14"
          cy="32"
          r="5"
          :class="{ alive: character.is_alive }"
        >
          <title>{{ character.display_name }}</title>
        </circle>
      </g>
    </svg>
    <div v-else class="map-empty">正在读取世界地点…</div>
    <div class="map-legend">
      <span><i class="current-dot"></i>当前场景</span>
      <span><i class="agent-dot"></i>角色位置</span>
    </div>
  </div>
</template>

<style scoped>
.map-wrap {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 8px 8px;
  background:
    linear-gradient(rgba(201, 169, 106, 0.025) 1px, transparent 1px),
    linear-gradient(90deg, rgba(201, 169, 106, 0.025) 1px, transparent 1px);
  background-size: 24px 24px;
}
.world-map { display: block; width: 100%; min-height: 190px; }
.map-edge {
  fill: none;
  stroke: var(--border);
  stroke-width: 2;
  stroke-dasharray: 5 5;
}
.map-node rect {
  fill: url(#map-node-fill);
  stroke: var(--border);
  stroke-width: 1.5;
}
.map-node.current rect {
  stroke: var(--accent);
  stroke-width: 2.5;
  filter: drop-shadow(0 0 7px rgba(201, 169, 106, 0.28));
}
.map-node.locked { opacity: 0.52; }
.location-name { fill: var(--text); font-size: 14px; font-weight: 600; }
.location-meta { fill: var(--text-faint); font-size: 9px; }
.map-node circle { fill: var(--danger); stroke: var(--bg); stroke-width: 2; }
.map-node circle.alive { fill: var(--player); }
.map-empty { padding: 65px 20px; color: var(--text-faint); text-align: center; font-size: 12px; }
.map-legend {
  display: flex;
  justify-content: center;
  gap: 16px;
  color: var(--text-faint);
  font-size: 10px;
}
.map-legend span { display: flex; align-items: center; gap: 5px; }
.map-legend i { width: 7px; height: 7px; border-radius: 50%; }
.current-dot { border: 2px solid var(--accent); }
.agent-dot { background: var(--player); }
</style>
