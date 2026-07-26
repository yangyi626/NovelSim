<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  controlCompilationJob,
  createCompilationJob,
  getCompilationJob,
  listCompilationJobs,
} from '../api.js'

const emit = defineEmits(['package-created'])

const jobs = ref([])
const selectedId = ref('')
const detail = ref(null)
const loading = ref(false)
const error = ref('')
const timer = ref(null)
const form = ref({
  novel_path: '',
  novel_name: '',
  package_id: '',
  chapters: '',
  volume_size: 20,
  timeline_plan: '',
})

const selected = computed(
  () => jobs.value.find((item) => item.job_id === selectedId.value) || null,
)
const statusLabel = {
  queued: '排队中',
  running: '编译中',
  paused: '已暂停',
  cancelled: '已取消',
  failed: '失败',
  completed: '已完成',
}
const qualityLabel = {
  pending: '等待评分',
  passed: '评分通过',
  failed: '评分未通过',
  error: '评分异常',
  skipped: '未评分',
}
const activeCount = computed(
  () => jobs.value.filter((item) => ['queued', 'running'].includes(item.status)).length,
)

function parseNumbers(value) {
  return [...new Set(
    String(value || '')
      .split(/[,，、\s]+/)
      .map((item) => Number(item))
      .filter((item) => Number.isInteger(item) && item > 0),
  )]
}

function parseTimelinePlan(value) {
  const result = {}
  for (const line of String(value || '').split(/\n+/)) {
    const [chapters, timeline] = line.split(/[:：]/)
    if (!timeline?.trim()) continue
    for (const chapter of parseNumbers(chapters)) {
      result[chapter] = timeline.trim()
    }
  }
  return result
}

async function refresh({ keepSelection = true } = {}) {
  const data = await listCompilationJobs()
  if (data.status === 'error') {
    error.value = data.error
    return
  }
  jobs.value = data.jobs || []
  if (!keepSelection || !jobs.value.some((item) => item.job_id === selectedId.value)) {
    selectedId.value = jobs.value[0]?.job_id || ''
  }
  if (selectedId.value) await loadDetail(selectedId.value)
}

async function loadDetail(jobId) {
  selectedId.value = jobId
  const data = await getCompilationJob(jobId)
  if (data.status === 'error') {
    error.value = data.error
    return
  }
  detail.value = data
  const index = jobs.value.findIndex((item) => item.job_id === jobId)
  if (index >= 0) jobs.value[index] = data.job
}

async function createJob() {
  error.value = ''
  if (!form.value.novel_path.trim() || !form.value.package_id.trim()) {
    error.value = '请填写 novels/ 下的 TXT 文件名和世界包 ID。'
    return
  }
  loading.value = true
  const data = await createCompilationJob({
    novel_path: form.value.novel_path.trim(),
    novel_name: form.value.novel_name.trim(),
    package_id: form.value.package_id.trim(),
    chapters: parseNumbers(form.value.chapters),
    timeline_plan: parseTimelinePlan(form.value.timeline_plan),
    volume_size: Number(form.value.volume_size) || 20,
    auto_start: true,
  })
  loading.value = false
  if (data.status === 'error' || data.status === 'invalid') {
    error.value = data.error
    return
  }
  selectedId.value = data.job.job_id
  await refresh()
}

async function control(action) {
  if (!selected.value || loading.value) return
  loading.value = true
  error.value = ''
  const data = await controlCompilationJob(selected.value.job_id, action)
  loading.value = false
  if (data.status === 'error' || data.status === 'conflict') {
    error.value = data.error
    return
  }
  await refresh()
}

function percent(job) {
  return Math.round((job?.progress || 0) * 100)
}

function shortId(value) {
  return value ? value.slice(0, 8) : ''
}

onMounted(async () => {
  await refresh({ keepSelection: false })
  timer.value = window.setInterval(() => refresh(), 3000)
})

onBeforeUnmount(() => {
  if (timer.value) window.clearInterval(timer.value)
})
</script>

<template>
  <section class="compiler-page">
    <header class="compiler-hero">
      <div>
        <span class="eyebrow">SQLite compilation control plane</span>
        <h1>全书编译任务</h1>
        <p>章节抽取会自动缓存；暂停、失败或重启后只重做未命中的场景。</p>
      </div>
      <div class="hero-metric">
        <strong>{{ jobs.length }}</strong>
        <span>任务 · {{ activeCount }} 个运行中</span>
      </div>
    </header>

    <div v-if="error" class="compiler-error">{{ error }}</div>

    <div class="compiler-layout">
      <aside class="job-list">
        <button
          v-for="job in jobs"
          :key="job.job_id"
          :class="{ active: job.job_id === selectedId }"
          @click="loadDetail(job.job_id)"
        >
          <span class="job-status" :class="job.status">
            {{ statusLabel[job.status] || job.status }}
          </span>
          <strong>{{ job.novel_name || job.package_id }}</strong>
          <small>{{ job.package_id }} · {{ shortId(job.job_id) }}</small>
          <div class="mini-progress"><i :style="{ width: `${percent(job)}%` }"></i></div>
          <small>{{ job.completed_chapters }}/{{ job.total_chapters || '?' }} 章 · {{ percent(job) }}%</small>
        </button>
        <p v-if="!jobs.length" class="empty">尚无编译任务</p>
      </aside>

      <main class="job-content">
        <form class="create-job" @submit.prevent="createJob">
          <div class="section-heading">
            <div>
              <h2>新建编译</h2>
              <p>留空章节表示编译全书；时间线每行使用“章节:时间线”格式。</p>
            </div>
            <button class="primary" :disabled="loading">开始编译</button>
          </div>
          <div class="job-form-grid">
            <label>TXT 文件名<input v-model="form.novel_path" placeholder="第一狂妃：废柴三小姐.txt" /></label>
            <label>世界包 ID<input v-model="form.package_id" placeholder="first_crazy_consoritum" /></label>
            <label>小说名<input v-model="form.novel_name" placeholder="可选，默认取文件名" /></label>
            <label>章节<input v-model="form.chapters" placeholder="1, 2, 3；留空=全书" /></label>
            <label>每卷章节数<input v-model.number="form.volume_size" type="number" min="1" /></label>
            <label class="wide">时间线规划<textarea v-model="form.timeline_plan" rows="2" placeholder="1:origin&#10;2,3,4:novel_world"></textarea></label>
          </div>
        </form>

        <section v-if="selected && detail" class="job-detail">
          <div class="section-heading">
            <div>
              <span class="job-status" :class="selected.status">
                {{ statusLabel[selected.status] || selected.status }}
              </span>
              <h2>{{ selected.novel_name || selected.package_id }}</h2>
              <p>{{ selected.novel_path }}</p>
            </div>
            <div class="job-actions">
              <button v-if="selected.status === 'running'" @click="control('pause')">暂停</button>
              <button v-if="['paused', 'failed'].includes(selected.status)" @click="control('resume')">继续</button>
              <button v-if="['queued', 'running', 'paused', 'failed'].includes(selected.status)" class="danger" @click="control('cancel')">取消</button>
            </div>
          </div>

          <div class="main-progress">
            <div><span>整体进度</span><strong>{{ percent(selected) }}%</strong></div>
            <div class="progress-track"><i :style="{ width: `${percent(selected)}%` }"></i></div>
            <small>
              {{ selected.completed_chapters }}/{{ selected.total_chapters || '?' }} 章
              <template v-if="selected.current_chapter"> · 正在处理第 {{ selected.current_chapter }} 章</template>
            </small>
          </div>

          <div class="quality-card" :class="selected.quality_status">
            <span>{{ qualityLabel[selected.quality_status] || selected.quality_status }}</span>
            <strong v-if="selected.quality_score != null">{{ selected.quality_score.toFixed(3) }}</strong>
            <p v-if="selected.quality_report?.error">{{ selected.quality_report.error }}</p>
            <p v-else>评分通过后，编译产物会自动进入创作者“待审核”状态。</p>
          </div>

          <div class="chapter-grid">
            <article v-for="chapter in detail.chapters" :key="chapter.chapter_index" :class="chapter.status">
              <strong>第 {{ chapter.chapter_index }} 章</strong>
              <span>{{ chapter.heading }}</span>
              <small>{{ statusLabel[chapter.status] || chapter.status }} · 缓存 {{ chapter.cache_hits }}/新抽取 {{ chapter.cache_misses }}</small>
            </article>
          </div>

          <div v-if="detail.snapshots?.length" class="snapshot-strip">
            <article v-for="snapshot in detail.snapshots" :key="snapshot.snapshot_id">
              <span>{{ snapshot.level }}</span>
              <strong>{{ snapshot.snapshot_id }}</strong>
              <small>章节 {{ snapshot.chapter_start }}–{{ snapshot.chapter_end }} · {{ snapshot.state_hash.slice(0, 10) }}</small>
            </article>
          </div>

          <button
            v-if="selected.status === 'completed' && selected.result_package_id"
            class="package-ready"
            @click="emit('package-created', selected.result_package_id)"
          >打开审核世界包：{{ selected.result_package_id }}</button>
        </section>
      </main>
    </div>
  </section>
</template>

<style scoped>
.compiler-page { display: grid; gap: 18px; }
.compiler-hero, .section-heading { display: flex; justify-content: space-between; gap: 18px; align-items: flex-start; }
.compiler-hero { padding: 24px; border: 1px solid rgba(133, 105, 73, .22); background: linear-gradient(135deg, #fffaf1, #f2e5ce); border-radius: 18px; }
.compiler-hero h1, .section-heading h2 { margin: 5px 0; }
.compiler-hero p, .section-heading p, .job-detail p { margin: 0; color: #786b5b; }
.eyebrow { color: #9b612d; text-transform: uppercase; letter-spacing: .12em; font-size: 11px; }
.hero-metric { min-width: 140px; text-align: right; }
.hero-metric strong { display: block; font-size: 34px; color: #663d23; }
.hero-metric span { color: #8d765e; font-size: 12px; }
.compiler-layout { display: grid; grid-template-columns: 250px 1fr; gap: 18px; }
.job-list { display: grid; align-content: start; gap: 9px; }
.job-list button { text-align: left; padding: 14px; border: 1px solid #ddcfbd; background: #fffdf8; border-radius: 13px; display: grid; gap: 6px; }
.job-list button.active { border-color: #9a6334; box-shadow: 0 0 0 2px rgba(154, 99, 52, .12); }
.job-list small { color: #8a7a68; }
.job-content { display: grid; gap: 18px; min-width: 0; }
.create-job, .job-detail { padding: 20px; border: 1px solid #dfd3c2; background: #fffdf9; border-radius: 16px; }
.job-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }
.job-form-grid label { display: grid; gap: 6px; font-size: 12px; color: #685a4a; }
.job-form-grid .wide { grid-column: 1 / -1; }
.job-form-grid input, .job-form-grid textarea { border: 1px solid #d8cab7; padding: 10px; border-radius: 9px; background: #fff; }
.primary, .package-ready { border: 0; border-radius: 10px; padding: 10px 16px; background: #74431f; color: white; }
.job-status { width: fit-content; padding: 3px 8px; border-radius: 999px; background: #eee5d8; color: #705e49; font-size: 11px; }
.job-status.running, .job-status.queued { background: #e5efff; color: #315f98; }
.job-status.completed { background: #dff3e6; color: #2d7046; }
.job-status.failed, .job-status.cancelled { background: #f8e1df; color: #93453e; }
.mini-progress, .progress-track { overflow: hidden; background: #eee5d8; border-radius: 999px; }
.mini-progress { height: 4px; }
.mini-progress i, .progress-track i { display: block; height: 100%; background: linear-gradient(90deg, #be8750, #75441f); }
.job-detail { display: grid; gap: 16px; }
.job-actions { display: flex; gap: 8px; }
.job-actions button { border: 1px solid #cdbba5; background: #fff9ef; padding: 8px 12px; border-radius: 8px; }
.job-actions .danger { color: #a2473d; border-color: #ddb5b0; }
.main-progress { display: grid; gap: 8px; }
.main-progress > div:first-child { display: flex; justify-content: space-between; }
.progress-track { height: 10px; }
.quality-card { padding: 14px; border-radius: 12px; background: #f3eee6; display: grid; gap: 4px; }
.quality-card.passed { background: #e4f3e8; }
.quality-card.failed, .quality-card.error { background: #f9e5e2; }
.quality-card strong { font-size: 24px; }
.chapter-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 9px; }
.chapter-grid article, .snapshot-strip article { border: 1px solid #e2d7c8; padding: 11px; border-radius: 10px; display: grid; gap: 4px; }
.chapter-grid article.completed { border-color: #a8d0b4; background: #f4fbf6; }
.chapter-grid span, .chapter-grid small, .snapshot-strip small { color: #857665; font-size: 11px; }
.snapshot-strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; }
.snapshot-strip article { min-width: 190px; }
.snapshot-strip span { color: #9b612d; font-size: 10px; text-transform: uppercase; }
.package-ready { justify-self: start; }
.compiler-error { padding: 12px; border-radius: 10px; color: #983f37; background: #fae5e2; }
.empty { color: #8b7b69; text-align: center; padding: 24px 0; }
@media (max-width: 900px) {
  .compiler-layout { grid-template-columns: 1fr; }
  .job-list { grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
}
@media (max-width: 620px) {
  .compiler-hero, .section-heading { display: grid; }
  .hero-metric { text-align: left; }
  .job-form-grid { grid-template-columns: 1fr; }
  .job-form-grid .wide { grid-column: auto; }
}
</style>
