<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  loading: { type: Boolean, default: false },
  suggestion: { type: String, default: '' },
  submitResult: { type: Object, default: null },
  developerMode: { type: Boolean, default: false },
})
const emit = defineEmits(['submit', 'suggestion-consumed'])

const text = ref('')
const useNpcAgents = ref(true)
const pendingText = ref('')

watch(() => props.suggestion, (value) => {
  if (!value) return
  text.value = value
  emit('suggestion-consumed')
})

watch(() => props.submitResult, (result) => {
  if (!result || result.requestText !== pendingText.value) return
  if (result.success) text.value = ''
  else text.value = pendingText.value
  pendingText.value = ''
})

function send() {
  const requestText = text.value.trim()
  if (!requestText || props.loading) return
  pendingText.value = requestText
  emit('submit', requestText, useNpcAgents.value)
}

function onEnter(event) {
  if (event.ctrlKey || event.metaKey) send()
}
</script>

<template>
  <div class="input-bar">
    <div v-if="submitResult && !submitResult.success" class="retry-note">
      这次行动没有成功，你的输入已保留。可以调整说法后再次尝试。
    </div>
    <div class="input-row">
      <textarea
        v-model="text"
        class="input"
        aria-label="你想做什么"
        placeholder="描述你想做的事，例如观察四周、和某人交谈，或尝试改变局势（Ctrl+Enter 发送）"
        :disabled="loading"
        rows="2"
        @keydown="onEnter"
      ></textarea>
      <button class="send-btn" :disabled="loading || !text.trim()" @click="send">
        {{ loading ? '故事回应中…' : '采取行动' }}
      </button>
    </div>
    <div class="opts">
      <label v-if="developerMode" class="opt" :class="{ disabled: loading }">
        <input v-model="useNpcAgents" type="checkbox" :disabled="loading" />
        <span>允许在场角色自主回应</span>
      </label>
      <span class="hint">角色会依据当前处境与记忆作出回应</span>
    </div>
  </div>
</template>

<style scoped>
.input-bar {
  padding: 16px clamp(22px, 5vw, 72px) 18px;
  border-top: 1px solid #343a45;
  background: linear-gradient(180deg, #1b1f24, #171a1f);
  flex-shrink: 0;
}
.input-row { max-width: 900px; margin: 0 auto; }
.opts { max-width: 900px; margin-inline: auto; }
.retry-note {
  margin-bottom: 8px;
  padding: 7px 9px;
  border: 1px solid rgba(216, 173, 102, .28);
  border-radius: 6px;
  background: rgba(216, 173, 102, .07);
  color: var(--warn);
  font-size: 10px;
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.input {
  flex: 1;
  min-height: 54px;
  background: #111316;
  border: 1px solid #3b4048;
  border-radius: 10px;
  padding: 11px 13px;
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
  margin-left: auto;
  color: var(--text-faint);
  font-size: 11px;
}
@media (max-width: 480px) {
  .input-bar { padding: 10px; }
  .input-row { align-items: stretch; flex-direction: column; }
  .send-btn { width: 100%; }
  .opts { align-items: flex-start; flex-direction: column; gap: 4px; }
  .hint { margin-left: 0; }
}
</style>
