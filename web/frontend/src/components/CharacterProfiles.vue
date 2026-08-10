<script setup>
import { computed } from 'vue'

const props = defineProps({
  state: { type: Object, default: null },
  defaultActor: { type: String, default: '' },
})

const characters = computed(() => {
  const currentScene = props.state?.current_scene_id
  return Object.values(props.state?.characters || {}).sort((a, b) => {
    if (a.character_id === props.defaultActor) return -1
    if (b.character_id === props.defaultActor) return 1
    if (a.location_id === currentScene && b.location_id !== currentScene) return -1
    if (b.location_id === currentScene && a.location_id !== currentScene) return 1
    return a.display_name.localeCompare(b.display_name, 'zh-CN')
  })
})

function locationName(id) {
  return props.state?.locations?.[id]?.display_name || id || '未知地点'
}

function psycheOf(id) {
  return props.state?.character_psyches?.[id] || null
}

function mainGoal(id) {
  const goals = psycheOf(id)?.goals || []
  return (goals.find((goal) => !goal.achieved) || goals[0])?.description || '等待下一步行动'
}

function initials(name) {
  return [...(name || '?')].slice(-2).join('')
}
</script>

<template>
  <div class="profiles">
    <article
      v-for="character in characters"
      :key="character.character_id"
      class="profile-card"
      :class="{
        player: character.character_id === defaultActor,
        present: character.location_id === state?.current_scene_id,
        dead: !character.is_alive,
      }"
    >
      <div class="avatar">{{ initials(character.display_name) }}</div>
      <div class="profile-body">
        <div class="profile-title">
          <strong>{{ character.display_name }}</strong>
          <span v-if="character.character_id === defaultActor" class="role-chip">玩家</span>
          <span v-else-if="character.location_id === state?.current_scene_id" class="role-chip present-chip">在场</span>
        </div>
        <div class="identity">{{ (character.identity_tags || []).slice(0, 3).join(' · ') || character.character_id }}</div>
        <div class="profile-facts">
          <span>⌖ {{ locationName(character.location_id) }}</span>
          <span v-if="psycheOf(character.character_id)?.emotion">
            ◉ {{ psycheOf(character.character_id).emotion }}
          </span>
          <span>◎ {{ mainGoal(character.character_id) }}</span>
        </div>
      </div>
    </article>
    <div v-if="!characters.length" class="profiles-empty">正在载入角色档案…</div>
  </div>
</template>

<style scoped>
.profiles {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 2px 10px 12px;
}
.profile-card {
  display: flex;
  gap: 10px;
  margin-bottom: 8px;
  padding: 10px;
  border: 1px solid var(--border-soft);
  border-left: 3px solid transparent;
  border-radius: 8px;
  background: rgba(44, 36, 28, 0.72);
}
.profile-card.present { border-left-color: var(--npc); }
.profile-card.player { border-left-color: var(--player); background: rgba(70, 82, 94, 0.18); }
.profile-card.dead { opacity: 0.5; }
.avatar {
  display: grid;
  width: 38px;
  height: 38px;
  flex: 0 0 38px;
  place-items: center;
  border: 1px solid var(--border);
  border-radius: 50%;
  background: linear-gradient(145deg, #4a3d2e, #241e18);
  color: var(--accent);
  font-size: 11px;
}
.profile-body { min-width: 0; flex: 1; }
.profile-title { display: flex; align-items: center; gap: 6px; line-height: 1.2; }
.profile-title strong { color: var(--text); font-size: 13px; }
.role-chip {
  padding: 1px 5px;
  border-radius: 3px;
  background: rgba(122, 162, 201, 0.15);
  color: var(--player);
  font-size: 9px;
}
.present-chip { background: rgba(201, 122, 122, 0.12); color: var(--npc); }
.identity {
  margin-top: 3px;
  overflow: hidden;
  color: var(--text-faint);
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.profile-facts { display: grid; gap: 1px; margin-top: 6px; }
.profile-facts span {
  overflow: hidden;
  color: var(--text-dim);
  font-size: 10px;
  line-height: 1.5;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.profiles-empty { padding: 45px 10px; color: var(--text-faint); text-align: center; font-size: 12px; }
</style>
