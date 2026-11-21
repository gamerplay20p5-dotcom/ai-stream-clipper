/// <reference types="node" />

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { renderToString } from 'react-dom/server'
import { App } from './App'
import { content } from './content'
import { editorial } from './editorial'
import type { Market } from './market'

const template = await readFile('dist/index.html', 'utf8')
const shareOrigin = process.env.COMMERCIAL_ORIGIN ? new URL(process.env.COMMERCIAL_ORIGIN).origin : ''
if (shareOrigin && !/^https?:\/\//.test(shareOrigin)) throw new Error('COMMERCIAL_ORIGIN must use HTTP or HTTPS')

function escape(value: string) {
  return value.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;')
}

await mkdir('dist/pages', { recursive: true })
for (const language of ['pt-BR', 'en'] as const) {
  const copy = content[language]
  const shareTitle = escape(copy.title)
  const description = escape(copy.description)
  const locale = language === 'en' ? 'en_US' : 'pt_BR'
  const alternateLocale = language === 'en' ? 'pt_BR' : 'en_US'
  for (const market of ['BR', 'US'] as const satisfies readonly Market[]) {
    const body = renderToString(<App initial={{ language, market }} />)
    const alternate = shareOrigin
      ? `<link rel="alternate" hreflang="pt-BR" href="${shareOrigin}/?lang=pt-BR" />
    <link rel="alternate" hreflang="en" href="${shareOrigin}/?lang=en" />
    <meta property="og:url" content="${shareOrigin}/?lang=${language}" />`
      : ''
    const result = template
      .replace('<html lang="pt-BR">', `<html lang="${language}">`)
      .replace(/<title>.*?<\/title>/, () => `<title>${shareTitle}</title>`)
      .replace(/(<meta name="description" content=")[^"]*/, (_whole, prefix) => prefix + description)
      .replace(/(<meta property="og:title" content=")[^"]*/, (_whole, prefix) => prefix + shareTitle)
      .replace(/(<meta property="og:description" content=")[^"]*/, (_whole, prefix) => prefix + description)
      .replace(/(<meta property="og:locale" content=")[^"]*/, (_whole, prefix) => prefix + locale)
      .replace(/(<meta property="og:image" content=")([^"]*)/, (_whole, prefix, image) => prefix + shareOrigin + image)
      .replace('</head>', () => `<meta property="og:locale:alternate" content="${alternateLocale}" />
    <meta property="og:image:alt" content="${escape(editorial[language].hero.wideAlt)}" />${alternate}
    </head>`)
      .replace('<div id="root"></div>', () => `<div id="root" data-language="${language}" data-market="${market}">${body}</div>`)
    await writeFile(`dist/pages/${language}-${market}.html`, result)
    if (language === 'pt-BR' && market === 'BR') await writeFile('dist/index.html', result)
  }
}
