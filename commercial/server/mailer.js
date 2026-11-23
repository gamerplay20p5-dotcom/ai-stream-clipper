// Optional SMTP for renewal reminders. Without SMTP_HOST the service keeps working and the
// pending renewal links are listed by `npm run billing:report` for manual sending.
import nodemailer from 'nodemailer'

export function mailSettings(env = process.env) {
  return {
    host: env.SMTP_HOST ?? '',
    port: Number(env.SMTP_PORT ?? 587),
    secure: env.SMTP_SECURE === 'true',
    user: env.SMTP_USER ?? '',
    pass: env.SMTP_PASS ?? '',
    from: env.SMTP_FROM ?? '',
  }
}

/** Returns send(message) or null when SMTP is not configured. */
export function createMailer(settings) {
  if (!settings.host || !settings.from) return null
  const transport = nodemailer.createTransport({
    host: settings.host,
    port: settings.port,
    secure: settings.secure,
    requireTLS: !settings.secure,
    auth: settings.user ? { user: settings.user, pass: settings.pass } : undefined,
  })
  // Payment links must reach the customer exactly as written: no click/open tracking rewrite by the provider
  // (Mailgun honours X-Mailgun-Track; other servers ignore the header)
  return message => transport.sendMail({ from: settings.from, ...message, headers: { 'X-Mailgun-Track': 'no', ...message.headers } })
}
