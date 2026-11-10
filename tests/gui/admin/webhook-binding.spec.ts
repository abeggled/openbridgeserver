/**
 * Playwright E2E for the WEBHOOK binding form (issue #1305, follow-up to #1256).
 *
 * One flow through the real Admin GUI:
 *  1. create a WEBHOOK instance with its own path prefix in the adapter view
 *  2. create a webhook binding on a DataPoint, set the webhook fields, save
 *  3. reopen the binding: fields persisted, both call URLs shown
 *  4. call the displayed URL for real → the DataPoint switches, and the call
 *     and rejection counters in the form go up
 *  5. reissue the token → the displayed URLs change, the old URL answers 404,
 *     the new one 204
 *  6. allowlist editor: edit a row, inline error for an invalid entry, the
 *     server normalises `10.38.111.21/16` to `10.38.0.0/16`
 *
 * The allowlist stays empty until the last step, so the calls from the test
 * runner (which reach OBS through the container's port mapping) are accepted.
 */

import { test, expect, type Page } from '@playwright/test'
import { apiDelete, apiGet, apiPost } from '../helpers'

type Instance = { id: string; name: string; running: boolean; connected: boolean }

async function openBinding(page: Page): Promise<void> {
  await page.getByTitle('Bearbeiten').click()
  await expect(page.locator('[data-testid="webhook-call-url"]')).toBeVisible({ timeout: 10_000 })
}

async function closeBinding(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Abbrechen' }).click()
  await expect(page.locator('[data-testid="webhook-slug"]')).toHaveCount(0)
}

async function saveBinding(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Speichern' }).click()
  await expect(page.locator('[data-testid="webhook-slug"]')).toHaveCount(0, { timeout: 10_000 })
}

test('Webhook: Instanz und Binding über die GUI, Aufruf, Token neu ausstellen, Allowlist', async ({ page, request }) => {
  // Unique per run and worker: prefixes and slugs must not collide with a
  // parallel run of this spec or with webhook data already on the instance.
  const stamp = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
  const prefix = `/e2e-hook-${stamp}`
  const slug = `e2e-bell-${stamp}`
  const instanceName = `E2E-Webhook-${stamp}`
  let instanceId: string | null = null

  const dp = await apiPost('/api/v1/datapoints', {
    name: `E2E-DP-Webhook-${stamp}`,
    data_type: 'BOOLEAN',
    tags: [],
  }) as { id: string }

  try {
    await test.step('WEBHOOK-Instanz in der Adapter-Ansicht anlegen', async () => {
      await page.goto('/adapters')
      await page.waitForLoadState('networkidle')
      await page.click('[data-testid="btn-new-instance"]')
      await page.selectOption('[data-testid="select-adapter-type"]', 'WEBHOOK')
      const prefixField = page.locator('[data-testid="config-field-path_prefix"]')
      await expect(prefixField).toBeVisible({ timeout: 5_000 })
      await page.fill('[data-testid="input-instance-name"]', instanceName)
      await prefixField.fill(prefix)
      await page.click('[data-testid="btn-save-instance"]')
      await expect(page.getByText(instanceName)).toBeVisible({ timeout: 8_000 })

      await expect
        .poll(async () => {
          const instances = await apiGet('/api/v1/adapters/instances') as Instance[]
          const created = instances.find(i => i.name === instanceName)
          instanceId = created?.id ?? null
          return created?.connected ?? false
        }, { timeout: 15_000 })
        .toBe(true)
    })

    await test.step('Webhook-Binding anlegen und webhook-spezifische Felder speichern', async () => {
      await page.goto(`/datapoints/${dp.id}`)
      await page.waitForLoadState('networkidle')
      await page.click('[data-testid="btn-add-binding"]')
      await page.selectOption('[data-testid="select-adapter-instance"]', instanceId!)
      await expect(page.locator('[data-testid="webhook-slug"]')).toBeVisible({ timeout: 5_000 })

      await page.fill('[data-testid="webhook-slug"]', slug)
      await expect(page.locator('[data-testid="webhook-method-GET"]')).toBeChecked()
      await expect(page.locator('[data-testid="webhook-method-POST"]')).not.toBeChecked()
      await page.selectOption('[data-testid="webhook-value-source"]', 'fixed')
      await page.fill('[data-testid="webhook-fixed-value"]', '1')
      await page.fill('[data-testid="webhook-debounce"]', '0')
      // A new binding has no token yet, so no URL is offered before the first save.
      await expect(page.locator('[data-testid="webhook-call-url"]')).toHaveCount(0)
      await saveBinding(page)
    })

    let queryUrl = ''
    let pathUrl = ''

    await test.step('Binding wieder öffnen: Felder gespeichert, beide Aufruf-URLs angezeigt', async () => {
      await openBinding(page)
      await expect(page.locator('[data-testid="webhook-slug"]')).toHaveValue(slug)
      await expect(page.locator('[data-testid="webhook-fixed-value"]')).toHaveValue('1')
      await expect(page.locator('[data-testid="webhook-debounce"]')).toHaveValue('0')

      queryUrl = await page.locator('[data-testid="webhook-call-url"]').inputValue()
      pathUrl = await page.locator('[data-testid="webhook-call-url-path"]').inputValue()
      const token = new URL(queryUrl).searchParams.get('token') ?? ''
      expect(token).toMatch(/^[A-Za-z0-9_-]{43}$/)
      expect(new URL(queryUrl).pathname).toBe(`${prefix}/${slug}`)
      expect(new URL(pathUrl).pathname).toBe(`${prefix}/${slug}/${token}`)
      await expect(page.locator('[data-testid="webhook-call-count"]')).toHaveText('0')
      await expect(page.locator('[data-testid="webhook-publish-count"]')).toHaveText('0')
      await closeBinding(page)
    })

    await test.step('Angezeigte URL real aufrufen: Datenpunkt schaltet, Zähler steigen', async () => {
      const accepted = await request.get(queryUrl)
      expect(accepted.status()).toBe(204)
      await expect
        .poll(async () => ((await apiGet(`/api/v1/datapoints/${dp.id}/value`)) as { value: unknown }).value, { timeout: 10_000 })
        .toBe(true)

      // A method the binding does not allow is turned away and attributed to it.
      expect((await request.post(queryUrl)).status()).toBe(404)
      // A wrong token cannot be attributed to a binding — only the instance sees it.
      expect((await request.get(`${new URL(queryUrl).origin}${prefix}/${slug}?token=wrong`)).status()).toBe(404)

      await openBinding(page)
      await expect(page.locator('[data-testid="webhook-call-count"]')).toHaveText('1')
      await expect(page.locator('[data-testid="webhook-publish-count"]')).toHaveText('1')
      await expect(page.locator('[data-testid="webhook-last-called"]')).not.toHaveText('nie')
      const bindingRejections = page.locator('[data-testid="webhook-rejections"]')
      await expect(bindingRejections).toContainText('1 Aufruf(e) abgewiesen')
      await expect(bindingRejections).toContainText('HTTP-Methode nicht erlaubt')
      const instanceRejections = page.locator('[data-testid="webhook-instance-rejections"]')
      await expect(instanceRejections).toContainText('Instanzweit abgewiesen: 2 Aufruf(e)')
      await expect(instanceRejections).toContainText('falsches Token')
    })

    await test.step('Token neu ausstellen: URLs aktualisiert, alte URL 404, neue 204', async () => {
      await page.click('[data-testid="webhook-rotate-token"]')
      const callUrl = page.locator('[data-testid="webhook-call-url"]')
      await expect(callUrl).not.toHaveValue(queryUrl, { timeout: 10_000 })
      await expect(page.locator('[data-testid="webhook-call-url-path"]')).not.toHaveValue(pathUrl)
      const newQueryUrl = await callUrl.inputValue()
      const newPathUrl = await page.locator('[data-testid="webhook-call-url-path"]').inputValue()
      const newToken = new URL(newQueryUrl).searchParams.get('token') ?? ''
      expect(new URL(newPathUrl).pathname).toBe(`${prefix}/${slug}/${newToken}`)

      expect((await request.get(queryUrl)).status()).toBe(404)
      expect((await request.get(pathUrl)).status()).toBe(404)
      expect((await request.get(newQueryUrl)).status()).toBe(204)
      expect((await request.get(newPathUrl)).status()).toBe(204)
    })

    await test.step('Allowlist-Editor: Zeile bearbeiten, Inline-Fehler, Normalisierung', async () => {
      await page.click('[data-testid="allowlist-add"]')
      const row = page.locator('[data-testid="allowlist-entry-0"]')
      await row.fill('10.38.999.1/16')
      await expect(page.locator('[data-testid="allowlist-invalid"]')).toBeVisible()
      await expect(row).toHaveClass(/border-red-400/)

      await row.fill('10.38.111.21/16')
      await expect(page.locator('[data-testid="allowlist-invalid"]')).toHaveCount(0)
      await saveBinding(page)

      await openBinding(page)
      await expect(page.locator('[data-testid="allowlist-entry-0"]')).toHaveValue('10.38.0.0/16')
      await expect(page.locator('[data-testid="allowlist-entry-1"]')).toHaveCount(0)
      await closeBinding(page)
    })
  } finally {
    if (instanceId) await apiDelete(`/api/v1/adapters/instances/${instanceId}`)
    await apiDelete(`/api/v1/datapoints/${dp.id}`)
  }
})
