import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, type Clip, type Infra, type Stats, type Streamer } from "../api";
import { ClipFrame, Empty, Tally } from "../components/ui";
import { platformName, timecode } from "../lib/format";

function Figure({ value, label, tone }: { value: string | number; label: string; tone?: "take" | "tally" }) {
  const color = tone === "take" ? "text-take" : tone === "tally" ? "text-tally" : "text-ink";
  return (
    <div className="min-w-[8.5rem] py-3 pr-6">
      <div className={`display text-xl ${color}`}>{value}</div>
      <div className="text-xs text-ink-muted">{label}</div>
    </div>
  );
}

function Spark({ data }: { data: number[] }) {
  const max = Math.max(1, ...data);
  return (
    <div className="flex h-10 items-end gap-[3px]" aria-label="Cortes por hora nas últimas 24 horas">
      {data.map((v, i) => (
        <div key={i} title={`${v} corte(s)`} className={`w-1.5 rounded-sm ${v ? "bg-take" : "bg-suite-line"}`}
          style={{ height: `${Math.max(8, (v / max) * 100)}%` }} />
      ))}
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
  const s = stats.data;
  const live = (streamers.data ?? []).filter((x) => x.live_session);
  const busy = (stats.data?.clips_by_status?.rendering ?? 0) + (stats.data?.clips_by_status?.analyzing ?? 0);

  return (
    <div className="space-y-10">
      <section>
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="display text-2xl">
            {review.data?.total ? `${review.data.total} ${review.data.total === 1 ? "corte para revisar" : "cortes para revisar"}` : "Nada para revisar agora"}
          </h1>
          {!!review.data?.total && <Link to="/cortes?status=ready" className="text-sm text-take hover:underline">Abrir a central de cortes</Link>}
        </div>
        {review.data && review.data.items.length > 0 ? (
          <div className="-mx-1 flex snap-x gap-3 overflow-x-auto px-1 pb-2">
            {review.data.items.map((c) => (
              <div key={c.id} className="w-36 shrink-0 snap-start sm:w-40">
                <ClipFrame clip={c} onOpen={() => navigate(`/cortes/${c.id}`)} />
              </div>
            ))}
          </div>
        ) : (
          <Empty title="A fila de revisão está vazia">
            {busy > 0 ? `${busy} corte(s) estão sendo processados.` : <>Cadastre um canal em <Link className="text-take hover:underline" to="/streamers">Streamers</Link> e os cortes aparecem aqui assim que a IA encontrar um momento.</>}
          </Empty>
        )}
      </section>

      {s && (
        <section className="flex flex-wrap items-end gap-x-2 border-y border-suite-line">
          <Figure value={s.live_sessions} label="ao vivo agora" tone={s.live_sessions ? "tally" : undefined} />
          <Figure value={`${s.streamers_enabled}/${s.streamers_total}`} label="streamers monitorados" />
          {s.hours_analyzed < 1 ? (
            <Figure value={Math.round(s.hours_analyzed * 60)} label="minutos analisados" />
          ) : (
            <Figure value={s.hours_analyzed.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} label="horas analisadas" />
          )}
          <Figure value={s.clips_total} label="cortes encontrados" />
          <Figure value={s.clips_approved} label="cortes aprovados" tone="take" />
          <Figure value={s.pending_jobs} label="tarefas pendentes" />
          <div className="ml-auto py-3">
            <Spark data={s.clips_last_24h} />
            <div className="mt-1 text-xs text-ink-muted">cortes por hora, últimas 24 h</div>
          </div>
        </section>
      )}

      <div className="grid gap-10 lg:grid-cols-2">
        <section>
          <h2 className="display mb-3 text-lg">Transmissões</h2>
          {live.length === 0 ? (
            <p className="text-sm text-ink-muted">Nenhum canal ao vivo. O monitor verifica os canais ativos a cada minuto.</p>
          ) : (
            <ul className="divide-y divide-suite-line">
              {live.map((st) => {
                const ls = st.live_session!;
                const lag = Math.max(0, ls.covered_until - ls.analyzed_until);
                return (
                  <li key={st.id} className="flex items-center justify-between gap-4 py-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-3">
                        <Tally live />
                        <span className="truncate font-medium">{st.display_name}</span>
                        <span className="text-xs text-ink-muted">{platformName[st.platform]}</span>
                      </div>
                      <p className="mt-0.5 truncate text-sm text-ink-muted">{ls.title || st.stream_title || "Sem título"}</p>
                    </div>
                    <div className="shrink-0 text-right text-xs text-ink-muted">
                      <div>capturado {timecode(ls.covered_until)}</div>
                      <div className={lag > 60 ? "text-take" : ""}>análise {lag > 1 ? `${Math.round(lag)} s atrás` : "em dia"}</div>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
        <section>
          <h2 className="display mb-3 text-lg">Máquinas</h2>
          <ul className="divide-y divide-suite-line">
            {(infra.data?.hosts ?? []).map((h) => {
              const online = h.workers.filter((w) => w.status === "online").length;
              return (
                <li key={h.label} className="flex items-center justify-between gap-4 py-3">
                  <div className="min-w-0">
                    <div className="font-medium">Servidor {h.label}</div>
                    <p className="truncate text-sm text-ink-muted">{h.role || h.hostname}</p>
                  </div>
                  <div className="shrink-0 text-right text-xs">
                    <div className={online ? "text-ok" : "text-ink-faint"}>{online ? `${online} worker(s) online` : "sem workers"}</div>
                    {h.metrics && <div className="text-ink-muted">CPU {Math.round(h.metrics.cpu_pct ?? 0)}%, RAM {Math.round(h.metrics.mem_pct ?? 0)}%</div>}
                  </div>
                </li>
              );
            })}
          </ul>
          {!!s?.dead_jobs && (
            <p className="mt-3 text-sm text-tally"><Link to="/infra" className="hover:underline">{s.dead_jobs} tarefa(s) falharam definitivamente. Ver detalhes</Link></p>
          )}
        </section>
      </div>
    </div>
  );
}
