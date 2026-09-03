import { expect, test } from '@playwright/test'

const sessionId = 'worldline-reader-session'
const packageId = 'worldline-reader-world'

function state(version = 2) {
  return {
    timeline_id: 'worldline-reader-timeline',
    version,
    world_time: '深夜',
    current_scene_id: 'courtyard',
    flags: {},
    characters: {
      protagonist: {
        character_id: 'protagonist',
        display_name: '夜轻歌',
        location_id: 'courtyard',
        is_alive: true,
      },
      rival: {
        character_id: 'rival',
        display_name: '夜清清',
        location_id: 'courtyard',
        is_alive: true,
      },
    },
    locations: {
      courtyard: { location_id: 'courtyard', display_name: '华容巷' },
    },
    items: {},
    relations: [],
    plot: {},
  }
}

function readyPassage(overrides = {}) {
  return {
    passage_id: 'passage-1',
    chapter: 1,
    order: 1,
    entry_id: 'worldline-book:chapter:1',
    entry_revision: 1,
    title: '雨夜分岔',
    paragraphs: [
      '雨丝沿着青瓦垂落，华容巷里只剩灯影在风中摇晃。',
      '夜轻歌越过积水，抬眼看向守在门前的夜清清。',
      '夜轻歌说：“这条路，从现在起由我自己选。”',
    ],
    dialogues: [{ speaker_id: 'protagonist', line: '这条路，从现在起由我自己选。', to_id: 'rival' }],
    system_hints: ['夜轻歌与原著命运的距离正在扩大。'],
    source_event_ids: ['event-1', 'event-2'],
    from_world_version: 1,
    to_world_version: 2,
    generation_kind: 'narrative_output',
    generation_status: 'ready',
    revision: 1,
    ...overrides,
  }
}

function storyBeat(overrides = {}) {
  return {
    event_id: 'event-1',
    chapter: 1,
    title: '雨夜试探',
    narrative: '夜轻歌先在雨中试探着向前一步，世界记录了她真正做出的选择。',
    source: 'player',
    alignment_status: 'new',
    dialogues: [],
    system_hints: [],
    world_version: 1,
    ...overrides,
  }
}

function playerView(overrides = {}) {
  return {
    status: 'ok',
    schema_version: 'player_story_view.v2',
    current_story_chapter: 1,
    canonical_baseline_available: false,
    manuscript: { manuscript_id: 'manuscript-1', total_passages: 1, current_revision: 1 },
    novel_passages: [readyPassage()],
    activity_items: [
      { activity_id: 'npc-1', category: 'npc_action', actor_name: '夜清清', summary: '夜清清退到门侧，暗中观察。', chapter: 1, world_version: 1 },
      { activity_id: 'world-1', category: 'world_change', title: '巷口局势', summary: '华容巷的出口已经被雨幕遮住。', chapter: 1, world_version: 2 },
      { activity_id: 'system-1', category: 'system', title: '认知记录', summary: '角色认知边界已更新。', chapter: 1, world_version: 2 },
    ],
    story_beats: [storyBeat()],
    original_chapters: [],
    comparison: [],
    unmatched_beats: [],
    metrics: {},
    ...overrides,
  }
}

async function installWorldRoutes(page, initialPlayerView) {
  let currentPlayerView = initialPlayerView
  const sessionPayload = {
    status: 'ok',
    session_id: sessionId,
    default_actor: 'protagonist',
    state: state(),
    world_meta: {
      novel: '世界线测试小说',
      scenario: '华容巷夜局',
      source_chapters: [1],
    },
    save: {
      session_id: sessionId,
      name: '世界线阅读测试',
      world_package_id: packageId,
      version: 2,
      updated_at: '',
    },
    turns: [],
    settlement: null,
  }

  await page.route('**/api/worlds', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'ok',
      worlds: [{ package_id: packageId, novel: '世界线测试小说', scenario: '华容巷夜局' }],
    }),
  }))
  await page.route('**/api/books', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'ok',
      books: [{ book_id: 'worldline-book', novel: '世界线测试小说', chapter_count: 1, revision: 1 }],
    }),
  }))
  await page.route('**/api/books/worldline-book/chapters', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      status: 'ok',
      chapters: [{
        book_id: 'worldline-book',
        entry_id: 'worldline-book:chapter:1',
        chapter_number: 1,
        title: '华容巷夜局',
        published: true,
        canonical: false,
        package_id: packageId,
      }],
    }),
  }))
  await page.route('**/api/saves', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', saves: [] }),
  }))
  await page.route('**/api/start', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(sessionPayload),
  }))
  await page.route(`**/api/player-view?session=${sessionId}`, async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(currentPlayerView),
  }))
  await page.route(`**/api/world-runs/${sessionId}/dashboard`, async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', dashboard: {} }),
  }))
  await page.route(`**/api/joint-plans?session=${sessionId}`, async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'ok', plans: [] }),
  }))

  return {
    setPlayerView(nextView) {
      currentPlayerView = nextView
    },
  }
}

async function enterWorld(page) {
  await page.goto('/')
  await page.getByRole('button', { name: '选择世界', exact: true }).click()
  const selector = page.getByRole('dialog', { name: '选择小说与进入章节' })
  await selector.getByRole('button', { name: /第 1 章/ }).click()
  await selector.getByRole('button', { name: '进入本章世界线' }).click()
  await expect(page.getByRole('tabpanel', { name: '世界演化' })).toBeVisible()
}

test('世界演化与我的小说作为平级主页面切换并保留草稿', async ({ page }) => {
  await installWorldRoutes(page, playerView())
  await enterWorld(page)

  const navigation = page.getByRole('tablist', { name: '玩家主页面' })
  const worldTab = navigation.getByRole('tab', { name: '世界演化' })
  const novelTab = navigation.getByRole('tab', { name: /我的小说/ })
  const worldPanel = page.getByRole('tabpanel', { name: '世界演化' })
  const novelPanel = page.getByRole('tabpanel', { name: /我的小说/ })

  await expect(worldTab).toHaveAttribute('aria-selected', 'true')
  await expect(worldTab).toHaveAttribute('tabindex', '0')
  await expect(novelTab).toHaveAttribute('aria-selected', 'false')
  await expect(novelTab).toHaveAttribute('tabindex', '-1')
  await expect(worldPanel).toContainText('雨夜试探')
  await expect(worldPanel).toContainText('世界记录了她真正做出的选择')
  await expect(novelPanel).toBeHidden()

  await page.getByRole('button', { name: '穿越干预' }).click()
  const input = worldPanel.getByRole('textbox', { name: '你想做什么' })
  await input.fill('先留在雨里观察夜清清')

  await worldTab.focus()
  await worldTab.press('ArrowRight')
  await expect(novelTab).toBeFocused()
  await expect(novelTab).toHaveAttribute('aria-selected', 'true')
  await expect(novelPanel).toBeVisible()
  const reader = novelPanel.getByRole('region', { name: '我的小说正文' })
  await expect(reader).toContainText('雨夜分岔')
  await expect(reader).toContainText('雨丝沿着青瓦垂落')
  await expect(reader).toContainText('这条路，从现在起由我自己选')
  await expect(reader.locator('.story-beat')).toHaveCount(0)

  await novelTab.press('Home')
  await expect(worldTab).toBeFocused()
  await expect(worldPanel).toBeVisible()
  await expect(input).toHaveValue('先留在雨里观察夜清清')

  await worldTab.press('End')
  await expect(novelTab).toBeFocused()
  await novelTab.press('ArrowLeft')
  await expect(worldTab).toBeFocused()
})

test('世界动态抽屉保留分类键盘操作和焦点恢复', async ({ page }) => {
  await installWorldRoutes(page, playerView())
  await enterWorld(page)

  const worldPanel = page.getByRole('tabpanel', { name: '世界演化' })
  const activityTrigger = worldPanel.getByRole('button', { name: /查看世界动态/ })
  await activityTrigger.click()
  const drawer = page.getByRole('dialog', { name: '世界动态' })
  await expect(drawer).toBeVisible()
  await expect(drawer.getByRole('tab', { name: /NPC 行动/ })).toHaveAttribute('aria-selected', 'true')
  await expect(drawer).toContainText('夜清清退到门侧')

  await drawer.getByRole('tab', { name: /NPC 行动/ }).press('ArrowRight')
  await expect(drawer.getByRole('tab', { name: /世界变化/ })).toBeFocused()
  await expect(drawer).toContainText('华容巷的出口已经被雨幕遮住')

  await drawer.getByRole('tab', { name: /世界变化/ }).press('End')
  await expect(drawer.getByRole('tab', { name: /系统记录/ })).toBeFocused()
  await expect(drawer).toContainText('角色认知边界已更新')

  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(activityTrigger).toBeFocused()
})

test('失败稿件只在我的小说显示状态，世界演化仍保留权威事件', async ({ page }) => {
  await installWorldRoutes(page, playerView({
    manuscript: { manuscript_id: 'manuscript-2', total_passages: 1, current_revision: 0 },
    novel_passages: [{
      passage_id: 'passage-failed',
      chapter: 1,
      order: 1,
      paragraphs: [],
      source_event_ids: ['event-1'],
      from_world_version: 1,
      to_world_version: 1,
      generation_kind: 'deterministic',
      generation_status: 'failed',
      revision: 0,
    }],
  }))
  await enterWorld(page)

  const worldPanel = page.getByRole('tabpanel', { name: '世界演化' })
  await expect(worldPanel).toContainText('世界记录了她真正做出的选择')

  await page.getByRole('tab', { name: '我的小说' }).click()
  const reader = page.getByRole('region', { name: '我的小说正文' })
  await expect(reader).toContainText('这一段正文生成失败')
  await expect(reader).toContainText('已提交的世界事件仍然保留')
  await expect(reader.locator('.continuous-manuscript')).toHaveCount(1)
  await expect(reader.locator('.story-beat')).toHaveCount(0)
})

test('同一世界线刷新出新 passage 时提示新正文并在进入后清除', async ({ page }) => {
  const routes = await installWorldRoutes(page, playerView({
    manuscript: { manuscript_id: 'manuscript-3', total_passages: 0, current_revision: 0 },
    novel_passages: [],
  }))
  await enterWorld(page)

  routes.setPlayerView(playerView({
    manuscript: { manuscript_id: 'manuscript-3', total_passages: 1, current_revision: 1 },
    novel_passages: [readyPassage({ passage_id: 'passage-new', title: '新写入的雨夜' })],
  }))

  await page.getByRole('button', { name: '穿越干预' }).click()
  await page.route('**/api/turn', async (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ status: 'committed', state: state(3), narrative: null }),
  }))
  const input = page.getByRole('textbox', { name: '你想做什么' })
  await input.fill('向巷口走一步')
  await input.press('Control+Enter')

  const novelTab = page.getByRole('tab', { name: /我的小说/ })
  await expect(novelTab).toContainText('新正文')
  await novelTab.click()
  await expect(page.getByRole('region', { name: '我的小说正文' })).toContainText('新写入的雨夜')
  await expect(novelTab).not.toContainText('新正文')
})

test('同章 passage 聚合为一次标题且正文不重复对白或泄露内部标识', async ({ page }) => {
  await installWorldRoutes(page, playerView({
    manuscript: { manuscript_id: 'manuscript-safe', total_passages: 3, current_revision: 3 },
    novel_passages: [
      readyPassage({
        passage_id: 'passage-safe-1',
        order: 1,
        paragraphs: ['雨丝沿着青瓦垂落。', '夜轻歌说：“只读正文中的这一遍对白。”'],
        dialogues: [{ speaker_id: 'char_hidden', speaker_name: '夜轻歌', line: '只读正文中的这一遍对白。' }],
        source_event_ids: ['event-safe-1'],
      }),
      readyPassage({
        passage_id: 'passage-safe-2',
        order: 2,
        title: '不应成为第二个章节头',
        paragraphs: ['她越过积水，能力留下的痕迹已经散去。'],
        source_event_ids: ['event-safe-2'],
      }),
      readyPassage({
        passage_id: 'passage-continuation',
        entry_id: '',
        entry_revision: 0,
        chapter: null,
        order: 3,
        title: '内部 passage 标题',
        paragraphs: ['巷外的雨还没有停。'],
        source_event_ids: ['event-safe-3'],
      }),
    ],
  }))
  await enterWorld(page)
  await page.getByRole('tab', { name: /我的小说/ }).click()

  const reader = page.getByRole('region', { name: '我的小说正文' })
  await expect(reader.getByRole('heading', { name: '雨夜分岔' })).toHaveCount(1)
  await expect(reader.locator('.passage-heading')).toHaveCount(2)
  await expect(reader.getByRole('heading', { name: '世界线续篇' })).toHaveCount(1)
  await expect(reader.getByText('只读正文中的这一遍对白', { exact: false })).toHaveCount(1)
  await expect(reader.locator('blockquote')).toHaveCount(0)
  await expect(reader).not.toContainText('char_')
  await expect(reader).not.toContainText('ability:')
  await expect(reader).not.toContainText('canonical.')
})

test('已生成正文与 pending failed 段落按顺序同时保留', async ({ page }) => {
  await installWorldRoutes(page, playerView({
    manuscript: { manuscript_id: 'manuscript-4', total_passages: 3, current_revision: 1 },
    novel_passages: [
      readyPassage({ order: 1 }),
      {
        passage_id: 'passage-pending',
        entry_id: 'worldline-book:chapter:1',
        entry_revision: 1,
        chapter: 1,
        order: 2,
        paragraphs: [],
        source_event_ids: ['event-3'],
        generation_status: 'pending',
        revision: 0,
      },
      {
        passage_id: 'passage-failed',
        entry_id: 'worldline-book:chapter:1',
        entry_revision: 1,
        chapter: 1,
        order: 3,
        paragraphs: [],
        source_event_ids: ['event-4'],
        generation_status: 'failed',
        revision: 0,
      },
    ],
  }))
  await enterWorld(page)
  await page.getByRole('tab', { name: /我的小说/ }).click()

  const reader = page.getByRole('region', { name: '我的小说正文' })
  await expect(reader).toContainText('雨夜分岔')
  await expect(reader).toContainText('这一段正文正在整理')
  await expect(reader).toContainText('这一段正文生成失败')
  await expect(reader).toContainText('部分小说正文尚未就绪')
  await expect(reader).toContainText('失败部分不会影响已经提交的世界事件')
  const states = reader.locator('.passage-generation-state')
  await expect(states).toHaveCount(2)
  await expect(states.nth(0)).toContainText('正在整理')
  await expect(states.nth(1)).toContainText('生成失败')
})

test('旧稿质量警告提供可访问的重写入口', async ({ page }) => {
  await installWorldRoutes(page, playerView({
    novel_passages: [readyPassage({
      passage_id: 'legacy-passage',
      generation_kind: 'legacy',
      quality_issues: ['旧稿含内部标识，读者版已安全泛化'],
      paragraphs: ['夜轻歌抬眼看向雨幕。'],
    })],
  }))
  await enterWorld(page)
  await page.getByRole('tab', { name: /我的小说/ }).click()

  const reader = page.getByRole('region', { name: '我的小说正文' })
  await expect(reader).toContainText('这段旧稿已使用安全读者版')
  await expect(reader.getByRole('button', { name: /重写旧稿/ })).toBeVisible()
})

test('重写旧稿携带期望 revision 并刷新为新正文', async ({ page }) => {
  const legacyPassage = readyPassage({
    passage_id: 'legacy-passage',
    generation_kind: 'legacy',
    quality_issues: ['旧稿含内部标识，读者版已安全泛化'],
    paragraphs: ['夜轻歌抬眼看向雨幕。'],
    revision: 3,
  })
  const routes = await installWorldRoutes(page, playerView({
    manuscript: { manuscript_id: 'manuscript-rewrite', total_passages: 1, current_revision: 3 },
    novel_passages: [legacyPassage],
  }))
  let rewriteRequest = null
  await page.route('**/api/manuscript/passages/legacy-passage/retry', async (route) => {
    rewriteRequest = route.request().postDataJSON()
    routes.setPlayerView(playerView({
      manuscript: { manuscript_id: 'manuscript-rewrite', total_passages: 1, current_revision: 4 },
      novel_passages: [readyPassage({
        ...legacyPassage,
        generation_kind: 'llm',
        quality_issues: [],
        paragraphs: ['夜轻歌推开院门，雨声随风落在她身后。'],
        revision: 4,
      })],
    }))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok' }),
    })
  })

  await enterWorld(page)
  await page.getByRole('tab', { name: /我的小说/ }).click()
  const reader = page.getByRole('region', { name: '我的小说正文' })
  await reader.getByRole('button', { name: /重写旧稿/ }).click()

  await expect.poll(() => rewriteRequest).toEqual({
    session_id: sessionId,
    rewrite_ready: true,
    expected_revision: 3,
  })
  await expect(reader).toContainText('夜轻歌推开院门，雨声随风落在她身后。')
  await expect(reader).not.toContainText('夜轻歌抬眼看向雨幕。')
  await expect(reader).not.toContainText('这段旧稿已使用安全读者版')
  await expect(reader.getByRole('button', { name: /重写旧稿/ })).toHaveCount(0)
})

test('版本记录可对比并只切换当前正文指针', async ({ page }) => {
  let selectedRevision = 2
  let selectRequest = null
  const routes = await installWorldRoutes(page, playerView({
    manuscript: { manuscript_id: 'manuscript-history', total_passages: 1, current_revision: 2 },
    novel_passages: [readyPassage({
      revision: 2,
      paragraphs: ['新版本正文：她推开了院门。'],
    })],
  }))

  await page.route(`**/api/manuscript/passages/passage-1/revisions?session=${sessionId}`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'ok',
        passage_id: 'passage-1',
        current_revision: selectedRevision,
        revisions: [
          {
            revision_number: 1,
            source: 'deterministic',
            title: '旧版本',
            paragraphs: ['旧版本正文：她停在门前。'],
            text: '旧版本正文：她停在门前。',
            selected: selectedRevision === 1,
          },
          {
            revision_number: 2,
            source: 'llm',
            title: '新版本',
            paragraphs: ['新版本正文：她推开了院门。'],
            text: '新版本正文：她推开了院门。',
            selected: selectedRevision === 2,
          },
        ],
      }),
    })
  })
  await page.route('**/api/manuscript/passages/passage-1/select-revision', async (route) => {
    selectRequest = route.request().postDataJSON()
    selectedRevision = 1
    routes.setPlayerView(playerView({
      manuscript: { manuscript_id: 'manuscript-history', total_passages: 1, current_revision: 1 },
      novel_passages: [readyPassage({
        revision: 1,
        paragraphs: ['旧版本正文：她停在门前。'],
      })],
    }))
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'ok' }),
    })
  })

  await enterWorld(page)
  await page.getByRole('tab', { name: /我的小说/ }).click()
  const reader = page.getByRole('region', { name: '我的小说正文' })
  await reader.getByRole('button', { name: '版本记录' }).click()
  await expect(reader).toContainText('旧版本正文：她停在门前')
  await expect(reader).toContainText('新版本正文：她推开了院门')
  await expect(reader).toContainText('当前使用')

  await reader.getByRole('button', { name: '切换到此版本' }).click()
  await expect.poll(() => selectRequest).toEqual({
    session_id: sessionId,
    revision_number: 1,
    expected_revision: 2,
  })
  await expect(reader).toContainText('旧版本正文：她停在门前')
  await expect(reader).toContainText('当前版本 v1')
})
