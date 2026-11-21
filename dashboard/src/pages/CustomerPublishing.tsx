import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CircleAlert, Film, KeyRound, Link2, Plus, Share2, Trash2, UserRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Brand } from "../components/Brand";
import { ConfirmAction, Empty, ErrorNote, LanguagePicker, Notice, Skeleton, Status } from "../components/ui";
import { locale, t } from "../i18n";

type Account = { id: number; platform: string; platform_name: string; name: string; handle: string; status: string;
  paused_reason: string; privacy_options: string[]; needs_reconnect: boolean };
type Rule = { id: number; account_id: number; channel: string; monitor_streamer_id: number; enabled: boolean;
  require_approval: boolean; min_score: number; privacy: string; slots: string[]; rights_confirmed: boolean; rights_note: string };
type Pub = { id: number; clip_id: number | null; title: string; platform_name: string; account_id: number; status: string;
  scheduled_at: string | null; published_at: string | null; url: string; error: string; score: number | null };
type State = {
  plan: string | null; allowed: boolean; upgrade?: boolean; limits: { destinations: number; daily: number };
  enabled?: boolean; terms_accepted?: boolean; timezone?: string; blocked?: boolean; status?: string; status_reason?: string;
  platforms?: { id: string; name: string }[]; today?: { used: number; limit: number; remaining: number; day: string };
  accounts?: Account[]; rules?: Rule[]; channels?: { streamer_id: number; name: string }[];
  upcoming?: Pub[]; history?: Pub[]; needs_action?: Pub[];
};

const when = (iso: string | null) => (iso ? new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }).format(new Date(iso)) : "");
const STATUS: Record<string, () => string> = {
  proposed: () => t("Aguarda sua aprovação"), waiting: () => t("Na fila"), scheduled: () => t("Agendada"),
  uploading: () => t("Enviando"), processing: () => t("Processando na rede"), published: () => t("Publicada"),
  failed: () => t("Falhou"), cancelled: () => t("Cancelada"), expired: () => t("Expirou na fila"), uncertain: () => t("Confirmar na rede"),
};
const PRIVACY: Record<string, () => string> = { private: () => t("Privado"), unlisted: () => t("Não listado"), public: () => t("Público") };

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="library-shell">
      <a className="skip-link" href="#conteudo">{t("Ir para o conteúdo")}</a>
      <header className="library-topbar">
        <Brand />
        <nav className="flex min-w-0 items-center gap-2" aria-label={t("Conta")}>
          <Link className="btn-quiet" to="/conta" aria-label={t("Minha assinatura")}><UserRound size={15} aria-hidden /><span className="hidden sm:inline">{t("Minha assinatura")}</span></Link>
          <Link className="btn-quiet" to="/biblioteca" aria-label={t("Meus cortes")}><Film size={15} aria-hidden /><span className="hidden sm:inline">{t("Meus cortes")}</span></Link>
          <Link className="btn-quiet" to="/conta/senha" aria-label={t("Senha")}><KeyRound size={15} aria-hidden /><span className="hidden sm:inline">{t("Senha")}</span></Link>
        </nav>
      </header>
      <section className="library-hero" aria-labelledby="publish-title">
        <div className="library-hero-inner">
          <div>
            <h1 id="publish-title" className="library-title">{t("Publicação automática")}</h1>
            <p className="mt-2 max-w-[60ch] text-sm text-ink-muted">{t("Os Shorts dos seus canais vão para as suas redes, nos horários que você escolher e dentro do limite diário do plano.")}</p>
          </div>
        </div>
      </section>
      <main id="conteudo" tabIndex={-1} className="library-main account-main">{children}
        <footer className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-muted"><LanguagePicker /></footer>
      </main>
    </div>
  );
}

function Upgrade() {
  return (
    <section className="account-card" aria-labelledby="upgrade-title">
      <h2 id="upgrade-title" className="account-card-title">{t("Disponível nos planos Creator e Pro")}</h2>
      <p className="text-sm text-ink-muted">{t("Creator: até 2 redes conectadas e 3 publicações por dia no total. Pro: até 4 redes conectadas e 4 publicações por dia no total.")}</p>
      <Link className="btn-take mt-3 w-fit" to="/conta">{t("Ver planos")}</Link>
    </section>
  );
}

function Switch({ s, refresh }: { s: State; refresh: () => void }) {
  const [terms, setTerms] = useState(false);
  const save = useMutation({ mutationFn: (body: object) => api.put("/api/account/publishing/settings", body), onSuccess: refresh });
  const tone = s.status === "ok" ? "success" : s.status === "hold" ? "warning" : "danger";
  return (
    <section className="account-card" aria-labelledby="switch-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="switch-title" className="account-card-title">{t("Automação")}</h2>
        <Status tone={s.enabled && s.status === "ok" ? "success" : tone}>{s.enabled ? (s.status === "ok" ? t("Ativa") : t("Em espera")) : t("Desativada")}</Status>
      </div>
      {s.blocked && <Notice tone="danger">{t("A publicação automática desta conta foi bloqueada pela HTS. Fale com o suporte.")}</Notice>}
      {s.status_reason && s.enabled && s.status !== "ok" && <p className="text-sm text-ink-muted">{s.status_reason}</p>}
      {!s.terms_accepted && !s.blocked && (
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={terms} onChange={(e) => setTerms(e.target.checked)} />
          <span>{t("Autorizo o AI Stream Clipper a publicar, em meu nome, os Shorts da minha biblioteca nas redes que eu conectar, seguindo as regras que eu definir. Posso pausar ou desconectar a qualquer momento.")}</span>
        </label>
      )}
      {!s.blocked && (
        <div className="flex flex-wrap items-center gap-3">
          {s.enabled
            ? <button type="button" className="btn-quiet" disabled={save.isPending} onClick={() => save.mutate({ enabled: false })}>{t("Pausar publicação automática")}</button>
            : <button type="button" className="btn-take" disabled={save.isPending || (!s.terms_accepted && !terms)}
                onClick={() => save.mutate({ enabled: true, accept_terms: !s.terms_accepted && terms ? true : undefined })}>{t("Ativar publicação automática")}</button>}
          <label className="flex items-center gap-2 text-xs text-ink-muted">{t("Fuso horário do dia de publicação")}
            <select className="field w-auto py-1 text-xs" value={s.timezone} onChange={(e) => save.mutate({ timezone: e.target.value })}>
              {["America/Sao_Paulo", "America/Manaus", "America/Fortaleza", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/Lisbon", "UTC"]
                .map((z) => <option key={z} value={z}>{z}</option>)}
            </select>
          </label>
        </div>
      )}
      <ErrorNote error={save.error} />
    </section>
  );
}

function Today({ s }: { s: State }) {
  const day = s.today!;
  const pct = day.limit ? Math.min(100, Math.round((day.used / day.limit) * 100)) : 100;
  return (
    <section className="account-card" aria-labelledby="today-title">
      <h2 id="today-title" className="account-card-title">{t("Publicações de hoje")}</h2>
      <div className="account-usage">
        <div className="flex items-baseline justify-between gap-3">
          <span className="text-sm">{t("Disponíveis hoje: {n}", { n: String(day.remaining) })}</span>
          <span className="font-mono text-xs text-ink-muted">{t("{used} de {total} {unit}", { used: String(day.used), total: String(day.limit), unit: "" })}</span>
        </div>
        <div className="account-bar" role="progressbar" aria-label={t("Publicações de hoje")} aria-valuemin={0} aria-valuemax={day.limit} aria-valuenow={day.used}>
          <span style={{ width: `${pct}%` }} data-full={day.used >= day.limit || undefined} />
        </div>
        <p className="text-xs text-ink-muted">{t("O limite é o total do dia somando todas as redes. O mesmo Short em duas redes conta duas vezes. Os excedentes aguardam o próximo dia.")}</p>
      </div>
    </section>
  );
}

function Networks({ s, refresh }: { s: State; refresh: () => void }) {
  const [, setParams] = useSearchParams();
  const connect = useMutation({
    mutationFn: (platform: string) => api.post<{ auth_url: string }>(`/api/account/publishing/connect/${platform}`),
    onSuccess: (r) => window.location.assign(r.auth_url),
  });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/account/publishing/accounts/${id}`), onSuccess: () => { setParams({}); refresh(); } });
  const accounts = s.accounts ?? [];
  const full = accounts.length >= s.limits.destinations;
  return (
    <section className="account-card" aria-labelledby="networks-title">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="networks-title" className="account-card-title">{t("Redes conectadas")}</h2>
        <span className="font-mono text-xs text-ink-muted">{t("{used} de {total} {unit}", { used: String(accounts.length), total: String(s.limits.destinations), unit: "" })}</span>
      </div>
      {accounts.length === 0 && <Empty title={t("Nenhuma rede conectada")}>{t("Conecte a conta onde os Shorts serão publicados. A autorização é feita por você, na própria rede.")}</Empty>}
      <ul className="account-channels">
        {accounts.map((a) => (
          <li key={a.id}>
            <Share2 size={16} aria-hidden className={a.status === "connected" && !a.paused_reason ? "text-take" : "text-ink-muted"} />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm">{a.name || a.handle} <span className="text-xs text-ink-muted">{a.platform_name}</span></p>
              <p className="text-xs text-ink-muted">{a.needs_reconnect ? t("Autorização expirada: conecte de novo") : a.paused_reason || t("Conectada")}</p>
            </div>
            <ConfirmAction title={t("Desconectar esta rede?")} description={t("As publicações agendadas para ela são canceladas. O que já foi publicado continua na rede.")}
              confirmLabel={t("Desconectar")} pending={remove.isPending} onConfirm={() => remove.mutate(a.id)} label={t("Desconectar {name}", { name: a.name || a.platform_name })}>
              <Trash2 size={15} aria-hidden />
            </ConfirmAction>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        {(s.platforms ?? []).map((p) => (
          <button key={p.id} type="button" className="btn-quiet" disabled={full || connect.isPending} onClick={() => connect.mutate(p.id)}>
            <Link2 size={15} aria-hidden />{t("Conectar {name}", { name: p.name })}
          </button>
        ))}
      </div>
      <p className="text-xs text-ink-muted">{t("Disponível agora: YouTube Shorts. Instagram, TikTok e Facebook Reels serão liberados depois da homologação de cada plataforma.")}</p>
      <ErrorNote error={connect.error ?? remove.error} />
    </section>
  );
}

function Rules({ s, refresh }: { s: State; refresh: () => void }) {
  const accounts = (s.accounts ?? []).filter((a) => a.status === "connected");
  const channels = s.channels ?? [];
  const [form, setForm] = useState({ account_id: 0, streamer_id: 0, slots: "12:00, 18:00, 21:00", privacy: "private",
    min_score: 70, require_approval: true, rights_confirmed: false, rights_note: "" });
  const create = useMutation({
    mutationFn: () => api.post("/api/account/publishing/rules", {
      ...form, account_id: form.account_id || accounts[0]?.id, streamer_id: form.streamer_id || channels[0]?.streamer_id,
      slots: form.slots.split(",").map((x) => x.trim()).filter(Boolean),
    }),
    onSuccess: () => { setForm((f) => ({ ...f, rights_confirmed: false, rights_note: "" })); refresh(); },
  });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/account/publishing/rules/${id}`), onSuccess: refresh });
  const account = accounts.find((a) => a.id === (form.account_id || accounts[0]?.id));
  const privacy = account?.privacy_options?.length ? account.privacy_options : ["private", "unlisted", "public"];
  return (
    <section className="account-card" aria-labelledby="rules-title">
      <h2 id="rules-title" className="account-card-title">{t("Canais e destinos")}</h2>
      {(s.rules ?? []).length === 0 && <p className="text-sm text-ink-muted">{t("Escolha qual canal acompanhado vai para qual rede, os horários e se cada Short precisa da sua aprovação.")}</p>}
      <ul className="account-channels">
        {(s.rules ?? []).map((r) => {
          const a = (s.accounts ?? []).find((x) => x.id === r.account_id);
          return (
            <li key={r.id}>
              <CalendarClock size={16} aria-hidden className={r.enabled ? "text-take" : "text-ink-muted"} />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm">{r.channel} → {a ? `${a.name || a.handle} (${a.platform_name})` : "—"}</p>
                <p className="text-xs text-ink-muted">{[r.slots.join(", "), PRIVACY[r.privacy]?.() ?? r.privacy,
                  r.require_approval ? t("com aprovação") : t("automática"), t("nota mínima {n}", { n: String(Math.round(r.min_score)) })].join(", ")}</p>
              </div>
              <ConfirmAction title={t("Remover esta regra?")} description={t("As publicações desta regra que ainda não começaram são canceladas.")}
                confirmLabel={t("Remover")} pending={remove.isPending} onConfirm={() => remove.mutate(r.id)} label={t("Remover regra")}>
                <Trash2 size={15} aria-hidden />
              </ConfirmAction>
            </li>
          );
        })}
      </ul>
      {accounts.length > 0 && channels.length > 0 ? (
        <form className="account-add grid gap-3 sm:grid-cols-2" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
          <label className="label">{t("Canal")}
            <select className="field" value={form.streamer_id || channels[0]?.streamer_id} onChange={(e) => setForm({ ...form, streamer_id: Number(e.target.value) })}>
              {channels.map((c) => <option key={c.streamer_id} value={c.streamer_id}>{c.name}</option>)}
            </select>
          </label>
          <label className="label">{t("Destino")}
            <select className="field" value={form.account_id || accounts[0]?.id} onChange={(e) => setForm({ ...form, account_id: Number(e.target.value) })}>
              {accounts.map((a) => <option key={a.id} value={a.id}>{`${a.name || a.handle} (${a.platform_name})`}</option>)}
            </select>
          </label>
          <label className="label">{t("Horários preferidos")}
            <input className="field" value={form.slots} onChange={(e) => setForm({ ...form, slots: e.target.value })} placeholder="12:00, 18:00" />
          </label>
          <label className="label">{t("Privacidade")}
            <select className="field" value={privacy.includes(form.privacy) ? form.privacy : privacy[0]} onChange={(e) => setForm({ ...form, privacy: e.target.value })}>
              {privacy.map((p) => <option key={p} value={p}>{PRIVACY[p]?.() ?? p}</option>)}
            </select>
          </label>
          <label className="label">{t("Nota mínima do Short")}
            <input className="field" type="number" min={0} max={100} value={form.min_score} onChange={(e) => setForm({ ...form, min_score: Number(e.target.value) })} />
          </label>
          <label className="flex items-center gap-2 text-sm sm:mt-6">
            <input type="checkbox" checked={form.require_approval} onChange={(e) => setForm({ ...form, require_approval: e.target.checked })} />
            {t("Aprovar cada Short antes de publicar")}
          </label>
          <label className="flex items-start gap-2 text-sm sm:col-span-2">
            <input type="checkbox" className="mt-1" checked={form.rights_confirmed} onChange={(e) => setForm({ ...form, rights_confirmed: e.target.checked })} />
            <span>{t("Declaro que sou o criador deste canal ou tenho autorização para republicar o conteúdo dele nas minhas redes.")}</span>
          </label>
          <input className="field sm:col-span-2" maxLength={500} placeholder={t("Como você tem essa autorização (opcional)")} value={form.rights_note} onChange={(e) => setForm({ ...form, rights_note: e.target.value })} />
          <button className="btn-take w-fit" disabled={create.isPending || !form.rights_confirmed}><Plus size={15} aria-hidden />{t("Criar regra")}</button>
        </form>
      ) : <p className="text-xs text-ink-muted">{t("Para criar uma regra, conecte uma rede e acompanhe pelo menos um canal em Minha assinatura.")}</p>}
      <ErrorNote error={create.error ?? remove.error} />
    </section>
  );
}

function Queue({ title, items, empty, actions, refresh }: { title: string; items: Pub[]; empty: string; actions?: boolean; refresh: () => void }) {
  const approve = useMutation({ mutationFn: (id: number) => api.post(`/api/account/publishing/publications/${id}/approve`), onSuccess: refresh });
  const cancel = useMutation({ mutationFn: (id: number) => api.post(`/api/account/publishing/publications/${id}/cancel`), onSuccess: refresh });
  const id = title.replace(/\W+/g, "-").toLowerCase();
  return (
    <section className="account-card" aria-labelledby={id}>
      <h2 id={id} className="account-card-title">{title}</h2>
      {items.length === 0 ? <p className="text-sm text-ink-muted">{empty}</p> : (
        <div className="overflow-x-auto"><table className="account-table">
          <thead><tr><th scope="col">{t("Short")}</th><th scope="col">{t("Rede")}</th><th scope="col">{t("Quando")}</th><th scope="col">{t("Situação")}</th>{actions && <th scope="col"><span className="sr-only">{t("Ações")}</span></th>}</tr></thead>
          <tbody>{items.map((p) => (
            <tr key={p.id}>
              <td className="max-w-[22ch] truncate">{p.url ? <a href={p.url} target="_blank" rel="noreferrer" className="underline">{p.title || `#${p.clip_id}`}</a> : (p.title || `#${p.clip_id}`)}</td>
              <td>{p.platform_name}</td>
              <td className="font-mono text-xs">{when(p.published_at ?? p.scheduled_at)}</td>
              <td>{STATUS[p.status]?.() ?? p.status}{p.error && <span className="block text-xs text-ink-muted">{p.error}</span>}</td>
              {actions && <td className="whitespace-nowrap">
                {p.status === "proposed" && <button type="button" className="btn-quiet" onClick={() => approve.mutate(p.id)}>{t("Aprovar")}</button>}
                {["proposed", "waiting", "scheduled"].includes(p.status) && <button type="button" className="btn-quiet" onClick={() => cancel.mutate(p.id)}>{t("Cancelar")}</button>}
              </td>}
            </tr>
          ))}</tbody>
        </table></div>
      )}
      <ErrorNote error={approve.error ?? cancel.error} />
    </section>
  );
}

export default function CustomerPublishing() {
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const q = useQuery({ queryKey: ["customer-publishing"], queryFn: () => api.get<State>("/api/account/publishing"), refetchInterval: 60_000 });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["customer-publishing"] });
  const s = q.data;
  return (
    <Shell>
      {params.get("conectado") && <Notice tone="success">{t("Rede conectada.")}</Notice>}
      {params.get("erro") && <Notice tone="danger">{params.get("erro")}</Notice>}
      <ErrorNote error={q.error} />
      {q.isLoading && <Skeleton label={t("Carregando a publicação automática")} />}
      {s && !s.allowed && <Upgrade />}
      {s && s.allowed && (
        <>
          {(s.needs_action ?? []).length > 0 && (
            <Notice tone="danger"><CircleAlert size={15} aria-hidden className="mr-1 inline" />{t("{n} publicação(ões) precisam da sua atenção.", { n: String(s.needs_action!.length) })}</Notice>
          )}
          <Switch s={s} refresh={refresh} />
          <Today s={s} />
          <Networks s={s} refresh={refresh} />
          <Rules s={s} refresh={refresh} />
          <Queue title={t("Próximas publicações")} items={s.upcoming ?? []} empty={t("Nada na fila. Os próximos Shorts dos seus canais aparecem aqui.")} actions refresh={refresh} />
          <Queue title={t("Histórico")} items={s.history ?? []} empty={t("Nenhuma publicação nos últimos 30 dias.")} refresh={refresh} />
        </>
      )}
    </Shell>
  );
}
