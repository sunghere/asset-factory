import { expect, test } from '@playwright/test';

/**
 * Critical user flow coverage (design doc §"User Journey Storyboards"):
 *  1. 신규 사용자 첫 batch — picker → "+ New" 모달 → 자동 선택.
 *  2. /projects 진입 → archive → BatchNew picker 에서 사라짐.
 *  3. /projects 의 purge dry-run → confirm → DB row 부재.
 *  4. archive 후 /assets 에서 read 정상 + archived 뱃지.
 *
 * 각 spec 은 unique slug 를 써서 다른 spec 과 충돌하지 않는다 (timestamp 접
 * 미사). dev server 가 같은 DB 를 공유하므로 isolation 은 slug 명으로만.
 */

const ts = () => Date.now().toString(36);

test('flow 1 — 신규 사용자 첫 batch (picker → +New 모달 → 자동 선택)', async ({ page }) => {
  const slug = `e2e-flow1-${ts()}`;

  await page.goto('/app/batches/new');
  // picker 옆 + New 버튼.
  await page.getByTestId('picker-new-project').click();

  // 모달 — slug + display_name + Create.
  const slugInput = page.locator('input[placeholder="wooridul-factory"]');
  await expect(slugInput).toBeVisible();
  await slugInput.fill(slug);
  await page.locator('input[placeholder="Wooridul Factory"]').fill(`E2E ${slug}`);
  await page.getByRole('button', { name: 'Create' }).click();

  // 모달 닫히고 picker 가 새 slug 선택.
  await expect(slugInput).toBeHidden();
  const picker = page.locator('select.input').first();
  await expect(picker).toHaveValue(slug);
});

test('flow 2 — archive 후 BatchNew picker 에서 사라짐', async ({ page }) => {
  const slug = `e2e-flow2-${ts()}`;

  // /projects 진입 → 신규 등록 (이번에는 /projects 의 + New Project).
  await page.goto('/app/projects');
  await page.getByTestId('new-project-btn').click();
  await page.locator('input[placeholder="wooridul-factory"]').fill(slug);
  await page.locator('input[placeholder="Wooridul Factory"]').fill(`E2E ${slug}`);
  await page.getByRole('button', { name: 'Create' }).click();

  // 행이 보임.
  await expect(page.locator('text=' + slug)).toBeVisible();

  // ⋯ → Archive.
  await page.locator(`text=${slug}`).first().scrollIntoViewIfNeeded();
  const row = page.locator('div[role="listitem"]').filter({ hasText: slug }).first();
  await row.getByRole('button', { name: 'actions' }).click();
  await page.getByRole('button', { name: 'Archive' }).click();

  // BatchNew picker 에 더 이상 안 나옴.
  await page.goto('/app/batches/new');
  const opts = page.locator('select.input').first().locator('option');
  await expect(opts.filter({ hasText: slug })).toHaveCount(0);
});

test('flow 3 — archive → purge dry-run → confirm → DB 부재', async ({ page, request }) => {
  const slug = `e2e-flow3-${ts()}`;

  // setup: 등록 + archive.
  await page.goto('/app/projects');
  await page.getByTestId('new-project-btn').click();
  await page.locator('input[placeholder="wooridul-factory"]').fill(slug);
  await page.locator('input[placeholder="Wooridul Factory"]').fill(`E2E ${slug}`);
  await page.getByRole('button', { name: 'Create' }).click();

  await page.locator('text=' + slug).first().waitFor();
  const row = page.locator('div[role="listitem"]').filter({ hasText: slug }).first();
  await row.getByRole('button', { name: 'actions' }).click();
  await page.getByRole('button', { name: 'Archive' }).click();

  // archived 만 보이게 토글.
  await page.getByLabel('Show archived').check();
  await page.locator('text=' + slug).first().waitFor();

  // ⋯ → Purge…
  const archivedRow = page.locator('div[role="listitem"]').filter({ hasText: slug }).first();
  await archivedRow.getByRole('button', { name: 'actions' }).click();
  await page.getByRole('button', { name: /Purge/ }).click();

  // 모달 — slug 타이핑 후 destructive 버튼 enable.
  const confirmInput = page.locator('input[placeholder="' + slug + '"]');
  await expect(confirmInput).toBeVisible();
  await confirmInput.fill(slug);
  await page.getByRole('button', { name: 'Permanently delete' }).click();

  // backend 가 DB 에서 제거됐는지 직접 확인 (read-after-write).
  await page.waitForTimeout(500);
  const r = await request.get(`/api/projects/${slug}`);
  expect(r.status()).toBe(404);
});

test('flow 4 — archive 후 /assets 에서 read 정상', async ({ page }) => {
  const slug = `e2e-flow4-${ts()}`;
  await page.goto('/app/projects');
  await page.getByTestId('new-project-btn').click();
  await page.locator('input[placeholder="wooridul-factory"]').fill(slug);
  await page.locator('input[placeholder="Wooridul Factory"]').fill(`E2E ${slug}`);
  await page.getByRole('button', { name: 'Create' }).click();
  await page.locator('text=' + slug).first().waitFor();

  const row = page.locator('div[role="listitem"]').filter({ hasText: slug }).first();
  await row.getByRole('button', { name: 'actions' }).click();
  await page.getByRole('button', { name: 'Archive' }).click();

  // /assets 사이드바 필터 — archived 옵션이 dim 으로 노출되어야 한다.
  await page.goto('/app/assets');
  const archivedOption = page.locator('label.row').filter({ hasText: slug });
  await expect(archivedOption).toBeVisible();
  // ⊘ 마크 (archived 표식) 가 라벨에 포함.
  await expect(archivedOption).toContainText('⊘');
});
