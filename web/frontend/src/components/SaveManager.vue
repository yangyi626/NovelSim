<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  saves: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  clearing: { type: Boolean, default: false },
  clearResult: { type: Object, default: null },
})

const emit = defineEmits([
  'close',
  'create',
  'load',
  'rename',
  'delete',
  'export',
  'import',
  'refresh',
  'clear-history',
])
const editingId = ref('')
const editingName = ref('')
const importInput = ref(null)
const importError = ref('')
const clearConfirmation = ref('')
const clearError = ref('')
const clearableCount = computed(() => props.saves.filter(
  (save) => save.session_id !== props.currentSessionId,
).length)

function beginRename(save) {
  editingId.value = save.session_id
  editingName.value = save.name
}

function submitRename(save) {
  const name = editingName.value.trim()
  if (!name) return
  emit('rename', save.session_id, name)
  editingId.value = ''
}

function requestDelete(save) {
  if (window.confirm(`确定删除存档“${save.name}”吗？此操作不可撤销。`)) {
    emit('delete', save.session_id)
  }
}

function requestClearHistory() {
  clearError.value = ''
  if (clearConfirmation.value.trim() !== '清空历史世界线') {
    clearError.value = '请输入“清空历史世界线”后再确认。'
    return
  }
  emit('clear-history', clearConfirmation.value.trim())
  clearConfirmation.value = ''
}

function formatTime(value) {
  if (!value) return '—'
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

async function handleImport(event) {
  importError.value = ''
  const file = event.target.files?.[0]
  if (!file) return
  try {
    const backup = JSON.parse(await file.text())
    emit('import', backup)
  } catch (_) {
    importError.value = '文件不是有效的 JSON 存档'
  } finally {
    event.target.value = ''
  }
}
</script>

<template>
  <div v-if="open" class="overlay" @click.self="emit('close')">
    <section class="save-dialog">
      <header class="save-head">
        <div>
          <h2>世界线存档</h2>
          <p>每条世界线拥有独立状态、事件与剧情历史</p>
        </div>
        <button class="close-btn" @click="emit('close')">×</button>
      </header>

      <div class="save-actions">
        <button class="primary-btn" :disabled="loading" @click="emit('create')">新建世界线</button>
        <button class="ghost-btn" :disabled="loading" @click="importInput?.click()">导入存档</button>
        <button class="ghost-btn" :disabled="loading" @click="emit('refresh')">刷新</button>
        <input
          ref="importInput"
          class="file-input"
          type="file"
          accept=".json,application/json"
          @change="handleImport"
        />
      </div>
      <div v-if="importError" class="import-error">{{ importError }}</div>

      <section class="clear-history-panel">
        <div>
          <strong>清空历史世界线</strong>
          <p>将删除 {{ clearableCount }} 条非当前存档；当前世界线默认保留。</p>
          <small>不可恢复；不会删除世界包、章节正文或编译数据。</small>
        </div>
        <div class="clear-history-form">
          <input
            v-model="clearConfirmation"
            aria-label="清空历史世界线确认短语"
            placeholder="输入：清空历史世界线"
            :disabled="loading || clearing || clearableCount === 0"
            @keyup.enter="requestClearHistory"
          />
          <button
            class="mini-btn danger"
            :disabled="loading || clearing || clearableCount === 0"
            @click="requestClearHistory"
          >{{ clearing ? '清理中…' : '清空历史' }}</button>
        </div>
        <div v-if="clearError" class="import-error">{{ clearError }}</div>
        <div v-if="clearResult" class="clear-result">
          已删除 {{ clearResult.deleted_count }} 条<span v-if="clearResult.preserved_session_id">，当前世界线已保留</span>。
          <span v-if="clearResult.failed_count">仍有 {{ clearResult.failed_count }} 条删除失败。</span>
        </div>
      </section>

      <div v-if="!saves.length" class="save-empty">暂无存档</div>
      <div v-else class="save-list">
        <article
          v-for="save in saves"
          :key="save.session_id"
          class="save-card"
          :class="{ current: save.session_id === currentSessionId }"
        >
          <div class="save-main">
            <div v-if="editingId === save.session_id" class="rename-row">
              <input
                v-model="editingName"
                maxlength="80"
                @keyup.enter="submitRename(save)"
                @keyup.esc="editingId = ''"
              />
              <button class="mini-btn" @click="submitRename(save)">确定</button>
            </div>
            <div v-else class="save-title">
              <strong>{{ save.name }}</strong>
              <span v-if="save.session_id === currentSessionId" class="current-tag">当前</span>
            </div>
            <div class="save-meta">
              <span>世界线 v{{ save.version }}</span>
              <span>{{ formatTime(save.updated_at) }}</span>
            </div>
          </div>
          <div class="card-actions">
            <button
              class="mini-btn"
              :disabled="loading || save.session_id === currentSessionId"
              @click="emit('load', save.session_id)"
            >载入</button>
            <button class="mini-btn" :disabled="loading" @click="emit('export', save.session_id)">导出</button>
            <button class="mini-btn" :disabled="loading" @click="beginRename(save)">改名</button>
            <button class="mini-btn danger" :disabled="loading" @click="requestDelete(save)">删除</button>
          </div>
        </article>
      </div>
    </section>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(8, 6, 4, 0.76);
  backdrop-filter: blur(3px);
}
.save-dialog {
  width: min(680px, 100%);
  max-height: min(720px, 90vh);
  display: flex;
  flex-direction: column;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 22px 60px rgba(0, 0, 0, 0.42);
}
.save-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--border-soft);
}
.save-head h2 {
  color: var(--accent);
  font-size: 20px;
  letter-spacing: 1px;
}
.save-head p {
  margin-top: 3px;
  color: var(--text-faint);
  font-size: 13px;
}
.close-btn {
  padding: 0 8px;
  background: transparent;
  color: var(--text-dim);
  font-size: 26px;
  line-height: 1;
}
.save-actions {
  display: flex;
  gap: 10px;
  padding: 14px 22px;
}
.file-input {
  display: none;
}
.import-error {
  margin: -4px 22px 8px;
  color: var(--danger);
  font-size: 13px;
}
.clear-history-panel {
  display: grid;
  gap: 8px;
  margin: 0 22px 12px;
  padding: 12px 14px;
  background: rgba(130, 36, 36, 0.12);
  border: 1px solid rgba(190, 85, 85, 0.42);
  border-radius: 6px;
}
.clear-history-panel p,
.clear-history-panel small {
  display: block;
  margin-top: 4px;
  color: var(--text-faint);
  font-size: 12px;
}
.clear-history-form {
  display: flex;
  gap: 8px;
}
.clear-history-form input {
  min-width: 0;
  flex: 1;
  padding: 6px 9px;
  color: var(--text);
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 4px;
}
.clear-result {
  color: var(--system);
  font-size: 12px;
}
.primary-btn {
  background: var(--accent);
  color: #211a12;
}
.ghost-btn, .mini-btn {
  background: var(--bg-card);
  color: var(--text-dim);
  border: 1px solid var(--border);
}
.save-list {
  overflow-y: auto;
  padding: 0 22px 22px;
}
.save-card {
  display: flex;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  margin-top: 10px;
  padding: 14px 16px;
  background: var(--bg-card);
  border: 1px solid var(--border-soft);
  border-radius: 6px;
}
.save-card.current {
  border-color: var(--accent-dim);
  box-shadow: inset 3px 0 var(--accent);
}
.save-main {
  min-width: 0;
  flex: 1;
}
.save-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.save-title strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.current-tag {
  flex-shrink: 0;
  padding: 1px 7px;
  color: var(--system);
  border: 1px solid var(--system);
  border-radius: 10px;
  font-size: 11px;
}
.save-meta {
  display: flex;
  gap: 14px;
  margin-top: 4px;
  color: var(--text-faint);
  font-size: 12px;
}
.card-actions, .rename-row {
  display: flex;
  align-items: center;
  gap: 7px;
}
.mini-btn {
  padding: 5px 10px;
  font-size: 13px;
}
.mini-btn.danger {
  color: var(--danger);
}
.rename-row input {
  width: min(300px, 100%);
  padding: 6px 9px;
  color: var(--text);
  background: var(--bg-input);
  border: 1px solid var(--accent-dim);
  border-radius: 4px;
  outline: none;
}
.save-empty {
  padding: 54px 20px;
  color: var(--text-faint);
  text-align: center;
}
@media (max-width: 640px) {
  .clear-history-form {
    align-items: stretch;
    flex-direction: column;
  }
  .save-card {
    align-items: flex-start;
    flex-direction: column;
  }
  .card-actions {
    width: 100%;
  }
}
</style>
