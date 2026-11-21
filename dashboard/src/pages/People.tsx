import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, Link2, LogOut, Mail, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type AdminUser, type ClipShareRow } from "../api";
import GiveAccess from "../components/GiveAccess";
import { ShareRow, shareDate } from "../components/SharePanel";
import { ConfirmAction, Empty, ErrorNote, Notice, PageHeader, Skeleton, Status } from "../components/ui";
import { t } from "../i18n";

type Secret = { email: string; initial_password?: string; note?: string; setup_url?: string; emailed?: boolean; purpose?: string };
type Smtp = { host: string; port: number; username: string; from_addr: string; security: string; has_password: boolean; configured: boolean };

/** Accounts and access (multi-user stage 1). No public sign-up and no e-mail: the admin hands the password over. */
export default function People() {
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<AdminUser[]>("/api/admin/users") });
  const [selected, setSelected] = useState<number | null>(null);
  const [secret, setSecret] = useState<Secret | null>(null);
  const list = users.data ?? [];
  const current = list.find((u) => u.id === selected) ?? null;
  return (
    <div>
      <PageHeader title={t("Pessoas e acesso")}
        description={t("Crie contas para quem é de fora da equipe e escolha quais cortes cada uma vê. Contas convidadas só enxergam a Minha Biblioteca.")} />
      <div className="section-flow">
        {secret && <OneTimePassword secret={secret} onClose={() => setSecret(null)} />}
        <CreateAccount onCreated={setSecret} />
        <EmailSettings />
        <section aria-labelledby="accounts-title">
          <h2 id="accounts-title" className="display mb-3 text-xl">{t("Contas")}</h2>
          <ErrorNote error={users.error} />
          {users.isLoading && <Skeleton label={t("Carregando contas")} />}
          {users.data && list.length === 0 && <Empty title={t("Nenhuma conta cadastrada")} />}
          {list.length > 0 && (
            <ul className="people-list">
              {list.map((u) => (
                <li key={u.id} data-current={u.id === selected || undefined}>
                  <button type="button" className="people-row" aria-expanded={u.id === selected}
                    onClick={() => setSelected(u.id === selected ? null : u.id)}>
                    <span className="min-w-0">
                      <strong className="block truncate">{u.email}</strong>
                      <span className="mt-1 block text-xs text-ink-muted">
                        {u.last_login_at ? t("Último acesso em {shareDate}", { shareDate: shareDate(u.last_login_at) }) : t("Ainda não entrou")}
                      </span>
                    </span>
                    <span className="flex flex-wrap items-center justify-end gap-1.5">
                      <Status tone={u.role === "admin" ? "accent" : "neutral"}>{u.role === "admin" ? t("Equipe") : t("Convidado")}</Status>
                      {!u.active && <Status tone="warning">{t("Desativada")}</Status>}
                      {u.role === "viewer" && <span className="text-xs text-ink-muted">{t(u.active_shares === 1 ? "{n} corte" : "{n} cortes", { n: u.active_shares })}</span>}
                    </span>
                  </button>
                  {u.id === selected && current && <AccountDetail user={current} onSecret={setSecret} />}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function CreateAccount({ onCreated }: { onCreated: (s: Secret) => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"viewer" | "admin">("viewer");
  const [fullAccess, setFullAccess] = useState(false);
  const [delivery, setDelivery] = useState<"link" | "email" | "password">("link");
  const smtp = useQuery({ queryKey: ["smtp"], queryFn: () => api.get<Smtp>("/api/admin/smtp") });
  const create = useMutation({
    mutationFn: () => api.post<AdminUser & Secret>("/api/admin/users", { email, role, delivery }),
    onSuccess: (r) => {
      setEmail("");
      setRole("viewer");
      setFullAccess(false);
      onCreated(r);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="panel panel-body grid gap-4 sm:grid-cols-[minmax(0,1fr)_14rem_auto] sm:items-end" onSubmit={submit}>
      <div>
        <label className="label" htmlFor="new-email">{t("Nova conta")}</label>
        <input id="new-email" type="email" required className="field" value={email} placeholder={t("nome@empresa.com")}
          onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
      </div>
      <div>
        <label className="label" htmlFor="new-role">{t("Tipo")}</label>
        <select id="new-role" className="field" value={role} onChange={(e) => { setRole(e.target.value as "viewer" | "admin"); setFullAccess(false); }}>
          <option value="viewer">{t("Convidado (só a biblioteca)")}</option>
          <option value="admin">{t("Equipe (painel completo)")}</option>
        </select>
      </div>
      <button className="btn-take" disabled={create.isPending || !email || (role === "admin" && !fullAccess)}><UserPlus size={16} aria-hidden /> {t("Criar conta")}</button>
      <div className="sm:col-span-3">
        <label className="label" htmlFor="new-delivery">{t("Como a pessoa recebe o acesso")}</label>
        <select id="new-delivery" className="field" value={delivery} onChange={(e) => setDelivery(e.target.value as typeof delivery)}>
          <option value="link">{t("Link para ela criar a própria senha (recomendado)")}</option>
          <option value="email" disabled={!smtp.data?.configured}>{t("Link enviado por e-mail")}{smtp.data?.configured ? "" : t(" (configure o e-mail abaixo)")}</option>
          <option value="password">{t("Senha inicial que eu entrego")}</option>
        </select>
      </div>
      {role === "admin" && (
        <label className="admin-ack sm:col-span-3">
          <input type="checkbox" checked={fullAccess} onChange={(e) => setFullAccess(e.target.checked)} />
          <span><strong>{t("Esta conta terá acesso completo à operação da HTS.")}</strong> {t("Ela poderá aprovar e publicar cortes, mudar regras de publicação, conectar contas, compartilhar vídeos, criar e desativar outras contas e ver os servidores. Confirmo que é uma pessoa da equipe.")}</span>
        </label>
      )}
      {create.error && <div className="sm:col-span-3"><ErrorNote error={create.error} /></div>}
    </form>
  );
}

function OneTimePassword({ secret, onClose }: { secret: Secret; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  if (secret.setup_url) {
    return (
      <section className="one-time" role="status" aria-labelledby="otp-title">
        <h2 id="otp-title" className="display text-lg">{t("Link de senha para {email}", { email: secret.email })}</h2>
        <p className="text-sm text-ink-muted">
          {secret.emailed ? t("O link foi enviado por e-mail. ") : t("Envie este link para a pessoa. ")}
          {secret.purpose === "reset" ? t("Vale por 2 horas, uma vez só.") : t("Vale por 72 horas, uma vez só.")} {t("Ele aparece só agora.")}</p>
        <div className="flex flex-wrap items-center gap-2">
          <code className="one-time-code break-all">{secret.setup_url}</code>
          <button type="button" className="btn-quiet" onClick={async () => {
            try { await navigator.clipboard.writeText(secret.setup_url!); setCopied(true); } catch { setCopied(false); }
          }}><Copy size={15} aria-hidden /> {copied ? t("Copiado") : t("Copiar")}</button>
          <button type="button" className="btn-quiet" onClick={onClose}>{t("Já entreguei, ocultar")}</button>
        </div>
      </section>
    );
  }
  return (
    <section className="one-time" role="status" aria-labelledby="otp-title">
      <h2 id="otp-title" className="display text-lg">{t("Senha inicial de")} {secret.email}</h2>
      <p className="text-sm text-ink-muted">{secret.note} {t("Ela aparece só agora.")}</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="one-time-code">{secret.initial_password}</code>
        <button type="button" className="btn-quiet" onClick={async () => {
          try { await navigator.clipboard.writeText(secret.initial_password ?? ""); setCopied(true); } catch { setCopied(false); }
        }}><Copy size={15} aria-hidden /> {copied ? t("Copiada") : t("Copiar")}</button>
        <button type="button" className="btn-quiet" onClick={onClose}>{t("Já entreguei, ocultar")}</button>
      </div>
    </section>
  );
}

function AccountDetail({ user, onSecret }: { user: AdminUser; onSecret: (s: Secret) => void }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const shares = useQuery({
    queryKey: ["shares", "user", user.id],
    queryFn: () => api.get<ClipShareRow[]>(`/api/admin/users/${user.id}/shares?include_revoked=true`),
  });
  const patch = useMutation({ mutationFn: (b: object) => api.patch(`/api/admin/users/${user.id}`, b), onSuccess: refresh });
  const reset = useMutation({
    mutationFn: () => api.post<Secret>(`/api/admin/users/${user.id}/reset-password`),
    onSuccess: (r) => { onSecret(r); refresh(); },
  });
  const end = useMutation({ mutationFn: () => api.post(`/api/admin/users/${user.id}/end-sessions`), onSuccess: refresh });
  const smtp = useQuery({ queryKey: ["smtp"], queryFn: () => api.get<Smtp>("/api/admin/smtp") });
  const link = useMutation({
    mutationFn: (send_email: boolean) => api.post<Secret>(`/api/admin/users/${user.id}/password-link`, { send_email }),
    onSuccess: (r) => { onSecret(r); refresh(); },
  });
  const active = (shares.data ?? []).filter((s) => s.state === "ativo");
  const past = (shares.data ?? []).filter((s) => s.state !== "ativo");
  return (
    <div className="people-detail">
      <div className="flex flex-wrap gap-2">
        <ConfirmAction title={t("Gerar uma nova senha?")} description={t("A senha atual deixa de funcionar e as sessões abertas desta conta terminam.")}
          confirmLabel={t("Gerar nova senha")} pending={reset.isPending} onConfirm={() => reset.mutate()}>
          <KeyRound size={15} aria-hidden /> {t("Nova senha")}
        </ConfirmAction>
        <button type="button" className="btn-quiet" disabled={link.isPending || !user.active} onClick={() => link.mutate(false)}
          title={t("A pessoa cria a própria senha; a atual continua valendo até lá.")}>
          <Link2 size={15} aria-hidden /> {t("Link para criar senha")}</button>
        {smtp.data?.configured && (
          <button type="button" className="btn-quiet" disabled={link.isPending || !user.active} onClick={() => link.mutate(true)}>
            <Mail size={15} aria-hidden /> {t("Enviar link por e-mail")}</button>)}
        <button type="button" className="btn-quiet" disabled={end.isPending} onClick={() => end.mutate()}>
          <LogOut size={15} aria-hidden /> {t("Encerrar sessões")}
        </button>
        {user.active
          ? <ConfirmAction title={t("Desativar esta conta?")} description={t("A pessoa sai na próxima ação e não entra mais. Os compartilhamentos ficam guardados.")}
              confirmLabel={t("Desativar")} pending={patch.isPending} onConfirm={() => patch.mutate({ active: false })}>{t("Desativar conta")}</ConfirmAction>
          : <button type="button" className="btn-quiet" disabled={patch.isPending} onClick={() => patch.mutate({ active: true })}>{t("Reativar conta")}</button>}
      </div>
      {end.isSuccess && <Notice>{t("Sessões encerradas.")}</Notice>}
      <ErrorNote error={patch.error || reset.error || end.error || link.error} />
      <GiveAccess user={user} shares={shares.data ?? []} />
      {user.role === "viewer" && (
        <div>
          <h3 className="mb-2 text-sm font-semibold">{t("Cortes compartilhados")}</h3>
          <ErrorNote error={shares.error} />
          {shares.data && active.length === 0 && (
            <p className="text-sm text-ink-muted">{t("Nenhum corte ativo. Use “Dar mais acesso” acima ou a aba Compartilhar da Central de cortes.")}</p>
          )}
          <ul className="share-list">{active.map((s) => <ShareRow key={s.id} share={s} showClip />)}</ul>
          {past.length > 0 && (
            <details className="mt-3 text-sm">
              <summary className="text-ink-muted">{t("Histórico ({length})", { length: past.length })}</summary>
              <ul className="share-list mt-2">{past.map((s) => <ShareRow key={s.id} share={s} showClip />)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

/** Outgoing e-mail (SMTP) for invite and reset links. The password is stored encrypted and never shown again. */
function EmailSettings() {
  const qc = useQueryClient();
  const smtp = useQuery({ queryKey: ["smtp"], queryFn: () => api.get<Smtp>("/api/admin/smtp") });
  const [form, setForm] = useState<Partial<Smtp> & { password?: string } | null>(null);
  const [to, setTo] = useState("");
  const f = form ?? smtp.data ?? null;
  const save = useMutation({
    mutationFn: () => api.put<Smtp>("/api/admin/smtp", { host: f?.host ?? "", port: Number(f?.port ?? 587), username: f?.username ?? "",
      from_addr: f?.from_addr ?? "", security: f?.security ?? "starttls", password: form?.password === undefined ? null : form.password }),
    onSuccess: () => { setForm(null); qc.invalidateQueries({ queryKey: ["smtp"] }); },
  });
  const test = useMutation({ mutationFn: () => api.post("/api/admin/smtp/test", { to }) });
  const set = (k: string, v: string) => setForm({ ...(f ?? {}), ...form, [k]: v });
  return (
    <details className="panel panel-body">
      <summary className="cursor-pointer font-semibold">{t("E-mail de envio (links de senha)")}
        <span className="ml-2 text-xs font-normal text-ink-muted">{smtp.data?.configured ? t("configurado") : t("não configurado")}</span></summary>
      <p className="mt-2 text-sm text-ink-muted">{t("Usado para enviar convites e o “Esqueci minha senha”. A senha do e-mail fica guardada criptografada no banco e nunca volta para o navegador.")}</p>
      {f && (
        <form className="mt-3 grid gap-3 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <label><span className="label">{t("Servidor SMTP")}</span><input className="field" value={f.host ?? ""} placeholder="smtp.gmail.com" onChange={(e) => set("host", e.target.value)} /></label>
          <label><span className="label">{t("Porta")}</span><input className="field" type="number" value={f.port ?? 587} onChange={(e) => set("port", e.target.value)} /></label>
          <label><span className="label">{t("Usuário")}</span><input className="field" value={f.username ?? ""} autoComplete="off" onChange={(e) => set("username", e.target.value)} /></label>
          <label><span className="label">{t("Senha do e-mail")}</span><input className="field" type="password" autoComplete="new-password"
            placeholder={smtp.data?.has_password ? t("guardada (deixe vazio para manter)") : ""} value={form?.password ?? ""} onChange={(e) => set("password", e.target.value)} /></label>
          <label><span className="label">{t("Remetente")}</span><input className="field" type="email" value={f.from_addr ?? ""} placeholder="clipper@empresa.com" onChange={(e) => set("from_addr", e.target.value)} /></label>
          <label><span className="label">{t("Segurança")}</span><select className="field" value={f.security ?? "starttls"} onChange={(e) => set("security", e.target.value)}>
            <option value="starttls">STARTTLS (587)</option><option value="ssl">SSL/TLS (465)</option><option value="none">{t("Nenhuma (não recomendado)")}</option></select></label>
          <div className="flex flex-wrap items-end gap-2 sm:col-span-2">
            <button className="btn-take" disabled={save.isPending || !form}>{t("Salvar")}</button>
            <input className="field max-w-xs" type="email" placeholder={t("e-mail para teste")} value={to} onChange={(e) => setTo(e.target.value)} />
            <button type="button" className="btn-quiet" disabled={!to || test.isPending || !smtp.data?.configured} onClick={() => test.mutate()}>
              <Mail size={15} aria-hidden /> {test.isPending ? t("Enviando…") : t("Enviar teste")}</button>
          </div>
        </form>
      )}
      <ErrorNote error={save.error || test.error} />
      {save.isSuccess && <Notice>{t("Configuração de e-mail salva.")}</Notice>}
      {test.isSuccess && <Notice>{t("E-mail de teste enviado.")}</Notice>}
    </details>
  );
}
