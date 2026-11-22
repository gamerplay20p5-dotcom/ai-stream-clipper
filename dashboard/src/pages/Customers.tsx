import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gift, ShieldOff, ShieldCheck } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Empty, ErrorNote, PageHeader, Skeleton, Status } from "../components/ui";
import { locale, t } from "../i18n";

type Plan = { plan: string; provider: string; status: string; until: string | null; courtesy: boolean; review: string } | null;
type Row = { id: number; email: string; active: boolean; plan: Plan; destinations: number;
  usage: { channels: number; live_seconds: number; storage_bytes: number };
  automation: { enabled: boolean; blocked: boolean }; failures_24h: number };
type Grant = { id: number; plan: string; ends_at: string; reason: string; created_by: string; active: boolean; revoke_reason: string };
type Detail = {
  id: number; email: string; plan: Plan; grants: Grant[];
  subscriptions: { provider: string; plan: string; access_status: string; financial_status: string; review: string; until: string | null }[];
  usage: { channels: number; live_seconds: number; storage_bytes: number }; publishing_today: { used: number; limit: number };
  automation: { enabled: boolean; terms_accepted: boolean; timezone: string | null; blocked_reason: string; blocked_by: string };
  monitors: { id: number; channel: string; platform: string; preset: string }[];
  destinations: { id: number; platform_name: string; name: string; status: string; paused_reason: string; connected_at: string | null }[];
  publications: { id: number; platform: string; status: string; title: string; scheduled_at: string | null; error: string; url: string }[];
};
type Channel = { id: number; platform: string; channel: string; display_name: string; status: string; hts_monitoring: boolean;
  followers: number; customer_publish_blocked: boolean };
type Capacity = { live_sessions: number; customers_following_now: number; customers: number; customer_library_bytes: number;
  jobs_pending: Record<string, number>; customer_publications_open: number; customer_destinations: number };

const PLAN: Record<string, string> = { basic: "Basic", creator: "Creator", pro: "Pro" };
const date = (iso: string | null) => (iso ? new Intl.DateTimeFormat(locale, { dateStyle: "short" }).format(new Date(iso)) : "");
const gb = (b: number) => `${(b / 1024 ** 3).toFixed(1)} GB`;

function PlanTag({ plan }: { plan: Plan }) {
  if (!plan) return <Status tone="neutral">{t("Sem plano")}</Status>;
  const label = `${PLAN[plan.plan] ?? plan.plan}${plan.courtesy ? ` (${t("cortesia")})` : ""}`;
  return <Status tone={plan.review ? "warning" : plan.status === "grace" ? "warning" : "success"}>{label}</Status>;
}

function CustomerDetail({ uid }: { uid: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-customer", uid], queryFn: () => api.get<Detail>(`/api/admin/customers/${uid}`) });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["admin-customer", uid] }); void qc.invalidateQueries({ queryKey: ["admin-customers"] }); };
  const [grant, setGrant] = useState({ plan: "creator", days: 14, reason: "" });
  const [blockReason, setBlockReason] = useState("");
  const give = useMutation({ mutationFn: () => api.post(`/api/admin/customers/${uid}/grants`, grant), onSuccess: () => { setGrant({ ...grant, reason: "" }); refresh(); } });
  const revoke = useMutation({ mutationFn: ({ id, reason }: { id: number; reason: string }) => api.post(`/api/admin/customers/${uid}/grants/${id}/revoke`, { reason }), onSuccess: refresh });
  const block = useMutation({ mutationFn: () => api.post(`/api/admin/customers/${uid}/publishing/block`, { reason: blockReason }), onSuccess: () => { setBlockReason(""); refresh(); } });
  const unblock = useMutation({ mutationFn: () => api.post(`/api/admin/customers/${uid}/publishing/unblock`), onSuccess: refresh });
  const d = q.data;
  if (q.isLoading) return <Skeleton label={t("Carregando cliente")} />;
  if (!d) return <ErrorNote error={q.error} />;
  return (
    <div className="panel panel-body grid gap-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="display text-lg">{d.email}</h2>
        <PlanTag plan={d.plan} />
      </div>
      <section className="grid gap-2" aria-label={t("Assinaturas")}>
        <h3 className="text-sm font-semibold">{t("Assinaturas e cortesias")}</h3>
        <ul className="text-sm">{d.subscriptions.map((s, i) => (
          <li key={i}>{`${PLAN[s.plan] ?? s.plan}, ${s.provider === "hts" ? t("cortesia HTS") : s.provider}: ${s.access_status}${s.review ? `, ${t("em revisão")}` : ""}${s.until ? `, ${t("até {d}", { d: date(s.until) })}` : ""}`}</li>
        ))}</ul>
        {d.grants.filter((g) => g.active).map((g) => (
          <form key={g.id} className="flex flex-wrap items-center gap-2 text-sm" onSubmit={(e: FormEvent<HTMLFormElement>) => {
            e.preventDefault(); revoke.mutate({ id: g.id, reason: String(new FormData(e.currentTarget).get("reason") ?? "") }); }}>
            <span>{t("Cortesia {plan} até {d}: {reason}", { plan: PLAN[g.plan] ?? g.plan, d: date(g.ends_at), reason: g.reason })}</span>
            <input name="reason" className="field w-auto" required minLength={5} placeholder={t("Motivo da revogação")} />
            <button className="btn-quiet">{t("Revogar")}</button>
          </form>
        ))}
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); give.mutate(); }}>
          <label className="label">{t("Plano")}
            <select className="field" value={grant.plan} onChange={(e) => setGrant({ ...grant, plan: e.target.value })}>
              {Object.entries(PLAN).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="label">{t("Dias")}
            <input className="field w-24" type="number" min={1} max={365} value={grant.days} onChange={(e) => setGrant({ ...grant, days: Number(e.target.value) })} />
          </label>
          <label className="label min-w-[14rem] flex-1">{t("Motivo")}
            <input className="field" required minLength={5} maxLength={300} value={grant.reason} onChange={(e) => setGrant({ ...grant, reason: e.target.value })} />
          </label>
          <button className="btn-take" disabled={give.isPending}><Gift size={15} aria-hidden />{t("Dar cortesia")}</button>
        </form>
        <p className="text-xs text-ink-muted">{t("Cortesias ficam separadas dos pagamentos (Stripe e InfinitePay) e não alteram o histórico financeiro. Vale o plano mais alto; no mesmo plano, o pago tem precedência.")}</p>
      </section>
      <section className="grid gap-2" aria-label={t("Publicação automática")}>
        <h3 className="text-sm font-semibold">{t("Publicação automática")}</h3>
        <p className="text-sm">{d.automation.blocked_reason
          ? t("Bloqueada por {who}: {reason}", { who: d.automation.blocked_by, reason: d.automation.blocked_reason })
          : d.automation.enabled ? t("Ativa, {used} de {limit} publicações hoje", { used: String(d.publishing_today.used), limit: String(d.publishing_today.limit) }) : t("Desativada pelo cliente")}</p>
        {d.automation.blocked_reason
          ? <button type="button" className="btn-quiet w-fit" onClick={() => unblock.mutate()}><ShieldCheck size={15} aria-hidden />{t("Desbloquear")}</button>
          : <form className="flex flex-wrap gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); block.mutate(); }}>
              <input className="field w-auto flex-1" required minLength={5} placeholder={t("Motivo do bloqueio")} value={blockReason} onChange={(e) => setBlockReason(e.target.value)} />
              <button className="btn-quiet"><ShieldOff size={15} aria-hidden />{t("Bloquear automação")}</button>
            </form>}
        <ul className="text-sm">{d.destinations.map((a) => (
          <li key={a.id}>{`${a.platform_name}: ${a.name}, ${a.status}${a.paused_reason ? `, ${a.paused_reason}` : ""}`}</li>
        ))}</ul>
        {d.destinations.length === 0 && <p className="text-xs text-ink-muted">{t("Nenhuma rede conectada.")}</p>}
      </section>
      <section className="grid gap-2" aria-label={t("Publicações")}>
        <h3 className="text-sm font-semibold">{t("Publicações recentes")}</h3>
        {d.publications.length === 0 ? <p className="text-xs text-ink-muted">{t("Nenhuma publicação.")}</p> : (
          <table className="account-table">
            <thead><tr><th scope="col">{t("Rede")}</th><th scope="col">{t("Título")}</th><th scope="col">{t("Situação")}</th></tr></thead>
            <tbody>{d.publications.slice(0, 30).map((p) => (
              <tr key={p.id}><td>{p.platform}</td><td className="max-w-[28ch] truncate">{p.title}</td>
                <td>{p.status}{p.error && <span className="block text-xs text-ink-muted">{p.error}</span>}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
      <ErrorNote error={give.error ?? revoke.error ?? block.error ?? unblock.error} />
    </div>
  );
}

function Channels() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-channels"], queryFn: () => api.get<Channel[]>("/api/admin/channels") });
  const patch = useMutation({ mutationFn: ({ id, body }: { id: number; body: object }) => api.patch(`/api/admin/channels/${id}`, body),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin-channels"] }) });
  return (
    <section className="panel panel-body grid gap-3" aria-labelledby="channels-h">
      <h2 id="channels-h" className="display text-lg">{t("Canais e seguidores")}</h2>
      {q.isLoading && <Skeleton />}
      <table className="account-table">
        <thead><tr><th scope="col">{t("Canal")}</th><th scope="col">{t("Clientes")}</th><th scope="col">{t("HTS monitora")}</th><th scope="col">{t("Republicação")}</th></tr></thead>
        <tbody>{(q.data ?? []).map((c) => (
          <tr key={c.id}>
            <td>{c.display_name || c.channel} <span className="text-xs text-ink-muted">{c.platform}</span></td>
            <td className="font-mono">{c.followers}</td>
            <td><input type="checkbox" aria-label={t("HTS monitora {c}", { c: c.channel })} checked={c.hts_monitoring}
              onChange={(e) => patch.mutate({ id: c.id, body: { hts_monitoring: e.target.checked } })} /></td>
            <td>{c.customer_publish_blocked
              ? <button type="button" className="btn-quiet" onClick={() => patch.mutate({ id: c.id, body: { customer_publish_blocked: false } })}>{t("Liberar")}</button>
              : <button type="button" className="btn-quiet" onClick={() => {
                  const reason = window.prompt(t("Motivo do bloqueio de republicação deste canal")) ?? "";
                  if (reason.trim().length >= 5) patch.mutate({ id: c.id, body: { customer_publish_blocked: true, reason } });
                }}>{t("Bloquear")}</button>}</td>
          </tr>
        ))}</tbody>
      </table>
      <ErrorNote error={q.error ?? patch.error} />
    </section>
  );
}

function CapacityPanel() {
  const qc = useQueryClient();
  const cap = useQuery({ queryKey: ["admin-capacity"], queryFn: () => api.get<Capacity>("/api/admin/capacity"), refetchInterval: 30_000 });
  const plat = useQuery({ queryKey: ["admin-publish-platforms"], queryFn: () => api.get<{ enabled: string[]; known: string[]; never_automatic: string[] }>("/api/admin/customer-publish-platforms") });
  const save = useMutation({ mutationFn: (platforms: string[]) => api.put("/api/admin/customer-publish-platforms", { platforms }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["admin-publish-platforms"] }) });
  const c = cap.data;
  return (
    <section className="panel panel-body grid gap-3" aria-labelledby="cap-h">
      <h2 id="cap-h" className="display text-lg">{t("Capacidade")}</h2>
      {c && (
        <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
          <div><dt className="text-ink-muted">{t("Lives capturadas agora")}</dt><dd className="font-mono">{c.live_sessions}</dd></div>
          <div><dt className="text-ink-muted">{t("Clientes acompanhando agora")}</dt><dd className="font-mono">{c.customers_following_now}</dd></div>
          <div><dt className="text-ink-muted">{t("Bibliotecas de clientes")}</dt><dd className="font-mono">{gb(c.customer_library_bytes)}</dd></div>
          <div><dt className="text-ink-muted">{t("Publicações na fila")}</dt><dd className="font-mono">{c.customer_publications_open}</dd></div>
          {Object.entries(c.jobs_pending).map(([k, v]) => (
            <div key={k}><dt className="text-ink-muted">{t("Jobs {type}", { type: k })}</dt><dd className="font-mono">{v}</dd></div>
          ))}
        </dl>
      )}
      <h3 className="text-sm font-semibold">{t("Redes liberadas para clientes")}</h3>
      <p className="text-xs text-ink-muted">{t("Libere uma rede só depois de homologar um envio real nela. O TikTok nunca tem publicação automática.")}</p>
      <div className="flex flex-wrap gap-3">{(plat.data?.known ?? []).map((p) => {
        const on = plat.data!.enabled.includes(p);
        const never = plat.data!.never_automatic.includes(p);
        return (
          <label key={p} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={on} disabled={never || save.isPending}
              onChange={() => save.mutate(on ? plat.data!.enabled.filter((x) => x !== p) : [...plat.data!.enabled, p])} />{p}
          </label>
        );
      })}</div>
      <ErrorNote error={cap.error ?? plat.error ?? save.error} />
    </section>
  );
}

/** HTS internal commercial panel: customers, plans, courtesy, automation blocks, channels and capacity. Admin only. */
export default function Customers() {
  const list = useQuery({ queryKey: ["admin-customers"], queryFn: () => api.get<Row[]>("/api/admin/customers") });
  const [selected, setSelected] = useState<number | null>(null);
  const rows = list.data ?? [];
  return (
    <div>
      <PageHeader title={t("Clientes")} description={t("Assinantes, planos, consumo, redes conectadas e publicações. Segredos e tokens nunca aparecem aqui.")} />
      <div className="section-flow">
        <ErrorNote error={list.error} />
        {list.isLoading && <Skeleton />}
        {list.data && rows.length === 0 && <Empty title={t("Nenhum cliente ainda")} />}
        {rows.length > 0 && (
          <div className="panel panel-body overflow-x-auto">
            <table className="account-table">
              <thead><tr><th scope="col">{t("Cliente")}</th><th scope="col">{t("Plano")}</th><th scope="col">{t("Canais")}</th>
                <th scope="col">{t("Horas")}</th><th scope="col">{t("Redes")}</th><th scope="col">{t("Automação")}</th><th scope="col">{t("Falhas 24 h")}</th></tr></thead>
              <tbody>{rows.map((r) => (
                <tr key={r.id} aria-selected={selected === r.id}>
                  <td><button type="button" className="underline" onClick={() => setSelected(r.id)}>{r.email}</button></td>
                  <td><PlanTag plan={r.plan} /></td>
                  <td className="font-mono">{r.usage.channels}</td>
                  <td className="font-mono">{Math.floor(r.usage.live_seconds / 3600)}</td>
                  <td className="font-mono">{r.destinations}</td>
                  <td>{r.automation.blocked ? t("Bloqueada") : r.automation.enabled ? t("Ativa") : t("Desativada")}</td>
                  <td className="font-mono">{r.failures_24h}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
        {selected && <CustomerDetail uid={selected} />}
        <Channels />
        <CapacityPanel />
      </div>
    </div>
  );
}
