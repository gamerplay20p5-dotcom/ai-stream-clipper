import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CreditCard, ExternalLink, Film, KeyRound, LogOut, Radio, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type AccountChannel, type AccountState } from "../api";
import { Brand } from "../components/Brand";
import { ConfirmAction, Empty, ErrorNote, LanguagePicker, Notice, Skeleton, Status } from "../components/ui";
import { locale, t } from "../i18n";

const PLAN_NAMES: Record<string, string> = { basic: "Basic", creator: "Creator", pro: "Pro" };
const dayFmt = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", year: "numeric", timeZone: "America/Sao_Paulo" });
const day = (iso: string | null | undefined) => (iso ? dayFmt.format(new Date(iso)) : "");
const GB = 1024 ** 3;

function statusView(s: AccountState): { tone: "success" | "warning" | "danger" | "neutral"; label: string } {
  if (!s.subscription) return { tone: "neutral", label: t("Sem assinatura") };
  if (s.entitled && s.subscription.status === "grace") return { tone: "warning", label: t("Pagamento atrasado") };
  if (s.entitled) return { tone: "success", label: t("Ativa") };
  if (s.subscription.status === "suspended") return { tone: "warning", label: t("Suspensa") };
  if (s.subscription.status === "none") return { tone: "neutral", label: t("Pagamento não concluído") };
  return { tone: "danger", label: t("Encerrada") };
}

function Usage({ label, used, total, unit }: { label: string; used: number; total: number; unit: string }) {
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 100;
  const full = total > 0 ? used >= total : true;
  return (
    <div className="account-usage">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm">{label}</span>
        <span className="font-mono text-xs text-ink-muted">{t("{used} de {total} {unit}", { used: String(used), total: String(total), unit })}</span>
      </div>
      <div className="account-bar" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={Math.min(used, total)}>
        <span style={{ width: `${pct}%` }} data-full={full || undefined} />
      </div>
    </div>
  );
}

function Plans({ available }: { available: boolean }) {
  const [params] = useSearchParams();
  const [market, setMarket] = useState<"BR" | "US">(params.get("market") === "US" ? "US" : "BR");
  const preselected = params.get("plan");
  const start = useMutation({
    mutationFn: (plan: string) => api.post<{ url: string }>("/api/account/checkout", { plan, market, language: locale.startsWith("pt") ? "pt-BR" : "en" }),
    onSuccess: (r) => window.location.assign(r.url),
  });
  return (
    <section className="account-card" aria-labelledby="plans-title">
      <h2 id="plans-title" className="account-card-title">{t("Escolha seu plano")}</h2>
      <p className="text-sm text-ink-muted">{market === "BR"
        ? t("Pagamento em reais por Pix ou cartão (InfinitePay). Cada mês é um novo pagamento: não há renovação automática no cartão.")
        : t("Pagamento em dólares no cartão (Stripe), com renovação automática mensal que você pode cancelar a qualquer momento.")}</p>
      <div className="segmented mt-3" role="group" aria-label={t("Moeda")}>
        <button type="button" aria-pressed={market === "BR"} onClick={() => setMarket("BR")}>BRL</button>
        <button type="button" aria-pressed={market === "US"} onClick={() => setMarket("US")}>USD</button>
      </div>
      {!available && <Notice tone="danger">{t("Os pagamentos estão indisponíveis no momento. Tente novamente mais tarde.")}</Notice>}
      <div className="account-plans">
        {(["basic", "creator", "pro"] as const).map((plan) => (
          <button key={plan} type="button" className={plan === preselected ? "btn-take" : "btn-quiet"} disabled={!available || start.isPending}
            onClick={() => start.mutate(plan)}>
            <CreditCard size={15} aria-hidden />{t("Assinar o {plan}", { plan: PLAN_NAMES[plan] })}
          </button>
        ))}
      </div>
      <ErrorNote error={start.error} />
    </section>
  );
}

function Manage({ state }: { state: AccountState }) {
  const sub = state.subscription!;
  const portal = useMutation({
    mutationFn: () => api.post<{ url: string }>("/api/account/portal"),
    onSuccess: (r) => window.location.assign(r.url),
  });
  if (sub.manage === "stripe_portal") {
    return (
      <section className="account-card" aria-labelledby="manage-title">
        <h2 id="manage-title" className="account-card-title">{t("Renovação e cancelamento")}</h2>
        <p className="text-sm text-ink-muted">{sub.cancel_at_period_end
          ? t("A assinatura foi cancelada e termina em {date}. Você pode reativá-la pelo portal até lá.", { date: day(sub.period_end) })
          : t("A cobrança em dólar renova automaticamente no cartão em {date}. Troque o cartão, baixe faturas ou cancele no portal seguro do Stripe.", { date: day(sub.renews_at ?? sub.period_end) })}</p>
        <button type="button" className="btn-quiet mt-3" onClick={() => portal.mutate()} disabled={portal.isPending}>
          <ExternalLink size={15} aria-hidden />{t("Abrir o portal do Stripe")}
        </button>
        <ErrorNote error={portal.error} />
      </section>
    );
  }
  return (
    <section className="account-card" aria-labelledby="manage-title">
      <h2 id="manage-title" className="account-card-title">{t("Renovação e cancelamento")}</h2>
      <p className="text-sm text-ink-muted">{t("No Brasil, cada mês é um novo pagamento por Pix ou cartão. Alguns dias antes do vencimento, o link da renovação aparece aqui e chega por e-mail. Para cancelar, basta não pagar a próxima renovação: o acesso vale até o fim do período pago.")}</p>
      {sub.renewal && (
        <a className="btn-take mt-3 inline-flex" href={sub.renewal.url} rel="noopener">
          <CreditCard size={15} aria-hidden />{t("Pagar a renovação (link válido até {date})", { date: day(new Date(sub.renewal.valid_until * 1000).toISOString()) })}
        </a>
      )}
    </section>
  );
}

function Channels({ state }: { state: AccountState }) {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["account-channels"], queryFn: () => api.get<AccountChannel[]>("/api/account/channels") });
  const [url, setUrl] = useState("");
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["account-channels"] }); void qc.invalidateQueries({ queryKey: ["account"] }); };
  const add = useMutation({ mutationFn: () => api.post("/api/account/channels", { url }), onSuccess: () => { setUrl(""); refresh(); } });
  const remove = useMutation({ mutationFn: (id: number) => api.del(`/api/account/channels/${id}`), onSuccess: refresh });
  const limit = state.limits?.channels ?? 0;
  const channels = list.data ?? [];
  return (
    <section className="account-card" aria-labelledby="channels-title">
      <h2 id="channels-title" className="account-card-title">{t("Canais monitorados")}</h2>
      <ErrorNote error={list.error} />
      {list.isLoading && <Skeleton label={t("Carregando canais")} lines={2} />}
      {list.data && channels.length === 0 && <Empty title={t("Nenhum canal ainda")}>{t("Adicione o link do seu canal da Twitch ou do YouTube para o Clipper acompanhar suas lives.")}</Empty>}
      <ul className="account-channels">
        {channels.map((ch) => (
          <li key={ch.id}>
            <Radio size={16} aria-hidden className={ch.status === "live" ? "text-take" : "text-ink-muted"} />
            <div className="min-w-0">
              <p className="truncate text-sm">{ch.display_name || ch.channel} <span className="text-xs text-ink-muted">{ch.platform}</span></p>
              <p className="text-xs text-ink-muted">{ch.note || (ch.status === "live" ? t("Ao vivo agora") : t("{n} cortes", { n: String(ch.clips) }))}</p>
            </div>
            <ConfirmAction title={t("Remover este canal?")} description={t("O monitoramento para e os cortes deste canal são apagados.")}
              confirmLabel={t("Remover")} pending={remove.isPending} onConfirm={() => remove.mutate(ch.id)} label={t("Remover {name}", { name: ch.display_name || ch.channel })}>
              <Trash2 size={15} aria-hidden />
            </ConfirmAction>
          </li>
        ))}
      </ul>
      {state.entitled && channels.length < limit && (
        <form className="account-add" onSubmit={(e: FormEvent) => { e.preventDefault(); add.mutate(); }}>
          <label className="label" htmlFor="channel-url">{t("Link do canal")}</label>
          <div className="flex gap-2">
            <input id="channel-url" className="field" type="url" required placeholder="https://www.twitch.tv/seucanal" value={url} onChange={(e) => setUrl(e.target.value)} />
            <button className="btn-take" disabled={add.isPending}>{add.isPending ? t("Adicionando…") : t("Adicionar")}</button>
          </div>
        </form>
      )}
      <ErrorNote error={add.error ?? remove.error} />
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
    <div className="library-shell">
      <a className="skip-link" href="#conteudo">{t("Ir para o conteúdo")}</a>
      <header className="library-topbar">
        <Brand />
        <nav className="flex min-w-0 items-center gap-2" aria-label={t("Conta")}>
          <Link className="btn-quiet" to="/biblioteca"><Film size={15} aria-hidden />{t("Meus cortes")}</Link>
          <Link className="btn-quiet" to="/conta/senha"><KeyRound size={15} aria-hidden />{t("Senha")}</Link>
          <span className="hidden truncate text-xs text-ink-muted md:inline">{email}</span>
          <button type="button" className="btn-quiet" onClick={logout} disabled={leaving}>
            <LogOut size={15} aria-hidden />{leaving ? t("Saindo…") : t("Sair")}
          </button>
        </nav>
      </header>
      <section className="library-hero" aria-labelledby="account-title">
        <div className="library-hero-inner">
          <div>
            <h1 id="account-title" className="library-title">{t("Minha assinatura")}</h1>
            <p className="mt-2 max-w-[56ch] text-sm text-ink-muted">{t("Seu plano, o consumo do ciclo e os canais que o Clipper acompanha.")}</p>
          </div>
          {view && <Status tone={view.tone}>{view.label}</Status>}
        </div>
      </section>
      <main id="conteudo" tabIndex={-1} className="library-main account-main">
        {returned === "returned" && !s?.entitled && (
          <Notice tone="success">{t("Recebemos o seu retorno do pagamento. A assinatura aparece aqui assim que o provedor confirmar, normalmente em poucos segundos.")}</Notice>
        )}
        {returned === "cancelled" && <div className="flex flex-wrap items-center gap-2"><Notice tone="danger">{t("Pagamento cancelado. Nenhuma cobrança foi feita.")}</Notice>
          <button type="button" className="btn-quiet" onClick={() => setParams({})}>{t("Ok")}</button></div>}
        <ErrorNote error={state.error} />
        {state.isLoading && <Skeleton label={t("Carregando sua assinatura")} />}
        {s && (
          <>
            {s.subscription?.review && <Notice tone="danger">{t("Há um pagamento desta assinatura em revisão financeira. Nenhum acesso novo é liberado até a revisão terminar.")}</Notice>}
            {s.subscription && (
              <section className="account-card" aria-labelledby="plan-title">
                <h2 id="plan-title" className="account-card-title">{t("Plano {plan}", { plan: PLAN_NAMES[s.subscription.plan] ?? s.subscription.plan })}</h2>
                <p className="text-sm text-ink-muted">
                  {s.entitled
                    ? (s.subscription.status === "grace"
                      ? t("A renovação não foi paga. O acesso continua até {date}; depois disso o monitoramento para.", { date: day(s.subscription.access_until) })
                      : t("Acesso ativo até {date}.", { date: day(s.subscription.period_end ?? s.subscription.access_until) }))
                    : t("Sem acesso no momento. Assine novamente para voltar a monitorar seus canais; seus cortes continuam na sua biblioteca.")}
                </p>
              </section>
            )}
            {s.entitled && limits && (
              <section className="account-card" aria-labelledby="usage-title">
                <h2 id="usage-title" className="account-card-title">{t("Consumo do ciclo")}</h2>
                <Usage label={t("Horas de live analisadas")} used={Math.floor(s.usage.live_seconds / 3600)} total={limits.hours} unit={t("h")} />
                <Usage label={t("Canais")} used={s.usage.channels} total={limits.channels} unit="" />
                <Usage label={t("Lives ao mesmo tempo")} used={s.usage.live_sessions} total={limits.simultaneous} unit="" />
                <Usage label={t("Armazenamento")} used={Math.round((s.usage.storage_bytes / GB) * 10) / 10} total={limits.storage_gb} unit="GB" />
                {limits.export_4k > 0
                  ? <Usage label={t("Exportações 4K")} used={s.usage.exports_4k} total={limits.export_4k} unit="" />
                  : <p className="text-xs text-ink-muted">{t("Exportação até 1080p. A exportação 4K faz parte do plano Pro.")}</p>}
              </section>
            )}
            {s.entitled && <Channels state={s} />}
            {s.subscription && s.entitled && <Manage state={s} />}
            {!s.entitled && <Plans available={s.checkout_available} />}
            {s.subscription && s.subscription.payments.length > 0 && (
              <section className="account-card" aria-labelledby="payments-title">
                <h2 id="payments-title" className="account-card-title">{t("Histórico de cobranças")}</h2>
                <table className="account-table">
                  <thead><tr><th scope="col">{t("Data")}</th><th scope="col">{t("Valor")}</th><th scope="col">{t("Situação")}</th></tr></thead>
                  <tbody>{s.subscription.payments.map((p) => (
                    <tr key={p.id}>
                      <td>{p.at ? dayFmt.format(new Date(p.at * 1000)) : ""}</td>
                      <td className="font-mono">{new Intl.NumberFormat(locale, { style: "currency", currency: p.currency.toUpperCase() }).format((p.amount ?? 0) / 100)}</td>
                      <td>{p.status === "paid" ? t("Pago") : p.status === "refunded" ? t("Estornado") : p.status === "payment_failed" ? t("Falhou") : p.status}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </section>
            )}
          </>
        )}
        <footer className="mt-8 flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-muted"><LanguagePicker /></footer>
      </main>
    </div>
  );
}
