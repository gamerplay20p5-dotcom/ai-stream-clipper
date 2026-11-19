import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarDays, Clapperboard, Radio, Workflow } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Clip, type Infra, type Stats, type Streamer } from "../api";
import type { CalendarData } from "../components/EditorialOps";
import { ClipFrame, Empty, ErrorNote, PageHeader, Skeleton, Status, Tally } from "../components/ui";
import { ago, jobLabel, platformName, timecode } from "../lib/format";

const number = new Intl.NumberFormat("pt-BR");
const date = new Intl.DateTimeFormat("pt-BR", { weekday: "long", day: "numeric", month: "long" });

function Activity({ values }: { values: number[] }) {
  const peak = Math.max(1, ...values);
  const total = values.reduce((sum, value) => sum + value, 0);
  return (
    <div className="panel-body">
      <p className="mb-3 text-sm"><strong>{number.format(total)}</strong> {total === 1 ? "corte encontrado" : "cortes encontrados"} nas últimas 24 horas</p>
      <div className="grid h-12 auto-cols-fr grid-flow-col items-end gap-1" role="img" aria-label="Volume de cortes por hora, da mais antiga à mais recente">
        {values.map((value, index) => (
          <span key={index} title={`${value} corte(s)`} className={`rounded-t-sm ${value ? "bg-take" : "bg-suite-outline/50"}`}
            style={{ height: `${Math.max(6, 100 * value / peak)}%` }} />
        ))}
      </div>
      <div className="mt-2 flex justify-between text-xs text-ink-muted"><span>24 h atrás</span><span>Agora</span></div>
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
    { label: "Lives agora", value: s?.live_sessions, detail: s ? `${s.streamers_enabled} canais monitorados` : "Status do monitoramento", icon: Radio, to: "/streamers", tone: s?.live_sessions ? "text-tally" : "text-ink-muted" },
    { label: "Para revisar", value: review.data?.total, detail: "Fila de cortes", icon: Clapperboard, to: "/cortes?status=ready", tone: "text-take" },
    { label: "Em processamento", value: processing, detail: s ? `${s.pending_jobs} tarefas na fila` : "Fila de tarefas", icon: Workflow, to: "/cortes?status=candidate%2Canalyzing%2Crendering%2Cwaiting", tone: "text-processing" },
    { label: "Agendados", value: scheduled?.length, detail: next?.when ? `Próximo: ${next.when.slice(8, 10)}/${next.when.slice(5, 7)} às ${next.when.slice(11, 16)}` : "Janela do calendário", icon: CalendarDays, to: "/publicacao?aba=calendario", tone: "text-ink-muted" },
  ];

  return (
    <div>
      <PageHeader title="Visão geral" description="Acompanhe a operação, da transmissão à publicação.">
        <time className="pb-1 text-sm text-ink-muted first-letter:uppercase">{date.format(new Date())}</time>
      </PageHeader>

      <div className="section-flow">
        <div className="panel grid grid-cols-2 divide-suite-line lg:grid-cols-4 lg:divide-x" aria-label="Prioridades da operação">
          {signals.map(({ label, value, detail, icon: Icon, to, tone }) => (
            <Link key={label} to={to} className="group min-w-0 border-suite-line p-3 transition-colors hover:bg-suite-raised/60 max-lg:[&:nth-child(-n+2)]:border-b max-lg:[&:nth-child(odd)]:border-r sm:p-4">
              <span className="flex items-center gap-2 text-sm font-medium"><Icon size={17} className={`shrink-0 ${tone}`} aria-hidden />{label}</span>
              <span className="display my-1 block text-2xl">{value === undefined ? "—" : number.format(value)}</span>
              <span className="block text-xs text-ink-muted">{detail}</span>
            </Link>
          ))}
        </div>

        <div className="grid min-w-0 grid-cols-1 items-start gap-[var(--space-section)] xl:grid-cols-[minmax(0,1.65fr)_minmax(290px,1fr)]">
          <div className="grid min-w-0 grid-cols-1 gap-[var(--space-section)]">
            <section className="panel">
              <header className="panel-heading">
                <h2 className="display text-lg">Fila de revisão</h2>
                <Link className="inline-flex items-center gap-1.5 text-sm font-semibold text-take hover:underline" to="/cortes?status=ready">Abrir central <ArrowRight size={15} aria-hidden /></Link>
              </header>
              <div className="panel-body">
                <ErrorNote error={review.error} />
                {review.isLoading && <Skeleton label="Carregando cortes para revisar" />}
                {review.data && (review.data.items.length > 0 ? (
                  <div className="flex snap-x gap-3 overflow-x-auto px-1 pb-2 pt-1" aria-label="Cortes com maior pontuação">
                    {review.data.items.map((clip) => (
                      <div key={clip.id} className="w-[9.25rem] shrink-0 snap-start sm:w-[9.75rem]">
                        <ClipFrame clip={clip} onOpen={() => navigate(`/cortes/${clip.id}`)} />
                      </div>
                    ))}
                  </div>
                ) : <Empty title="Fila de revisão vazia">Os cortes prontos para avaliação aparecem aqui.</Empty>)}
              </div>
            </section>

            <section className="panel">
              <header className="panel-heading">
                <h2 className="display text-lg">Lives monitoradas</h2>
                <Link className="text-sm font-semibold text-take hover:underline" to="/streamers">Gerenciar canais</Link>
              </header>
              {streamers.isLoading && <div className="panel-body"><Skeleton label="Carregando transmissões" /></div>}
              {streamers.error && <div className="panel-body"><ErrorNote error={streamers.error} /></div>}
              {streamers.data && live.length === 0 && <p className="panel-body text-sm text-ink-muted">Nenhum canal monitorado está ao vivo.</p>}
              <ul className="divide-y divide-suite-line">
                {live.map((st) => {
                  const session = st.live_session!;
                  const lag = Math.max(0, session.covered_until - session.analyzed_until);
                  return (
                    <li key={st.id} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2"><strong>{st.display_name}</strong><Tally live /></div>
                        <p className="truncate text-xs text-ink-muted">{platformName[st.platform]} / {session.title || "Sem título"}</p>
                      </div>
                      <div className="font-mono text-xs text-ink-muted">
                        <p>Captura {timecode(session.covered_until)}</p>
                        <p className={lag > 60 ? "text-warning" : ""}>{lag > 1 ? `Análise ${Math.round(lag)} s atrás` : "Análise em dia"}</p>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          </div>

          <div className="grid min-w-0 grid-cols-1 gap-[var(--space-section)]">
            <section className="panel">
              <header className="panel-heading">
                <h2 className="display text-lg">Saúde dos serviços</h2>
                <Link className="text-sm font-semibold text-take hover:underline" to="/infra">Ver infraestrutura</Link>
              </header>
              <div className="panel-body space-y-3">
                <ErrorNote error={infra.error ?? stats.error} />
                {infra.isLoading && <Skeleton label="Carregando status da infraestrutura" />}
                {!!s?.dead_jobs && (
                  <Link className="notice border-tally/50 bg-tally-dim font-semibold text-tally hover:underline" to="/infra#falhas">
                    {number.format(s.dead_jobs)} {s.dead_jobs === 1 ? "tarefa com falha definitiva" : "tarefas com falha definitiva"}
                  </Link>
                )}
                {infra.data?.hosts.map((host) => {
                  const online = host.workers.filter((worker) => worker.status === "online").length;
                  return (
                    <div key={host.label} className="flex flex-wrap items-start justify-between gap-2 border-b border-suite-line pb-2.5 last:border-b-0 last:pb-0">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold">{host.label}</p>
                        <p className="break-words text-xs text-ink-muted">{host.role || host.hostname}</p>
                      </div>
                      <Status tone={online ? "success" : "warning"}>{online ? `${online}/${host.workers.length} online` : "Sem workers online"}</Status>
                    </div>
                  );
                })}
                {infra.data?.hosts.length === 0 && <p className="text-sm text-ink-muted">Nenhum servidor reportou status.</p>}
              </div>
            </section>
            <section className="panel">
              <header className="panel-heading"><h2 className="display text-lg">Atividade recente</h2></header>
              {s && <Activity values={s.clips_last_24h} />}
              {stats.isLoading && <div className="panel-body"><Skeleton label="Carregando atividade" lines={2} /></div>}
              {(infra.data?.failures ?? []).slice(0, 3).map((failure) => (
                <Link key={failure.id} to="/infra#falhas" className="flex justify-between gap-2 border-t border-suite-line px-4 py-2.5 text-xs hover:bg-suite-raised">
                  <span><span className="text-tally">Falha</span> / {jobLabel[failure.type] ?? failure.type} #{failure.id}</span>
                  <span className="shrink-0 text-ink-muted">{ago(failure.finished_at)}</span>
                </Link>
              ))}
              {calendar.error && <div className="panel-body"><ErrorNote error={calendar.error} /></div>}
            </section>
          </div>
        </div>
      </div>
    </div>
  );
}
