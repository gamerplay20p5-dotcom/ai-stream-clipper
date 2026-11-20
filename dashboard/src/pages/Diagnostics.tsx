import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { api, type DiagStreamer, type Diagnostics as DiagT } from "../api";
import { Disclosure, Empty, ErrorNote, Notice, PageHeader, Skeleton, Status } from "../components/ui";
import { ago, jobLabel, timecode } from "../lib/format";

const roleLabel: Record<string, string> = {
  capture: "captura", monitor: "monitor", audio: "áudio", scoring: "pontuação", vision: "visão", render: "renderização",
};
const reasonLabel: Record<string, string> = {
  below_min_score: "abaixo da nota mínima",
  duplicate: "duplicado",
  text_only: "só fala, sem reação",
  rate_limit: "limite por hora",
  too_short: "curto demais",
};

function StateIcon({ state }: { state: string }) {
  if (state === "error") return <AlertTriangle className="h-5 w-5 text-tally" aria-label="falha" />;
  if (state === "working" || state === "waiting") return <Loader2 className="h-5 w-5 animate-spin text-take" aria-label="processando" />;
  return <CheckCircle2 className="h-5 w-5 text-ok" aria-label="ok" />;
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
          <p className="text-xs text-ink-muted">{d.streamer.platform}, canal {d.streamer.status}, verificado {ago(d.streamer.last_checked_at)}</p>
        </div>
        <div className="flex items-center gap-2">
          <StateIcon state={d.state} />
          <span className="font-semibold">{d.stage}</span>
        </div>
      </div>
      <div className="panel-body">
      <p className={`mt-3 mb-3 rounded-md px-3 py-2 text-sm ${d.state === "error" ? "bg-tally-dim text-tally" : "bg-suite text-ink"}`}>
        {d.explanation}
      </p>

      {d.session && (
        <Disclosure title="Detalhes da sessão">
        <div className="grid gap-6 lg:grid-cols-2">
          <dl>
            <Fact label="Sessão">#{d.session.id}, {d.session.status}, desde {ago(d.session.started_at)}</Fact>
            <Fact label="Último segmento">{d.last_segment ? `#${d.last_segment.idx}, há ${Math.round(d.last_segment.age_s)} s` : "nenhum"}</Fact>
            <Fact label="Buffer disponível">{d.buffer ? `${Math.round(d.buffer.seconds)} s (${timecode(d.buffer.start)}–${timecode(d.buffer.end)})` : "vazio"}</Fact>
            <Fact label="Capturado / analisado / pontuado">
              {timecode(d.session.covered_until)} / {timecode(d.session.analyzed_until)} / {timecode(d.session.scored_until)}
            </Fact>
            <Fact label="Última transcrição">{d.last_transcript_t !== null ? `palavra em ${timecode(d.last_transcript_t)}` : "nenhuma"}</Fact>
            <Fact label="Análise de áudio (YAMNet)">{d.audio_features.segments} segmento(s)</Fact>
            <Fact label="Última análise visual">
              {d.last_visual ? `${d.last_visual.fallback ? "fallback" : d.last_visual.model} ${ago(d.last_visual.at)}` : "nenhuma"}
            </Fact>
            <Fact label="Última pontuação">{sc ? ago(sc.last_scored_at) : "—"}</Fact>
            <Fact label="Último corte renderizado">
              {d.last_render ? <Link className="text-take hover:underline" to={`/cortes/${d.last_render.clip_id}?status=`}>#{d.last_render.clip_id}{d.last_render.is_test ? " (teste)" : ""} {ago(d.last_render.at)}</Link> : "nenhum"}
            </Fact>
          </dl>
          <div className="space-y-4">
            {sc && (
              <div>
                <h3 className="mb-1 text-xs text-ink-muted">Pontuação (nota mínima {sc.min_score})</h3>
                <p className="text-sm">{sc.candidates} candidato(s), {sc.rejected_by_director} descartado(s) pelo diretor
                  {sc.best_rejected_score !== null && sc.best_rejected_score !== undefined && `, melhor nota rejeitada ${sc.best_rejected_score.toFixed(1)}`}.</p>
                {Object.keys(sc.rejected_by_reason).length > 0 && (
                  <ul className="mt-1 text-xs text-ink-muted">
                    {Object.entries(sc.rejected_by_reason).map(([r, n]) => <li key={r}>{n} momento(s) {reasonLabel[r] ?? r}</li>)}
                  </ul>
                )}
              </div>
            )}
            <div>
              <h3 className="mb-1 text-xs text-ink-muted">Tarefas desta sessão</h3>
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
                <h3 className="mb-1 text-xs text-tally">Falhas</h3>
                <ul className="space-y-1 text-xs">
                  {d.failures.map((f) => <li key={f.id}><b>{jobLabel[f.type] ?? f.type} #{f.id}</b>: {f.error.slice(-160)}</li>)}
                </ul>
                <ErrorNote error={retry.error} />
                {retry.isSuccess && <Notice>{retry.data.retried.length} tarefa(s) devolvida(s) à fila.</Notice>}
                <button className="btn-quiet mt-2" disabled={retry.isPending} onClick={() => retry.mutate()}>
                  <RotateCcw className="h-4 w-4" /> Reprocessar falhas
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
      <PageHeader title="Diagnóstico do pipeline" description="Investigue a etapa atual e os gargalos de cada transmissão monitorada." />
      <div className="section-flow">
        <ErrorNote error={q.error} />
        {q.isLoading && <Skeleton label="Carregando diagnóstico" />}
        {(q.data?.streamers ?? []).map((d) => <StreamerDiag key={d.streamer.id} d={d} />)}
        {q.data && (
          <section className="panel">
            <div className="panel-heading"><h2 className="display text-lg">Workers por função</h2></div>
            <dl className="panel-body grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
              {["capture", "audio", "scoring", "vision", "render"].map((role) => (
                <div key={role}>
                  <dt className="mb-1 text-sm font-medium capitalize">{roleLabel[role]}</dt>
                  <dd>
                    <Status tone={roles[role]?.length ? "success" : "warning"}>{roles[role]?.length ?? 0} online</Status>
                    {!!roles[role]?.length && <p className="mt-1 break-words text-xs text-ink-muted">{roles[role].join(", ")}</p>}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
        )}
        {q.data?.streamers.length === 0 && <Empty title="Nenhum canal monitorado"><Link to="/streamers" className="text-take hover:underline">Cadastro de transmissões</Link></Empty>}
      </div>
    </div>
  );
}
