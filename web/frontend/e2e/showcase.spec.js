import { expect, test } from '@playwright/test'

test('存档管理提供保留当前世界线的历史清理确认', async ({ page }) => {
  const requests = []
  const sessionId = 'current-session'
  const packageId = 'showcase-world'
  const state = {
    version: 0,
    world_time: '清晨',
    current_scene_id: 'courtyard',
    flags: {},
    characters: {
      protagonist: {
        character_id: 'protagonist',
        display_name: '主角',
        location_id: 'courtyard',
        is_alive: true,
      },
    },
    locations: {
      courtyard: { location_id: 'courtyard', display_name: '庭院' },
    },
    items: {},
    relations: [],
    plot: {},
  }
  const sessionPayload = {
    status: 'ok',
    session_id: sessionId,
    default_actor: 'protagonist',
    state,
    world_meta: {
      novel: '演示小说',
      scenario: '开场庭院',
      source_chapters: [1],
    },
    save: {
      session_id: sessionId,
      name: '当前世界线',
      world_package_id: packageId,
      version: 0,
      updated_at: '',
    },
    turns: [],
    settlement: null,
  }

  await page.route('**/api/worlds', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        worlds: [{ package_id: packageId, novel: '演示小说', scenario: '开场庭院' }],
      }),
    })
  })
  await page.route('**/api/books', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        books: [{ book_id: 'showcase-book', novel: '演示小说', chapter_count: 1, revision: 1 }],
      }),
    })
  })
  await page.route('**/api/books/showcase-book/chapters', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        chapters: [{
          book_id: 'showcase-book',
          entry_id: 'showcase-book:chapter:1',
          chapter_number: 1,
          title: '开场庭院',
          published: true,
          canonical: false,
          package_id: packageId,
        }],
      }),
    })
  })
  await page.route('**/api/saves', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        saves: [
          { session_id: sessionId, name: '当前世界线', version: 0, updated_at: '' },
          { session_id: 'old-session', name: '旧世界线', version: 1, updated_at: '' },
        ],
      }),
    })
  })
  await page.route('**/api/start', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(sessionPayload),
    })
  })
  await page.route(`**/api/player-view?session=${sessionId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', current_story_chapter: 1 }),
    })
  })
  await page.route(`**/api/world-runs/${sessionId}/dashboard`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', dashboard: {} }),
    })
  })
  await page.route(`**/api/joint-plans?session=${sessionId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok', plans: [] }),
    })
  })
  await page.route('**/api/saves/clear-history', async (route) => {
    requests.push(JSON.parse(route.request().postData() || '{}'))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok', candidate_count: 1, deleted_count: 1,
        deleted_session_ids: ['old-session'], preserved_session_id: sessionId,
        failed_count: 0, failures: [],
      }),
    })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '选择世界', exact: true }).click()
  const selector = page.getByRole('dialog', { name: '选择小说与进入章节' })
  await selector.getByRole('button', { name: /第 1 章/ }).click()
  await selector.getByRole('button', { name: '进入本章世界线' }).click()
  await expect(page.getByRole('button', { name: '世界线', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '世界线', exact: true }).click()
  await expect(page.getByText('将删除 1 条非当前存档')).toBeVisible()
  await page.getByLabel('清空历史世界线确认短语').fill('清空历史世界线')
  await page.getByRole('button', { name: '清空历史' }).click()
  await expect(page.getByText('已删除 1 条')).toBeVisible()
  expect(requests).toEqual([{
    preserve_session_id: sessionId, confirmation: '清空历史世界线',
  }])
})

test('演示与技术验证入口保持可用且无浏览器错误', async ({ page }) => {
  const browserErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.goto('/')
  await page.getByRole('button', { name: '打开更多入口' }).click()
  await page.getByRole('menuitem', { name: '演示与技术验证' }).click()
  const dialog = page.getByRole('dialog', { name: '选择一条一键演示' })
  await expect(dialog).toContainText('无需 API Key')

  const illegalCard = page.locator('.demo-card').filter({ hasText: '非法行动：开飞机' })
  await illegalCard.getByRole('button', { name: '运行演示' }).click()
  await expect(page.locator('.demo-evidence-banner')).toContainText('非法行动 · 世界规则拦截')
  await expect(page.getByText('行动未能发生', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('WORLD_CONCEPT_UNAVAILABLE', { exact: true })).toHaveCount(0)
  await expect(page.locator('.version-tag')).toContainText('v0')

  await page.getByRole('button', { name: '系统空间', exact: true }).click()
  await page.getByRole('button', { name: '打开更多入口' }).click()
  await page.getByRole('menuitem', { name: '演示与技术验证' }).click()
  const multiAgentCard = page.locator('.demo-card').filter({ hasText: '自主行为：传播与结盟' })
  await multiAgentCard.getByRole('button', { name: '运行演示' }).click()
  await expect(page.locator('.demo-evidence-banner')).toContainText('多 Agent · 信息传播与结盟')
  await expect(page.locator('.demo-evidence-banner')).toContainText('v5')
  await expect(page.locator('.version-tag')).toContainText('v5')

  await page.getByRole('button', { name: '系统空间', exact: true }).click()
  await page.getByRole('button', { name: '打开更多入口' }).click()
  await page.getByRole('menuitem', { name: '开发者模式' }).click()
  await expect(page.getByText('管家与盟友最终形成防卫联盟。')).toBeVisible()
  await expect(page.locator('.tool-trace-card')).toHaveCount(5)

  expect(browserErrors).toEqual([])
})
