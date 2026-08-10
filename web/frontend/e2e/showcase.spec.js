import { expect, test } from '@playwright/test'

test('无需 API Key 的一键演示展示规则拒绝与多 Agent 证据链', async ({ page }) => {
  const browserErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.goto('/')
  await page.getByRole('button', { name: '一键演示', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '选择一条一键演示' })
  await expect(dialog).toContainText('无需 API Key')

  const illegalCard = page.locator('.demo-card').filter({ hasText: '非法行动：开飞机' })
  await illegalCard.getByRole('button', { name: '运行演示' }).click()
  await expect(page.locator('.demo-evidence-banner')).toContainText('非法行动 · 世界规则拦截')
  await expect(page.getByText('WORLD_CONCEPT_UNAVAILABLE', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.version-tag')).toContainText('v0')

  await page.getByRole('button', { name: '一键演示', exact: true }).click()
  const multiAgentCard = page.locator('.demo-card').filter({ hasText: '自主行为：传播与结盟' })
  await multiAgentCard.getByRole('button', { name: '运行演示' }).click()
  await expect(page.locator('.demo-evidence-banner')).toContainText('多 Agent · 信息传播与结盟')
  await expect(page.locator('.demo-evidence-banner')).toContainText('v5')
  await expect(page.locator('.tool-trace-card')).toHaveCount(5)
  await expect(page.getByText('管家与盟友最终形成防卫联盟。')).toBeVisible()
  await expect(page.locator('.version-tag')).toContainText('v5')

  expect(browserErrors).toEqual([])
})
