import { useQuery } from "@tanstack/react-query";
import { ChevronRight, CircleAlert, CircleCheck, Loader2, RefreshCw } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Clip, type Infra, type Stats, type Streamer } from "../api";
import type { CalendarData } from "../components/EditorialOps";
import { ClipFrame, DecisionChip, PageHeader, Skeleton, Tally } from "../components/ui";
import { ago, jobLabel, platformName, timecode } from "../lib/format";
import { decisionLabel, decisionOf } from "../lib/publishing";
import { t, locale, tx } from "../i18n";

const number = new Intl.NumberFormat(locale);
const date = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long", timeZone: "America/Sao_Paulo" });
const plural = (n: number, one: string, many: string) => `${number.format(n)} ${n === 1 ? one : many}`;
type Control = { paused: boolean; reason?: string; by?: string };
type Attention = { key: string; tone: "danger" | "warning" | "take"; text: string; detail?: string; to: string; action: string };

function Activity({ values }: { values: number[] }) {
  const peak = Math.max(1, ...values);
  const total = values.reduce((sum, value) => sum + value, 0);
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-muted"><strong className="text-ink">{number.format(total)}</strong> {t("{v} nas últimas 24 horas", { v: total === 1 ? t("corte encontrado") : t("cortes encontrados") })}</p>
      <div className="grid h-10 auto-cols-fr grid-flow-col items-end gap-1 border-b border-suite-line" role="img" aria-label={t("Volume de cortes por hora, da mais antiga à mais recente")}>
        {values.map((value, index) => (
          <span key={index} title={`${value} corte(s)`} className={value ? "bg-ink-muted" : "bg-suite-line"}
            style={{ height: `${Math.max(5, 100 * value / peak)}%` }} />
        ))}
      </div>
      <div className="flex justify-between text-xs text-ink-muted"><span>{t("24 h atrás")}</span><span>{t("Agora")}</span></div>
    </div>
  );
}

/** "Hoje": what needs a person now, what is live, what goes out next. Every number comes from the API. */
export default function Overview() {
  const navigate = useNavigate();
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api.get<Stats>("/api/stats"), refetchInterval: 10_000 });
  const review = useQuery({
    queryKey: ["clips", "ready-strip"],
    queryFn: () => api.get<{ items: Clip[]; total: number }>("/api/clips?status=ready&sort=score&limit=4"),
    refetchInterval: 10_000,
  });
  const human = useQuery({
    queryKey: ["clips", "human-review-count"],
    queryFn: () => api.get<{ items: Clip[]; total: number }>("/api/clips?status=ready&gate=HUMAN_REVIEW&limit=1"),
    refetchInterval: 20_000,
  });
  const streamers = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers"), refetchInterval: 10_000 });
  const infra = useQuery({ queryKey: ["infra"], queryFn: () => api.get<Infra>("/api/infrastructure"), refetchInterval: 15_000 });
  const calendar = useQuery({ queryKey: ["calendar"], queryFn: () => api.get<CalendarData>("/api/publish/calendar?days=14"), refetchInterval: 20_000 });
  const control = useQuery({ queryKey: ["publish-control"], queryFn: () => api.get<Control>("/api/publish/control"), refetchInterval: 20_000 });
  // All-time totals per publication status: the reliable source for states that wait for a person (proposed, uncertain).
  const totals = useQuery({
    queryKey: ["publish-overview"], queryFn: () => api.get<{ counts: Record<string, number> }>("/api/publish/overview"), refetchInterval: 20_000,
  });

  const s = stats.data;
  const items = calendar.data?.items ?? [];
  const count = (status: string) => items.filter((i) => i.status === status).length;
  const live = (streamers.data ?? []).filter((streamer) => streamer.live_session);
  const nowLocal = new Intl.DateTimeFormat("sv-SE", { timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short" })
    .format(new Date()).replace(" ", "T");
  const upcoming = items.filter((i) => ["proposed", "scheduled"].includes(i.status) && (i.when ?? "") >= nowLocal).slice(0, 5);
  const silentHosts = (infra.data?.hosts ?? []).filter((h) => h.workers.length > 0 && !h.workers.some((w) => w.status === "online"));

  // Every check that feeds "Precisa de você". Missing data is never read as zero: a check is either
  // verified (data, no error), still loading, or failed; "nothing pending" needs ALL of them verified.
  const checks = [
    { name: t("cortes"), q: stats }, { name: t("revisão humana"), q: human }, { name: t("publicações"), q: totals },
    { name: t("envios recentes"), q: calendar }, { name: t("pausa dos envios"), q: control }, { name: t("servidores"), q: infra },
  ];
  const verifying = checks.filter((c) => c.q.isLoading).map((c) => c.name);
  const failed = checks.filter((c) => c.q.isError).map((c) => c.name);
  const allVerified = !verifying.length && !failed.length;
  const counts = totals.data?.counts;
  const checkedAt = Math.max(...checks.map((c) => c.q.dataUpdatedAt));

  const attention: Attention[] = [];
  if (control.data?.paused) attention.push({ key: "paused", tone: "danger", text: t("As publicações estão pausadas"),
    detail: control.data.reason ? t("Motivo: {reason}", { reason: control.data.reason }) : undefined, to: "/publicacao", action: t("Ver agenda") });
  const ready = s?.clips_by_status?.ready ?? 0;
  if (ready) attention.push({ key: "ready", tone: "take", text: plural(ready, t("corte pronto para revisar"), t("cortes prontos para revisar")),
    to: "/cortes?status=ready", action: t("Revisar") });
  if (human.data?.total) attention.push({ key: "human", tone: "warning",
    text: plural(human.data.total, t("corte pede revisão humana"), t("cortes pedem revisão humana")),
    detail: t("A IA não teve evidência suficiente para decidir sozinha."), to: "/cortes?status=gate%3AHUMAN_REVIEW", action: t("Decidir") });
  if (counts?.proposed) attention.push({ key: "proposed", tone: "take",
    text: plural(counts.proposed, t("sugestão de publicação aguarda você"), t("sugestões de publicação aguardam você")),
    detail: t("Total pendente. Nada é enviado sem a sua confirmação."), to: "/publicacao", action: t("Revisar sugestões") });
  if (counts?.uncertain) attention.push({ key: "uncertain", tone: "warning",
    text: plural(counts.uncertain, t("envio com resultado incerto"), t("envios com resultado incerto")),
    detail: t("Total pendente. Confira no perfil se o vídeo foi publicado."), to: "/publicacao?aba=envios", action: t("Conferir") });
  if (count("failed")) attention.push({ key: "failed", tone: "danger", text: plural(count("failed"), "envio falhou", "envios falharam"),
    detail: t("Envios agendados a partir de 2 dias atrás (janela da agenda)."), to: "/publicacao?aba=envios", action: t("Ver motivo") });
  if (s?.dead_jobs) attention.push({ key: "dead", tone: "danger",
    text: plural(s.dead_jobs, t("tarefa parou com falha"), t("tarefas pararam com falha")), to: "/infra#falhas", action: t("Ver falhas") });
  if (silentHosts.length) attention.push({ key: "hosts", tone: "danger",
    text: t("{join} sem nenhum worker online", { join: silentHosts.map((h) => h.label).join(", ") }), to: "/infra", action: t("Ver servidores") });
  const retry = () => checks.forEach((c) => c.q.isError && c.q.refetch());

  return (
    <div>
      <div className="overview-masthead today-masthead">
        <PageHeader title={t("Hoje")}>
          <time className="text-sm text-ink-muted first-letter:uppercase">{date.format(new Date())}</time>
        </PageHeader>
      </div>

      <div className="grid grid-cols-1 gap-[var(--space-section)] pt-5 xl:grid-cols-[minmax(0,1fr)_minmax(17rem,0.38fr)] xl:pt-6">
        <div className="min-w-0 space-y-[var(--space-section)]">
          <section aria-labelledby="attention-title">
            <h2 id="attention-title" className="display mb-3 text-xl">{t("Precisa de você")}</h2>
            {verifying.length > 0 && (
              <p className="check-state" role="status">
                <Loader2 size={15} className="motion-safe:animate-spin" aria-hidden /> {t("Ainda verificando: {join}.", { join: verifying.join(", ") })}
              </p>
            )}
            {failed.length > 0 && (
              <div className="check-state check-failed" role="alert">
                <CircleAlert size={16} aria-hidden />
                <p className="min-w-0 flex-1">{t("Não foi possível verificar:")} <strong>{failed.join(", ")}</strong>{t(". Pendências dessas áreas podem não aparecer aqui.")}</p>
                <button type="button" className="btn-quiet" onClick={retry}><RefreshCw size={14} aria-hidden /> {t("Tentar de novo")}</button>
              </div>
            )}
            {verifying.length === checks.length && <Skeleton label={t("Conferindo o que precisa de atenção")} lines={2} />}
            {allVerified && attention.length === 0 && (
              <div className="all-clear" role="status">
                <CircleCheck size={22} className="text-ok" aria-hidden />
                <div><p className="font-semibold">{t("Tudo verificado e sem pendências")}</p>
                  <p className="text-sm text-ink-muted">{t("Nenhum corte, sugestão ou falha esperando por alguém.")}
                    {checkedAt > 0 && t(" Verificado às {toLocaleTimeString}.", { toLocaleTimeString: new Date(checkedAt).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone: "America/Sao_Paulo" }) })}</p></div>
              </div>
            )}
            {attention.length > 0 && (
              <ul className="attention-list">
                {attention.map((a) => (
                  <li key={a.key} data-tone={a.tone}>
                    <Link to={a.to} className="attention-row">
                      <span className="min-w-0"><strong>{a.text}</strong>{a.detail && <small>{a.detail}</small>}</span>
                      <span className="attention-action">{a.action}<ChevronRight size={15} aria-hidden /></span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {review.data && review.data.items.length > 0 && (
            <section aria-labelledby="review-queue-title">
              <header className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
                <h2 id="review-queue-title" className="display text-lg">{t("Melhores cortes da fila")}</h2>
                <Link className="text-sm font-medium text-take hover:underline" to="/cortes?status=ready">{t("Abrir a central")}</Link>
              </header>
              <ul className="today-reel" aria-label={t("Cortes prontos com maior pontuação")}>
                {review.data.items.map((clip) => (
                  <li key={clip.id}><ClipFrame clip={clip} onOpen={() => navigate(`/cortes/${clip.id}`)} /></li>
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="next-title">
            <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="next-title" className="display text-lg">{t("Próximos envios")}</h2>
              <Link className="text-sm font-medium text-take hover:underline" to="/publicacao">{t("Ver agenda")}</Link>
            </header>
            {calendar.isError && <p className="py-2 text-sm text-warning">{t("Não foi possível carregar a agenda agora.")}</p>}
            {calendar.data && !calendar.isError && upcoming.length === 0 && <p className="py-2 text-sm text-ink-muted">{t("Nada agendado para os próximos 14 dias.")}</p>}
            <ul className="divide-y divide-suite-line">
              {upcoming.map((i) => {
                const kind = decisionOf(i);
                return (
                  <li key={i.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                    <span className="min-w-0">
                      <span className="font-mono text-xs text-ink-muted">{i.when?.slice(8, 10)}/{i.when?.slice(5, 7)} {i.when?.slice(11, 16)}</span>
                      <strong className="ml-2 text-sm">{i.title || t("Corte #{clip_id}", { clip_id: i.clip_id })}</strong>
                      <span className="block text-xs text-ink-muted">{platformName[i.platform] ?? i.platform} · {i.account}</span>
                    </span>
                    {kind && <DecisionChip kind={kind} label={decisionLabel[kind]} />}
                  </li>
                );
              })}
            </ul>
          </section>
        </div>

        <div className="operation-rail">
          <section aria-labelledby="live-overview-title">
            <header className="mb-2 flex flex-wrap items-center justify-between gap-3">
              <h2 id="live-overview-title" className="display text-lg">{t("Ao vivo agora")}</h2>
              <Link className="text-xs font-medium text-take hover:underline" to="/streamers">{t("Canais")}</Link>
            </header>
            {streamers.isLoading && <Skeleton label={t("Carregando transmissões")} lines={1} />}
            {streamers.isError && <p className="py-2 text-sm text-warning">{t("Não foi possível consultar as lives agora.")}</p>}
            {streamers.data && !streamers.isError && !live.length && <p className="py-2 text-sm text-ink-muted">{t("Nenhum canal monitorado está ao vivo.")}</p>}
            <ul className="divide-y divide-suite-line">
              {live.map((st) => {
                const session = st.live_session!;
                const lag = Math.max(0, session.covered_until - session.analyzed_until);
                return (
                  <li key={st.id} className="py-2.5">
                    <div className="flex flex-wrap items-center gap-2"><strong className="text-sm">{st.display_name}</strong><Tally live /></div>
                    <p className="mt-1 break-words text-xs text-ink-muted">{platformName[st.platform]} · {session.title || t("Sem título")}</p>
                    <p className={`mt-1 text-xs ${lag > 60 ? "text-warning" : "text-ink-muted"}`}>
                      {t("{timecode} capturados · {v}", { timecode: timecode(session.covered_until), v: lag > 1 ? t("análise {round} s atrás", { round: Math.round(lag) }) : t("análise em dia") })}
                    </p>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-labelledby="services-title" className="border-t border-suite-line pt-4">
            <h2 id="services-title" className="display mb-2 text-lg">{t("Servidores")}</h2>
            {infra.isError && <p className="py-2 text-sm text-warning">{t("Não foi possível consultar os servidores agora.")}</p>}
            {infra.isLoading && <Skeleton label={t("Carregando status dos servidores")} lines={1} />}
            <ul className="divide-y divide-suite-line">
              {infra.data?.hosts.map((host) => {
                const online = host.workers.filter((worker) => worker.status === "online").length;
                const healthy = online > 0 && online === host.workers.length;
                return (
                  <li key={host.label} className="flex items-center justify-between gap-3 py-2 text-xs">
                    <span className="min-w-0"><span className="text-sm font-medium">{host.label}</span>
                      <span className="block truncate text-ink-muted">{tx(host.role) || host.hostname}</span></span>
                    <span className={`shrink-0 ${healthy ? "text-ok" : "text-warning"}`}>
                      {online ? `${online}/${host.workers.length} online` : t("sem workers online")}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-labelledby="activity-title" className="border-t border-suite-line pt-4">
            <h2 id="activity-title" className="display mb-3 text-lg">{t("Atividade")}</h2>
            {s && <Activity values={s.clips_last_24h} />}
            {stats.isLoading && <Skeleton label={t("Carregando atividade")} lines={2} />}
            {(infra.data?.failures ?? []).slice(0, 3).map((failure) => (
              <Link key={failure.id} to="/infra#falhas" className="mt-2 flex justify-between gap-2 border-t border-suite-line py-2 text-xs hover:text-take">
                <span><CircleAlert size={12} className="mr-1 inline text-tally" aria-hidden />{jobLabel[failure.type] ?? failure.type} #{failure.id}</span>
                <span className="shrink-0 text-ink-muted">{ago(failure.finished_at)}</span>
              </Link>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
