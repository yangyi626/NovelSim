<script setup>
import { ref } from 'vue'

defineProps({
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['submit'])

const text = ref('')
const useNpcAgents = ref(true)  // 默认开启 NPC 自主反应
const demoPrompts = [
  '我冷冷地命令夜清清当众解释陷害我的证据',
  '夜轻歌开飞机离开华容巷',
]

function send() {
  if (!text.value.trim()) return
  emit('submit', text.value, useNpcAgents.value)
  text.value = ''
}

function onEnter(e) {
  if (e.ctrlKey || e.metaKey) {
    send()
  }
}
</script>

<template>
  <div class="input-bar">
    <div class="demo-prompts">
      <span>DEMO INPUT</span>
      <button
        v-for="prompt in demoPrompts"
        :key="prompt"
        type="button"
        :disabled="loading"
        @click="text = prompt"
      >{{ prompt }}</button>
    </div>
    <div class="input-row">
      <textarea
        v-model="text"
        class="input"
        placeholder="输入玩家行动；系统会先解析意图，再经过世界规则校验（Ctrl+Enter 发送）"
        :disabled="loading"
        rows="2"
        @keydown="onEnter"
      ></textarea>
      <button class="send-btn" :disabled="loading || !text.trim()" @click="send">
        {{ loading ? '推演中…' : '发送回合' }}
      </button>
    </div>
    <div class="opts">
      <label class="opt" :class="{ disabled: loading }">
        <input type="checkbox" v-model="useNpcAgents" :disabled="loading" />
        <span>⚡ NPC 自主反应（夜清清/林管家会主动行动）</span>
      </label>
      <span class="hint">提示：每回合需调用 LLM，约 10–60 秒</span>
    </div>
  </div>
</template>

<style scoped>
.input-bar {
  padding: 12px 20px 14px;
  border-top: 1px solid var(--border);
  background: var(--bg-panel);
  flex-shrink: 0;
}
.demo-prompts {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 8px;
  overflow-x: auto;
}
.demo-prompts > span {
  flex: 0 0 auto;
  color: var(--accent-dim);
  font: 9px/1.2 ui-monospace, monospace;
  letter-spacing: 1px;
}
.demo-prompts button {
  flex: 0 0 auto;
  padding: 3px 8px;
  border: 1px solid var(--border-soft);
  border-radius: 999px;
  background: var(--bg-card);
  color: var(--text-faint);
  font-size: 10px;
}
.demo-prompts button:hover:not(:disabled) { border-color: var(--accent-dim); color: var(--accent); }
.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.input {
  flex: 1;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px 12px;
  color: var(--text);
  font-family: inherit;
  font-size: 15px;
  line-height: 1.6;
  resize: none;
  outline: none;
  transition: border-color 0.15s;
}
.input:focus {
  border-color: var(--accent-dim);
}
.input::placeholder {
  color: var(--text-faint);
}
.send-btn {
  background: var(--accent);
  color: #1a1612;
  font-weight: 600;
  padding: 12px 22px;
  height: fit-content;
}
.send-btn:hover:not(:disabled) {
  background: var(--accent-dim);
  color: var(--text);
}
.opts {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
  font-size: 13px;
}
.opt {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--text-dim);
  cursor: pointer;
  user-select: none;
}
.opt.disabled { opacity: 0.5; cursor: not-allowed; }
.opt input { accent-color: var(--accent); }
.hint {
  color: var(--text-faint);
  font-size: 12px;
}
</style>
