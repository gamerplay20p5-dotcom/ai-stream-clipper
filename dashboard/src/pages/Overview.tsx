import { useQuery } from "@tanstack/react-query";
import { ChevronRight, CircleAlert, CircleCheck } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Clip, type Infra, type Stats, type Streamer } from "../api";
import type { CalendarData } from "../components/EditorialOps";
import { ClipFrame, DecisionChip, ErrorNote, PageHeader, Skeleton, Tally } from "../components/ui";
import { ago, jobLabel, platformName, timecode } from "../lib/format";
import { decisionLabel, decisionOf } from "../lib/publishing";

const number = new Intl.NumberFormat("pt-BR");
const date = new Intl.DateTimeFormat("pt-BR", { weekday: "long", day: "numeric", month: "long", timeZone: "America/Sao_Paulo" });
const plural = (n: number, one: string, many: string) => `${number.format(n)} ${n === 1 ? one : many}`;
type Control = { paused: boolean; reason?: string; by?: string };
type Attention = { key: string; tone: "danger" | "warning" | "take"; text: string; detail?: string; to: string; action: string };

function Activity({ values }: { values: number[] }) {
  const peak = Math.max(1, ...values);
  const total = values.reduce((sum, value) => sum + value, 0);
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-muted"><strong className="text-ink">{number.format(total)}</strong> {total === 1 ? "corte encontrado" : "cortes encontrados"} nas últimas 24 horas</p>
      <div className="grid h-10 auto-cols-fr grid-flow-col items-end gap-1 border-b border-suite-line" role="img" aria-label="Volume de cortes por hora, da mais antiga à mais recente">
        {values.map((value, index) => (
          <span key={index} title={`${value} corte(s)`} className={value ? "bg-ink-muted" : "bg-suite-line"}
            style={{ height: `${Math.max(5, 100 * value / peak)}%` }} />
        ))}
      </div>
      <div className="flex justify-between text-xs text-ink-muted"><span>24 h atrás</span><span>Agora</span></div>
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

  const s = stats.data;
  const items = calendar.data?.items ?? [];
  const count = (status: string) => items.filter((i) => i.status === status).length;
  const live = (streamers.data ?? []).filter((streamer) => streamer.live_session);
  const nowLocal = new Intl.DateTimeFormat("sv-SE", { timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short" })
    .format(new Date()).replace(" ", "T");
  const upcoming = items.filter((i) => ["proposed", "scheduled"].includes(i.status) && (i.when ?? "") >= nowLocal).slice(0, 5);
  const silentHosts = (infra.data?.hosts ?? []).filter((h) => h.workers.length > 0 && !h.workers.some((w) => w.status === "online"));

  const attention: Attention[] = [];
  if (control.data?.paused) attention.push({ key: "paused", tone: "danger", text: "As publicações estão pausadas",
    detail: control.data.reason ? `Motivo: ${control.data.reason}` : undefined, to: "/publicacao", action: "Ver agenda" });
  const ready = s?.clips_by_status?.ready ?? 0;
  if (ready) attention.push({ key: "ready", tone: "take", text: plural(ready, "corte pronto para revisar", "cortes prontos para revisar"),
    to: "/cortes?status=ready", action: "Revisar" });
  if (human.data?.total) attention.push({ key: "human", tone: "warning",
    text: plural(human.data.total, "corte pede revisão humana", "cortes pedem revisão humana"),
    detail: "A IA não teve evidência suficiente para decidir sozinha.", to: "/cortes?status=gate%3AHUMAN_REVIEW", action: "Decidir" });
  if (count("proposed")) attention.push({ key: "proposed", tone: "take",
    text: plural(count("proposed"), "sugestão de publicação aguarda você", "sugestões de publicação aguardam você"),
    detail: "Nada é enviado sem a sua confirmação.", to: "/publicacao", action: "Confirmar ou recusar" });
  if (count("uncertain")) attention.push({ key: "uncertain", tone: "warning",
    text: plural(count("uncertain"), "envio com resultado incerto", "envios com resultado incerto"),
    detail: "Confira no perfil se o vídeo foi publicado.", to: "/publicacao?aba=envios", action: "Conferir" });
  if (count("failed")) attention.push({ key: "failed", tone: "danger", text: plural(count("failed"), "envio falhou", "envios falharam"),
    to: "/publicacao?aba=envios", action: "Ver motivo" });
  if (s?.dead_jobs) attention.push({ key: "dead", tone: "danger",
    text: plural(s.dead_jobs, "tarefa parou com falha", "tarefas pararam com falha"), to: "/infra#falhas", action: "Ver falhas" });
  if (silentHosts.length) attention.push({ key: "hosts", tone: "danger",
    text: `${silentHosts.map((h) => h.label).join(", ")} sem nenhum worker online`, to: "/infra", action: "Ver servidores" });
  const loading = stats.isLoading || calendar.isLoading;

  return (
    <div>
      <div className="overview-masthead today-masthead">
        <PageHeader title="Hoje">
          <time className="text-sm text-ink-muted first-letter:uppercase">{date.format(new Date())}</time>
        </PageHeader>
      </div>

      <div className="grid grid-cols-1 gap-[var(--space-section)] pt-5 xl:grid-cols-[minmax(0,1fr)_minmax(17rem,0.38fr)] xl:pt-6">
        <div className="min-w-0 space-y-[var(--space-section)]">
          <section aria-labelledby="attention-title">
            <h2 id="attention-title" className="display mb-3 text-xl">Precisa de você</h2>
            <ErrorNote error={stats.error ?? calendar.error} />
            {loading && <Skeleton label="Conferindo o que precisa de atenção" lines={2} />}
            {!loading && attention.length === 0 && (
              <div className="all-clear">
                <CircleCheck size={22} className="text-ok" aria-hidden />
                <div><p className="font-semibold">Tudo em dia</p>
                  <p className="text-sm text-ink-muted">Nenhum corte, sugestão ou falha esperando por alguém agora.</p></div>
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
                <h2 id="review-queue-title" className="display text-lg">Melhores cortes da fila</h2>
                <Link className="text-sm font-medium text-take hover:underline" to="/cortes?status=ready">Abrir a central</Link>
              </header>
              <ul className="today-reel" aria-label="Cortes prontos com maior pontuação">
                {review.data.items.map((clip) => (
                  <li key={clip.id}><ClipFrame clip={clip} onOpen={() => navigate(`/cortes/${clip.id}`)} /></li>
                ))}
              </ul>
            </section>
          )}

          <section aria-labelledby="next-title">
            <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="next-title" className="display text-lg">Próximos envios</h2>
              <Link className="text-sm font-medium text-take hover:underline" to="/publicacao">Ver agenda</Link>
            </header>
            {calendar.data && upcoming.length === 0 && <p className="py-2 text-sm text-ink-muted">Nada agendado para os próximos 14 dias.</p>}
            <ul className="divide-y divide-suite-line">
              {upcoming.map((i) => {
                const kind = decisionOf(i);
                return (
                  <li key={i.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                    <span className="min-w-0">
                      <span className="font-mono text-xs text-ink-muted">{i.when?.slice(8, 10)}/{i.when?.slice(5, 7)} {i.when?.slice(11, 16)}</span>
                      <strong className="ml-2 text-sm">{i.title || `Corte #${i.clip_id}`}</strong>
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
              <h2 id="live-overview-title" className="display text-lg">Ao vivo agora</h2>
              <Link className="text-xs font-medium text-take hover:underline" to="/streamers">Canais</Link>
            </header>
            {streamers.isLoading && <Skeleton label="Carregando transmissões" lines={1} />}
            <ErrorNote error={streamers.error} />
            {streamers.data && !live.length && <p className="py-2 text-sm text-ink-muted">Nenhum canal monitorado está ao vivo.</p>}
            <ul className="divide-y divide-suite-line">
              {live.map((st) => {
                const session = st.live_session!;
                const lag = Math.max(0, session.covered_until - session.analyzed_until);
                return (
                  <li key={st.id} className="py-2.5">
                    <div className="flex flex-wrap items-center gap-2"><strong className="text-sm">{st.display_name}</strong><Tally live /></div>
                    <p className="mt-1 break-words text-xs text-ink-muted">{platformName[st.platform]} · {session.title || "Sem título"}</p>
                    <p className={`mt-1 text-xs ${lag > 60 ? "text-warning" : "text-ink-muted"}`}>
                      {timecode(session.covered_until)} capturados · {lag > 1 ? `análise ${Math.round(lag)} s atrás` : "análise em dia"}
                    </p>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-labelledby="services-title" className="border-t border-suite-line pt-4">
            <h2 id="services-title" className="display mb-2 text-lg">Servidores</h2>
            <ErrorNote error={infra.error} />
            {infra.isLoading && <Skeleton label="Carregando status dos servidores" lines={1} />}
            <ul className="divide-y divide-suite-line">
              {infra.data?.hosts.map((host) => {
                const online = host.workers.filter((worker) => worker.status === "online").length;
                const healthy = online > 0 && online === host.workers.length;
                return (
                  <li key={host.label} className="flex items-center justify-between gap-3 py-2 text-xs">
                    <span className="min-w-0"><span className="text-sm font-medium">{host.label}</span>
                      <span className="block truncate text-ink-muted">{host.role || host.hostname}</span></span>
                    <span className={`shrink-0 ${healthy ? "text-ok" : "text-warning"}`}>
                      {online ? `${online}/${host.workers.length} online` : "sem workers online"}
                    </span>
                  </li>
                );
              })}
            </ul>
          </section>

          <section aria-labelledby="activity-title" className="border-t border-suite-line pt-4">
            <h2 id="activity-title" className="display mb-3 text-lg">Atividade</h2>
            {s && <Activity values={s.clips_last_24h} />}
            {stats.isLoading && <Skeleton label="Carregando atividade" lines={2} />}
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
