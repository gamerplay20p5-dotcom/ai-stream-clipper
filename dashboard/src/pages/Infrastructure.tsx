import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { api, type Host, type Infra, type ModelInfo } from "../api";
import { Disclosure, Empty, ErrorNote, Meter, Notice, PageHeader, Skeleton, Status } from "../components/ui";
import { ago, bytes, jobLabel } from "../lib/format";
import { t, tx } from "../i18n";

type ModelWorker = ModelInfo["workers"][number];

const kindLabel: Record<string, string> = { vlm: t("visão"), llm: t("texto"), audio: t("áudio") };

function modelState(w: ModelWorker): string {
  if (w.loaded) return t("carregado em {v}{v2}", { v: w.stats?.device ?? "?", v2: w.stats?.fell_back_to_cpu ? t(" (fallback CPU)") : "" });
  const st = w.stats?.state;
  if (st === "failed") return t("falhou ao carregar");
  if (st === "missing") return "arquivos ausentes";
  return t("disponível");
}

function modelStateTone(w: ModelWorker): string {
  if (w.stats?.state === "failed" || w.stats?.state === "missing") return "text-tally";
  return w.loaded ? "text-ok" : "text-ink-muted";
}

function HostMetric({ label, value, percent, warning = false }: {
  label: string; value: string; percent: number | null; warning?: boolean;
}) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-2 gap-y-1">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="text-right">{value}</dd>
      <dd className="col-span-2"><Meter value={percent} max={100} tone={warning ? "tally" : "ink"} label={label} /></dd>
    </div>
  );
}

function HostColumn({ h }: { h: Host }) {
  const m = h.metrics;
  const online = h.workers.filter((w) => w.status === "online");
  const gpuWorker = h.workers.find((w) => w.capabilities?.gpu);
  return (
    <section className="panel">
      <div className="panel-heading">
        <h2 className="display text-lg">{h.label}</h2>
        <Status tone={online.length === 0 ? "warning" : online.length === h.workers.length ? "success" : "warning"}>
          {online.length ? `${online.length}/${h.workers.length} online` : t("Sem workers online")}
        </Status>
      </div>
      <div className="panel-body">
      <p className="text-xs text-ink-muted">{h.hostname} {h.ip && `(${h.ip})`}</p>
      <p className="mt-2 text-sm">{tx(h.role)}</p>
      {m ? (
        <dl className="mt-4 space-y-3 text-xs">
          <HostMetric label={t("CPU")} value={`${Math.round(m.cpu_pct ?? 0)}%`} percent={m.cpu_pct ?? null} />
          <HostMetric label={t("Memória")} value={`${bytes(m.mem_used ?? 0)} de ${bytes(m.mem_total ?? 0)}`} percent={m.mem_pct ?? null} />
          <HostMetric label={t("Disco de trabalho")} value={`${Math.round(m.disk_pct ?? 0)}%`} percent={m.disk_pct ?? null} warning={(m.disk_pct ?? 0) > 85} />
          {h.gpu && (
            <HostMetric label={`GPU ${h.gpu}`} value={m.gpu_busy_pct !== undefined ? `${m.gpu_freq_mhz} MHz` : gpuWorker ? "Detectada" : "Não validada"}
              percent={m.gpu_busy_pct ?? null} />
          )}
        </dl>
      ) : (
        <p className="mt-4 text-sm text-ink-muted">{t("Nenhuma métrica recebida. Os workers desta máquina ainda não foram implantados ou estão desligados.")}</p>
      )}
      {h.workers.length > 0 && (
        <div className="mt-4"><Disclosure title={h.workers.length + " workers"}>
          <ul className="space-y-3 text-xs">
          {h.workers.map((w) => (
            <li key={w.id} className="flex justify-between gap-2">
              <span className="min-w-0 break-words">
                <span className={w.status === "online" ? "text-ink" : "text-ink-faint"}>{w.id}</span>
                <span className="block text-ink-muted">{w.roles.join(", ")}{w.capabilities?.gpu ? ", GPU" : ""}</span>
              </span>
              <span className={`shrink-0 ${w.status === "online" ? "text-ok" : "text-ink-faint"}`}>{w.status === "online" ? `${w.metrics?.running_jobs ?? 0} tarefa(s)` : `visto ${ago(w.last_heartbeat_at)}`}</span>
            </li>
          ))}
        </ul>
        </Disclosure></div>
      )}
      </div>
    </section>
  );
}

export default function Infrastructure() {
  const qc = useQueryClient();
  const { hash } = useLocation();
  const q = useQuery({ queryKey: ["infra"], queryFn: () => api.get<Infra>("/api/infrastructure"), refetchInterval: 5_000 });
  const models = useQuery({ queryKey: ["ai-models"], queryFn: () => api.get<ModelInfo[]>("/api/ai/models"), refetchInterval: 10_000 });
  const retry = useMutation({ mutationFn: (id: number) => api.post(`/api/jobs/${id}/retry`), onSuccess: () => qc.invalidateQueries({ queryKey: ["infra"] }) });
  const d = q.data;
  const ready = Boolean(d);
  useEffect(() => {
    if (ready && hash === "#falhas") document.getElementById("falhas")?.scrollIntoView();
  }, [ready, hash]);
  const types = ["capture", "audio_analyze", "score", "vision_analyze", "render"];

  return (
    <div>
      <PageHeader title={t("Infraestrutura")} description={t("Acompanhe a capacidade dos servidores, o estado dos modelos e as falhas do pipeline.")} />
      <div className="section-flow">
      <ErrorNote error={q.error ?? retry.error} />
      {retry.isSuccess && <Notice>{t("Tarefa devolvida à fila.")}</Notice>}
      {q.isLoading && <Skeleton label={t("Consultando servidores")} />}
      {d && d.hosts.length === 0 && <Empty title={t("Sem servidores reportados")}>{t("O uso de recursos aparece após o primeiro sinal de um worker.")}</Empty>}
      {d && (
        <>
          <div className="host-rack">
            {d.hosts.map((h) => <HostColumn key={h.label} h={h} />)}
          </div>

          <div className="grid gap-6 xl:grid-cols-[minmax(0,2fr)_minmax(270px,1fr)]">
            <section className="min-w-0">
              <h2 className="display mb-3 text-lg">{t("Filas de processamento")}</h2>
              <div className="table-scroll" tabIndex={0} role="region" aria-label={t("Filas por etapa")}>
              <table className="w-full min-w-[34rem] text-sm">
                <thead className="text-left text-xs text-ink-muted">
                  <tr><th className="py-2 font-normal">{t("Etapa")}</th><th className="font-normal">{t("Na fila")}</th><th className="font-normal">{t("Rodando")}</th>
                    <th className="font-normal">{t("Concluídas")}</th><th className="font-normal">{t("Falhas")}</th><th className="font-normal">{t("Tempo médio 24 h")}</th></tr>
                </thead>
                <tbody className="divide-y divide-suite-line">
                  {[...new Set([...types, ...Object.keys(d.queues)])].map((kind) => {
                    const s = d.queues[kind] ?? {};
                    const p = d.processing[kind];
                    return (
                      <tr key={kind}>
                        <td className="py-2.5">{jobLabel[kind] ?? kind}</td>
                        <td className={s.queued ? "text-take" : "text-ink-muted"}>{s.queued ?? 0}</td>
                        <td>{s.running ?? 0}</td>
                        <td className="text-ink-muted">{s.done ?? 0}</td>
                        <td className={s.dead ? "text-tally" : "text-ink-muted"}>{s.dead ?? 0}</td>
                        <td className="text-ink-muted">{kind === "capture" ? t("contínua") : p ? `${p.avg_s.toFixed(1)} s` : t("sem dados")}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              </div>
            </section>
            <section className="panel panel-body self-start">
              <h2 className="display mb-3 text-lg">{t("Armazenamento dos cortes")}</h2>
              <div className="mb-1 flex justify-between text-sm"><span>{bytes(d.storage.used_bytes)} {t("usados")}</span><span className="text-ink-muted">{t("limite")} {bytes(d.storage.limit_bytes)}</span></div>
              <Meter value={d.storage.used_bytes} max={d.storage.limit_bytes || 1} label={t("Uso da cota dos cortes")} tone={d.storage.used_bytes > 0.85 * d.storage.limit_bytes ? "tally" : "take"} />
              <p className="mt-2 text-xs text-ink-muted">{bytes(d.storage.disk_free_bytes)} {t("livres no disco do servidor 210.")}</p>
              <Link to="/armazenamento" className="mt-3 inline-block text-sm text-take hover:underline">{t("Gerenciar armazenamento")}</Link>
            </section>
          </div>

          <section>
            <h2 className="display mb-3 text-lg">{t("Modelos de IA")}</h2>
            <ErrorNote error={models.error} />
            {models.isLoading && <Skeleton label={t("Carregando modelos")} />}
            {models.data && <div className="table-scroll" tabIndex={0} role="region" aria-label={t("Modelos e workers")}>
            <table className="w-full min-w-[39rem] text-sm">
              <thead className="text-left text-xs text-ink-muted">
                <tr><th className="py-2 font-normal">{t("Modelo")}</th><th className="font-normal">{t("Tamanho")}</th><th className="font-normal">{t("Onde")}</th>
                  <th className="font-normal">{t("Estado")}</th><th className="font-normal">{t("Latência média")}</th><th className="font-normal">{t("Chamadas / falhas")}</th></tr>
              </thead>
              <tbody className="divide-y divide-suite-line">
                {(models.data ?? []).map((m) => (
                  <tr key={m.key} className="align-top">
                    <td className="py-2.5">{m.key}<div className="text-xs text-ink-faint">{kindLabel[m.kind]}</div></td>
                    <td className="text-ink-muted">{m.size_gb.toFixed(2)} {t("GB")}</td>
                    <td>{m.workers.length ? m.workers.map((w) => (
                      <div key={w.worker} className={w.online ? "" : "text-ink-faint"}>{w.host_label || w.worker} {w.gpu ? `(${w.gpu_name || "GPU"})` : "(CPU)"}</div>
                    )) : <span className="text-ink-faint">{t("não instalado")}</span>}</td>
                    <td>{m.workers.map((w) => (
                      <div key={w.worker} className={modelStateTone(w)} title={w.stats?.last_error || undefined}>{modelState(w)}</div>
                    ))}</td>
                    <td className="text-ink-muted">{m.workers.map((w) => (
                      <div key={w.worker}>{w.stats?.avg_latency_s ? `${w.stats.avg_latency_s.toFixed(1)} s` : t("sem dados")}</div>
                    ))}</td>
                    <td className="text-ink-muted">{m.workers.map((w) => (
                      <div key={w.worker}>{w.stats ? `${w.stats.calls} / ${w.stats.failures}` : "0 / 0"}</div>
                    ))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>}
            <p className="mt-2 text-xs text-ink-faint">{t("Cada GPU Arc A750 tem cerca de 8 GB e carrega um modelo pesado por vez. Sem modelo disponível, os cortes seguem com áudio e visão heurística.")}</p>
          </section>

          <section id="falhas" className="scroll-mt-20">
            <h2 className="display mb-3 text-lg">{t("Falhas definitivas")}</h2>
            {d.failures.length === 0 ? (
              <p className="text-sm text-ink-muted">{t("Nenhuma tarefa falhou depois de todas as tentativas.")}</p>
            ) : (
              <ul className="panel divide-y divide-suite-line">
                {d.failures.map((j) => (
                  <li key={j.id} className="flex flex-wrap items-start justify-between gap-3 px-4 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="text-sm">{jobLabel[j.type] ?? j.type} {t("#{id}, {attempts} tentativa(s),", { id: j.id, attempts: j.attempts })} {ago(j.finished_at)}</div>
                      <details className="mt-2 text-xs text-ink-muted"><summary>{t("Mensagem do worker")}</summary><pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap break-all rounded-md bg-suite-media p-3">{j.last_error}</pre></details>
                    </div>
                    <button className="btn-quiet" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>{t("Tentar de novo")}</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
      </div>
    </div>
  );
}
