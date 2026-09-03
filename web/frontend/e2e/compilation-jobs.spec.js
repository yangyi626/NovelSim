import { expect, test } from '@playwright/test'

const job = {
  job_id: 'job-report-e2e',
  package_id: 'compiled-e2e',
  book_id: 'book-e2e',
  novel_name: '报告演练小说',
  novel_path: 'book.txt',
  status: 'paused',
  total_chapters: 10,
  completed_chapters: 4,
  progress: 0.4,
  current_chapter: null,
  llm_calls_used: 8,
  max_llm_calls: 20,
  quality_status: 'pending',
  quality_score: null,
  quality_report: {},
  retry_count: 0,
  max_retries: 2,
  retryable: false,
  failure_kind: 'unknown',
  chapters: [],
  timeline_plan: {},
  volume_plan: {},
  volume_size: 20,
}

const detail = {
  status: 'ok',
  job,
  chapters: [],
  snapshots: [],
  worker_active: false,
}

const report = {
  status: 'ok',
  report: {
    job,
    elapsed_seconds: 125.5,
    chapters_per_hour: 114.7,
    cache_hit_rate: 0.75,
    extraction_count: 12,
    llm_calls_remaining: 12,
    attempt_count: 1,
    retry_count: 0,
    chapter_count: 10,
    snapshot_count: 4,
    worker_active: false,
  },
}

test('创作台编译任务展示报告摘要并支持追加预算与继续', async ({ page }) => {
  let budgetRequests = 0
  let actionRequests = []
  await page.route('**/api/auth/me', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'ok',
      user: {
        username: 'creator',
        roles: ['creator'],
        permissions: ['compiler.manage', 'creator.read'],
      },
    }),
  }))
  await page.route('**/api/creator/packages', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', packages: [] }),
  }))
  await page.route('**/api/creator/compiler/jobs?limit=100', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', jobs: [job] }),
  }))
  await page.route('**/api/creator/compiler/jobs/job-report-e2e/report', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(report),
  }))
  await page.route('**/api/creator/compiler/jobs/job-report-e2e', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(detail),
  }))
  await page.route('**/api/creator/compiler/jobs/job-report-e2e/budget', async (route) => {
    budgetRequests += 1
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', job: { ...job, max_llm_calls: 25 } }),
    })
  })
  await page.route('**/api/creator/compiler/jobs/job-report-e2e/actions', async (route) => {
    actionRequests.push(JSON.parse(route.request().postData() || '{}'))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', job: { ...job, status: 'queued' } }),
    })
  })

  await page.addInitScript(() => {
    window.localStorage.setItem('novelsim_creator_token', 'e2e-token')
  })
  await page.goto('/#/creator')
  await page.getByRole('button', { name: '全书编译' }).click()
  await expect(page.getByText('全书编译任务')).toBeVisible()
  await page.getByRole('button', { name: '追加 20 次预算' }).click()
  await expect.poll(() => budgetRequests).toBe(1)
  await expect(page.getByText('已耗时')).toBeVisible()
  await expect(page.getByText('章节吞吐')).toBeVisible()
  await expect(page.getByText('缓存命中率')).toBeVisible()
  await expect(page.getByText('75.0%')).toBeVisible()
  await page.getByRole('button', { name: '继续' }).click()
  await expect.poll(() => actionRequests).toEqual([{ action: 'resume' }])
})
