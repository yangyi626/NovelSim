import { expect, test } from '@playwright/test'

const sessionId = 'e2e-canonical-settlement'
const packageId = 'first_crazy_ch1_checkpoint'

const world = {
  package_id: packageId,
  novel: '第一狂妃：废柴三小姐',
  scenario: '华容巷受辱',
  source: 'builtin',
  mission: '在受辱的起点夺回主动权，改写夜轻歌的命运。',
  anchor: '第 1 章华容巷冲突结束',
  source_chapters: [1, 2, 3, 4, 5],
  player_identity: '快穿者 · 夜轻歌',
  manifest: {
    entry_kind: 'canonical_checkpoint',
    checkpoint_chapter: 1,
    target_chapters: [2, 3, 4, 5],
    character_count: 3,
    location_count: 4,
  },
}

const state = {
  version: 1,
  world_time: '夜晚',
  current_scene_id: 'loc_ye_clan_hall',
  flags: {
    'canonical.checkpoint_chapter': 1,
    'canonical.hall_summons_issued': true,
  },
  characters: {
    night: {
      character_id: 'night',
      display_name: '夜轻歌',
      location_id: 'loc_ye_clan_hall',
      identity_tags: ['夜家三小姐'],
      is_alive: true,
    },
  },
  locations: {
    loc_ye_clan_hall: { location_id: 'loc_ye_clan_hall', display_name: '夜家大堂' },
  },
  items: {},
  relations: [],
  plot: {},
}

const worldMeta = {
  novel: world.novel,
  scenario: world.scenario,
  anchor: world.anchor,
  source_chapters: world.source_chapters,
}

const ending = {
  ending_id: packageId,
  title: '夜家大堂·命运转折',
  summary: '夜轻歌带着夜家传唤抵达大堂，第一章检查点的主线已经闭合。',
  objective_satisfied: true,
}

function settlement(status = 'available') {
  const settled = status === 'settled'
  return {
    status,
    ending_id: packageId,
    ending_title: ending.title,
    title: ending.title,
    summary: ending.summary,
    reason: '已满足夜家传唤与抵达夜家大堂的确定性终点。',
    objective_satisfied: true,
    can_settle: !settled,
    reward_preview: { reward_points: 100 },
    reward: settled ? { reward_points: 100 } : null,
    reward_points: 100,
    reward_claimed: settled,
    settled_at: settled ? '2026-08-25T12:00:00+00:00' : null,
    settlement_version: settled ? 2 : null,
    world_version: settled ? 2 : 1,
    can_continue: true,
    next_chapter: {
      package_id: 'first_crazy_ch5_checkpoint',
      title: '《第一狂妃》第 6–10 章',
      chapter_start: 6,
      chapter_end: 10,
      status: settled ? 'unlocked' : 'locked',
      reason: settled ? '完成第 1–5 章世界线结算后解锁。' : '完成本世界线结算后解锁下一章。',
      child_session_id: '',
    },
    inheritance_preview: settled
      ? [
          { title: 'flag', text: 'canonical.hall_summons_issued' },
          { title: 'flag', text: 'canonical.returned_fengyue_pavilion' },
        ]
      : [],
    canonical_changes: [
      { status: '与原著一致', summary: '夜轻歌抵达夜家大堂，原著转折点被保留。', world_version: 1 },
    ],
    npc_memory_echoes: [
      { npc_name: '夜家众人', text: '他们记住了你带着传唤走进大堂的这一刻。' },
    ],
  }
}

function save(status = 'active') {
  const settled = status === 'settled'
  return {
    session_id: sessionId,
    name: '华容巷受辱世界线',
    world_package_id: packageId,
    default_actor: 'night',
    version: settled ? 2 : 1,
    created_at: '2026-08-25T12:00:00+00:00',
    updated_at: '2026-08-25T12:00:00+00:00',
    status,
    settlement_status: settled ? 'settled' : 'available',
    ending_id: settled ? packageId : '',
    ending_title: settled ? ending.title : '',
    settled_at: settled ? '2026-08-25T12:00:00+00:00' : null,
    reward_points: settled ? 100 : 0,
    campaign_id: 'campaign_e2e',
    root_session_id: sessionId,
    parent_session_id: '',
    depth: 0,
    chapter_label: '第 2–5 章',
    chapter_access: [
      {
        package_id: 'first_crazy_ch5_checkpoint',
        status: settled ? 'unlocked' : 'locked',
        reason: settled ? '完成第 1–5 章世界线结算后解锁。' : '完成本世界线结算后解锁下一章。',
        child_session_id: '',
      },
    ],
  }
}

function sessionPayload() {
  return {
    status: 'ok',
    session_id: sessionId,
    default_actor: 'night',
    state,
    world_meta: worldMeta,
    save: save('active'),
    turns: [],
    settlement: null,
  }
}

function dashboardPayload(status = 'available') {
  const currentSettlement = settlement(status)
  return {
    status: 'ok',
    dashboard: {
      schema_version: 'player_dashboard.v1',
      session_id: sessionId,
      world_version: currentSettlement.world_version,
      identity: '夜轻歌',
      mission: '在第 1 章冲突之后查清陷害真相，决定接下来的人生走向。',
      mission_title: '改写夜轻歌的命运',
      mission_progress: { percent: status === 'settled' ? 100 : 80 },
      current_scene: { id: 'loc_ye_clan_hall', name: '夜家大堂' },
      context_choices: [],
      suggested_actions: [],
      npc_memory_echoes: currentSettlement.npc_memory_echoes,
      recent_world_changes: currentSettlement.canonical_changes,
      canonical_changes: currentSettlement.canonical_changes,
      settlement: currentSettlement,
      can_settle: !['settled'].includes(status),
      save: save(status === 'settled' ? 'settled' : 'active'),
    },
  }
}

function playerViewPayload() {
  return {
    status: 'ok',
    checkpoint_chapter: 1,
    current_story_chapter: 1,
    story_beats: [],
    canonical_baseline_available: true,
  }
}

async function json(route, body, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

test('终点后的自动演化入口直接打开结算且不再生成规划', async ({ page }) => {
  let generateRequests = 0

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/worlds' && request.method() === 'GET') {
      return json(route, { status: 'ok', worlds: [world] })
    }
    if (path === '/api/books' && request.method() === 'GET') {
      return json(route, {
        status: 'ok',
        books: [{ book_id: 'first_crazy', novel: world.novel, chapter_count: 5, revision: 1 }],
      })
    }
    if (path === '/api/books/first_crazy/chapters' && request.method() === 'GET') {
      return json(route, {
        status: 'ok',
        chapters: [{
          book_id: 'first_crazy',
          entry_id: 'first_crazy:chapter:1',
          chapter_number: 1,
          title: '华容巷受辱',
          published: true,
          canonical: true,
          package_id: packageId,
        }],
      })
    }
    if (path === '/api/saves' && request.method() === 'GET') {
      return json(route, { status: 'ok', saves: [] })
    }
    if (path === '/api/start' && request.method() === 'POST') {
      return json(route, sessionPayload())
    }
    if (path === '/api/player-view' && request.method() === 'GET') {
      return json(route, playerViewPayload())
    }
    if (path === `/api/world-runs/${sessionId}/dashboard` && request.method() === 'GET') {
      return json(route, dashboardPayload('available'))
    }
    if (path === '/api/joint-plans' && request.method() === 'GET') {
      return json(route, { status: 'ok', plans: [] })
    }
    if (path === '/api/joint-plans/generate' && request.method() === 'POST') {
      generateRequests += 1
      return json(route, { status: 'error', error: '终点后不应调用规划接口' }, 500)
    }
    if (path === `/api/world-runs/${sessionId}/settlement` && request.method() === 'GET') {
      return json(route, {
        ...dashboardPayload('available'),
        status: 'available',
        session_id: sessionId,
        ending,
        settlement: settlement('available'),
        reward: { reward_points: 100 },
        state_version: 1,
        world_version: 1,
      })
    }

    return json(route, { status: 'ok' })
  })

  await page.goto('/')
  await page.getByRole('button', { name: '选择世界', exact: true }).click()
  const selector = page.getByRole('dialog', { name: '选择小说与进入章节' })
  await selector.getByRole('button', { name: /第 1 章/ }).click()
  await selector.getByRole('button', { name: '进入本章世界线' }).click()

  const evolutionButton = page.getByRole('button', { name: '查看世界线结算', exact: true })
  await expect(evolutionButton).toBeVisible()
  await evolutionButton.click()

  await expect(page.getByRole('dialog', { name: '世界线结算' })).toBeVisible()
  expect(generateRequests).toBe(0)
})


test('玩家完成第一狂妃终点结算并在系统空间保留历史', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  let settled = false

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/worlds' && request.method() === 'GET') {
      return json(route, { status: 'ok', worlds: [world] })
    }
    if (path === '/api/books' && request.method() === 'GET') {
      return json(route, {
        status: 'ok',
        books: [{ book_id: 'first_crazy', novel: world.novel, chapter_count: 5, revision: 1 }],
      })
    }
    if (path === '/api/books/first_crazy/chapters' && request.method() === 'GET') {
      return json(route, {
        status: 'ok',
        chapters: [{
          book_id: 'first_crazy',
          entry_id: 'first_crazy:chapter:1',
          chapter_number: 1,
          title: '华容巷受辱',
          published: true,
          canonical: true,
          package_id: packageId,
        }],
      })
    }
    if (path === '/api/saves' && request.method() === 'GET') {
      return json(route, { status: 'ok', saves: settled ? [save('settled')] : [] })
    }
    if (path === '/api/start' && request.method() === 'POST') {
      return json(route, sessionPayload())
    }
    if (path === '/api/session' && request.method() === 'GET') {
      return json(route, sessionPayload())
    }
    if (path === '/api/player-view' && request.method() === 'GET') {
      return json(route, playerViewPayload())
    }
    if (path === `/api/world-runs/${sessionId}/dashboard` && request.method() === 'GET') {
      return json(route, dashboardPayload(settled ? 'settled' : 'available'))
    }
    if (path === '/api/joint-plans' && request.method() === 'GET') {
      return json(route, { status: 'ok', plans: [] })
    }
    if (path === `/api/world-runs/${sessionId}/settlement` && request.method() === 'GET') {
      return json(route, {
        ...dashboardPayload(settled ? 'settled' : 'available'),
        status: settled ? 'settled' : 'available',
        session_id: sessionId,
        ending,
        settlement: settlement(settled ? 'settled' : 'available'),
        reward: settled ? { reward_points: 100 } : { reward_points: 100 },
        state_version: settled ? 2 : 1,
        world_version: settled ? 2 : 1,
      })
    }
    if (path === `/api/world-runs/${sessionId}/settlement` && request.method() === 'POST') {
      settled = true
      return json(route, {
        ...dashboardPayload('settled'),
        status: 'settled',
        session_id: sessionId,
        ending,
        settlement: settlement('settled'),
        reward: { reward_points: 100 },
        event_id: 'settlement_e2e',
        state_version: 2,
        world_version: 2,
      })
    }

    return json(route, { status: 'ok' })
  })

  await page.goto('/')
  await expect(page.getByRole('main', { name: 'SystemSpace 世界中转站' })).toBeVisible()
  await page.getByRole('button', { name: '选择世界', exact: true }).click()

  const selector = page.getByRole('dialog', { name: '选择小说与进入章节' })
  await expect(selector).toBeVisible()
  await expect(selector).toContainText('第一狂妃：废柴三小姐')
  await selector.getByRole('button', { name: /第 1 章/ }).click()
  await selector.getByRole('button', { name: '进入本章世界线' }).click()

  await page.getByRole('button', { name: '章节', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '旅程指引' })).toBeVisible()
  await expect(page.getByText('在第 1 章冲突之后查清陷害真相，决定接下来的人生走向。', { exact: true })).toBeVisible()
  await expect(page.getByText('世界线已抵达终点', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '进入结算', exact: true })).toBeVisible()
  await expect(page.getByText('first_crazy_ch1_checkpoint', { exact: true })).toHaveCount(0)
  await expect(page.getByText('Planner', { exact: true })).toHaveCount(0)
  await expect(page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy()

  await page.getByRole('button', { name: '关闭章节', exact: true }).click()
  await expect(page.getByRole('complementary', { name: '旅程指引' })).toBeVisible()
  await page.getByRole('button', { name: '进入结算', exact: true }).click()
  const settlementDialog = page.getByRole('dialog', { name: '世界线结算' })
  await expect(settlementDialog).toBeVisible()
  await expect(settlementDialog).toContainText('夜家大堂·命运转折')
  await expect(settlementDialog).toContainText('预计结算积分')
  await expect(settlementDialog).toContainText('100')
  await expect(settlementDialog.getByRole('button', { name: '确认结算', exact: true })).toBeVisible()
  await expect(page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy()

  await settlementDialog.getByRole('button', { name: '确认结算', exact: true }).click()
  await expect(settlementDialog).toContainText('已结算')
  await expect(settlementDialog).toContainText('本次获得积分')
  await expect(settlementDialog.getByRole('button', { name: '返回系统空间', exact: true })).toBeVisible()

  await settlementDialog.getByRole('button', { name: '返回系统空间', exact: true }).click()
  await expect(page.getByRole('main', { name: 'SystemSpace 世界中转站' })).toBeVisible()
  await expect(page.getByText('已结算', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('你的跨章旅程', { exact: true })).toHaveCount(0)

  await page.reload()
  await expect(page.getByRole('main', { name: 'SystemSpace 世界中转站' })).toBeVisible()
  await expect(page.getByText('已结算', { exact: true }).first()).toBeVisible()
  await expect(page.getByText('你的跨章旅程', { exact: true })).toHaveCount(0)
  await expect(page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBeTruthy()
})

test('结算后进入第6–10章并保留父子世界线', async ({ page }) => {
  const childSessionId = 'e2e-canonical-child'
  let transitionRequests = 0

  const nextWorld = {
    package_id: 'first_crazy_ch5_checkpoint',
    novel: world.novel,
    scenario: '原著时间线 · 第6—10章',
    source: 'builtin',
    anchor: '夜家大堂对峙开始',
    source_chapters: [6, 7, 8, 9, 10],
    player_identity: '快穿者 · 夜轻歌',
    manifest: {
      entry_kind: 'canonical_checkpoint',
      checkpoint_chapter: 5,
      target_chapters: [6, 7, 8, 9, 10],
      character_count: 9,
      location_count: 8,
    },
  }

  const childState = {
    ...state,
    version: 1,
    flags: { ...state.flags, 'canonical.checkpoint_chapter': 5 },
  }
  const childSessionPayload = {
    status: 'ok',
    session_id: childSessionId,
    default_actor: 'night',
    state: childState,
    world_meta: {
      ...worldMeta,
      scenario: nextWorld.scenario,
    },
    save: {
      ...save('active'),
      session_id: childSessionId,
      name: '原著时间线 · 第6—10章世界线',
      world_package_id: 'first_crazy_ch5_checkpoint',
      parent_session_id: sessionId,
      depth: 1,
      chapter_label: '第 6–10 章',
      chapter_access: [],
    },
    turns: [],
    settlement: null,
  }

  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname

    if (path === '/api/worlds' && request.method() === 'GET') {
      return json(route, { status: 'ok', worlds: [world, nextWorld] })
    }
    if (path === '/api/saves' && request.method() === 'GET') {
      const list = [save('settled')]
      if (transitionRequests > 0) {
        list.push({
          ...childSessionPayload.save,
          updated_at: '2026-08-25T13:00:00+00:00',
        })
      }
      return json(route, { status: 'ok', saves: list })
    }
    if (path === '/api/session' && request.method() === 'GET') {
      const requested = url.searchParams.get('session')
      if (requested === childSessionId) return json(route, childSessionPayload)
      return json(route, sessionPayload())
    }
    if (path === '/api/player-view' && request.method() === 'GET') {
      return json(route, playerViewPayload())
    }
    if (path === `/api/world-runs/${childSessionId}/dashboard` && request.method() === 'GET') {
      return json(route, dashboardPayload('available'))
    }
    if (path === `/api/world-runs/${sessionId}/dashboard` && request.method() === 'GET') {
      return json(route, dashboardPayload('settled'))
    }
    if (path === '/api/joint-plans' && request.method() === 'GET') {
      return json(route, { status: 'ok', plans: [] })
    }
    if (path === `/api/world-runs/${sessionId}/settlement` && request.method() === 'GET') {
      return json(route, {
        ...dashboardPayload('settled'),
        status: 'settled',
        session_id: sessionId,
        ending,
        settlement: settlement('settled'),
        reward: { reward_points: 100 },
        state_version: 2,
        world_version: 2,
      })
    }
    if (
      path === `/api/world-runs/${sessionId}/transitions`
      && request.method() === 'POST'
    ) {
      transitionRequests += 1
      const body = request.postDataJSON()
      expect(String(body.idempotency_key || '').length).toBeGreaterThan(8)
      return json(route, {
        status: 'ok',
        transition: {
          created: true,
          reused: false,
          child_session_id: childSessionId,
          target_package_id: 'first_crazy_ch5_checkpoint',
          parent_session_id: sessionId,
          campaign_id: 'campaign_e2e',
          depth: 1,
        },
        inheritance_summary: [
          { title: 'flag', text: 'canonical.hall_summons_issued' },
        ],
        inheritance_entries: [
          { kind: 'flag', path: 'canonical.hall_summons_issued', applied: true },
          { kind: 'flag', path: 'canonical.lin_warning_done', applied: false, reason: '父世界线未确认该持久事实' },
        ],
        dashboard: dashboardPayload('available').dashboard,
        parent_settlement: settlement('settled'),
      })
    }

    return json(route, { status: 'ok' })
  })

  await page.goto('/')
  await expect(page.getByRole('main', { name: 'SystemSpace 世界中转站' })).toBeVisible()

  // 从系统空间继续已结算的父世界线，再通过演化入口打开结算。
  await page.getByRole('button', { name: '继续旅程', exact: true }).click()
  const evolutionEntry = page.getByRole('button', { name: '查看世界线结算', exact: true })
  await expect(evolutionEntry).toBeVisible()
  await evolutionEntry.click()
  const settlementDialog = page.getByRole('dialog', { name: '世界线结算' })
  await expect(settlementDialog).toBeVisible()
  await expect(settlementDialog).toContainText('《第一狂妃》第 6–10 章')
  await expect(settlementDialog).toContainText('进入下一章将继承')

  // 双击保护与单请求语义由后端幂等约束保证；这里只发一次请求。
  const ctaLabel = /进入下一章|继续下一章/
  const enterNext = settlementDialog.getByRole('button', { name: ctaLabel }).first()
  await expect(enterNext).toBeVisible()
  await enterNext.click()

  // 成功后结算关闭并载入子世界线。
  await expect(settlementDialog).toBeHidden()
  expect(transitionRequests).toBe(1)

  await page.reload()
  await expect(page.getByRole('main', { name: 'SystemSpace 世界中转站' })).toBeVisible()
  // 父子世界线在同一旅程下分组展示。
  await expect(page.getByText('2 个章节世界线')).toBeVisible()
  await expect(page.getByText('承接上一章 · 深度 1').first()).toBeVisible()
})
