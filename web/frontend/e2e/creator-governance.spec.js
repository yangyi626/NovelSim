import { expect, test } from '@playwright/test'

async function login(page, username) {
  await page.getByLabel('用户名').fill(username)
  await page.getByLabel('密码').fill('e2e-password')
  await page.getByRole('button', { name: '登录创作台' }).click()
  await expect(page.getByText('世界创作台', { exact: true })).toBeVisible()
}

async function logout(page) {
  await page.getByRole('button', { name: '退出', exact: true }).click()
  await expect(page.getByRole('heading', { name: '创作者账户' })).toBeVisible()
}

test('创作者、审核者和发布者按 RBAC 完成发布闭环', async ({ page }) => {
  const browserErrors = []
  page.on('console', (message) => {
    if (message.type() === 'error') browserErrors.push(message.text())
  })
  page.on('pageerror', (error) => browserErrors.push(error.message))

  await page.goto('/#/creator')
  await login(page, 'creator')
  await page.locator('.package-card', { hasText: 'huarong_lane' }).click()
  await page.getByRole('button', { name: '另存为新版本' }).click()
  await expect(page.getByText('huarong_lane_v2', { exact: false }).first()).toBeVisible()
  await page.getByPlaceholder('审核说明（可选）').fill('E2E 创作者提交')
  await page.getByRole('button', { name: '提交审核', exact: true }).click()
  await expect(page.getByText('待审核', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '批准', exact: true })).toHaveCount(0)
  await logout(page)

  await login(page, 'reviewer')
  await page.locator('.package-card', { hasText: 'huarong_lane_v2' }).click()
  await page.getByPlaceholder('审核说明（可选）').fill('E2E 审核通过')
  await page.getByRole('button', { name: '批准', exact: true }).click()
  await expect(page.getByText('已批准', { exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '正式发布', exact: true })).toHaveCount(0)
  await logout(page)

  await login(page, 'publisher')
  await page.locator('.package-card', { hasText: 'huarong_lane_v2' }).click()
  await page.getByPlaceholder('审核说明（可选）').fill('E2E 正式发布')
  await page.getByRole('button', { name: '正式发布', exact: true }).click()
  await expect(page.getByText('已发布', { exact: true }).first()).toBeVisible()
  await page.getByRole('button', { name: '刷新审核审计' }).click()
  await expect(page.getByText(/world_package\.review\.published/)).toBeVisible()

  expect(browserErrors).toEqual([])
})
