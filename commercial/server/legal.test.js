// Terms and privacy: two locks (complete company details AND COMMERCIAL_LEGAL_PUBLISHED=true); the waitlist
// cannot open before the legal pages are published.
import assert from 'node:assert/strict'
import { once } from 'node:events'
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { buildLegal, fill, renderMarkdown } from '../scripts/build-legal.mjs'
import { createApp } from './app.js'
import { legalState } from './legal.js'

const ROOT = fileURLToPath(new URL('../', import.meta.url))
const quiet = { warn() {}, error() {} }

async function site(t, { complete, published, waitlistOpen = true }) {
  const dir = await mkdtemp(join(tmpdir(), 'asc-legal-'))
  await cp(join(ROOT, 'legal'), join(dir, 'legal'), { recursive: true })
  if (complete) {
    const company = JSON.parse(await readFile(join(dir, 'legal', 'empresa.json'), 'utf8'))
    for (const key of Object.keys(company)) if (!key.startsWith('_') && !company[key]) company[key] = `valor de ${key}`
    await writeFile(join(dir, 'legal', 'empresa.json'), JSON.stringify(company))
  }
  const dist = join(dir, 'dist')
  await mkdir(join(dist, 'pages'), { recursive: true })
  const status = await buildLegal({ root: dir, out: join(dist, 'legal') })
  const legal = legalState(dist, published)
  const { app, close } = createApp({ databasePath: join(dir, 'db.sqlite'), dist, log: quiet, waitlistOpen, legalPublished: legal.published })
  const server = app.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(async () => { await new Promise(r => server.close(r)); close(); await rm(dir, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 }) })
  return { base: `http://127.0.0.1:${server.address().port}`, status, legal, dist }
}

const ROUTES = ['/privacidade', '/termos', '/en/privacy', '/en/terms']
const interest = { email: 'pessoa@example.com', plan: 'creator', country: 'BR', platform: 'twitch', language: 'pt-BR', market: 'BR', consent: true }

test('markdown renderer: headings, lists, bold, safe links and escaping', () => {
  const html = renderMarkdown('# Título\n\nTexto **forte** e [política](/privacidade) <script>x</script>\n\n- um\n- dois\n\n[mal](javascript:alert(1))')
  assert.match(html, /<h1 id="titulo">Título<\/h1>/)
  assert.match(html, /<strong>forte<\/strong>/)
  assert.match(html, /<a href="\/privacidade">política<\/a>/)
  assert.match(html, /&lt;script&gt;/)
  assert.match(html, /<ul><li>um<\/li><li>dois<\/li><\/ul>/)
  assert.doesNotMatch(html, /href="javascript/)
  assert.deepEqual(fill('A {{X}} e {{Y}}', { X: 'ok', Y: ' ' }), { text: 'A ok e [Y]', missing: ['Y'] })
})

test('the real documents have every company field declared in empresa.json', async () => {
  const company = JSON.parse(await readFile(join(ROOT, 'legal', 'empresa.json'), 'utf8'))
  for (const file of ['privacidade.pt-BR.md', 'termos.pt-BR.md', 'privacy.en.md', 'terms.en.md']) {
    const fields = [...(await readFile(join(ROOT, 'legal', file), 'utf8')).matchAll(/\{\{([A-Z_]+)\}\}/g)].map(m => m[1])
    for (const field of fields) assert.ok(Object.hasOwn(company, field), `${file}: ${field} missing from empresa.json`)
  }
})

test('drafts (company details missing) are never published, even with the flag on', async t => {
  const { base, status, legal, dist } = await site(t, { complete: false, published: true })
  assert.equal(status.complete, false)
  assert.ok(status.missing.includes('CNPJ'))
  assert.equal(legal.published, false)
  assert.match(await readFile(join(dist, 'legal', 'privacidade.pt-BR.html'), 'utf8'), /class="draft"/)
  for (const route of [...ROUTES, '/legal/privacidade.pt-BR.html', '/legal/status.json']) assert.equal((await fetch(base + route)).status, 404, route)
  const config = await (await fetch(`${base}/api/billing/config`)).json()
  assert.equal(config.legal, false)
  // The waitlist stays closed although COMMERCIAL_WAITLIST_OPEN asked for it
  assert.equal(config.waitlist, false)
  assert.equal((await fetch(`${base}/api/waitlist`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(interest) })).status, 503)
})

test('complete but not approved (flag off): still 404', async t => {
  const { base, status, legal } = await site(t, { complete: true, published: false })
  assert.equal(status.complete, true)
  assert.equal(legal.published, false)
  for (const route of ROUTES) assert.equal((await fetch(base + route)).status, 404, route)
})

test('complete and approved: pages served, footer flag on, waitlist can open', async t => {
  const { base, legal } = await site(t, { complete: true, published: true })
  assert.equal(legal.published, true)
  for (const route of ROUTES) {
    const r = await fetch(base + route)
    assert.equal(r.status, 200, route)
    const html = await r.text()
    assert.doesNotMatch(html, /class="draft"|\{\{|\[[A-Z_]{3,}\]/, `${route} still has a placeholder`)
  }
  assert.match(await (await fetch(`${base}/en/privacy`)).text(), /<html lang="en">/)
  const config = await (await fetch(`${base}/api/billing/config`)).json()
  assert.equal(config.legal, true)
  assert.equal(config.waitlist, true)
  assert.equal((await fetch(`${base}/api/waitlist`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(interest) })).status, 202)
})
