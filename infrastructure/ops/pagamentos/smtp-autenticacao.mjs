// Dentro do asc-commercial: conexão, STARTTLS e AUTH no Mailgun, sem enviar mensagem. Nada secreto é impresso.
import nodemailer from 'nodemailer'
import { mailSettings } from './server/mailer.js'
const s = mailSettings()
console.log(`remetente efetivo: ${s.from}`)
const lines = []
const log = { level: () => {}, trace() {}, debug(...a) { lines.push(a.map(String).join(' ')) }, info(...a) { lines.push(a.map(String).join(' ')) }, warn() {}, error(...a) { lines.push('ERR ' + a.map(String).join(' ')) }, fatal() {} }
const t = nodemailer.createTransport({ host: s.host, port: s.port, secure: s.secure, requireTLS: !s.secure, auth: { user: s.user, pass: s.pass }, logger: log, debug: true })
try {
  await t.verify()
  console.log('AUTENTICAÇÃO OK')
} catch (e) {
  console.log(`FALHOU: ${e.code ?? ''} ${e.responseCode ?? ''} ${String(e.response ?? e.message).slice(0, 120)}`)
}
const pick = re => lines.filter(l => re.test(l)).map(l => l.replace(/AUTH PLAIN \S+/, 'AUTH PLAIN ***').replace(/[A-Za-z0-9+/=]{24,}/g, '***').slice(0, 110))
for (const l of [...pick(/^\S*\s*\[.*\] S: 220|S: 250-STARTTLS|C: STARTTLS|S: 220 .*TLS|Connection upgraded|S: 250-AUTH|S: 235|S: 535|S: 5\d\d/), ...pick(/upgraded/i)]) console.log('  ' + l)
t.close()
