import { useQuery } from "@tanstack/react-query";
import { ChevronRight, CircleAlert } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Clip, type Infra, type Stats, type Streamer } from "../api";
import type { CalendarData } from "../components/EditorialOps";
import { ClipFrame, Empty, ErrorNote, PageHeader, Skeleton, Tally } from "../components/ui";
import { ago, jobLabel, platformName, timecode } from "../lib/format";

const number = new Intl.NumberFormat("pt-BR");
const date = new Intl.DateTimeFormat("pt-BR", { weekday: "long", day: "numeric", month: "long" });

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

export default function Overview() {
  const navigate = useNavigate();
  const stats = useQuery({ queryKey: ["stats"], queryFn: () => api.get<Stats>("/api/stats"), refetchInterval: 10_000 });
  const review = useQuery({
    queryKey: ["clips", "ready-strip"],
    queryFn: () => api.get<{ items: Clip[]; total: number }>("/api/clips?status=ready&sort=score&limit=12"),
    refetchInterval: 10_000,
  });
  const streamers = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers"), refetchInterval: 10_000 });
  const infra = useQuery({ queryKey: ["infra"], queryFn: () => api.get<Infra>("/api/infrastructure"), refetchInterval: 15_000 });
  const calendar = useQuery({ queryKey: ["calendar"], queryFn: () => api.get<CalendarData>("/api/publish/calendar?days=14"), refetchInterval: 20_000 });
  const s = stats.data;
  const live = (streamers.data ?? []).filter((streamer) => streamer.live_session);
  const processing = s ? ["candidate", "analyzing", "rendering", "waiting"].reduce((sum, key) => sum + (s.clips_by_status[key] ?? 0), 0) : undefined;
  const scheduled = calendar.data?.items.filter((item) => item.status === "scheduled");
  const next = scheduled?.find((item) => item.when && Date.parse(item.when) >= Date.now());
  const signals = [
    { label: "Lives no ar", value: s?.live_sessions, detail: s ? `${s.streamers_enabled} canais monitorados` : undefined, to: "/streamers" },
    { label: "Cortes em processamento", value: processing, detail: s ? `${s.pending_jobs} tarefas na fila` : undefined, to: "/cortes?status=candidate%2Canalyzing%2Crendering%2Cwaiting" },
    { label: "Publicações agendadas", value: scheduled?.length, detail: next?.when ? `Próximo envio em ${next.when.slice(8, 10)}/${next.when.slice(5, 7)} às ${next.when.slice(11, 16)}` : undefined, to: "/publicacao?aba=calendario" },
  ];

  const failureLink = !!s?.dead_jobs && (
    <Link className="flex items-center gap-2 py-1.5 text-sm font-medium text-tally hover:underline" to="/infra#falhas">
      <CircleAlert size={16} className="shrink-0" aria-hidden />
      {number.format(s.dead_jobs)} {s.dead_jobs === 1 ? "tarefa com falha definitiva" : "tarefas com falha definitiva"}
    </Link>
  );

  return (
    <div>
      <PageHeader title="Visão geral">
        <time className="text-sm text-ink-muted first-letter:uppercase">{date.format(new Date())}</time>
      </PageHeader>
      {failureLink && <div className="mb-3 xl:hidden">{failureLink}</div>}

      <div className="grid grid-cols-1 gap-[var(--space-section)] border-t border-suite-line pt-5 xl:grid-cols-[minmax(0,1fr)_minmax(17rem,0.38fr)] xl:pt-6">
        <div className="min-w-0 space-y-[var(--space-section)]">
          <section aria-labelledby="review-queue-title">
            <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 id="review-queue-title" className="display text-xl">Fila de revisão</h2>
                {review.data && <p className="mt-1 text-sm text-ink-muted">
                  {number.format(review.data.total)} {review.data.total === 1 ? "corte pronto para revisar" : "cortes prontos para revisar"}
                </p>}
              </div>
              <Link className="btn-quiet self-center" to="/cortes?status=ready">Abrir central</Link>
            </header>
            <ErrorNote error={review.error} />
            {review.isLoading && <Skeleton label="Carregando cortes para revisar" />}
            {review.data && (review.data.items.length > 0 ? (
              <ul className="overview-reel" aria-label="Cortes prontos com maior pontuação">
                {review.data.items.slice(0, 4).map((clip) => (
                  <li key={clip.id}><ClipFrame clip={clip} onOpen={() => navigate(`/cortes/${clip.id}`)} /></li>
                ))}
              </ul>
            ) : <Empty title="Fila de revisão vazia">Os cortes prontos para avaliação aparecem aqui.</Empty>)}
          </section>

          <section aria-labelledby="live-overview-title" className="border-t border-suite-line pt-5">
            <header className="mb-2 flex flex-wrap items-center justify-between gap-3">
              <h2 id="live-overview-title" className="display text-lg">Lives monitoradas</h2>
              <Link className="text-sm font-medium text-take hover:underline" to="/streamers">Gerenciar canais</Link>
            </header>
            {streamers.isLoading && <Skeleton label="Carregando transmissões" />}
            <ErrorNote error={streamers.error} />
            {streamers.data && !live.length && <p className="py-3 text-sm text-ink-muted">Nenhum canal monitorado está ao vivo.</p>}
            <ul className="divide-y divide-suite-line">
              {live.map((st) => {
                const session = st.live_session!;
                const lag = Math.max(0, session.covered_until - session.analyzed_until);
                return (
                  <li key={st.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-3"><strong>{st.display_name}</strong><Tally live /></div>
                      <p className="mt-1 break-words text-xs text-ink-muted">{platformName[st.platform]} / {session.title || "Sem título"}</p>
                    </div>
                    <div className="text-xs text-ink-muted">
                      <p>Captura <span className="font-mono">{timecode(session.covered_until)}</span></p>
                      <p className={lag > 60 ? "text-warning" : ""}>{lag > 1 ? `Análise ${Math.round(lag)} s atrás` : "Análise em dia"}</p>
                    </div>
                  </li>
                );
              })}
            </ul>
          </section>
        </div>

        <div className="operation-rail">
          <section aria-labelledby="operation-title">
            <h2 id="operation-title" className="display mb-2 text-lg">Operação</h2>
            {failureLink && <div className="mb-2 hidden xl:block">{failureLink}</div>}
            <ul className="divide-y divide-suite-line">
              {signals.map(({ label, value, detail, to }) => (
                <li key={label}>
                  <Link to={to} className="group grid min-w-0 grid-cols-[1fr_auto_auto] items-center gap-2 py-2.5 hover:text-take">
                    <span className="min-w-0 text-sm font-medium">{label}{detail && <span className="block text-xs font-normal text-ink-muted">{detail}</span>}</span>
                    <span className="font-mono text-sm font-semibold">{value === undefined ? "—" : number.format(value)}</span>
                    <ChevronRight size={14} className="text-ink-faint transition-colors group-hover:text-take" aria-hidden />
                  </Link>
                </li>
              ))}
            </ul>
            <ErrorNote error={stats.error ?? calendar.error} />
          </section>

          <section aria-labelledby="services-title" className="border-t border-suite-line pt-4">
            <h2 id="services-title" className="display mb-2 text-lg">Saúde dos serviços</h2>
            <ErrorNote error={infra.error} />
            {infra.isLoading && <Skeleton label="Carregando status da infraestrutura" />}
            <ul className="divide-y divide-suite-line">
              {infra.data?.hosts.map((host) => {
                const online = host.workers.filter((worker) => worker.status === "online").length;
                const healthy = online > 0 && online === host.workers.length;
                return (
                  <li key={host.label} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 py-2.5 text-xs">
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{host.label}</p>
                      <p className="break-words text-ink-muted">{host.role || host.hostname}</p>
                    </div>
                    <span className={healthy ? "text-ok" : "text-warning"}>{online ? `${online}/${host.workers.length} online` : "Sem workers online"}</span>
                  </li>
                );
              })}
            </ul>
            {infra.data?.hosts.length === 0 && <p className="py-2 text-sm text-ink-muted">Nenhum servidor reportou status.</p>}
            <Link className="mt-2 inline-block text-xs font-medium text-take hover:underline" to="/infra">Ver infraestrutura</Link>
          </section>

          <section aria-labelledby="activity-title" className="border-t border-suite-line pt-4">
            <h2 id="activity-title" className="display mb-3 text-lg">Atividade recente</h2>
            {s && <Activity values={s.clips_last_24h} />}
            {stats.isLoading && <Skeleton label="Carregando atividade" lines={2} />}
            {(infra.data?.failures ?? []).slice(0, 3).map((failure) => (
              <Link key={failure.id} to="/infra#falhas" className="mt-2 flex justify-between gap-2 border-t border-suite-line py-2 text-xs hover:text-take">
                <span><span className="text-tally">Falha</span> / {jobLabel[failure.type] ?? failure.type} #{failure.id}</span>
                <span className="shrink-0 text-ink-muted">{ago(failure.finished_at)}</span>
              </Link>
            ))}
          </section>
        </div>
      </div>
    </div>
  );
}
