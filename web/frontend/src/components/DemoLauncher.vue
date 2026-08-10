<script setup>
defineProps({
  open: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['close', 'run'])

const cases = [
  {
    id: 'invalid_airplane',
    index: '01',
    tag: '规则门禁',
    title: '非法行动：开飞机',
    description: '展示不存在的现代概念如何被确定性拒绝，且世界版本保持不变。',
    evidence: 'REJECTED · WORLD_CONCEPT_UNAVAILABLE · v0',
    tone: 'danger',
  },
  {
    id: 'valid_intervention',
    index: '02',
    tag: '权威提交',
    title: '合法行动：销毁密信',
    description: '玩家通过受控工具取得并销毁密信，事件与状态在同一链路提交。',
    evidence: 'pick_up → destroy_item · 2 events',
    tone: 'accepted',
  },
  {
    id: 'multi_agent',
    index: '03',
    tag: '多智能体',
    title: '自主行为：传播与结盟',
    description: '无玩家干预，NPC 基于有限认知逐跳传播证据并形成联盟。',
    evidence: 'belief evidence · propagation · alliance',
    tone: 'agent',
  },
]
</script>

<template>
  <div v-if="open" class="demo-overlay" @click.self="emit('close')">
    <section class="demo-dialog" role="dialog" aria-modal="true" aria-labelledby="demo-title">
      <header>
        <div>
          <span class="eyebrow">RECRUITER SHOWCASE</span>
          <h2 id="demo-title">选择一条一键演示</h2>
          <p>全部使用确定性本地链路，无需 API Key，不会产生模型费用。</p>
        </div>
        <button class="close-btn" aria-label="关闭演示选择" @click="emit('close')">×</button>
      </header>

      <div class="demo-grid">
        <article v-for="item in cases" :key="item.id" class="demo-card" :class="item.tone">
          <div class="card-top">
            <span class="case-index">{{ item.index }}</span>
            <span class="case-tag">{{ item.tag }}</span>
          </div>
          <h3>{{ item.title }}</h3>
          <p>{{ item.description }}</p>
          <code>{{ item.evidence }}</code>
          <button :disabled="loading" @click="emit('run', item.id)">
            {{ loading ? '运行中…' : '运行演示' }}
          </button>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.demo-overlay {
  position: fixed;
  z-index: 1200;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(12, 10, 8, 0.78);
  backdrop-filter: blur(7px);
}
.demo-dialog {
  width: min(920px, 94vw);
  padding: 22px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: #211b16;
  box-shadow: 0 24px 80px rgba(0, 0, 0, 0.48);
}
.demo-dialog > header { display: flex; justify-content: space-between; gap: 20px; }
.eyebrow { display: block; color: var(--accent-dim); font: 9px/1.2 ui-monospace, monospace; letter-spacing: 1.4px; }
.demo-dialog h2 { margin-top: 5px; color: var(--text); font-size: 20px; }
.demo-dialog header p { margin-top: 5px; color: var(--text-dim); font-size: 12px; }
.close-btn { padding: 0 8px; align-self: flex-start; background: transparent; color: var(--text-faint); font-size: 25px; line-height: 1; }
.demo-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-top: 20px; }
.demo-card {
  display: flex;
  min-height: 270px;
  flex-direction: column;
  padding: 16px;
  border: 1px solid var(--border-soft);
  border-top: 3px solid var(--text-faint);
  border-radius: 9px;
  background: var(--bg-card);
}
.demo-card.danger { border-top-color: var(--danger); }
.demo-card.accepted { border-top-color: var(--system); }
.demo-card.agent { border-top-color: var(--player); }
.card-top { display: flex; justify-content: space-between; align-items: center; }
.case-index { color: var(--text-faint); font: 700 20px/1 ui-monospace, monospace; }
.case-tag { padding: 2px 7px; border: 1px solid var(--border); border-radius: 999px; color: var(--accent); font-size: 9px; }
.demo-card h3 { margin-top: 18px; color: var(--text); font-size: 15px; }
.demo-card p { margin-top: 8px; color: var(--text-dim); font-size: 11px; line-height: 1.7; }
.demo-card code { display: block; margin-top: 14px; color: var(--text-faint); font-size: 9px; overflow-wrap: anywhere; }
.demo-card button { margin-top: auto; border: 1px solid var(--accent-dim); background: rgba(201, 169, 106, 0.12); color: var(--accent); font-size: 12px; }
.demo-card button:hover:not(:disabled) { background: rgba(201, 169, 106, 0.2); }
@media (max-width: 760px) {
  .demo-grid { grid-template-columns: 1fr; }
  .demo-dialog { max-height: 90vh; overflow-y: auto; }
  .demo-card { min-height: 220px; }
}
</style>
