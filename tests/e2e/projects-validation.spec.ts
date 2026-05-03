import { expect, test } from '@playwright/test';

/**
 * Slug inline validation — 모달에서 4 케이스를 hard assert:
 *   - 대소문자 (MyGame)
 *   - 공백/특수문자 (my project!)
 *   - 너무 짧음 (a)
 *   - reserved (admin)
 *
 * Suggestion 텍스트가 helper text 에 포함되어야 한다 — UI 의 "올바른 형태로
 * 고쳐주는" 가이드 역할.
 */

test.describe('+ New Project slug validation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/app/projects');
    await page.getByTestId('new-project-btn').click();
  });

  test('대문자는 거부 + 소문자 suggestion', async ({ page }) => {
    const slug = page.locator('input[placeholder="wooridul-factory"]');
    await slug.fill('MyGame');
    const helper = page.locator('text=Lowercase, hyphens');
    await expect(helper).toBeVisible();
    await expect(helper).toContainText('mygame');
    // Create disabled (display_name 도 비어있고 slugError 가 있어 비활성).
    await expect(page.getByRole('button', { name: 'Create' })).toBeDisabled();
  });

  test('공백/특수문자 거부 + dash 변환 suggestion', async ({ page }) => {
    const slug = page.locator('input[placeholder="wooridul-factory"]');
    await slug.fill('my project!');
    const helper = page.locator('text=Lowercase, hyphens');
    await expect(helper).toBeVisible();
    await expect(helper).toContainText('my-project');
  });

  test('너무 짧은 slug 거부', async ({ page }) => {
    const slug = page.locator('input[placeholder="wooridul-factory"]');
    await slug.fill('a');
    const helper = page.locator('text=Lowercase, hyphens');
    await expect(helper).toBeVisible();
  });

  test('reserved word 거부 (admin)', async ({ page }) => {
    const slug = page.locator('input[placeholder="wooridul-factory"]');
    await slug.fill('admin');
    const helper = page.locator('text=Lowercase, hyphens');
    await expect(helper).toBeVisible();
  });
});
