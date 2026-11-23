// Builds the Terms of Use and Privacy Policy pages from commercial/legal/*.md (the reviewed source) and
// legal/empresa.json (company details). Run by `npm run build` after the prerender.
//
// Publication has two locks (server/legal.js): every {{FIELD}} filled in empresa.json (status.json "complete")
// AND COMMERCIAL_LEGAL_PUBLISHED=true. Until then the routes answer 404. A page with missing fields carries a
// visible DRAFT banner, so a preview can never be mistaken for the published text.
//
//   node scripts/build-legal.mjs [--out dist/legal]
import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const DOCUMENTS = [
  { file: 'privacidade.pt-BR.md', out: 'privacidade.pt-BR.html', lang: 'pt-BR', route: '/privacidade' },
  { file: 'termos.pt-BR.md', out: 'termos.pt-BR.html', lang: 'pt-BR', route: '/termos' },
  { file: 'privacy.en.md', out: 'privacy.en.html', lang: 'en', route: '/en/privacy' },
  { file: 'terms.en.md', out: 'terms.en.html', lang: 'en', route: '/en/terms' },
]

const escape = value => value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;')

function inline(text) {
  return escape(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\[([^\]]+)\]\(((?:https:\/\/|mailto:|\/)[^)\s]+)\)/g, (_w, label, href) => `<a href="${href}">${label}</a>`)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

/** Minimal Markdown: #/##/### headings, paragraphs, "- " lists, **bold**, [links](https://, mailto:, /), `code`. */
export function renderMarkdown(source) {
  const html = []
  let list = null
  let paragraph = []
  const flush = () => {
    if (paragraph.length) html.push(`<p>${inline(paragraph.join(' '))}</p>`)
    paragraph = []
    if (list) html.push(`<ul>${list.map(item => `<li>${inline(item)}</li>`).join('')}</ul>`)
    list = null
  }
  for (const raw of source.split(/\r?\n/)) {
    const line = raw.trimEnd()
    const heading = /^(#{1,3}) (.+)$/.exec(line)
    if (heading) {
      flush()
      const level = heading[1].length
      const id = heading[2].toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      html.push(`<h${level} id="${id}">${inline(heading[2])}</h${level}>`)
    } else if (/^- /.test(line)) {
      if (paragraph.length) { html.push(`<p>${inline(paragraph.join(' '))}</p>`); paragraph = [] }
      list ??= []
      list.push(line.slice(2))
    } else if (/^ {2,}\S/.test(line) && list) {
      list[list.length - 1] += ` ${line.trim()}`
    } else if (!line.trim()) {
      flush()
    } else {
      if (list) flush()
      paragraph.push(line.trim())
    }
  }
  flush()
  return html.join('\n')
}

/** Replaces {{FIELD}} with empresa.json values; returns the text and the fields still missing. */
export function fill(source, company) {
  const missing = new Set()
  const text = source.replace(/\{\{([A-Z_]+)\}\}/g, (whole, key) => {
    const value = company[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
    missing.add(key)
    return `[${key}]`
  })
  return { text, missing: [...missing] }
}

const LABELS = {
  'pt-BR': { draft: 'RASCUNHO, não publicado: faltam dados da empresa', back: 'Voltar ao site', other: 'English', otherLang: 'en' },
  en: { draft: 'DRAFT, not published: company details missing', back: 'Back to the site', other: 'Português', otherLang: 'pt-BR' },
}
const PAIRS = { '/privacidade': '/en/privacy', '/en/privacy': '/privacidade', '/termos': '/en/terms', '/en/terms': '/termos' }

export async function buildLegal({ root = fileURLToPath(new URL('../', import.meta.url)), out } = {}) {
  const dir = join(root, 'legal')
  const target = out ?? join(root, 'dist', 'legal')
  const company = JSON.parse(await readFile(join(dir, 'empresa.json'), 'utf8'))
  await mkdir(target, { recursive: true })
  const missing = new Set()
  for (const doc of DOCUMENTS) {
    const filled = fill(await readFile(join(dir, doc.file), 'utf8'), company)
    filled.missing.forEach(key => missing.add(key))
    const body = renderMarkdown(filled.text)
    const title = (/^# (.+)$/m.exec(filled.text)?.[1] ?? 'AI Stream Clipper').trim()
    const t = LABELS[doc.lang]
    const page = `<!doctype html>
<html lang="${doc.lang}">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${escape(title)} | AI Stream Clipper by HTS</title>
    <meta name="robots" content="${filled.missing.length ? 'noindex' : 'index'}" />
    <link rel="stylesheet" href="/legal/legal.css" />
  </head>
  <body>
    ${filled.missing.length ? `<p class="draft" role="alert">${t.draft}</p>` : ''}
    <header><a href="${doc.lang === 'en' ? '/?lang=en' : '/'}">${t.back}</a> <a href="${PAIRS[doc.route]}" hreflang="${t.otherLang}">${t.other}</a></header>
    <main>
${body}
    </main>
  </body>
</html>
`
    await writeFile(join(target, doc.out), page)
  }
  await writeFile(join(target, 'legal.css'), await readFile(join(dir, 'legal.css'), 'utf8'))
  const status = { complete: missing.size === 0, missing: [...missing].sort(), version: company.VERSAO ?? '' }
  await writeFile(join(target, 'status.json'), JSON.stringify(status, null, 1))
  return status
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const outIndex = process.argv.indexOf('--out')
  const status = await buildLegal(outIndex > 0 ? { out: process.argv[outIndex + 1] } : {})
  console.log(status.complete ? `Legal pages complete (version ${status.version})` : `Legal pages are DRAFTS, missing: ${status.missing.join(', ')}`)
}
