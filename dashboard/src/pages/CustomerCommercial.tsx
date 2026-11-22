import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarPlus, History, RefreshCw, Rocket, TicketPercent, Undo2 } from "lucide-react";
import { useMemo, useState, type FormEvent } from "react";
import { api } from "../api";
import { Empty, ErrorNote, Notice, Skeleton, Status } from "../components/ui";
import { locale, t } from "../i18n";

/* HTS commercial operations inside Clientes (admin only): access credits, temporary upgrades, the audited history and
 * promotion codes. Access benefits never change payments; promotion codes live in the commercial service and every
 * amount is computed by the server. */

const PLAN: Record<string, string> = { basic: "Basic", creator: "Creator", pro: "Pro" };
const fmt = (iso: string | null | undefined) => (iso ? new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso)) : "");
const day = (epoch: number | null | undefined) => (epoch ? new Intl.DateTimeFormat(locale, { dateStyle: "short" }).format(new Date(epoch * 1000)) : "");
const money = (minor: number, currency: string) => new Intl.NumberFormat(locale, { style: "currency", currency: currency.toUpperCase() }).format(minor / 100);
const requestKey = () => (crypto.randomUUID ? crypto.randomUUID().replace(/-/g, "") : `${Date.now()}${Math.random().toString(36).slice(2)}`.padEnd(24, "0"));

export type Access = { entitled: boolean; plan?: string; paid_plan?: string; provider?: string; paid_until?: string | null;
  access_until?: string | null; credit_days?: number; upgrade_until?: string | null };
export type Benefit = { id: number; kind: "credit" | "upgrade"; days: number; plan: string; starts_at: string | null; ends_at: string | null;
  reason: string; created_by: string; created_at: string | null; revoked_at: string | null; revoked_by: string; revoke_reason: string;
  state: "active" | "ended" | "revoked" };
type Preview = { days: number; applies: boolean; reason: string; before: { plan: string; until: string } | null;
  after: { plan: string; until: string } | null; paid_plan: string | null };
type HistoryRow = { id: number; action: string; by: string; at: string | null; details: Record<string, unknown> };

const ACTIONS: Record<string, () => string> = {
  "admin.courtesy_grant": () => t("Cortesia dada"),
  "admin.courtesy_revoke": () => t("Cortesia revogada"),
  "admin.access_credit": () => t("Crédito de acesso"),
  "admin.temporary_upgrade": () => t("Upgrade temporário"),
  "admin.benefit_revoke": () => t("Benefício revogado"),
  "admin.customer_publish_block": () => t("Automação bloqueada"),
  "admin.customer_publish_unblock": () => t("Automação desbloqueada"),
};

/** Paid plan, benefit in effect and dates. */
export function AccessSummary({ access }: { access: Access }) {
  if (!access.entitled) return <p className="text-sm text-ink-muted">{t("Sem acesso no momento.")}</p>;
  return (
    <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
      <div><dt className="text-ink-muted">{t("Plano pago")}</dt><dd>{PLAN[access.paid_plan ?? ""] ?? access.paid_plan}{access.provider === "hts" ? ` (${t("cortesia")})` : ""}</dd></div>
      <div><dt className="text-ink-muted">{t("Plano em uso")}</dt><dd>{PLAN[access.plan ?? ""] ?? access.plan}{access.upgrade_until ? ` (${t("upgrade até {d}", { d: fmt(access.upgrade_until) })})` : ""}</dd></div>
      <div><dt className="text-ink-muted">{t("Pago até")}</dt><dd>{fmt(access.paid_until) || "-"}</dd></div>
      <div><dt className="text-ink-muted">{t("Acesso até")}</dt><dd>{fmt(access.access_until)}{access.credit_days ? ` (+${access.credit_days} ${t("dias de crédito")})` : ""}</dd></div>
    </dl>
  );
}

function RevokeForm({ uid, b, onDone }: { uid: number; b: Benefit; onDone: () => void }) {
  const revoke = useMutation({ mutationFn: (reason: string) => api.post(`/api/admin/customers/${uid}/benefits/${b.id}/revoke`, { reason }), onSuccess: onDone });
  return (
    <form className="flex flex-wrap items-center gap-2" onSubmit={(e: FormEvent<HTMLFormElement>) => {
      e.preventDefault(); revoke.mutate(String(new FormData(e.currentTarget).get("reason") ?? "")); }}>
      <input name="reason" className="field w-auto" required minLength={5} maxLength={300} placeholder={t("Motivo da revogação")} aria-label={t("Motivo da revogação")} />
      <button className="btn-quiet" disabled={revoke.isPending}><Undo2 size={15} aria-hidden />{t("Revogar")}</button>
      <ErrorNote error={revoke.error} />
    </form>
  );
}

function BenefitList({ uid, items, kind, onDone }: { uid: number; items: Benefit[]; kind: "credit" | "upgrade"; onDone: () => void }) {
  const rows = items.filter((b) => b.kind === kind);
  if (!rows.length) return null;
  return (
    <ul className="grid gap-2 text-sm">{rows.map((b) => (
      <li key={b.id} className="grid gap-1">
        <span>
          <Status tone={b.state === "active" ? "success" : "neutral"}>{b.state === "active" ? t("Ativo") : b.state === "revoked" ? t("Revogado") : t("Encerrado")}</Status>{" "}
          {kind === "credit"
            ? t("+{n} dias, por {who} em {d}: {reason}", { n: String(b.days), who: b.created_by, d: fmt(b.created_at), reason: b.reason })
            : t("{plan} até {until}, por {who}: {reason}", { plan: PLAN[b.plan] ?? b.plan, until: fmt(b.ends_at), who: b.created_by, reason: b.reason })}
          {b.revoked_at && <span className="block text-xs text-ink-muted">{t("Revogado por {who} em {d}: {reason}", { who: b.revoked_by, d: fmt(b.revoked_at), reason: b.revoke_reason })}</span>}
        </span>
        {b.state === "active" && <RevokeForm uid={uid} b={b} onDone={onDone} />}
      </li>
    ))}</ul>
  );
}

/** A. Compensation: days of access without touching payments or the next charge. */
export function CreditSection({ uid, items, onDone }: { uid: number; items: Benefit[]; onDone: () => void }) {
  const [days, setDays] = useState(1);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(requestKey);
  const preview = useMutation({ mutationFn: () => api.post<Preview>(`/api/admin/customers/${uid}/credits/preview`, { days }) });
  const give = useMutation({
    mutationFn: () => api.post(`/api/admin/customers/${uid}/credits`, { days, reason, request_key: key }),
    onSuccess: () => { setReason(""); setKey(requestKey()); preview.reset(); onDone(); },
  });
  const p = preview.data;
  return (
    <section className="grid gap-2" aria-labelledby={`credit-${uid}`}>
      <h3 id={`credit-${uid}`} className="text-sm font-semibold">{t("Compensar dias de acesso")}</h3>
      <p className="text-xs text-ink-muted">{t("Crédito de acesso: soma dias depois do fim do período pago. Não altera pagamentos nem faturas e não adia a próxima cobrança no Stripe ou na InfinitePay. O crédito continua valendo quando o cliente renova.")}</p>
      <form className="flex flex-wrap items-end gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); preview.mutate(); }}>
        <label className="label">{t("Dias")}
          <input className="field w-24" type="number" min={1} max={90} value={days} onChange={(e) => { setDays(Number(e.target.value)); preview.reset(); }} />
        </label>
        <label className="label min-w-[14rem] flex-1">{t("Motivo")}
          <input className="field" required minLength={5} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={t("Ex.: instabilidade em 20/11")} />
        </label>
        <button className="btn-quiet" disabled={preview.isPending}>{t("Ver resultado")}</button>
      </form>
      {p && (p.applies
        ? <div className="grid gap-2">
            <p className="text-sm">{t("O acesso passa de {before} para {after}.", { before: fmt(p.before?.until), after: fmt(p.after?.until) })}</p>
            <button type="button" className="btn-take w-fit" disabled={give.isPending || reason.trim().length < 5} onClick={() => give.mutate()}>
              <CalendarPlus size={15} aria-hidden />{t("Confirmar +{n} dias", { n: String(days) })}
            </button>
          </div>
        : <Notice tone="danger">{p.reason}</Notice>)}
      <BenefitList uid={uid} items={items} kind="credit" onDone={onDone} />
      <ErrorNote error={preview.error ?? give.error} />
    </section>
  );
}

/** B. Temporary upgrade: a higher plan's limits for a few days, then the paid plan again, same price and period. */
export function UpgradeSection({ uid, items, access, onDone }: { uid: number; items: Benefit[]; access: Access; onDone: () => void }) {
  const [plan, setPlan] = useState("pro");
  const [days, setDays] = useState(7);
  const [reason, setReason] = useState("");
  const [key, setKey] = useState(requestKey);
  const give = useMutation({
    mutationFn: () => api.post(`/api/admin/customers/${uid}/upgrades`, { plan, days, reason, request_key: key }),
    onSuccess: () => { setReason(""); setKey(requestKey()); onDone(); },
  });
  const active = items.some((b) => b.kind === "upgrade" && b.state === "active");
  return (
    <section className="grid gap-2" aria-labelledby={`upgrade-${uid}`}>
      <h3 id={`upgrade-${uid}`} className="text-sm font-semibold">{t("Upgrade temporário")}</h3>
      <p className="text-xs text-ink-muted">{t("Usa os limites do plano escolhido até a data final e depois volta sozinho ao plano pago. O preço da assinatura não muda e o consumo do ciclo continua o mesmo, sem zerar cotas.")}</p>
      {!active && access.entitled && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(e: FormEvent) => { e.preventDefault(); give.mutate(); }}>
          <label className="label">{t("Plano")}
            <select className="field" value={plan} onChange={(e) => setPlan(e.target.value)}>
              <option value="creator">Creator</option><option value="pro">Pro</option>
            </select>
          </label>
          <label className="label">{t("Dias")}
            <input className="field w-24" type="number" min={1} max={90} value={days} onChange={(e) => setDays(Number(e.target.value))} />
          </label>
          <label className="label min-w-[14rem] flex-1">{t("Motivo")}
            <input className="field" required minLength={5} maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} />
          </label>
          <button className="btn-take" disabled={give.isPending}><Rocket size={15} aria-hidden />{t("Dar upgrade")}</button>
        </form>
      )}
      {!access.entitled && <p className="text-xs text-ink-muted">{t("Sem plano ativo: o upgrade não reativa assinaturas suspensas, estornadas ou expiradas.")}</p>}
      <BenefitList uid={uid} items={items} kind="upgrade" onDone={onDone} />
      <ErrorNote error={give.error} />
    </section>
  );
}

/** Audited history of administrative changes of one customer. */
export function HistorySection({ uid }: { uid: number }) {
  const q = useQuery({ queryKey: ["admin-customer-history", uid], queryFn: () => api.get<HistoryRow[]>(`/api/admin/customers/${uid}/history`) });
  return (
    <section className="grid gap-2" aria-labelledby={`history-${uid}`}>
      <h3 id={`history-${uid}`} className="flex items-center gap-2 text-sm font-semibold"><History size={15} aria-hidden />{t("Histórico de alterações")}</h3>
      {q.isLoading && <Skeleton lines={2} />}
      {q.data && q.data.length === 0 && <p className="text-xs text-ink-muted">{t("Nenhuma alteração administrativa.")}</p>}
      {q.data && q.data.length > 0 && (
        <table className="account-table">
          <thead><tr><th scope="col">{t("Quando")}</th><th scope="col">{t("Ação")}</th><th scope="col">{t("Quem")}</th><th scope="col">{t("Detalhes")}</th></tr></thead>
          <tbody>{q.data.map((h) => (
            <tr key={h.id}><td>{fmt(h.at)}</td><td>{ACTIONS[h.action]?.() ?? h.action}</td><td>{h.by}</td>
              <td className="max-w-[36ch] break-words text-xs">{["plan", "days", "reason"].filter((k) => h.details[k] !== undefined).map((k) => String(h.details[k])).join(" · ")}</td></tr>
          ))}</tbody>
        </table>
      )}
      <ErrorNote error={q.error} />
    </section>
  );
}

// ------------------------------------------------------------------ promotion codes
type Promo = { code: string; kind: "percent" | "amount" | "free_days"; percent: number | null; amount_brl: number | null; amount_usd: number | null;
  free_days: number | null; applies_to: "new" | "existing"; duration: string; duration_months: number | null; plans: string[]; markets: string[];
  starts_at: number; ends_at: number | null; max_redemptions: number | null; max_per_account: number; active: boolean;
  stripe: { state: string; error: string; synced: boolean }; note: string; created_by: string;
  stats: { reserved: number; redeemed: number; released: number; refunded: number; accounts: number; discount: Record<string, number> } };
type PromoList = { connected: boolean; codes: Promo[]; stripe: boolean; infinitepay: boolean };
type PromoDetail = Promo & { redemptions: { id: string; email: string; account_id: number; market: string; plan: string; status: string;
  list_amount: number; discount_amount: number; final_amount: number; currency: string; created_at: number }[];
  events: { type: string; detail: string; actor: string; created_at: number }[] };

function describe(p: Promo): string {
  const value = p.kind === "percent" ? `${p.percent}%`
    : p.kind === "amount" ? [p.amount_brl ? money(p.amount_brl, "brl") : "", p.amount_usd ? money(p.amount_usd, "usd") : ""].filter(Boolean).join(" / ")
      : t("{n} dias grátis", { n: String(p.free_days) });
  const use = p.applies_to === "new" ? t("só na primeira cobrança de uma assinatura nova")
    : p.duration === "forever" ? t("recorrente, em todas as renovações de uma assinatura em andamento")
      : t("recorrente, nas próximas {n} renovações de uma assinatura em andamento", { n: String(p.duration_months) });
  return `${value}, ${use}`;
}

function PromoDetails({ code }: { code: string }) {
  const q = useQuery({ queryKey: ["admin-promo", code], queryFn: () => api.get<PromoDetail>(`/api/admin/promotions/${code}`) });
  const d = q.data;
  if (q.isLoading) return <Skeleton lines={2} />;
  if (!d) return <ErrorNote error={q.error} />;
  return (
    <div className="grid gap-2">
      {d.redemptions.length === 0 ? <p className="text-xs text-ink-muted">{t("Nenhum uso ainda.")}</p> : (
        <table className="account-table">
          <thead><tr><th scope="col">{t("Cliente")}</th><th scope="col">{t("Plano")}</th><th scope="col">{t("Preço")}</th><th scope="col">{t("Desconto")}</th><th scope="col">{t("Situação")}</th><th scope="col">{t("Data")}</th></tr></thead>
          <tbody>{d.redemptions.map((r) => (
            <tr key={r.id}><td>{r.email || `#${r.account_id}`}</td><td>{PLAN[r.plan] ?? r.plan} {r.market}</td>
              <td className="font-mono">{money(r.final_amount, r.currency)}</td><td className="font-mono">{money(r.discount_amount, r.currency)}</td>
              <td>{({ reserved: t("Reservado"), redeemed: t("Usado"), released: t("Liberado"), refunded: t("Estornado") } as Record<string, string>)[r.status] ?? r.status}</td>
              <td>{day(r.created_at)}</td></tr>
          ))}</tbody>
        </table>
      )}
      <details className="text-xs"><summary>{t("Histórico do cupom")}</summary>
        <ul>{d.events.map((e, i) => <li key={i}>{`${day(e.created_at)} ${e.type}${e.actor ? ` (${e.actor})` : ""}`}</li>)}</ul>
      </details>
    </div>
  );
}

const EMPTY = { code: "", kind: "percent", percent: 10, amount_brl: "", amount_usd: "", free_days: 7, applies_to: "new", duration: "repeating",
  duration_months: 3, plans: ["basic", "creator", "pro"], markets: ["BR", "US"], ends_at: "", max_redemptions: "", max_per_account: 1, note: "" };

function CreatePromo({ onDone }: { onDone: () => void }) {
  const [f, setF] = useState({ ...EMPTY });
  const set = (k: string, v: unknown) => setF((x) => ({ ...x, [k]: v }));
  const toggle = (k: "plans" | "markets", v: string) => set(k, f[k].includes(v) ? f[k].filter((x) => x !== v) : [...f[k], v]);
  const body = useMemo(() => ({
    code: f.code.trim().toUpperCase(), kind: f.kind, applies_to: f.applies_to, plans: f.plans, markets: f.markets,
    percent: f.kind === "percent" ? Number(f.percent) : null,
    amount_brl: f.kind === "amount" && f.markets.includes("BR") && f.amount_brl !== "" ? Math.round(Number(f.amount_brl) * 100) : null,
    amount_usd: f.kind === "amount" && f.markets.includes("US") && f.amount_usd !== "" ? Math.round(Number(f.amount_usd) * 100) : null,
    free_days: f.kind === "free_days" ? Number(f.free_days) : null,
    duration: f.applies_to === "new" ? "once" : f.duration, duration_months: f.applies_to === "existing" && f.duration === "repeating" ? Number(f.duration_months) : null,
    ends_at: f.ends_at ? new Date(f.ends_at).toISOString() : null, max_redemptions: f.max_redemptions === "" ? null : Number(f.max_redemptions),
    max_per_account: Number(f.max_per_account), note: f.note,
  }), [f]);
  const create = useMutation({ mutationFn: () => api.post("/api/admin/promotions", body), onSuccess: () => { setF({ ...EMPTY }); onDone(); } });
  return (
    <form className="grid gap-3" onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }}>
      <div className="flex flex-wrap items-end gap-2">
        <label className="label">{t("Código")}
          <input className="field w-40 font-mono uppercase" required pattern="[A-Za-z0-9]{3,32}" value={f.code} onChange={(e) => set("code", e.target.value)} placeholder="MALAIO20" />
        </label>
        <label className="label">{t("Uso")}
          <select className="field" value={f.applies_to} onChange={(e) => set("applies_to", e.target.value)}>
            <option value="new">{t("Assinatura nova (só a primeira cobrança)")}</option>
            <option value="existing">{t("Recorrente para assinaturas em andamento")}</option>
          </select>
        </label>
        <label className="label">{t("Tipo")}
          <select className="field" value={f.kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="percent">{t("Percentual")}</option><option value="amount">{t("Valor fixo")}</option>
            {f.applies_to === "new" && <option value="free_days">{t("Dias grátis")}</option>}
          </select>
        </label>
        {f.kind === "percent" && <label className="label">{t("Desconto (%)")}<input className="field w-24" type="number" min={1} max={100} value={f.percent} onChange={(e) => set("percent", e.target.value)} /></label>}
        {f.kind === "amount" && f.markets.includes("BR") && <label className="label">{t("Desconto em R$")}<input className="field w-28" type="number" min={0.01} step={0.01} value={f.amount_brl} onChange={(e) => set("amount_brl", e.target.value)} /></label>}
        {f.kind === "amount" && f.markets.includes("US") && <label className="label">{t("Desconto em US$")}<input className="field w-28" type="number" min={0.01} step={0.01} value={f.amount_usd} onChange={(e) => set("amount_usd", e.target.value)} /></label>}
        {f.kind === "free_days" && <label className="label">{t("Dias grátis")}<input className="field w-24" type="number" min={1} max={90} value={f.free_days} onChange={(e) => set("free_days", e.target.value)} /></label>}
        {f.applies_to === "existing" && (
          <label className="label">{t("Duração")}
            <select className="field" value={f.duration} onChange={(e) => set("duration", e.target.value)}>
              <option value="repeating">{t("Por alguns meses")}</option><option value="forever">{t("Para sempre")}</option>
            </select>
          </label>
        )}
        {f.applies_to === "existing" && f.duration === "repeating" && <label className="label">{t("Meses")}<input className="field w-20" type="number" min={1} max={24} value={f.duration_months} onChange={(e) => set("duration_months", e.target.value)} /></label>}
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <fieldset className="flex flex-wrap gap-3 text-sm"><legend className="label">{t("Planos")}</legend>
          {Object.entries(PLAN).map(([k, v]) => <label key={k} className="flex items-center gap-1"><input type="checkbox" checked={f.plans.includes(k)} onChange={() => toggle("plans", k)} />{v}</label>)}
        </fieldset>
        <fieldset className="flex flex-wrap gap-3 text-sm"><legend className="label">{t("Mercados")}</legend>
          <label className="flex items-center gap-1"><input type="checkbox" checked={f.markets.includes("BR")} onChange={() => toggle("markets", "BR")} />BRL (InfinitePay)</label>
          <label className="flex items-center gap-1"><input type="checkbox" checked={f.markets.includes("US")} onChange={() => toggle("markets", "US")} />USD (Stripe)</label>
        </fieldset>
        <label className="label">{t("Expira em")}<input className="field" type="datetime-local" value={f.ends_at} onChange={(e) => set("ends_at", e.target.value)} /></label>
        <label className="label">{t("Limite total")}<input className="field w-24" type="number" min={1} value={f.max_redemptions} onChange={(e) => set("max_redemptions", e.target.value)} placeholder={t("sem limite")} /></label>
        <label className="label">{t("Por cliente")}<input className="field w-20" type="number" min={1} max={100} value={f.max_per_account} onChange={(e) => set("max_per_account", e.target.value)} /></label>
      </div>
      <p className="text-xs text-ink-muted">{t("O servidor calcula e valida todos os valores. Depois de criado, só dá para ativar, desativar, mudar a expiração e os limites: o desconto e as regras não mudam, para o histórico financeiro ficar intacto.")}</p>
      <button className="btn-take w-fit" disabled={create.isPending}><TicketPercent size={15} aria-hidden />{t("Criar cupom")}</button>
      <ErrorNote error={create.error} />
    </form>
  );
}

/** Promotion codes: create, switch on/off, see uses, customers and statistics. */
export function PromotionsPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["admin-promos"], queryFn: () => api.get<PromoList>("/api/admin/promotions") });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["admin-promos"] });
  const toggle = useMutation({ mutationFn: (p: Promo) => api.patch(`/api/admin/promotions/${p.code}`, { active: !p.active }), onSuccess: refresh });
  const sync = useMutation({ mutationFn: (code: string) => api.post(`/api/admin/promotions/${code}/sync`), onSuccess: refresh });
  const [open, setOpen] = useState<string | null>(null);
  const data = q.data;
  return (
    <section className="panel panel-body grid gap-4" aria-labelledby="promos-h">
      <h2 id="promos-h" className="display flex items-center gap-2 text-lg"><TicketPercent size={18} aria-hidden />{t("Cupons promocionais")}</h2>
      {q.isLoading && <Skeleton />}
      {data && !data.connected && (
        <Notice tone="danger">{t("O serviço comercial não está conectado nesta instalação. Os cupons só funcionam depois que os pagamentos (Stripe e InfinitePay) e a ligação com a API estiverem ativos.")}</Notice>
      )}
      {data?.connected && (
        <>
          <p className="text-xs text-ink-muted">{t("Pagamentos: USD {us}, BRL {br}.", { us: data.stripe ? t("ativo") : t("desligado"), br: data.infinitepay ? t("ativo") : t("desligado") })}</p>
          <CreatePromo onDone={refresh} />
          {data.codes.length === 0 ? <Empty title={t("Nenhum cupom ainda")} /> : (
            <ul className="grid gap-3">{data.codes.map((p) => (
              <li key={p.code} className="grid gap-2 border-t border-line pt-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-base">{p.code}</span>
                  <Status tone={p.active ? "success" : "neutral"}>{p.active ? t("Ativo") : t("Desativado")}</Status>
                  {p.markets.includes("US") && p.kind !== "free_days" && !p.stripe.synced && <Status tone="warning">{t("Stripe: {s}", { s: p.stripe.state || t("pendente") })}</Status>}
                </div>
                <p className="text-sm">{describe(p)}</p>
                <p className="text-xs text-ink-muted">{t("Planos: {plans}. Mercados: {markets}. Expira: {end}. Limite: {total} no total, {per} por cliente.", {
                  plans: p.plans.map((x) => PLAN[x] ?? x).join(", "), markets: p.markets.join(", "), end: p.ends_at ? day(p.ends_at) : t("nunca"),
                  total: p.max_redemptions ? String(p.max_redemptions) : t("sem limite"), per: String(p.max_per_account) })}</p>
                <p className="text-xs">{t("Usos: {redeemed} confirmados, {reserved} em checkout, {refunded} estornados, {accounts} clientes.", {
                  redeemed: String(p.stats.redeemed), reserved: String(p.stats.reserved), refunded: String(p.stats.refunded), accounts: String(p.stats.accounts) })}
                  {Object.entries(p.stats.discount).map(([c, v]) => ` ${t("Desconto total: {v}.", { v: money(v, c) })}`).join("")}</p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className="btn-quiet" disabled={toggle.isPending} onClick={() => toggle.mutate(p)}>{p.active ? t("Desativar") : t("Ativar")}</button>
                  {p.stripe.state === "error" || p.stripe.state === "pending"
                    ? <button type="button" className="btn-quiet" disabled={sync.isPending} onClick={() => sync.mutate(p.code)}><RefreshCw size={15} aria-hidden />{t("Sincronizar com o Stripe")}</button> : null}
                  <button type="button" className="btn-quiet" aria-expanded={open === p.code} onClick={() => setOpen(open === p.code ? null : p.code)}>{t("Usos e clientes")}</button>
                </div>
                {open === p.code && <PromoDetails code={p.code} />}
              </li>
            ))}</ul>
          )}
        </>
      )}
      <ErrorNote error={q.error ?? toggle.error ?? sync.error} />
    </section>
  );
}
