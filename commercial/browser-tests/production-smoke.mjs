// Production smoke test of the commercial site (no test data is written).
//   node browser-tests/production-smoke.mjs <base url> <screenshot dir>
// Checks: PT/EN pages, language attribute, no horizontal scroll at 390 and 1440 px, closed waitlist notice,
// checkout off, static assets, security headers, API config and the 404 route.
import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { chromium } from '@playwright/test'

const base = (process.argv[2] ?? 'http://127.0.0.1:8790').replace(/\/$/, '')
const shots = process.argv[3] ?? 'test-results/production-smoke'
mkdirSync(shots, { recursive: true })
const results = []
const check = (name, ok, detail = '') => { results.push({ name, ok, detail }); console.log(`${ok ? 'ok ' : 'ERRO'}  ${name}${detail ? `: ${detail}` : ''}`) }

const config = await (await fetch(`${base}/api/billing/config`)).json()
check('config: checkout desligado', !config.markets.BR && !config.markets.US, JSON.stringify(config.markets))
check('config: lista de espera fechada', config.waitlist === false)
const home = await fetch(`${base}/`)
check('cabeçalhos de segurança', Boolean(home.headers.get('content-security-policy')) && home.headers.get('x-content-type-options') === 'nosniff')
check('404 em rota inexistente', (await fetch(`${base}/nao-existe`)).status === 404)
const closed = await fetch(`${base}/api/waitlist`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
check('lista de espera não coleta dados (503)', closed.status === 503)

const browser = await chromium.launch()
try {
  for (const [lang, expectLang, closedText] of [['pt-BR', 'pt-BR', 'A lista de espera abre em breve'], ['en', 'en', 'The waitlist opens soon']]) {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 900 } })
      const errors = []
      page.on('pageerror', e => errors.push(String(e)))
      const assets = []
      page.on('response', r => { if (r.url().includes('/assets/') && r.status() >= 400) assets.push(`${r.status()} ${r.url()}`) })
      const resp = await page.goto(`${base}/?lang=${lang}`, { waitUntil: 'networkidle' })
      check(`${lang} ${width}px: HTTP 200`, resp.status() === 200)
      check(`${lang} ${width}px: idioma`, (await page.getAttribute('html', 'lang')) === expectLang)
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
      check(`${lang} ${width}px: sem rolagem horizontal`, overflow <= 1, `excesso ${overflow}px`)
      await page.waitForFunction(text => document.body.innerText.includes(text), closedText, { timeout: 8000 }).catch(() => {})
      check(`${lang} ${width}px: aviso de lista de espera fechada`, (await page.content()).includes(closedText))
      check(`${lang} ${width}px: formulário de coleta ausente`, (await page.locator('form.waitlist-form').count()) === 0)
      check(`${lang} ${width}px: sem erros de JavaScript nem assets quebrados`, errors.length === 0 && assets.length === 0, [...errors, ...assets].join(' | '))
      await page.screenshot({ path: join(shots, `${lang}-${width}.png`), fullPage: false })
      await page.close()
    }
  }
} finally {
  await browser.close()
}
const failed = results.filter(r => !r.ok)
console.log(`\n${results.length - failed.length}/${results.length} verificações ok`)
process.exit(failed.length ? 1 : 0)
