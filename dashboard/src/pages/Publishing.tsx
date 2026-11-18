import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Link2, RotateCcw, Trash2, X } from "lucide-react";
import { useState } from "react";
import { api, type Streamer } from "../api";
import { Empty, ErrorNote, PageHeader } from "../components/ui";
import { EditorialCalendar, PauseBanner, Reports } from "../components/EditorialOps";

export type PubApp = { platform: string; name: string; configured: boolean; client_id: string; has_secret: boolean; redirect_uri: string; validated: boolean; config_id: string; callback_url: string; flow: string };
export type PubAccount = { id: number; platform: string; platform_name: string; name: string; handle: string; status: string;
  meta: { privacy_options?: string[]; upload_limit?: number; units_limit?: number }; quota: { day?: string; uploads?: number; units?: number }; last_error: string; connected_at: string };
type Rule = { id: number; account_id: number; streamer_id: number | null; enabled: boolean; mode: string; require_approval: boolean;
  min_score: number; privacy: string; allow_public_auto: boolean; max_per_day: number; slots: string[]; timezone: string;
  templates: Record<string, { title?: string; description?: string; hashtags?: string }> };
export type Pub = { id: number; clip_id: number | null; clip_title: string | null; clip_score: number | null; account_id: number;
  platform: string; status: string; origin: string; scheduled_at: string | null; title: string; privacy: string; remote_url: string;
  remote_status: Record<string, unknown>; metrics: Record<string, number>; attempts: number; last_error: string;
  log: { at: string; event: string; detail: string }[]; published_at: string | null; created_at: string;
  upload_progress: { size?: number; sent?: number } };
type Overview = { apps: Record<string, PubApp>; accounts: PubAccount[]; rules: Rule[]; counts: Record<string, number> };

export const pubStatus: Record<string, [string, string]> = {
  draft: ["Rascunho", "text-ink-muted"], scheduled: ["Agendado", "text-take"], uploading: ["Enviando", "text-take"],
  processing: ["Processando na plataforma", "text-take"], published: ["Publicado", "text-ok"], failed: ["Falhou", "text-tally"],
  cancelled: ["Cancelado", "text-ink-faint"], proposed: ["Proposta", "text-take"], expired: ["Expirou", "text-ink-faint"],
};
const privacyLabel: Record<string, string> = {
  private: "Privado", unlisted: "Não listado", public: "Público", SELF_ONLY: "Só eu (TikTok)",
  MUTUAL_FOLLOW_FRIENDS: "Amigos", FOLLOWER_OF_CREATOR: "Seguidores", PUBLIC_TO_EVERYONE: "Público (TikTok)",
};
const redirectHelp: Record<string, string> = {
  youtube: "Google Cloud → APIs e serviços → Credenciais → ID do cliente OAuth do tipo “App para computador”. Use http://127.0.0.1:8765/ como URI de retorno (o servidor está só na rede local; você cola o endereço final aqui).",
  tiktok: "TikTok for Developers → seu app → Login Kit + Content Posting API. Cadastre um Redirect URI https (ex.: https://localhost/asc-callback) e use o mesmo aqui.",
  instagram: "Meta for Developers → seu app → API do Instagram (login unificado): cadastre como URL de redirecionamento exatamente o endereço do campo abaixo e copie o config_id da configuração (Login do Facebook para Empresas → Configurações). App ID e chave secreta: Configurações do app → Básico.",
};

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "assim que possível");

export default function Publishing() {
  const qs = new URLSearchParams(window.location.search);
  const ov = useQuery({ queryKey: ["publish-overview"], queryFn: () => api.get<Overview>("/api/publish/overview"), refetchInterval: 20_000 });
  return (
    <div className="space-y-8">
      <PageHeader title="Publicação automática" />
      <PauseBanner />
      <p className="max-w-3xl text-sm text-ink-muted">
        Envio pelas APIs oficiais. Por padrão as regras automáticas exigem aprovação humana e usam <b>privado</b> (público
        automático só com ativação explícita); na publicação manual você escolhe Privado, Não listado ou Público. Cortes de teste manual e
        cortes rejeitados nunca são publicados automaticamente, e só streamers marcados com direito de republicação entram nas regras automáticas.
      </p>
      {qs.get("conectado") && <p className="rounded-md border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok">Conta conectada.</p>}
      {qs.get("erro") && <ErrorNote error={`Conexão não concluída: ${qs.get("erro")}`} />}
      <ErrorNote error={ov.error} />
      {ov.data && <Apps apps={ov.data.apps} />}
      {ov.data && <Accounts ov={ov.data} />}
      <Rights />
      {ov.data && <Rules ov={ov.data} />}
      <Section title="5. Calendário editorial" hint="Próximos 14 dias no horário de Brasília. Propostas do modo semiautomático só são enviadas depois que você confirma; você pode mudar o horário de qualquer item ainda não enviado.">
        <EditorialCalendar />
      </Section>
      {ov.data && <Calendar accounts={ov.data.accounts} />}
      <Section title="7. Relatórios" hint="Métricas oficiais das plataformas, agrupadas por categoria da história, horário e título.">
        <Reports />
      </Section>
    </div>
  );
}

function Section({ title, children, hint }: { title: string; children: React.ReactNode; hint?: string }) {
  return (
    <section>
      <h2 className="display mb-1 text-lg">{title}</h2>
      {hint && <p className="mb-3 text-xs text-ink-muted">{hint}</p>}
      {children}
    </section>
  );
}

function Apps({ apps }: { apps: Record<string, PubApp> }) {
  return (
    <Section title="1. Apps OAuth das plataformas" hint="Credenciais do app que você cria no console de cada plataforma. O segredo é cifrado e nunca volta para o navegador.">
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
        <span className={`text-xs ${app.configured ? "text-ok" : "text-ink-muted"}`}>{app.configured ? "configurado" : "não configurado"}{app.validated ? "" : " · integração ainda não validada"}</span>
      </div>
      <p className="mb-2 text-[11px] text-ink-faint">{redirectHelp[app.platform]}</p>
      <label className="mb-2 block"><span className="text-xs text-ink-muted">Client ID {app.client_id && `(atual: ${app.client_id})`}</span>
        <input className="field" value={cid} onChange={(e) => setCid(e.target.value)} placeholder={app.client_id ? "manter" : ""} /></label>
      <label className="mb-2 block"><span className="text-xs text-ink-muted">Client secret</span>
        <input className="field" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder={app.has_secret ? "••••• (manter)" : ""} /></label>
      <label className="mb-2 block"><span className="text-xs text-ink-muted">URI de retorno (igual ao cadastrado no console)</span>
        <input className="field" value={uri} onChange={(e) => setUri(e.target.value)} /></label>
      {app.platform === "instagram" && (
        <label className="mb-2 block"><span className="text-xs text-ink-muted">config_id (login unificado; vazio = login do Instagram antigo)</span>
          <input className="field" value={configId} inputMode="numeric" onChange={(e) => setConfigId(e.target.value.trim())} /></label>
      )}
      {app.platform === "instagram" && app.configured && app.flow !== "unified" && (
        <p className="mb-2 text-xs text-[#ffb4b6]">Falta o config_id: sem ele a Meta recusa a conexão (“Invalid platform app”).</p>
      )}
      <ErrorNote error={save.error ?? remove.error} />
      <div className="flex gap-2">
        <button className="btn-quiet" disabled={save.isPending || (!cid && !app.client_id)} onClick={() => save.mutate()}>Salvar</button>
        {app.configured && <button className="btn-danger" disabled={remove.isPending}
          onClick={() => confirm(`Remover as credenciais de ${app.name}?`) && remove.mutate()}>Remover</button>}
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
    <Section title="2. Contas conectadas">
      <div className="mb-3 flex flex-wrap gap-2">
        {Object.values(ov.apps).map((a) => (
          <button key={a.platform} className="btn-quiet" disabled={!a.configured || start.isPending} onClick={() => start.mutate(a.platform)}>
            <Link2 className="h-4 w-4" /> Conectar conta {a.name}
          </button>
        ))}
      </div>
      {pending && (
        <div className="mb-4 rounded-lg border border-take/50 bg-take-dim/30 p-3 text-sm">
          <p className="mb-2">1. Autorize na aba que abriu (<a className="text-take underline" href={pending.url} target="_blank" rel="noreferrer">abrir de novo</a>).
            2. A página final pode dar erro de conexão — é esperado. Copie o endereço completo da barra (com <code>?code=</code>) e cole aqui:</p>
          <input className="field" value={pasted} onChange={(e) => setPasted(e.target.value)} placeholder="http://127.0.0.1:8765/?state=...&code=..." />
          <ErrorNote error={finish.error} />
          <div className="mt-2 flex gap-2">
            <button className="btn-take" disabled={!pasted || finish.isPending} onClick={() => finish.mutate()}>Concluir conexão</button>
            <button className="btn-quiet" onClick={() => setPending(null)}>Cancelar</button>
          </div>
        </div>
      )}
      <ErrorNote error={start.error ?? disconnect.error} />
      {ov.accounts.length === 0 ? <Empty title="Nenhuma conta conectada">Configure o app OAuth e clique em Conectar.</Empty> : (
        <table className="w-full text-sm">
          <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">Conta</th><th>Plataforma</th><th>Estado</th><th>Cota hoje</th><th /></tr></thead>
          <tbody>{ov.accounts.map((a) => (
            <tr key={a.id} className="border-t border-suite-line/50">
              <td className="py-1.5">{a.name} <span className="text-ink-muted">{a.handle}</span></td>
              <td>{a.platform_name}</td>
              <td className={a.status === "connected" ? "text-ok" : "text-tally"}>{a.status === "connected" ? "conectada" : a.status}{a.last_error && <span className="block text-[11px] text-ink-faint">{a.last_error}</span>}</td>
              <td className="text-xs">{a.platform === "youtube" ? `${a.quota?.uploads ?? 0} / ${a.meta.upload_limit ?? 100} envios hoje` : "—"}</td>
              <td className="text-right">{a.status !== "revoked" && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => confirm("Desconectar e apagar os tokens desta conta?") && disconnect.mutate(a.id)}><Trash2 className="h-3 w-3" /> Desconectar</button>}</td>
            </tr>
          ))}</tbody>
        </table>
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
    <Section title="3. Direitos de republicação" hint="Marque apenas streamers que autorizaram a republicação dos cortes (ou cujo conteúdo é seu). Sem essa marca, nada entra nas regras automáticas; na publicação manual você confirma caso a caso.">
      <div className="flex flex-wrap gap-2">
        {(st.data ?? []).filter((s) => s.platform !== "file").map((s) => {
          const on = Boolean((s.preferences as Record<string, unknown>)?.republish_allowed);
          return (
            <button key={s.id} onClick={() => toggle.mutate(s)} aria-pressed={on}
              className={`rounded-full border px-3 py-1 text-xs ${on ? "border-ok bg-ok/15 text-ok" : "border-suite-line text-ink-muted"}`}>
              {s.display_name} {on ? "· autorizado" : "· sem autorização"}
            </button>
          );
        })}
      </div>
      <ErrorNote error={toggle.error} />
    </Section>
  );
}

const emptyRule = (account_id: number): Omit<Rule, "id"> => ({ account_id, streamer_id: null, enabled: true, mode: "manual", require_approval: true,
  min_score: 70, privacy: "private", allow_public_auto: false, max_per_day: 3, slots: ["12:00", "18:00", "21:00"], timezone: "America/Sao_Paulo", templates: {} });

function Rules({ ov }: { ov: Overview }) {
  const [editing, setEditing] = useState<(Omit<Rule, "id"> & { id?: number }) | null>(null);
  const st = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers") });
  const acctName = (id: number) => ov.accounts.find((a) => a.id === id);
  return (
    <Section title="4. Regras de publicação" hint="Modo manual: você escolhe cada corte. Semiautomático: a IA propõe os melhores cortes nos horários e você confirma com um clique. Agendado: cortes elegíveis entram nos horários do calendário. Imediato: publica assim que elegível. Público automático só com ativação explícita.">
      {ov.accounts.filter((a) => a.status === "connected").map((a) => (
        <button key={a.id} className="btn-quiet mb-3 mr-2" onClick={() => setEditing(emptyRule(a.id))}>Nova regra para {a.name}</button>
      ))}
      {ov.rules.length > 0 && (
        <table className="mb-3 w-full text-sm">
          <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">Conta</th><th>Streamer</th><th>Modo</th><th>Nota mín.</th><th>Aprovação</th><th>Privacidade</th><th>Por dia</th><th>Horários</th><th /></tr></thead>
          <tbody>{ov.rules.map((r) => (
            <tr key={r.id} className={`border-t border-suite-line/50 ${r.enabled ? "" : "opacity-50"}`}>
              <td className="py-1">{acctName(r.account_id)?.name}</td>
              <td>{r.streamer_id ? st.data?.find((s) => s.id === r.streamer_id)?.display_name : "todos"}</td>
              <td>{({ manual: "manual", semi: "semiautomático", scheduled: "agendado", immediate: "imediato" } as Record<string, string>)[r.mode]}</td>
              <td>{r.min_score}</td><td>{r.require_approval ? "exigida" : "não"}</td>
              <td>{privacyLabel[r.privacy] ?? r.privacy}{r.allow_public_auto && " · público automático"}</td>
              <td>{r.max_per_day}</td><td className="text-xs">{r.slots.join(", ")}</td>
              <td><button className="btn-quiet px-2 py-1 text-xs" onClick={() => setEditing(r)}>Editar</button></td>
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
        <label><span className="text-xs text-ink-muted">Streamer</span>
          <select className="field" value={r.streamer_id ?? ""} onChange={(e) => setR({ ...r, streamer_id: e.target.value ? Number(e.target.value) : null })}>
            <option value="">Todos os autorizados</option>{streamers.map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
          </select></label>
        <label><span className="text-xs text-ink-muted">Modo</span>
          <select className="field" value={r.mode} onChange={(e) => setR({ ...r, mode: e.target.value })}>
            <option value="manual">Manual</option>
            <option value="semi" disabled={acct?.platform === "tiktok"}>Semiautomático (a IA propõe, você confirma)</option>
            <option value="scheduled" disabled={acct?.platform === "tiktok"}>Automático agendado</option>
            <option value="immediate" disabled={acct?.platform === "tiktok"}>Automático imediato</option>
          </select></label>
        <label><span className="text-xs text-ink-muted">Privacidade</span>
          <select className="field" value={r.privacy} onChange={(e) => setR({ ...r, privacy: e.target.value })}>
            {(acct?.meta.privacy_options ?? ["private"]).map((p) => <option key={p} value={p}>{privacyLabel[p] ?? p}</option>)}
          </select></label>
        <label><span className="text-xs text-ink-muted">Nota editorial mínima</span>
          <input className="field" type="number" min={0} max={100} value={r.min_score} onChange={(e) => setR({ ...r, min_score: Number(e.target.value) })} /></label>
        <label><span className="text-xs text-ink-muted">Máximo por dia</span>
          <input className="field" type="number" min={1} max={50} value={r.max_per_day} onChange={(e) => setR({ ...r, max_per_day: Number(e.target.value) })} /></label>
        <label><span className="text-xs text-ink-muted">Horários (HH:MM, vírgula)</span>
          <input className="field" value={r.slots.join(", ")} onChange={(e) => setR({ ...r, slots: e.target.value.split(/[,\s]+/).filter(Boolean) })} /></label>
      </div>
      <div className="mt-3 flex flex-wrap gap-4">
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.require_approval} onChange={(e) => setR({ ...r, require_approval: e.target.checked })} /> Exigir aprovação humana</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={r.enabled} onChange={(e) => setR({ ...r, enabled: e.target.checked })} /> Regra ativa</label>
        <label className="flex items-center gap-2 text-tally"><input type="checkbox" checked={r.allow_public_auto} onChange={(e) => setR({ ...r, allow_public_auto: e.target.checked })} /> Permitir postagem PÚBLICA automática</label>
      </div>
      {acct && (
        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <label><span className="text-xs text-ink-muted">Modelo de título ({"{title}"}, {"{streamer}"})</span><input className="field" value={tpl.title ?? ""} placeholder="{title}" onChange={(e) => setTpl("title", e.target.value)} /></label>
          <label><span className="text-xs text-ink-muted">Modelo de descrição</span><input className="field" value={tpl.description ?? ""} placeholder="{description}" onChange={(e) => setTpl("description", e.target.value)} /></label>
          <label><span className="text-xs text-ink-muted">Hashtags extras</span><input className="field" value={tpl.hashtags ?? ""} placeholder="gtarp shorts" onChange={(e) => setTpl("hashtags", e.target.value)} /></label>
        </div>
      )}
      <ErrorNote error={save.error ?? del.error} />
      <div className="mt-3 flex gap-2">
        <button className="btn-take" disabled={save.isPending} onClick={() => save.mutate()}>Salvar regra</button>
        {r.id && <button className="btn-danger" onClick={() => del.mutate()}>Excluir</button>}
        <button className="btn-quiet" onClick={onClose}>Fechar</button>
      </div>
    </div>
  );
}

function Calendar({ accounts }: { accounts: PubAccount[] }) {
  const qc = useQueryClient();
  const pubs = useQuery({ queryKey: ["publications"], queryFn: () => api.get<Pub[]>("/api/publish/publications?days=60"), refetchInterval: 10_000 });
  const act = useMutation({
    mutationFn: ({ id, a }: { id: number; a: "cancel" | "retry" | "metrics" }) => api.post(`/api/publish/publications/${id}/${a}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["publications"] }),
  });
  const [open, setOpen] = useState<number | null>(null);
  const groups = new Map<string, Pub[]>();
  for (const p of pubs.data ?? []) {
    const d = new Date(p.scheduled_at ?? p.published_at ?? p.created_at).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
    groups.set(d, [...(groups.get(d) ?? []), p]);
  }
  const acct = (id: number) => accounts.find((a) => a.id === id);
  return (
    <Section title="6. Acompanhamento" hint="Estados, IDs e links das publicações, erros e reenvio administrativo. Métricas oficiais quando a plataforma fornece.">
      <ErrorNote error={pubs.error ?? act.error} />
      {(pubs.data ?? []).length === 0 && <Empty title="Nenhuma publicação ainda">Publique um corte pela Central de cortes (botão Publicar) ou crie uma regra automática.</Empty>}
      <div className="space-y-4">
        {[...groups.entries()].map(([day, items]) => (
          <div key={day}>
            <p className="mb-1 text-xs uppercase tracking-wide text-ink-muted">{day}</p>
            <ul className="space-y-1">
              {items.map((p) => {
                const [label, tone] = pubStatus[p.status] ?? [p.status, ""];
                const prog = p.upload_progress?.size ? Math.round(((p.upload_progress.sent ?? 0) / p.upload_progress.size) * 100) : null;
                return (
                  <li key={p.id} className="rounded-md border border-suite-line/60 px-3 py-2 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <button className="text-left" onClick={() => setOpen(open === p.id ? null : p.id)}>
                        <span className="font-medium">{p.title}</span>
                        <span className="ml-2 text-xs text-ink-muted">{acct(p.account_id)?.platform_name} · {acct(p.account_id)?.name} · {privacyLabel[p.privacy] ?? p.privacy} · {p.origin === "auto" ? "automático" : "manual"} · {when(p.scheduled_at)}</span>
                      </button>
                      <span className="flex items-center gap-2">
                        <span className={`text-xs ${tone}`}>{label}{p.status === "uploading" && prog !== null ? ` ${prog}%` : ""}</span>
                        {p.remote_url && <a className="text-take" href={p.remote_url} target="_blank" rel="noreferrer" aria-label="Abrir publicação"><ExternalLink className="h-4 w-4" /></a>}
                        {["draft", "scheduled"].includes(p.status) && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => act.mutate({ id: p.id, a: "cancel" })}><X className="h-3 w-3" /> Cancelar</button>}
                        {p.status === "failed" && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => act.mutate({ id: p.id, a: "retry" })}><RotateCcw className="h-3 w-3" /> Reenviar</button>}
                        {p.status === "published" && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => act.mutate({ id: p.id, a: "metrics" })}>Atualizar métricas</button>}
                      </span>
                    </div>
                    {p.last_error && <p className="mt-1 text-xs text-[#ffb4b6]">{p.last_error}</p>}
                    {Object.keys(p.metrics ?? {}).length > 0 && <p className="mt-1 text-xs text-ink-muted">{Object.entries(p.metrics).map(([k, v]) => `${k}: ${v}`).join(" · ")}</p>}
                    {open === p.id && (
                      <ul className="mt-2 space-y-0.5 border-t border-suite-line/50 pt-2 text-[11px] text-ink-muted">
                        {p.log.map((l, i) => <li key={i}>{new Date(l.at).toLocaleString("pt-BR")} — {l.event}{l.detail ? `: ${l.detail}` : ""}</li>)}
                        {p.clip_id && <li>Corte #{p.clip_id} (nota {p.clip_score?.toFixed(0)})</li>}
                      </ul>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}
