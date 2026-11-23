import assert from 'node:assert/strict'
import { mkdtemp, unlink, rmdir } from 'node:fs/promises'
import { once } from 'node:events'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { test } from 'node:test'
import { createApp } from './app.js'

test('waitlist validates, deduplicates, persists and limits submissions', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'asc-waitlist-'))
  const databasePath = join(directory, 'test.sqlite')
  const { app, close } = createApp({ databasePath })
  const server = app.listen(0, '127.0.0.1')
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    close()
    await unlink(databasePath)
    await rmdir(directory)
  })
  await once(server, 'listening')

  const url = `http://127.0.0.1:${server.address().port}/api/waitlist`
  const interest = {
    email: 'Creator@Example.com',
    plan: 'pro',
    country: 'OTHER',
    otherCountry: 'Canadá',
    platform: 'kick',
    language: 'pt-BR',
    market: 'US',
    consent: true,
  }
  const post = (changes = {}) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...interest, ...changes }),
  })

  assert.equal((await post()).status, 202)
  assert.equal((await post({ email: 'creator@example.com', plan: 'basic' })).status, 202)
  assert.equal((await post({ email: 'bot@example.com', website: 'https://example.com' })).status, 202)
  assert.equal((await post({ consent: false })).status, 422)
  assert.equal((await post({ otherCountry: '  ' })).status, 422)
  assert.equal((await post({ market: 'toString' })).status, 422)
  assert.equal((await post({ plan: 'unknown' })).status, 422)
  assert.equal((await post({ email: 'invalid' })).status, 422)
  assert.equal((await post({ email: 'x'.repeat(4500) })).status, 413)
  assert.equal((await fetch(url, { method: 'POST', body: 'email=creator@example.com' })).status, 415)
  assert.equal((await fetch(url)).status, 404)

  const inspect = new DatabaseSync(databasePath, { readOnly: true })
  try {
    const rows = inspect.prepare('SELECT * FROM waitlist').all().map(row => ({ ...row }))
    assert.equal(rows.length, 1)
    assert.equal(rows[0].email, 'creator@example.com')
    assert.equal(rows[0].plan, 'pro')
    assert.equal(rows[0].other_country, 'Canadá')
    assert.equal(rows[0].platform, 'kick')
    assert.equal(rows[0].market, 'US')
    assert.equal(rows[0].consent_version, 1)
  } finally {
    inspect.close()
  }

  for (let i = 0; i < 5; i++) assert.equal((await post()).status, 202)
  assert.equal((await post()).status, 429)
})

test('a closed waitlist (no privacy policy and terms yet) collects nothing and says so', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'asc-waitlist-closed-'))
  const databasePath = join(directory, 'test.sqlite')
  const { app, close } = createApp({ databasePath, waitlistOpen: false })
  const server = app.listen(0, '127.0.0.1')
  t.after(async () => {
    await new Promise(resolve => server.close(resolve))
    close()
    await unlink(databasePath)
    await rmdir(directory)
  })
  await once(server, 'listening')
  const base = `http://127.0.0.1:${server.address().port}`
  const response = await fetch(`${base}/api/waitlist`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'fan@example.com', plan: 'pro', country: 'BR', platform: 'twitch', language: 'pt-BR', market: 'BR', consent: true }) })
  assert.equal(response.status, 503)
  assert.deepEqual(await response.json(), { error: 'waitlist_closed' })
  assert.equal((await (await fetch(`${base}/api/billing/config`)).json()).waitlist, false)
  const db = new DatabaseSync(databasePath, { readOnly: true })
  try { assert.equal(db.prepare('SELECT COUNT(*) AS n FROM waitlist').get().n, 0) } finally { db.close() }
})
