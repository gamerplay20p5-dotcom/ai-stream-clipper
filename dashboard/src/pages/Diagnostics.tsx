import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { api, type DiagStreamer, type Diagnostics as DiagT } from "../api";
import { Disclosure, Empty, ErrorNote, Notice, PageHeader, Skeleton, Status } from "../components/ui";
import { ago, jobLabel, timecode } from "../lib/format";
import { t, tx } from "../i18n";

const roleLabel: Record<string, string> = {
  capture: t("captura"), monitor: t("monitor"), audio: t("áudio"), scoring: t("pontuação"), vision: t("visão"), render: t("renderização"),
};
const reasonLabel: Record<string, string> = {
  below_min_score: t("abaixo da nota mínima"),
  duplicate: t("duplicado"),
  text_only: t("só fala, sem reação"),
  rate_limit: t("limite por hora"),
  too_short: t("curto demais"),
};

function StateIcon({ state }: { state: string }) {
  if (state === "error") return <AlertTriangle className="h-5 w-5 text-tally" aria-label={t("falha")} />;
  if (state === "working" || state === "waiting") return <Loader2 className="h-5 w-5 animate-spin text-take" aria-label={t("processando")} />;
  return <CheckCircle2 className="h-5 w-5 text-ok" aria-label={t("ok")} />;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-suite-line/60 py-1.5 text-sm">
      <dt className="text-ink-muted">{label}</dt>
      <dd className="text-right">{children}</dd>
    </div>
  );
}

function StreamerDiag({ d }: { d: DiagStreamer }) {
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: () => api.post<{ retried: number[] }>(`/api/diagnostics/streamers/${d.streamer.id}/retry-failed`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["diagnostics"] }),
  });
  const sc = d.scoring;
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2 className="display text-lg">{d.streamer.name}</h2>
          <p className="text-xs text-ink-muted">{d.streamer.platform}{t(", canal {status}, verificado", { status: d.streamer.status })} {ago(d.streamer.last_checked_at)}</p>
        </div>
        <div className="flex items-center gap-2">
          <StateIcon state={d.state} />
          <span className="font-semibold">{tx(d.stage)}</span>
        </div>
      </div>
      <div className="panel-body">
      <p className={`mt-3 mb-3 rounded-md px-3 py-2 text-sm ${d.state === "error" ? "bg-tally-dim text-tally" : "bg-suite text-ink"}`}>
        {d.explanation}
      </p>

      {d.session && (
        <Disclosure title={t("Detalhes da sessão")}>
        <div className="grid gap-6 lg:grid-cols-2">
          <dl>
            <Fact label={t("Sessão")}>{t("#{id}, {status}, desde", { id: d.session.id, status: d.session.status })} {ago(d.session.started_at)}</Fact>
            <Fact label={t("Último segmento")}>{d.last_segment ? t("#{idx}, há {round} s", { idx: d.last_segment.idx, round: Math.round(d.last_segment.age_s) }) : t("nenhum")}</Fact>
            <Fact label={t("Buffer disponível")}>{d.buffer ? `${Math.round(d.buffer.seconds)} s (${timecode(d.buffer.start)}–${timecode(d.buffer.end)})` : "vazio"}</Fact>
            <Fact label={t("Capturado / analisado / pontuado")}>
              {timecode(d.session.covered_until)} / {timecode(d.session.analyzed_until)} / {timecode(d.session.scored_until)}
            </Fact>
            <Fact label={t("Última transcrição")}>{d.last_transcript_t !== null ? t("palavra em {timecode}", { timecode: timecode(d.last_transcript_t) }) : t("nenhuma")}</Fact>
            <Fact label={t("Análise de áudio (YAMNet)")}>{d.audio_features.segments} {t("segmento(s)")}</Fact>
            <Fact label={t("Última análise visual")}>
              {d.last_visual ? `${d.last_visual.fallback ? "fallback" : d.last_visual.model} ${ago(d.last_visual.at)}` : "nenhuma"}
            </Fact>
            <Fact label={t("Última pontuação")}>{sc ? ago(sc.last_scored_at) : "—"}</Fact>
            <Fact label={t("Último corte renderizado")}>
              {d.last_render ? <Link className="text-take hover:underline" to={`/cortes/${d.last_render.clip_id}?status=`}>#{d.last_render.clip_id}{d.last_render.is_test ? t(" (teste)") : ""} {ago(d.last_render.at)}</Link> : t("nenhum")}
            </Fact>
          </dl>
          <div className="space-y-4">
            {sc && (
              <div>
                <h3 className="mb-1 text-xs text-ink-muted">{t("Pontuação (nota mínima {min_score})", { min_score: sc.min_score })}</h3>
                <p className="text-sm">{sc.candidates} {t("candidato(s), {rejected_by_director} descartado(s) pelo diretor", { rejected_by_director: sc.rejected_by_director })}
                  {sc.best_rejected_score !== null && sc.best_rejected_score !== undefined && `, melhor nota rejeitada ${sc.best_rejected_score.toFixed(1)}`}.</p>
                {Object.keys(sc.rejected_by_reason).length > 0 && (
                  <ul className="mt-1 text-xs text-ink-muted">
                    {Object.entries(sc.rejected_by_reason).map(([r, n]) => <li key={r}>{n} {t("momento(s)")} {reasonLabel[r] ?? r}</li>)}
                  </ul>
                )}
              </div>
            )}
            <div>
              <h3 className="mb-1 text-xs text-ink-muted">{t("Tarefas desta sessão")}</h3>
              <div className="table-scroll"><table className="w-full text-xs">
                <tbody>
                  {Object.entries(d.jobs).map(([t, st]) => (
                    <tr key={t} className="border-b border-suite-line/60">
                      <td className="py-1">{jobLabel[t] ?? t}</td>
                      <td className="text-ink-muted">{Object.entries(st).map(([k, v]) => `${v} ${k}`).join(", ")}</td>
                      <td className="text-right text-ink-muted">{d.latency_s[t] !== undefined ? `${d.latency_s[t].toFixed(1)} s` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table></div>
            </div>
            {d.failures.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs text-tally">{t("Falhas")}</h3>
                <ul className="space-y-1 text-xs">
                  {d.failures.map((f) => <li key={f.id}><b>{jobLabel[f.type] ?? f.type} #{f.id}</b>: {f.error.slice(-160)}</li>)}
                </ul>
                <ErrorNote error={retry.error} />
                {retry.isSuccess && <Notice>{retry.data.retried.length} {t("tarefa(s) devolvida(s) à fila.")}</Notice>}
                <button className="btn-quiet mt-2" disabled={retry.isPending} onClick={() => retry.mutate()}>
                  <RotateCcw className="h-4 w-4" /> {t("Reprocessar falhas")}
                </button>
              </div>
            )}
          </div>
        </div>
        </Disclosure>
      )}
      </div>
    </section>
  );
}

export default function Diagnostics() {
  const q = useQuery({ queryKey: ["diagnostics"], queryFn: () => api.get<DiagT>("/api/diagnostics"), refetchInterval: 5_000 });
  const roles = q.data?.workers_online_by_role ?? {};
  return (
    <div>
      <PageHeader title={t("Diagnóstico do pipeline")} description={t("Investigue a etapa atual e os gargalos de cada transmissão monitorada.")} />
      <div className="section-flow">
        <ErrorNote error={q.error} />
        {q.isLoading && <Skeleton label={t("Carregando diagnóstico")} />}
        {(q.data?.streamers ?? []).map((d) => <StreamerDiag key={d.streamer.id} d={d} />)}
        {q.data && (
          <section className="panel">
            <div className="panel-heading"><h2 className="display text-lg">{t("Workers por função")}</h2></div>
            <dl className="panel-body grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
              {["capture", "audio", "scoring", "vision", "render"].map((role) => (
                <div key={role}>
                  <dt className="mb-1 text-sm font-medium capitalize">{roleLabel[role]}</dt>
                  <dd>
                    <Status tone={roles[role]?.length ? "success" : "warning"}>{roles[role]?.length ?? 0} {t("online")}</Status>
                    {!!roles[role]?.length && <p className="mt-1 break-words text-xs text-ink-muted">{roles[role].join(", ")}</p>}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}
        {q.data?.streamers.length === 0 && <Empty title={t("Nenhum canal monitorado")}><Link to="/streamers" className="text-take hover:underline">{t("Cadastro de transmissões")}</Link></Empty>}
      </div>
    </div>
  );
}
