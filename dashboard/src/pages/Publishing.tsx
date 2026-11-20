import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronDown, ExternalLink, Link2, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type Streamer } from "../api";
import { Disclosure, Empty, ErrorNote, Notice, PageHeader, Skeleton } from "../components/ui";
import { distributionView, distributionViews } from "../lib/publishing";
import PrivacyPicker, { initialPrivacy } from "../components/PrivacyPicker";
import { Reports } from "../components/EditorialOps";
import Agenda, { PauseControl } from "../components/Agenda";
import { t, locale } from "../i18n";

export type PubApp = { platform: string; name: string; configured: boolean; client_id: string; has_secret: boolean; redirect_uri: string; validated: boolean; config_id: string; callback_url: string; flow: string };
export type PubAccount = { id: number; platform: string; platform_name: string; name: string; handle: string; status: string;
  meta: { privacy_options?: string[]; upload_limit?: number; units_limit?: number }; quota: { day?: string; uploads?: number; units?: number }; last_error: string; connected_at: string };
type Rule = { id: number; account_id: number; streamer_id: number | null; enabled: boolean; mode: string; require_approval: boolean;
  min_score: number; privacy: string; allow_public_auto: boolean; max_per_day: number; per_slot: number; slots: string[]; timezone: string;
  templates: Record<string, { title?: string; description?: string; hashtags?: string }> };
export type Pub = { id: number; clip_id: number | null; clip_title: string | null; clip_score: number | null; account_id: number;
  platform: string; status: string; origin: string; scheduled_at: string | null; title: string; privacy: string; remote_url: string;
  remote_status: Record<string, unknown>; metrics: Record<string, number>; attempts: number; last_error: string;
  log: { at: string; event: string; detail: string }[]; published_at: string | null; created_at: string;
  upload_progress: { size?: number; sent?: number } };
type Overview = { apps: Record<string, PubApp>; accounts: PubAccount[]; rules: Rule[]; counts: Record<string, number> };

export const pubStatus: Record<string, [string, string]> = {
  draft: [t("Rascunho"), "text-ink-muted"], scheduled: [t("Agendado"), "text-take"], uploading: [t("Enviando"), "text-processing"],
  processing: [t("Processando na plataforma"), "text-processing"], published: [t("Publicado"), "text-ok"], failed: [t("Falhou"), "text-tally"],
  cancelled: [t("Cancelado"), "text-ink-faint"], proposed: [t("Proposta"), "text-take"], expired: [t("Expirou"), "text-ink-faint"],
  uncertain: [t("Resultado incerto: confira no perfil"), "text-warning"],
};
const privacyLabel: Record<string, string> = {
  private: t("Privado"), unlisted: t("Não listado"), public: t("Público"), SELF_ONLY: t("Só eu (TikTok)"),
  MUTUAL_FOLLOW_FRIENDS: t("Amigos"), FOLLOWER_OF_CREATOR: t("Seguidores"), PUBLIC_TO_EVERYONE: t("Público (TikTok)"),
};
const redirectHelp: Record<string, string> = {
  youtube: t("Google Cloud → APIs e serviços → Credenciais → ID do cliente OAuth do tipo “App para computador”. Use http://127.0.0.1:8765/ como URI de retorno (o servidor está só na rede local; você cola o endereço final aqui)."),
  tiktok: t("TikTok for Developers → seu app → Login Kit + Content Posting API. Cadastre um Redirect URI https (ex.: https://localhost/asc-callback) e use o mesmo aqui."),
  instagram: t("Meta for Developers → seu app → API do Instagram (login unificado): cadastre como URL de redirecionamento exatamente o endereço do campo abaixo e copie o config_id da configuração (Login do Facebook para Empresas → Configurações). App ID e chave secreta: Configurações do app → Básico."),
};

const publicationTime = new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short", timeZone: "America/Sao_Paulo" });
const publicationDay = new Intl.DateTimeFormat(locale, { weekday: "short", day: "2-digit", month: "2-digit", timeZone: "America/Sao_Paulo" });
const when = (iso: string | null) => iso ? publicationTime.format(new Date(iso)) : t("assim que possível");

export default function Publishing() {
  const [qs] = useSearchParams();
  const view = distributionView(qs);
  const ov = useQuery({ queryKey: ["publish-overview"], queryFn: () => api.get<Overview>("/api/publish/overview"), refetchInterval: 20_000 });
  const descriptions = {
    agenda: t("O que vai ao ar e quando, no horário de Brasília. Sugestões da IA esperam a sua confirmação."),
    envios: t("Cada envio com o andamento, falhas e resultados que precisam de conferência."),
    contas: t("As contas que recebem os cortes e as integrações com cada plataforma."),
    regras: t("Quando a IA pode sugerir ou agendar sozinha, em quais horários e com qual privacidade."),
    relatorios: t("Visualizações e curtidas informadas pelas próprias plataformas."),
  };
  const config = view.key === "contas" || view.key === "regras";
  const tabs = distributionViews.filter((item) => (item.key === "contas" || item.key === "regras") === config);
  return (
    <div>
      <PageHeader title={config ? t("Contas e regras de publicação") : t("Agenda de publicações")} description={descriptions[view.key]} />
      <nav className="view-switch mb-5" aria-label={t("Áreas da distribuição")}>
        {tabs.map((item) => (
          <Link key={item.key} to={item.to} className="view-switch-item" aria-current={view.key === item.key ? "page" : undefined}>{item.label}</Link>
        ))}
      </nav>
      <div className="section-flow">
        {view.key === "envios" && <PauseControl />}
        {qs.get("conectado") && <Notice>{t("Conta conectada.")}</Notice>}
        {qs.get("erro") && <ErrorNote error={t("Conexão não concluída: {get}", { get: qs.get("erro") })} />}
        <ErrorNote error={ov.error} />
        {view.key === "envios" && <Calendar accounts={ov.data?.accounts ?? []} />}
        {view.key === "agenda" && <Agenda />}
        {view.key === "contas" && (ov.data ? (
          <>
            <Accounts ov={ov.data} />
            <Disclosure title={t("Configuração dos aplicativos OAuth")}><Apps apps={ov.data.apps} /></Disclosure>
          </>
        ) : ov.isLoading && <Skeleton label={t("Carregando contas")} />)}
        {view.key === "regras" && (
          <>
            <Rights />
            {ov.data && <Rules ov={ov.data} />}
            {ov.isLoading && <Skeleton label={t("Carregando regras")} />}
          </>
        )}
        {view.key === "relatorios" && <Reports />}
      </div>
    </div>
  );
}

function Section({ title, children, hint }: { title: string; children: React.ReactNode; hint?: string }) {
  return (
    <section>
      <h2 className="display mb-2 text-xl">{title}</h2>
      {hint && <p className="mb-3 text-xs text-ink-muted">{hint}</p>}
      {children}
    </section>
  );
}

function Apps({ apps }: { apps: Record<string, PubApp> }) {
  return (
    <Section title={t("Aplicativos das plataformas")} hint={t("Credenciais do app que você cria no console de cada plataforma. O segredo é cifrado e nunca volta para o navegador.")}>
      <div className="grid gap-4 lg:grid-cols-3">{Object.values(apps).map((a) => <AppCard key={a.platform} app={a} />)}</div>
    </Section>
  );
}

function AppCard({ app }: { app: PubApp }) {
  const qc = useQueryClient();
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [uri, setUri] = useState(app.redirect_uri || (app.platform === "youtube" ? "http://127.0.0.1:8765/" : app.platform === "instagram" ? app.callback_url : "https://localhost/asc-callback"));
  const [configId, setConfigId] = useState(app.config_id ?? "");
  const save = useMutation({
    mutationFn: () => api.put(`/api/publish/apps/${app.platform}`, { client_id: cid || undefined, client_secret: secret, redirect_uri: uri,
      ...(app.platform === "instagram" ? { config_id: configId } : {}) }),
    onSuccess: () => { setSecret(""); qc.invalidateQueries({ queryKey: ["publish-overview"] }); },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/publish/apps/${app.platform}`),
    onSuccess: () => { setCid(""); setSecret(""); qc.invalidateQueries({ queryKey: ["publish-overview"] }); },
  });
  return (
    <div className="rounded-lg border border-suite-line p-3 text-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">{app.name}</span>
        <span className={`text-xs ${app.configured ? "text-ok" : "text-ink-muted"}`}>{app.configured ? t("configurado") : t("não configurado")}{app.validated ? "" : t(" · integração ainda não validada")}</span>
      </div>
      <p className="mb-2 text-[11px] text-ink-faint">{redirectHelp[app.platform]}</p>
      <label className="mb-2 block"><span className="text-xs text-ink-muted">{t("Client ID")} {app.client_id && `(atual: ${app.client_id})`}</span>
        <input className="field" value={cid} onChange={(e) => setCid(e.target.value)} placeholder={app.client_id ? "manter" : ""} /></label>
      <label className="mb-2 block"><span className="text-xs text-ink-muted">{t("Client secret")}</span>
        <input className="field" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={app.has_secret ? "••••• (manter)" : ""} /></label>
      <label className="mb-2 block"><span className="text-xs text-ink-muted">{t("URI de retorno (igual ao cadastrado no console)")}</span>
        <input className="field" value={uri} onChange={(e) => setUri(e.target.value)} /></label>
      {app.platform === "instagram" && (
        <label className="mb-2 block"><span className="text-xs text-ink-muted">{t("config_id (login unificado; vazio = login do Instagram antigo)")}</span>
          <input className="field" value={configId} inputMode="numeric" onChange={(e) => setConfigId(e.target.value.trim())} /></label>
      )}
      {app.platform === "instagram" && app.configured && app.flow !== "unified" && (
        <p className="mb-2 text-xs text-tally">{t("Falta o config_id: sem ele a Meta recusa a conexão (“Invalid platform app”).")}</p>
      )}
      <ErrorNote error={save.error ?? remove.error} />
      <div className="flex gap-2">
        <button className="btn-quiet" disabled={save.isPending || (!cid && !app.client_id)} onClick={() => save.mutate()}>{t("Salvar")}</button>
        {app.configured && <button className="btn-danger" disabled={remove.isPending}
          onClick={() => confirm(t("Remover as credenciais de {name}?", { name: app.name })) && remove.mutate()}>{t("Remover")}</button>}
      </div>
    </div>
  );
}

function Accounts({ ov }: { ov: Overview }) {
  const qc = useQueryClient();
  const [pending, setPending] = useState<{ platform: string; url: string } | null>(null);
  const [pasted, setPasted] = useState("");
  const start = useMutation({
    mutationFn: (platform: string) => api.post<{ auth_url: string }>(`/api/publish/oauth/${platform}/start`),
    onSuccess: (d, platform) => { setPending({ platform, url: d.auth_url }); window.open(d.auth_url, "_blank", "noopener"); },
  });
  const finish = useMutation({
    mutationFn: () => api.post(`/api/publish/oauth/${pending!.platform}/finish`, { redirected_url: pasted }),
    onSuccess: () => { setPending(null); setPasted(""); qc.invalidateQueries({ queryKey: ["publish-overview"] }); },
  });
  const disconnect = useMutation({
    mutationFn: (id: number) => api.del(`/api/publish/accounts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["publish-overview"] }),
  });
  return (
    <Section title={t("Contas por plataforma")}>
      <div className="mb-3 flex flex-wrap gap-2">
        {Object.values(ov.apps).map((a) => (
          <button key={a.platform} className="btn-quiet" disabled={!a.configured || start.isPending} onClick={() => start.mutate(a.platform)}>
            <Link2 className="h-4 w-4" /> {t("Conectar conta {name}", { name: a.name })}
          </button>
        ))}
      </div>
      {pending && (
        <div className="mb-4 rounded-lg border border-take/50 bg-take-dim/30 p-3 text-sm">
          <p className="mb-2">{t("1. Autorize na aba que abriu (")}<a className="text-take underline" href={pending.url} target="_blank" rel="noreferrer">{t("abrir de novo")}</a>{t("). 2. A página final pode dar erro de conexão — é esperado. Copie o endereço completo da barra (com")} <code>?code=</code>{t(") e cole aqui:")}</p>
          <input className="field" aria-label={t("Endereço de retorno OAuth")} value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="http://127.0.0.1:8765/?state=...&code=..." />
          <ErrorNote error={finish.error} />
          <div className="mt-2 flex gap-2">
            <button className="btn-take" disabled={!pasted || finish.isPending} onClick={() => finish.mutate()}>{t("Concluir conexão")}</button>
            <button className="btn-quiet" onClick={() => setPending(null)}>{t("Cancelar")}</button>
          </div>
        </div>
      )}
      <ErrorNote error={start.error ?? disconnect.error} />
      {ov.accounts.length === 0 ? <Empty title={t("Nenhuma conta conectada")}>{t("Configure o app OAuth e clique em Conectar.")}</Empty> : (
        <ul className="account-roster">{ov.accounts.map(a => <li key={a.id} className="account-destination">
          <div className="min-w-0"><span className="text-sm text-ink-muted">{a.platform_name}</span>
            <h3 className="display break-words text-lg">{a.name}</h3><p className="break-words text-sm text-ink-muted">{a.handle}</p></div>
          <div className="min-w-0"><span className={a.status === "connected" ? "text-ok" : "text-tally"}>{a.status === "connected" ? t("Conectada") : a.status}</span>
            {a.last_error && <p className="break-words text-sm text-ink-muted">{a.last_error}</p>}
            {a.platform === "youtube" && <p className="text-sm text-ink-muted">{a.quota?.uploads ?? 0} {t("/ {v} envios hoje", { v: a.meta.upload_limit ?? 100 })}</p>}</div>
          {a.status !== "revoked" && <button className="btn-quiet" onClick={() => confirm(t("Desconectar e apagar os tokens desta conta?")) && disconnect.mutate(a.id)}>
            <Trash2 size={14} aria-hidden/> {t("Desconectar")}</button>}
        </li>)}</ul>
      )}
    </Section>
  );
}

function Rights() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers") });
  const toggle = useMutation({
    mutationFn: (s: Streamer) => api.patch(`/api/streamers/${s.id}`, { preferences: { ...(s.preferences ?? {}), republish_allowed: !(s.preferences as Record<string, unknown>)?.republish_allowed } }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["streamers"] }),
  });
  return (
    <Section title={t("Direitos de republicação")} hint={t("Marque apenas streamers que autorizaram a republicação dos cortes (ou cujo conteúdo é seu). Sem essa marca, nada entra nas regras automáticas; na publicação manual você confirma caso a caso.")}>
      <div className="flex flex-wrap gap-2">
        {(st.data ?? []).filter((s) => s.platform !== "file").map((s) => {
          const on = Boolean((s.preferences as Record<string, unknown>)?.republish_allowed);
          return (
            <button key={s.id} onClick={() => toggle.mutate(s)} aria-pressed={on}
              className={`rounded-full border px-3 py-1 text-xs ${on ? "border-ok bg-ok/15 text-ok" : "border-suite-line text-ink-muted"}`}>
              {s.display_name} {on ? t("· autorizado") : t("· sem autorização")}
            </button>
          );
        })}
      </div>
      <ErrorNote error={toggle.error} />
    </Section>
  );
}

const emptyRule = (a: PubAccount): Omit<Rule, "id"> => ({ account_id: a.id, streamer_id: null, enabled: true, mode: "manual", require_approval: true,
  min_score: 70, privacy: initialPrivacy(a.meta.privacy_options ?? []), allow_public_auto: false, max_per_day: 3, per_slot: 1, slots: ["12:00", "18:00", "21:00"], timezone: "America/Sao_Paulo", templates: {} });

type Moved = { id: number; clip_id: number | null; platform: string; title: string; from: string | null; to: string };
const fmtWhen = (iso: string | null) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)} ${iso.slice(11, 16)}` : t("sem horário"));

/** Preview, then bring the account's scheduled posts forward into the next free peak slots. */
function RepackButton({ accountId, name }: { accountId: number; name: string }) {
  const qc = useQueryClient();
  const [plan, setPlan] = useState<Moved[] | null>(null);
  const preview = useMutation({
    mutationFn: () => api.post<{ moved: Moved[] }>("/api/publish/repack", { account_id: accountId, dry_run: true }),
    onSuccess: (r) => setPlan(r.moved),
  });
  const apply = useMutation({
    mutationFn: () => api.post<{ moved: Moved[] }>("/api/publish/repack", { account_id: accountId, dry_run: false }),
    onSuccess: () => { setPlan(null); qc.invalidateQueries(); },
  });
  const changes = (plan ?? []).filter((m) => m.from !== m.to);
  return (
    <>
      <button className="btn-quiet ml-1 px-2 py-1 text-xs" disabled={preview.isPending} onClick={() => preview.mutate()}>
        {preview.isPending ? t("Calculando…") : t("Reorganizar agenda")}</button>
      <ErrorNote error={preview.error ?? apply.error} />
      {apply.isSuccess && <Notice>{t("Agenda reorganizada.")}</Notice>}
      {plan && (
        <div role="dialog" aria-label={t("Reorganizar a agenda de {name}", { name: name })} className="panel panel-body mt-2 max-w-xl whitespace-normal text-left">
          <p className="text-sm font-medium">{t("Reorganizar a agenda de")} {name}</p>
          {changes.length === 0 ? <p className="mt-1 text-sm text-ink-muted">{t("Nada muda: os envios já estão nos próximos horários livres.")}</p> : (
            <>
              <p className="mt-1 text-sm text-ink-muted">{changes.length} {t("envio(s) mudam de horário, seguindo os horários, o limite por dia e os vídeos por horário da regra. Os melhores cortes vão primeiro. Sugestões que esperam confirmação não mudam.")}</p>
              <ul className="mt-2 max-h-56 overflow-auto text-xs">
                {changes.map((m) => <li key={m.id} className="py-0.5"><span className="font-mono">{fmtWhen(m.from)} → {fmtWhen(m.to)}</span> · {m.title || t("Corte #{clip_id}", { clip_id: m.clip_id })}</li>)}
              </ul>
            </>
          )}
          <div className="mt-3 flex flex-wrap justify-end gap-2">
            <button className="btn-quiet" onClick={() => setPlan(null)}>{t("Voltar")}</button>
            {changes.length > 0 && <button className="btn-take" disabled={apply.isPending} onClick={() => apply.mutate()}>
              {apply.isPending ? t("Aplicando…") : t("Mudar {length} horário(s)", { length: changes.length })}</button>}
          </div>
        </div>
      )}
    </>
  );
}

function Rules({ ov }: { ov: Overview }) {
  const [editing, setEditing] = useState<(Omit<Rule, "id"> & { id?: number }) | null>(null);
  const st = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers") });
  const acctName = (id: number) => ov.accounts.find((a) => a.id === id);
  return (
    <Section title={t("Regras de publicação")} hint={t("Modo manual: você escolhe cada corte. Semiautomático: a IA propõe os melhores cortes nos horários e você confirma com um clique. Agendado: cortes elegíveis entram nos horários do calendário. Imediato: publica assim que elegível. Público automático só com ativação explícita.")}>
      {ov.accounts.filter((a) => a.status === "connected").map((a) => (
        <button key={a.id} className="btn-quiet mb-3 mr-2" onClick={() => setEditing(emptyRule(a))}>{t("Nova regra para")} {a.name}</button>
      ))}
      {ov.rules.length > 0 && (
        <table className="mb-3 w-full text-sm">
          <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">{t("Conta")}</th><th>{t("Streamer")}</th><th>{t("Modo")}</th><th>{t("Nota mín.")}</th><th>{t("Aprovação")}</th><th>{t("Privacidade")}</th><th>{t("Por dia")}</th><th>{t("Por horário")}</th><th>{t("Horários")}</th><th /></tr></thead>
          <tbody>{ov.rules.map((r) => (
            <tr key={r.id} className={`border-t border-suite-line/50 ${r.enabled ? "" : "opacity-50"}`}>
              <td className="py-1">{acctName(r.account_id)?.name}</td>
              <td>{r.streamer_id ? st.data?.find((s) => s.id === r.streamer_id)?.display_name : t("todos")}</td>
              <td>{({ manual: t("manual"), semi: t("semiautomático"), scheduled: t("agendado"), immediate: t("imediato") } as Record<string, string>)[r.mode]}</td>
              <td>{r.min_score}</td><td>{r.require_approval ? t("exigida") : t("não")}</td>
              <td>{privacyLabel[r.privacy] ?? r.privacy}{r.allow_public_auto && t(" · público automático")}
                {acctName(r.account_id)?.meta.privacy_options && !acctName(r.account_id)!.meta.privacy_options!.includes(r.privacy) &&
                  <span className="block text-xs text-tally">{t("inválida para esta conta: edite a regra (nada é publicado por ela assim)")}</span>}</td>
              <td>{r.max_per_day}</td><td>{r.per_slot ?? 1}</td><td className="text-xs">{r.slots.join(", ")}</td>
              <td className="whitespace-nowrap"><button className="btn-quiet px-2 py-1 text-xs" onClick={() => setEditing(r)}>{t("Editar")}</button>
                {r.enabled && <RepackButton accountId={r.account_id} name={acctName(r.account_id)?.name ?? ""} />}</td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {editing && <RuleForm rule={editing} accounts={ov.accounts} streamers={st.data ?? []} onClose={() => setEditing(null)} />}
    </Section>
  );
}

function RuleForm({ rule, accounts, streamers, onClose }: { rule: Omit<Rule, "id"> & { id?: number }; accounts: PubAccount[]; streamers: Streamer[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [r, setR] = useState(rule);
  const acct = accounts.find((a) => a.id === r.account_id);
  const tpl = r.templates[acct?.platform ?? ""] ?? {};
  const setTpl = (k: string, v: string) => setR({ ...r, templates: { ...r.templates, [acct!.platform]: { ...tpl, [k]: v } } });
  const save = useMutation({
    mutationFn: () => (r.id ? api.put(`/api/publish/rules/${r.id}`, r) : api.post("/api/publish/rules", r)),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["publish-overview"] }); onClose(); },
  });
  const del = useMutation({ mutationFn: () => api.del(`/api/publish/rules/${r.id}`), onSuccess: () => { qc.invalidateQueries({ queryKey: ["publish-overview"] }); onClose(); } });
  return (
    <div className="rounded-lg border border-suite-line p-4 text-sm">
      <div className="grid gap-3 md:grid-cols-3">
        <label><span className="text-xs text-ink-muted">{t("Streamer")}</span>
          <select className="field" value={r.streamer_id ?? ""} onChange={(e) => setR({ ...r, streamer_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">{t("Todos os autorizados")}</option>{streamers.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
          </select></label>
        <label><span className="text-xs text-ink-muted">{t("Modo")}</span>
          <select className="field" value={r.mode} onChange={(e) => setR({ ...r, mode: e.target.value })}>
            <option value="manual">{t("Manual")}</option>
            <option value="semi" disabled={acct?.platform === "tiktok"}>{t("Semiautomático (a IA propõe, você confirma)")}</option>
            <option value="scheduled" disabled={acct?.platform === "tiktok"}>{t("Automático agendado")}</option>
            <option value="immediate" disabled={acct?.platform === "tiktok"}>{t("Automático imediato")}</option>
          </select></label>
        <div><span className="text-xs text-ink-muted">{t("Privacidade")}</span>
          <PrivacyPicker options={acct?.meta.privacy_options ?? ["private"]} value={r.privacy} platform={acct?.platform}
            onChange={(v) => setR({ ...r, privacy: v })} />
          {acct?.platform === "instagram" && r.mode === "semi" && <p className="mt-1 text-xs text-ink-muted">{t("No semiautomático cada Reel só sai depois que você confirma a proposta.")}</p>}
        </div>
        <label><span className="text-xs text-ink-muted">{t("Nota editorial mínima")}</span>
          <input className="field" type="number" min={0} max={100} value={r.min_score} onChange={(e) => setR({ ...r, min_score: Number(e.target.value) })} /></label>
        <label><span className="text-xs text-ink-muted">{t("Máximo por dia")}</span>
          <input className="field" type="number" min={1} max={50} value={r.max_per_day} onChange={(e) => setR({ ...r, max_per_day: Number(e.target.value) })} /></label>
        <label><span className="text-xs text-ink-muted">{t("Vídeos por horário (1 a 6)")}</span>
          <input className="field" type="number" min={1} max={6} value={r.per_slot ?? 1} onChange={(e) => setR({ ...r, per_slot: Number(e.target.value) })} />
          <span className="mt-1 block text-xs text-ink-muted">{t("Shorts e Reels: 3 ou 4 no mesmo horário de pico alimentam mais o algoritmo. Os envios saem com 1 minuto de intervalo.")}</span></label>
        <label><span className="text-xs text-ink-muted">{t("Horários (HH:MM, vírgula)")}</span>
          <input className="field" value={r.slots.join(", ")} onChange={(e) => setR({ ...r, slots: e.target.value.split(/[,\s]+/).filter(Boolean) })} /></label>
      </div>
      <div className="mt-3 flex flex-wrap gap-4">
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.require_approval} onChange={(e) => setR({ ...r, require_approval: e.target.checked })} /> {t("Exigir aprovação humana")}</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.enabled} onChange={(e) => setR({ ...r, enabled: e.target.checked })} /> {t("Regra ativa")}</label>
        <label className="flex items-center gap-2 text-tally"><input type="checkbox" checked={r.allow_public_auto} onChange={(e) => setR({ ...r, allow_public_auto: e.target.checked })} /> {t("Permitir postagem PÚBLICA automática")}</label>
      </div>
      {acct && (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <label><span className="text-xs text-ink-muted">{t("Modelo de título (")}{"{title}"}, {"{streamer}"})</span><input className="field" value={tpl.title ?? ""} placeholder={"{title}"} onChange={(e) => setTpl("title", e.target.value)} /></label>
          <label><span className="text-xs text-ink-muted">{t("Modelo de descrição")}</span><input className="field" value={tpl.description ?? ""} placeholder="{description}" onChange={(e) => setTpl("description", e.target.value)} /></label>
          <label><span className="text-xs text-ink-muted">{t("Hashtags extras")}</span><input className="field" value={tpl.hashtags ?? ""} placeholder={t("gtarp shorts")} onChange={(e) => setTpl("hashtags", e.target.value)} /></label>
        </div>
      )}
      <ErrorNote error={save.error ?? del.error} />
      <div className="mt-3 flex gap-2">
        <button className="btn-take" disabled={save.isPending || !r.privacy} onClick={() => save.mutate()}>{t("Salvar regra")}</button>
        {r.id && <button className="btn-danger" onClick={() => del.mutate()}>{t("Excluir")}</button>}
        <button className="btn-quiet" onClick={onClose}>{t("Fechar")}</button>
      </div>
    </div>
  );
}

const publicationFilters = [
  { key: "all", label: t("Todas"), statuses: [] },
  { key: "pending", label: t("Pendentes"), statuses: ["draft", "proposed"] },
  { key: "scheduled", label: t("Agendadas"), statuses: ["scheduled"] },
  { key: "processing", label: t("Em processamento"), statuses: ["uploading", "processing"] },
  { key: "published", label: t("Publicadas"), statuses: ["published"] },
  { key: "failed", label: t("Com falha"), statuses: ["failed"] },
  { key: "uncertain", label: t("Resultado incerto"), statuses: ["uncertain"] },
  { key: "closed", label: t("Encerradas"), statuses: ["cancelled", "expired"] },
];

function Calendar({ accounts }: { accounts: PubAccount[] }) {
  const qc = useQueryClient();
  const pubs = useQuery({ queryKey: ["publications", 60], queryFn: () => api.get<Pub[]>("/api/publish/publications?days=60"), refetchInterval: 10_000 });
  const act = useMutation({
    mutationFn: ({ id, a }: { id: number; a: "cancel" | "retry" | "metrics" }) => api.post(`/api/publish/publications/${id}/${a}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["publications"] }),
  });
  const [open, setOpen] = useState<number | null>(null);
  const [filter, setFilter] = useState("all");
  const publications = pubs.data ?? [];
  const statuses = publicationFilters.find((item) => item.key === filter)?.statuses ?? [];
  const visible = statuses.length ? publications.filter((pub) => statuses.includes(pub.status)) : publications;
  const groups = new Map<string, Pub[]>();
  for (const p of visible) {
    const d = publicationDay.format(new Date(p.scheduled_at ?? p.published_at ?? p.created_at));
    groups.set(d, [...(groups.get(d) ?? []), p]);
  }
  const acct = (id: number) => accounts.find((a) => a.id === id);
  return (
    <section className="space-y-4" aria-label={t("Acompanhamento de publicações")}>
      <nav className="view-switch" aria-label={t("Filtrar publicações por estado")}>
        {publicationFilters.map((item) => {
          const count = item.statuses.length ? publications.filter((pub) => item.statuses.includes(pub.status)).length : publications.length;
          return (
            <button type="button" key={item.key} className="view-switch-item" aria-pressed={filter === item.key}
              onClick={() => { setFilter(item.key); setOpen(null); }}>
              {item.label}{pubs.data && <span className="rounded border border-suite-line px-1.5 font-mono text-xs">{count}</span>}
            </button>
          );
        })}
      </nav>
      <ErrorNote error={pubs.error ?? act.error} />
      {act.isSuccess && <Notice>
        {{ cancel: t("Publicação cancelada."), retry: t("Reenvio solicitado."), metrics: t("Métricas atualizadas.") }[act.variables!.a]}
      </Notice>}
      {pubs.isLoading && <Skeleton label={t("Carregando publicações")} />}
      {pubs.data && visible.length === 0 && <Empty title={filter === "all" ? t("Nenhuma publicação nos últimos 60 dias") : t("Nenhuma publicação neste estado")}>
        {filter === "all" ? t("Envie um corte pela Central de cortes ou acompanhe as propostas no calendário.") : t("Escolha outro estado para ver mais publicações.")}
      </Empty>}
      <div className="space-y-4">
        {[...groups.entries()].map(([day, items]) => (
          <div key={day} className="panel">
            <h2 className="panel-heading text-sm font-semibold capitalize text-ink-muted">{day}</h2>
            <ul className="divide-y divide-suite-line">
              {items.map((p) => {
                const [label, tone] = pubStatus[p.status] ?? [p.status, ""];
                const prog = p.upload_progress?.size ? Math.round(((p.upload_progress.sent ?? 0) / p.upload_progress.size) * 100) : null;
                return (
                  <li key={p.id} className="min-w-0 px-4 py-3 text-sm sm:px-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <button className="min-w-0 rounded-md text-left hover:text-take" aria-expanded={open === p.id} onClick={() => setOpen(open === p.id ? null : p.id)}>
                        <span className="flex items-center gap-2 break-words font-semibold"><ChevronDown size={15} className={open === p.id ? "shrink-0 rotate-180" : "shrink-0"} aria-hidden />{p.title || t("Publicação sem título")}</span>
                        <span className="ml-5 block break-words text-xs text-ink-muted">{acct(p.account_id)?.platform_name} · {acct(p.account_id)?.name} · {privacyLabel[p.privacy] ?? p.privacy} · {p.origin === "auto" ? t("automático") : t("manual")} · {when(p.scheduled_at)}</span>
                      </button>
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <span className={`inline-flex items-center gap-1.5 rounded bg-suite-raised px-2 py-1 text-xs font-semibold ${tone}`}>{p.status === "uncertain" && <AlertTriangle size={14} aria-hidden />}{label}{p.status === "uploading" && prog !== null ? ` ${prog}%` : ""}</span>
                        {p.remote_url && <a className="text-take" href={p.remote_url} target="_blank" rel="noreferrer" aria-label={t("Abrir publicação")}><ExternalLink className="h-4 w-4" /></a>}
                        {["draft", "scheduled"].includes(p.status) && <button className="btn-quiet px-2 py-1 text-xs" disabled={act.isPending} onClick={() => window.confirm(t("Cancelar esta publicação?")) && act.mutate({ id: p.id, a: "cancel" })}><X className="h-3 w-3" /> {t("Cancelar")}</button>}
                        {p.status === "failed" && <button className="btn-quiet px-2 py-1 text-xs" disabled={act.isPending} onClick={() => act.mutate({ id: p.id, a: "retry" })}><RotateCcw className="h-3 w-3" /> {t("Reenviar")}</button>}
                        {p.status === "uncertain" && <ResolveButtons id={p.id} />}
                        {p.status === "published" && <button className="btn-quiet px-2 py-1 text-xs" disabled={act.isPending} onClick={() => act.mutate({ id: p.id, a: "metrics" })}>{t("Atualizar métricas")}</button>}
                      </div>
                    </div>
                    {p.last_error && <p className="mt-1 text-xs text-tally">{p.last_error}</p>}
                    {Object.keys(p.metrics ?? {}).length > 0 && <p className="mt-1 text-xs text-ink-muted">{Object.entries(p.metrics).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>}
                    {open === p.id && (
                      <ul className="mt-2 space-y-0.5 border-t border-suite-line/50 pt-2 text-[11px] text-ink-muted">
                        {p.log.map((l, i) => <li key={i}>{when(l.at)} — {l.event}{l.detail ? `: ${l.detail}` : ""}</li>)}
                        {p.clip_id && <li><Link className="text-take hover:underline" to={`/cortes/${p.clip_id}?status=`}>{t("Corte #")}{p.clip_id}</Link> {t("(nota {toFixed})", { toFixed: p.clip_score?.toFixed(0) })}</li>}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}

/** An uncertain publication (the video may already be on the profile): the person checks and says. */
function ResolveButtons({ id }: { id: number }) {
  const qc = useQueryClient();
  const res = useMutation({
    mutationFn: (b: { outcome: "published" | "not_published"; url?: string }) => api.post(`/api/publish/publications/${id}/resolve`, b),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["publications"] }),
  });
  return (
    <span className="inline-flex flex-wrap gap-1">
      <button className="btn-quiet px-2 py-1 text-xs" disabled={res.isPending}
        onClick={() => { const url = window.prompt(t("Está no perfil. Cole o link do vídeo (opcional):")); if (url !== null) res.mutate({ outcome: "published", url: url.trim() }); }}>
        {t("Está no perfil")}</button>
      <button className="btn-quiet px-2 py-1 text-xs" disabled={res.isPending}
        onClick={() => window.confirm(t("Conferiu no perfil e o vídeo NÃO foi publicado? Isso libera um novo envio.")) && res.mutate({ outcome: "not_published" })}>
        {t("Não foi publicado")}</button>
      <ErrorNote error={res.error} />
    </span>
  );
}
