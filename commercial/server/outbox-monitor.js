// Operational watch over the entitlement outbox: logs alerts while something is wrong (rate limited, codes and
// counts only, no personal data or tokens) and reconciles with the main backend every 30 minutes.
const REPEAT_EVERY = 15 * 60
const RECONCILE_EVERY = 30 * 60

export function createOutboxMonitor({ outbox, readState = null, log = console, now = () => Math.floor(Date.now() / 1000) }) {
  const lastLogged = new Map()
  let lastReconcile = 0

  async function tick() {
    const t = now()
    const active = outbox.alerts()
    const codes = new Set(active.map(alert => alert.code))
    for (const alert of active) {
      if (t - (lastLogged.get(alert.code) ?? 0) >= REPEAT_EVERY) {
        log.error('Entitlement outbox alert', alert)
        lastLogged.set(alert.code, t)
      }
    }
    for (const code of [...lastLogged.keys()]) {
      if (!codes.has(code)) {
        log.log('Entitlement outbox alert cleared', { code })
        lastLogged.delete(code)
      }
    }
    let reconciliation = null
    if (readState && t - lastReconcile >= RECONCILE_EVERY) {
      lastReconcile = t
      reconciliation = await outbox.reconcile(readState)
    }
    return { alerts: active, reconciliation }
  }

  return { tick }
}
