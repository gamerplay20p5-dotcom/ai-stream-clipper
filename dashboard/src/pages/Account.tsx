import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check, CreditCard, ExternalLink, Film, KeyRound, LoaderCircle, LogOut, QrCode, Radio, RefreshCcw, Send, ShieldCheck, TicketPercent, Trash2,
} from "lucide-react";
import { useEffect, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AccountChannel, type AccountPreset, type AccountState } from "../api";
import { Brand } from "../components/Brand";
import { ConfirmAction, Empty, ErrorNote, LanguagePicker, Notice, Skeleton } from "../components/ui";
import { locale, t } from "../i18n";
import { money, monthly, PLAN_IDS, plans, type Market, type PlanId } from "../plans";

const PLAN_NAMES: Record<string, string> = { basic: "Basic", creator: "Creator", pro: "Pro" };
const TAGLINES: Record<PlanId, () => string> = {
  basic: () => t("Para começar a receber cortes"),
  creator: () => t("Para quem publica com frequência"),
  pro: () => t("Para criadores e equipes profissionais"),
};
const AI_TIER: Record<string, () => string> = {
  economy: () => t("IA econômica"), enhanced: () => t("IA aprimorada"), premium: () => t("IA premium com segunda análise"),
};
const dayFmt = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone: "America/Sao_Paulo" });
const day = (iso: string | null | undefined) => (iso ? dayFmt.format(new Date(iso)) : "");
const GB = 1024 ** 3;

type Tone = "active" | "warning" | "ended" | "none";
function statusView(s: AccountState): { tone: Tone; label: string } {
  if (!s.subscription) return { tone: "none", label: t("Sem assinatura") };
  if (s.entitled && s.subscription.status === "grace") return { tone: "warning", label: t("Pagamento atrasado") };
  if (s.entitled) return { tone: "active", label: t("Ativa") };
  if (s.subscription.status === "suspended") return { tone: "warning", label: t("Suspensa") };
  if (s.subscription.status === "none") return { tone: "none", label: t("Pagamento não concluído") };
  return { tone: "ended", label: t("Encerrada") };
}

function StatusChip({ tone, label }: { tone: Tone; label: string }) {
  return <span className="acct-status" data-tone={tone}><i aria-hidden />{label}</span>;
}

/** What each plan includes, straight from the catalog (same rules the API enforces). */
function planFeatures(id: PlanId): string[] {
  const p = plans[id];
  const f = p.features;
  return [
    t("{n} h de live por mês", { n: String(p.hours) }),
    p.channels === 1 ? t("1 canal") : t("{n} canais", { n: String(p.channels) }),
    p.simultaneous === 1 ? t("1 live por vez") : t("{n} lives ao mesmo tempo", { n: String(p.simultaneous) }),
    t("{n} GB de armazenamento", { n: String(p.storageGb) }),
    AI_TIER[f.ai]?.() ?? f.ai,
    p.export4k ? t("Exportação até 4K, {n} por mês", { n: String(p.export4k) }) : t("Exportação até 1080p"),
    f.autoPublish
      ? t("Publicação automática em até {d} contas, {n} posts por dia", { d: String(f.publishDestinations), n: String(f.publishDaily) })
      : t("Sem publicação automática"),
  ];
}

// ------------------------------------------------------------------ usage

function Meter({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 100;
  const full = total > 0 ? used >= total : true;
  return (
    <div className="acct-meter">
      <span className="acct-meter-label">{label}</span>
      <span className="acct-meter-value"><b>{used}</b><span>{t("de {total} {unit}", { total: String(total), unit })}</span></span>
      <div className="acct-bar" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(used, total)}>
        <span style={{ transform: `scaleX(${pct / 100})` }} data-full={full || undefined} />
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ plan picker (no subscription)

type Quote = { code: string; list: number; discount: number; final: number; currency: string; freeDays: number };

function PlanPicker({ available }: { available: boolean }) {
  const qc = useQueryClient();
  const [params] = useSearchParams();
  const [market, setMarket] = useState<Market>(params.get("market") === "US" ? "US" : "BR");
  const pre = params.get("plan");
  const [selected, setSelected] = useState<PlanId>(pre === "basic" || pre === "pro" ? pre : "creator");
  const [code, setCode] = useState("");
  const [quotes, setQuotes] = useState<Record<string, Quote | string>>({});
  // Prices with a code always come from the server; the browser only shows them
  const check = useMutation({
    mutationFn: async () => {
      const out: Record<string, Quote | string> = {};
      for (const plan of PLAN_IDS) {
        try { out[plan] = await api.post<Quote>("/api/account/promo-quote", { plan, market, promo_code: code.trim() }); }
        catch (e) { out[plan] = e instanceof Error ? e.message : String(e); }
      }
      return out;
    },
    onSuccess: setQuotes,
  });
  const start = useMutation({
    mutationFn: (plan: string) => api.post<{ url?: string; free?: boolean }>("/api/account/checkout", {
      plan, market, language: locale.startsWith("pt") ? "pt-BR" : "en", ...(code.trim() ? { promo_code: code.trim() } : {}) }),
    onSuccess: (r) => { if (r.url) window.location.assign(r.url); else void qc.invalidateQueries({ queryKey: ["account"] }); },
  });
  const switchMarket = (next: Market) => { setMarket(next); setQuotes({}); check.reset(); };
  const q = quotes[selected];
  const price = monthly(selected, market);
  const anyQuote = Object.values(quotes).some((v) => typeof v !== "string");
  const codeError = Object.keys(quotes).length > 0 && !anyQuote ? String(Object.values(quotes)[0]) : null;

  function onCardKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const keys: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    if (!(event.key in keys)) return;
    event.preventDefault();
    const next = (index + keys[event.key] + PLAN_IDS.length) % PLAN_IDS.length;
    setSelected(PLAN_IDS[next]);
    (event.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
  }

  return (
    <section className="acct-plans" aria-labelledby="plans-title">
      <div className="acct-plans-head">
        <div>
          <h2 id="plans-title" className="acct-h2">{t("Escolha seu plano")}</h2>
          <p className="acct-sub">{t("Preços mensais. Você revisa tudo antes de pagar.")}</p>
        </div>
        <div className="acct-segmented" role="group" aria-label={t("Moeda")}>
          <button type="button" aria-pressed={market === "BR"} onClick={() => switchMarket("BR")}>BRL</button>
          <button type="button" aria-pressed={market === "US"} onClick={() => switchMarket("US")}>USD</button>
        </div>
      </div>

      <div className="acct-method" data-market={market}>
        {market === "BR" ? <QrCode size={18} aria-hidden /> : <CreditCard size={18} aria-hidden />}
        <p>
          <strong>{market === "BR" ? t("Pix ou cartão, pela InfinitePay") : t("Cartão, pelo Stripe")}</strong>
          <span>{market === "BR"
            ? t("Pagamento em reais por Pix ou cartão (InfinitePay). Cada mês é um novo pagamento: não há renovação automática no cartão.")
            : t("Pagamento em dólares no cartão (Stripe), com renovação automática mensal que você pode cancelar a qualquer momento.")}</span>
        </p>
      </div>

      <div className="acct-plan-grid" role="radiogroup" aria-labelledby="plans-title">
        {PLAN_IDS.map((plan, index) => {
          const quote = quotes[plan];
          const base = monthly(plan, market);
          const discounted = quote && typeof quote !== "string" ? quote : null;
          return (
            <button key={plan} type="button" role="radio" aria-checked={selected === plan} tabIndex={selected === plan ? 0 : -1}
              className={`acct-plan acct-plan-${plan}`} onClick={() => setSelected(plan)} onKeyDown={(e) => onCardKey(e, index)}>
              {plan === "creator" && <span className="acct-plan-badge">{t("Mais escolhido")}</span>}
              <span className="acct-plan-top">
                <span className="acct-plan-name">{PLAN_NAMES[plan]}</span>
                <span className="acct-plan-radio" aria-hidden><Check size={13} /></span>
              </span>
              <span className="acct-plan-tagline">{TAGLINES[plan]()}</span>
              <span className="acct-price">
                {discounted && !discounted.freeDays && discounted.final !== discounted.list
                  ? <><s>{base.label}</s><b>{money(discounted.final, discounted.currency)}</b><span>{t("no 1º mês")}</span></>
                  : <><b>{base.label}</b><span>{t("/mês")}</span></>}
              </span>
              {discounted && (
                <span className="acct-price-note">{discounted.freeDays
                  ? t("{n} dias grátis, depois {p} por mês", { n: String(discounted.freeDays), p: money(discounted.list, discounted.currency) })
                  : discounted.final === 0 ? t("Primeiro mês grátis, depois {p} por mês", { p: money(discounted.list, discounted.currency) })
                    : t("Depois {p} por mês", { p: money(discounted.list, discounted.currency) })}</span>
              )}
              <span className="acct-plan-features">
                {planFeatures(plan).map((item) => <span key={item}><Check size={14} aria-hidden />{item}</span>)}
              </span>
            </button>
          );
        })}
      </div>

      <div className="acct-checkout">
        {available ? (
          <form className="acct-coupon" onSubmit={(e: FormEvent) => { e.preventDefault(); if (code.trim()) check.mutate(); }}>
            <label htmlFor="coupon">{t("Cupom")}</label>
            <div className="acct-coupon-row">
              <input id="coupon" className="acct-input" value={code} maxLength={32} pattern="[A-Za-z0-9]{3,32}" autoComplete="off"
                aria-describedby="coupon-help" onChange={(e) => { setCode(e.target.value); setQuotes({}); check.reset(); }} placeholder={t("Opcional")} />
              <button className="acct-btn-secondary" disabled={!code.trim() || check.isPending}>
                {check.isPending ? <LoaderCircle size={15} className="acct-spin" aria-hidden /> : <TicketPercent size={15} aria-hidden />}
                {check.isPending ? t("Conferindo…") : t("Aplicar cupom")}
              </button>
            </div>
            <p id="coupon-help" className="acct-help">{t("Cupons de assinatura nova valem só para a primeira cobrança; as renovações seguem o preço normal.")}</p>
            {anyQuote && <p className="acct-coupon-ok" role="status"><Check size={14} aria-hidden />{t("Cupom {code} aplicado ao preço dos planos.", { code: code.trim().toUpperCase() })}</p>}
            {codeError && <p className="acct-coupon-error" role="alert">{codeError}</p>}
          </form>
        ) : (
          <Notice tone="danger">{t("Os pagamentos estão indisponíveis no momento. Tente novamente mais tarde.")}</Notice>
        )}
        <div className="acct-pay-panel">
        <div className="acct-summary-line" aria-live="polite">
          <span>{t("Plano {plan}", { plan: PLAN_NAMES[selected] })}</span>
          <b>{q && typeof q !== "string" ? (q.freeDays ? t("{n} dias grátis", { n: String(q.freeDays) }) : money(q.final, q.currency)) : price.label}</b>
          <span className="acct-help">{q && typeof q !== "string" ? t("na primeira cobrança, depois {p} por mês", { p: money(q.list, q.currency) }) : t("por mês")}</span>
        </div>
        <button type="button" className="acct-btn-primary" disabled={!available || start.isPending || typeof q === "string"} onClick={() => start.mutate(selected)}>
          {start.isPending ? <LoaderCircle size={17} className="acct-spin" aria-hidden /> : <ShieldCheck size={17} aria-hidden />}
          {start.isPending ? t("Abrindo o pagamento…") : t("Assinar o {plan}", { plan: PLAN_NAMES[selected] })}
        </button>
        <p className="acct-help">{market === "BR" ? t("Você será levado ao pagamento seguro da InfinitePay.") : t("Você será levado ao pagamento seguro do Stripe.")}</p>
        {start.data?.free && <Notice tone="success">{t("Cupom aplicado: o acesso foi liberado sem cobrança. A assinatura aparece aqui em instantes.")}</Notice>}
        <ErrorNote error={start.error ?? check.error} />
        </div>
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ running subscription

/** A recurring HTS code on the running subscription: discount on the next renewals, never on what was already paid. */
function RecurringCode() {
  const qc = useQueryClient();
  const [code, setCode] = useState("");
  const preview = useMutation({ mutationFn: () => api.post<Quote & { duration: string; months: number | null }>("/api/account/promo-apply", { promo_code: code.trim(), preview: true }) });
  const apply = useMutation({
    mutationFn: () => api.post("/api/account/promo-apply", { promo_code: code.trim() }),
    onSuccess: () => { setCode(""); preview.reset(); void qc.invalidateQueries({ queryKey: ["account"] }); },
  });
  const p = preview.data;
  return (
    <div className="acct-recurring">
      <form className="acct-coupon" onSubmit={(e: FormEvent) => { e.preventDefault(); if (code.trim()) preview.mutate(); }}>
        <label htmlFor="recurring-code">{t("Cupom para a assinatura em andamento")}</label>
        <div className="acct-coupon-row">
          <input id="recurring-code" className="acct-input" value={code} maxLength={32} pattern="[A-Za-z0-9]{3,32}" autoComplete="off"
            onChange={(e) => { setCode(e.target.value); preview.reset(); apply.reset(); }} />
          <button className="acct-btn-secondary" disabled={!code.trim() || preview.isPending}>
            {preview.isPending ? <LoaderCircle size={15} className="acct-spin" aria-hidden /> : <TicketPercent size={15} aria-hidden />}{t("Ver desconto")}
          </button>
        </div>
      </form>
      {p && (
        <div className="acct-recurring-preview">
          <p>{p.duration === "forever"
            ? t("As próximas renovações passam a custar {final} (de {list}).", { final: money(p.final, p.currency), list: money(p.list, p.currency) })
            : t("As próximas {n} renovações passam a custar {final} (de {list}).", { n: String(p.months), final: money(p.final, p.currency), list: money(p.list, p.currency) })}</p>
          <button type="button" className="acct-btn-primary" disabled={apply.isPending} onClick={() => apply.mutate()}>{t("Aplicar à minha assinatura")}</button>
        </div>
      )}
      {apply.isSuccess && <Notice tone="success">{t("Cupom aplicado. O desconto vale a partir da próxima renovação.")}</Notice>}
      <ErrorNote error={preview.error ?? apply.error} />
    </div>
  );
}

function Manage({ state }: { state: AccountState }) {
  const sub = state.subscription!;
  const portal = useMutation({
    mutationFn: () => api.post<{ url: string }>("/api/account/portal"),
    onSuccess: (r) => window.location.assign(r.url),
  });
  const stripe = sub.manage === "stripe_portal";
  return (
    <section className="acct-card" aria-labelledby="manage-title">
      <h2 id="manage-title" className="acct-h3"><RefreshCcw size={16} aria-hidden />{t("Renovação e cancelamento")}</h2>
      {stripe ? (
        <>
          <p className="acct-text">{sub.cancel_at_period_end
            ? t("A assinatura foi cancelada e termina em {date}. Você pode reativá-la pelo portal até lá.", { date: day(sub.period_end) })
            : t("A cobrança em dólar renova automaticamente no cartão em {date}. Troque o cartão, baixe faturas ou cancele no portal seguro do Stripe.", { date: day(sub.renews_at ?? sub.period_end) })}</p>
          <button type="button" className="acct-btn-secondary" onClick={() => portal.mutate()} disabled={portal.isPending}>
            {portal.isPending ? <LoaderCircle size={15} className="acct-spin" aria-hidden /> : <ExternalLink size={15} aria-hidden />}{t("Abrir o portal do Stripe")}
          </button>
          <ErrorNote error={portal.error} />
        </>
      ) : (
        <>
          <p className="acct-text">{t("No Brasil, cada mês é um novo pagamento por Pix ou cartão. Alguns dias antes do vencimento, o link da renovação aparece aqui e chega por e-mail. Para cancelar, basta não pagar a próxima renovação: o acesso vale até o fim do período pago.")}</p>
          {sub.renewal && (
            <a className="acct-btn-primary" href={sub.renewal.url} rel="noopener">
              <CreditCard size={15} aria-hidden />{t("Pagar a renovação (link válido até {date})", { date: day(new Date(sub.renewal.valid_until * 1000).toISOString()) })}
            </a>
          )}
        </>
      )}
      {state.checkout_available && <RecurringCode />}
    </section>
  );
}

const PRESET_LABELS: Record<AccountPreset, () => string> = {
  balanced: () => t("Seleção equilibrada"),
  best: () => t("Melhores momentos"),
  humor: () => t("Humor e reações"),
  highlights: () => t("Jogadas e highlights"),
  intense: () => t("Momentos intensos"),
};

function monitoringText(ch: AccountChannel): string {
  switch (ch.monitoring.state) {
    case "watching": return t("Acompanhando a live agora");
    case "live_pending": return t("Ao vivo agora");
    case "paused": return t("Pausado pelo plano");
    case "not_following": return t("Fora do seu plano nesta live: {reason}", { reason: ch.monitoring.note });
    default: return t("{n} cortes", { n: String(ch.clips) });
  }
}

function Channels({ state }: { state: AccountState }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["account-channels"], queryFn: () => api.get<AccountChannel[]>("/api/account/channels") });
  const presets = useQuery({ queryKey: ["account-presets"], queryFn: () => api.get<{ id: AccountPreset; allowed: boolean }[]>("/api/account/presets") });
  const [url, setUrl] = useState("");
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["account-channels"] }); void qc.invalidateQueries({ queryKey: ["account"] }); };
  const add = useMutation({ mutationFn: () => api.post("/api/account/channels", { url }), onSuccess: () => { setUrl(""); refresh(); } });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/account/channels/${id}`), onSuccess: refresh });
  const setPreset = useMutation({
    mutationFn: ({ id, preset }: { id: number; preset: AccountPreset }) => api.patch(`/api/account/channels/${id}`, { preset }),
    onSuccess: refresh,
  });
  const limit = state.limits?.channels ?? 0;
  const channels = list.data ?? [];
  const options = presets.data ?? [];
  const canChoose = options.some((o) => o.allowed && o.id !== "balanced");
  return (
    <section className="acct-card" aria-labelledby="channels-title">
      <div className="acct-card-head">
        <h2 id="channels-title" className="acct-h3"><Radio size={16} aria-hidden />{t("Meus canais")}</h2>
        <span className="acct-count">{t("{used} de {total}", { used: String(channels.length), total: String(limit) })}</span>
      </div>
      <ErrorNote error={list.error} />
      {list.isLoading && <Skeleton label={t("Carregando canais")} lines={2} />}
      {list.data && channels.length === 0 && <Empty title={t("Nenhum canal ainda")}>{t("Adicione o link de um canal da Twitch ou do YouTube para o Clipper acompanhar as lives e gerar os seus Shorts.")}</Empty>}
      <ul className="acct-channels">
        {channels.map((ch) => (
          <li key={ch.id}>
            <span className="acct-channel-dot" data-live={ch.monitoring.state === "watching" || ch.monitoring.state === "live_pending" || undefined} aria-hidden />
            <div className="acct-channel-body">
              <p className="acct-channel-name">{ch.display_name || ch.channel} <span>{ch.platform}</span></p>
              <p className="acct-help">{monitoringText(ch)}</p>
              <label className="acct-preset">
                <span>{t("Preferência de cortes")}</span>
                <select className="acct-input acct-select" value={ch.preset} disabled={!canChoose || setPreset.isPending}
                  onChange={(e) => setPreset.mutate({ id: ch.id, preset: e.target.value as AccountPreset })}>
                  {options.filter((o) => o.allowed || o.id === ch.preset).map((o) => (
                    <option key={o.id} value={o.id}>{PRESET_LABELS[o.id]()}</option>
                  ))}
                </select>
              </label>
            </div>
            <ConfirmAction title={t("Parar de acompanhar este canal?")} description={t("O acompanhamento para e as publicações automáticas deste canal que ainda não começaram são canceladas. Os Shorts que você já recebeu continuam na sua biblioteca.")}
              confirmLabel={t("Remover")} pending={remove.isPending} onConfirm={() => remove.mutate(ch.id)} label={t("Remover {name}", { name: ch.display_name || ch.channel })}>
              <Trash2 size={15} aria-hidden />
            </ConfirmAction>
          </li>
        ))}
      </ul>
      {channels.length > 0 && !canChoose && presets.data && (
        <p className="acct-help">{t("No Basic, os cortes seguem a seleção equilibrada. Creator e Pro escolhem a preferência por canal.")}</p>
      )}
      {state.entitled && channels.length < limit && (
        <form className="acct-add" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
          <label htmlFor="channel-url">{t("Link do canal")}</label>
          <div className="acct-coupon-row">
            <input id="channel-url" className="acct-input" type="url" required placeholder="https://www.twitch.tv/canal" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="acct-btn-primary" disabled={add.isPending}>{add.isPending ? t("Adicionando…") : t("Adicionar")}</button>
          </div>
        </form>
      )}
      <ErrorNote error={add.error ?? remove.error ?? setPreset.error} />
    </section>
  );
}

/** Plan, status, renewal and the main actions, in one compact block. */
function Summary({ s }: { s: AccountState }) {
  const sub = s.subscription!;
  const plan = (sub.plan in plans ? sub.plan : "basic") as PlanId;
  const stripe = sub.provider === "stripe";
  const until = s.benefits?.credit_days ? s.benefits.access_until : (sub.period_end ?? sub.access_until);
  return (
    <section className={`acct-summary acct-plan-${plan}`} aria-labelledby="plan-title">
      <div className="acct-summary-main">
        <p className="acct-kicker">{t("Seu plano")}</p>
        <h2 id="plan-title" className="acct-plan-title">{PLAN_NAMES[sub.plan] ?? sub.plan}</h2>
        <p className="acct-text">{s.entitled
          ? (sub.status === "grace"
            ? t("A renovação não foi paga. O acesso continua até {date}; depois disso o monitoramento para.", { date: day(sub.access_until) })
            : t("Acesso ativo até {date}.", { date: day(until) }))
          : t("Sem acesso no momento. Assine novamente para voltar a monitorar seus canais; seus cortes continuam na sua biblioteca.")}</p>
        {s.benefits && s.benefits.credit_days > 0 && (
          <p className="acct-text">{t("Inclui {n} dias de crédito de acesso da HTS. A cobrança segue a data normal.", { n: String(s.benefits.credit_days) })}</p>
        )}
        {s.benefits?.upgrade && (
          <p className="acct-text">{t("Upgrade temporário para o {plan} até {date}. Depois, volta ao seu plano {paid}.", {
            plan: PLAN_NAMES[s.benefits.upgrade.plan] ?? s.benefits.upgrade.plan, date: day(s.benefits.upgrade.until), paid: PLAN_NAMES[s.benefits.paid_plan] ?? s.benefits.paid_plan })}</p>
        )}
      </div>
      <dl className="acct-facts">
        <div><dt>{t("Pagamento")}</dt><dd>{stripe ? t("Cartão, pelo Stripe") : sub.provider === "infinitepay" ? t("Pix ou cartão, pela InfinitePay") : t("Cortesia da HTS")}</dd></div>
        <div><dt>{stripe ? t("Próxima cobrança") : t("Acesso até")}</dt>
          <dd>{stripe ? (sub.cancel_at_period_end ? t("Não renova") : day(sub.renews_at ?? sub.period_end)) : day(sub.access_until)}</dd></div>
        <div><dt>{t("Renovação")}</dt><dd>{stripe ? (sub.cancel_at_period_end ? t("Cancelada") : t("Automática")) : t("Manual, por link mensal")}</dd></div>
      </dl>
    </section>
  );
}

/** Subscriber area: plan, status, usage and channels. Read-only for limits: the backend decides everything. */
export default function AccountApp({ email }: { email: string }) {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const returned = params.get("checkout");
  const [watching, setWatching] = useState(returned === "returned");
  const state = useQuery({
    queryKey: ["account"], queryFn: () => api.get<AccountState>("/api/account/subscription"),
    // After a provider return, keep asking until the confirmed state arrives (the return itself grants nothing)
    refetchInterval: watching ? 5_000 : 60_000,
  });
  useEffect(() => {
    if (watching && state.data?.entitled) setWatching(false);
    if (!watching) return;
    const stop = window.setTimeout(() => setWatching(false), 120_000);
    return () => window.clearTimeout(stop);
  }, [watching, state.data?.entitled]);
  const [leaving, setLeaving] = useState(false);
  async function logout() {
    setLeaving(true);
    try { await api.post("/api/auth/logout"); } finally { qc.clear(); window.location.assign("/login"); }
  }
  const s = state.data;
  const view = s ? statusView(s) : null;
  const limits = s?.limits;

  return (
    <div className="library-shell customer-area">
      <a className="skip-link" href="#conteudo">{t("Ir para o conteúdo")}</a>
      <header className="library-topbar acct-topbar">
        <Brand />
        <nav className="acct-nav" aria-label={t("Conta")}>
          <Link className="acct-navlink" to="/biblioteca" aria-label={t("Meus cortes")}><Film size={16} aria-hidden /><span>{t("Meus cortes")}</span></Link>
          <Link className="acct-navlink" to="/conta/publicacao" aria-label={t("Publicação automática")}><Send size={16} aria-hidden /><span>{t("Publicação automática")}</span></Link>
          <Link className="acct-navlink" to="/conta/senha" aria-label={t("Senha")}><KeyRound size={16} aria-hidden /><span>{t("Senha")}</span></Link>
          <span className="acct-email">{email}</span>
          <button type="button" className="acct-navlink" onClick={logout} disabled={leaving} aria-label={leaving ? t("Saindo…") : t("Sair")}>
            <LogOut size={16} aria-hidden /><span>{leaving ? t("Saindo…") : t("Sair")}</span>
          </button>
        </nav>
      </header>
      <main id="conteudo" tabIndex={-1} className="acct-main">
        <div className="acct-head">
          <div>
            <h1 className="acct-h1">{t("Minha assinatura")}</h1>
            <p className="acct-sub">{t("Seu plano, o consumo do ciclo e os canais que o Clipper acompanha.")}</p>
          </div>
          {view && <StatusChip tone={view.tone} label={view.label} />}
        </div>
        {returned === "returned" && !s?.entitled && (
          <Notice tone="success">{t("Recebemos o seu retorno do pagamento. A assinatura aparece aqui assim que o provedor confirmar, normalmente em poucos segundos.")}</Notice>
        )}
        {returned === "cancelled" && <div className="flex flex-wrap items-center gap-2"><Notice tone="danger">{t("Pagamento cancelado. Nenhuma cobrança foi feita.")}</Notice>
          <button type="button" className="acct-btn-secondary" onClick={() => setParams({})}>{t("Ok")}</button></div>}
        <ErrorNote error={state.error} />
        {state.isLoading && <Skeleton label={t("Carregando sua assinatura")} />}
        {s && (
          <>
            {s.subscription?.review && <Notice tone="danger">{t("Há um pagamento desta assinatura em revisão financeira. Nenhum acesso novo é liberado até a revisão terminar.")}</Notice>}
            {s.subscription && <Summary s={s} />}
            {s.entitled && limits && (
              <section className="acct-card" aria-labelledby="usage-title">
                <h2 id="usage-title" className="acct-h3">{t("Consumo do ciclo")}</h2>
                <div className="acct-meters">
                  <Meter label={t("Horas de live analisadas")} used={Math.floor(s.usage.live_seconds / 3600)} total={limits.hours} unit={t("h")} />
                  <Meter label={t("Canais")} used={s.usage.channels} total={limits.channels} unit="" />
                  <Meter label={t("Lives ao mesmo tempo")} used={s.usage.live_sessions} total={limits.simultaneous} unit="" />
                  <Meter label={t("Armazenamento")} used={Math.round((s.usage.storage_bytes / GB) * 10) / 10} total={limits.storage_gb} unit="GB" />
                  {limits.export_4k > 0 && <Meter label={t("Exportações 4K")} used={s.usage.exports_4k} total={limits.export_4k} unit="" />}
                </div>
                {!(limits.export_4k > 0) && <p className="acct-help">{t("Exportação até 1080p. A exportação 4K faz parte do plano Pro.")}</p>}
              </section>
            )}
            <div className="acct-columns">
              {s.entitled && <Channels state={s} />}
              {s.subscription && s.entitled && <Manage state={s} />}
            </div>
            {!s.entitled && <PlanPicker available={s.checkout_available} />}
            {s.subscription && s.subscription.payments.length > 0 && (
              <section className="acct-card" aria-labelledby="payments-title">
                <h2 id="payments-title" className="acct-h3">{t("Histórico de cobranças")}</h2>
                <div className="acct-table-wrap">
                  <table className="acct-table">
                    <thead><tr><th scope="col">{t("Data")}</th><th scope="col">{t("Valor")}</th><th scope="col">{t("Situação")}</th></tr></thead>
                    <tbody>{s.subscription.payments.map((p) => (
                      <tr key={p.id}>
                        <td>{p.at ? dayFmt.format(new Date(p.at * 1000)) : ""}</td>
                        <td className="font-mono">{money(p.amount ?? 0, p.currency)}</td>
                        <td><span className="acct-pay" data-status={p.status}>{p.status === "paid" ? t("Pago") : p.status === "refunded" ? t("Estornado") : p.status === "payment_failed" ? t("Falhou") : p.status}</span></td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              </section>
            )}
          </>
        )}
        <footer className="acct-footer"><LanguagePicker /></footer>
      </main>
    </div>
  );
}
