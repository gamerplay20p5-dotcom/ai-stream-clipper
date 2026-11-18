import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Host, type Infra } from "../api";
import { ErrorNote, Meter, PageHeader } from "../components/ui";
import { ago, bytes, jobLabel } from "../lib/format";

function HostColumn({ h }: { h: Host }) {
  const m = h.metrics;
  const online = h.workers.filter((w) => w.status === "online");
  const gpuWorker = h.workers.find((w) => w.capabilities?.gpu);
  return (
    <section className="rounded-lg bg-suite-panel p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="display text-lg">{h.label}</h2>
        <span className={`text-xs ${online.length ? "text-ok" : "text-ink-faint"}`}>{online.length ? `${online.length} online` : "sem workers"}</span>
      </div>
      <p className="text-xs text-ink-muted">{h.hostname} {h.ip && `(${h.ip})`}</p>
      <p className="mt-2 text-sm">{h.role}</p>
      {m ? (
        <dl className="mt-4 space-y-2.5 text-xs">
          <div><div className="mb-1 flex justify-between"><dt className="text-ink-muted">CPU</dt><dd>{Math.round(m.cpu_pct ?? 0)}%</dd></div><Meter value={m.cpu_pct ?? 0} max={100} tone="ink" /></div>
          <div><div className="mb-1 flex justify-between"><dt className="text-ink-muted">Memória</dt><dd>{bytes(m.mem_used ?? 0)} de {bytes(m.mem_total ?? 0)}</dd></div><Meter value={m.mem_pct ?? 0} max={100} tone="ink" /></div>
          <div><div className="mb-1 flex justify-between"><dt className="text-ink-muted">Disco de trabalho</dt><dd>{Math.round(m.disk_pct ?? 0)}%</dd></div><Meter value={m.disk_pct ?? 0} max={100} tone={(m.disk_pct ?? 0) > 85 ? "tally" : "ink"} /></div>
          {h.gpu && (
            <div>
              <div className="mb-1 flex justify-between"><dt className="text-ink-muted">GPU {h.gpu}</dt>
                <dd>{m.gpu_busy_pct !== undefined ? `${m.gpu_freq_mhz} MHz` : gpuWorker ? "detectada" : "não validada"}</dd></div>
              <Meter value={m.gpu_busy_pct ?? null} max={100} tone="ink" />
            </div>
          )}
        </dl>
      ) : (
        <p className="mt-4 text-sm text-ink-muted">Nenhuma métrica recebida. Os workers desta máquina ainda não foram implantados ou estão desligados.</p>
      )}
      {h.workers.length > 0 && (
        <ul className="mt-4 space-y-2 border-t border-suite-line pt-3 text-xs">
          {h.workers.map((w) => (
            <li key={w.id} className="flex justify-between gap-2">
              <span className="min-w-0">
                <span className={w.status === "online" ? "text-ink" : "text-ink-faint"}>{w.id}</span>
                <span className="block text-ink-muted">{w.roles.join(", ")}{w.capabilities?.gpu ? ", GPU" : ""}</span>
              </span>
              <span className={`shrink-0 ${w.status === "online" ? "text-ok" : "text-ink-faint"}`}>{w.status === "online" ? `${w.metrics?.running_jobs ?? 0} tarefa(s)` : `visto ${ago(w.last_heartbeat_at)}`}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function Infrastructure() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["infra"], queryFn: () => api.get<Infra>("/api/infrastructure"), refetchInterval: 5_000 });
  const retry = useMutation({ mutationFn: (id: number) => api.post(`/api/jobs/${id}/retry`), onSuccess: () => qc.invalidateQueries({ queryKey: ["infra"] }) });
  const d = q.data;
  const types = ["capture", "audio_analyze", "score", "vision_analyze", "render"];

  return (
    <div className="space-y-10">
      <PageHeader title="Infraestrutura" />
      <ErrorNote error={q.error} />
      {d && (
        <>
          <div className="grid gap-4 lg:grid-cols-3">
            {d.hosts.map((h) => <HostColumn key={h.label} h={h} />)}
          </div>

          <div className="grid gap-10 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <section>
              <h2 className="display mb-3 text-lg">Filas</h2>
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-ink-muted">
                  <tr><th className="py-2 font-normal">Etapa</th><th className="font-normal">Na fila</th><th className="font-normal">Rodando</th>
                    <th className="font-normal">Concluídas</th><th className="font-normal">Falhas</th><th className="font-normal">Tempo médio 24 h</th></tr>
                </thead>
                <tbody className="divide-y divide-suite-line">
                  {types.map((t) => {
                    const s = d.queues[t] ?? {};
                    const p = d.processing[t];
                    return (
                      <tr key={t}>
                        <td className="py-2.5">{jobLabel[t]}</td>
                        <td className={s.queued ? "text-take" : "text-ink-muted"}>{s.queued ?? 0}</td>
                        <td>{s.running ?? 0}</td>
                        <td className="text-ink-muted">{s.done ?? 0}</td>
                        <td className={s.dead ? "text-tally" : "text-ink-muted"}>{s.dead ?? 0}</td>
                        <td className="text-ink-muted">{t === "capture" ? "contínua" : p ? `${p.avg_s.toFixed(1)} s` : "sem dados"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
            <section>
              <h2 className="display mb-3 text-lg">Armazenamento dos cortes</h2>
              <div className="mb-1 flex justify-between text-sm"><span>{bytes(d.storage.used_bytes)} usados</span><span className="text-ink-muted">limite {bytes(d.storage.limit_bytes)}</span></div>
              <Meter value={d.storage.used_bytes} max={d.storage.limit_bytes || 1} tone={d.storage.used_bytes > 0.85 * d.storage.limit_bytes ? "tally" : "take"} />
              <p className="mt-2 text-xs text-ink-muted">{bytes(d.storage.disk_free_bytes)} livres no disco do servidor 210.</p>
            </section>
          </div>

          <section>
            <h2 className="display mb-3 text-lg">Falhas definitivas</h2>
            {d.failures.length === 0 ? (
              <p className="text-sm text-ink-muted">Nenhuma tarefa falhou depois de todas as tentativas.</p>
            ) : (
              <ul className="divide-y divide-suite-line border-y border-suite-line">
                {d.failures.map((j) => (
                  <li key={j.id} className="flex flex-wrap items-start justify-between gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm">{jobLabel[j.type] ?? j.type} #{j.id}, {j.attempts} tentativa(s), {ago(j.finished_at)}</div>
                      <pre className="mt-1 max-h-24 overflow-auto whitespace-pre-wrap text-xs text-ink-muted">{j.last_error}</pre>
                    </div>
                    <button className="btn-quiet" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>Tentar de novo</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
