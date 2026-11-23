// Terms of Use and Privacy Policy routes. Published only with BOTH locks: the build found every company field
// (dist/legal/status.json "complete") AND the operator set COMMERCIAL_LEGAL_PUBLISHED=true. Otherwise every legal
// route, and the files under /legal/, answer 404 (the site keeps saying "in preparation").
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import express from 'express'

export const LEGAL_ROUTES = {
  '/privacidade': 'privacidade.pt-BR.html',
  '/termos': 'termos.pt-BR.html',
  '/en/privacy': 'privacy.en.html',
  '/en/terms': 'terms.en.html',
}

/** { published, reason } for the startup log; never throws. */
export function legalState(dist, requested) {
  if (!requested) return { published: false, reason: 'COMMERCIAL_LEGAL_PUBLISHED is not true' }
  const file = dist ? join(dist, 'legal', 'status.json') : ''
  if (!file || !existsSync(file)) return { published: false, reason: 'legal pages were not built' }
  try {
    const status = JSON.parse(readFileSync(file, 'utf8'))
    if (status.complete !== true) return { published: false, reason: `company details missing: ${(status.missing ?? []).join(', ')}` }
    return { published: true, reason: `version ${status.version || '?'}` }
  } catch {
    return { published: false, reason: 'legal/status.json unreadable' }
  }
}

export function legalRouter(dist, published) {
  const router = express.Router()
  // The raw files are never reachable before publication (express.static would otherwise serve them)
  router.use('/legal', (_req, res, next) => (published ? next() : res.status(404).json({ error: 'not_found' })))
  for (const [route, file] of Object.entries(LEGAL_ROUTES)) {
    router.get(route, (_req, res, next) => {
      if (!published) return next()
      res.set('Cache-Control', 'public, max-age=300')
      return res.sendFile(join(dist, 'legal', file))
    })
  }
  return router
}
