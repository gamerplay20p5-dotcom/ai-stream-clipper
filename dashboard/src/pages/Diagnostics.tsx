import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, RotateCcw } from "lucide-react";
import { Link } from "react-router-dom";
import { api, type DiagStreamer, type Diagnostics as DiagT } from "../api";
import { ErrorNote, PageHeader } from "../components/ui";
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
    <section className="rounded-lg bg-suite-panel p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="display text-lg">{d.streamer.name}</h2>
          <p className="text-xs text-ink-muted">{d.streamer.platform}, canal {d.streamer.status}, verificado {ago(d.streamer.last_checked_at)}</p>
        </div>
        <div className="flex items-center gap-2">
          <StateIcon state={d.state} />
          <span className="font-semibold">{d.stage}</span>
        </div>
      </div>
      <p className={`mt-3 rounded-md px-3 py-2 text-sm ${d.state === "error" ? "bg-tally-dim text-[#ffb4b6]" : "bg-suite text-ink"}`}>
        {d.explanation}
      </p>

      {d.session && (
        <div className="mt-4 grid gap-6 lg:grid-cols-2">
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
              <table className="w-full text-xs">
                <tbody>
                  {Object.entries(d.jobs).map(([t, st]) => (
                    <tr key={t} className="border-b border-suite-line/60">
                      <td className="py-1">{jobLabel[t] ?? t}</td>
                      <td className="text-ink-muted">{Object.entries(st).map(([k, v]) => `${v} ${k}`).join(", ")}</td>
                      <td className="text-right text-ink-muted">{d.latency_s[t] !== undefined ? `${d.latency_s[t].toFixed(1)} s` : ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {d.failures.length > 0 && (
              <div>
                <h3 className="mb-1 text-xs text-tally">Falhas</h3>
                <ul className="space-y-1 text-xs">
                  {d.failures.map((f) => <li key={f.id}><b>{jobLabel[f.type] ?? f.type} #{f.id}</b>: {f.error.slice(-160)}</li>)}
                </ul>
                <ErrorNote error={retry.error} />
                <button className="btn-quiet mt-2" disabled={retry.isPending} onClick={() => retry.mutate()}>
                  <RotateCcw className="h-4 w-4" /> Reprocessar falhas
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

export default function Diagnostics() {
  const q = useQuery({ queryKey: ["diagnostics"], queryFn: () => api.get<DiagT>("/api/diagnostics"), refetchInterval: 5_000 });
  const roles = q.data?.workers_online_by_role ?? {};
  return (
    <div className="max-w-6xl space-y-6">
      <PageHeader title="Diagnóstico do pipeline" />
      <ErrorNote error={q.error} />
      <div className="flex flex-wrap gap-2 text-xs">
        {["capture", "audio", "scoring", "vision", "render"].map((r) => (
          <span key={r} className={`rounded-full border px-2.5 py-1 ${roles[r]?.length ? "border-ok/50 text-ok" : "border-tally/50 text-tally"}`}
            title={(roles[r] ?? []).join(", ")}>
            {roleLabel[r]}: {roles[r]?.length ?? 0} online
          </span>
        ))}
      </div>
      {(q.data?.streamers ?? []).map((d) => <StreamerDiag key={d.streamer.id} d={d} />)}
      {q.data && q.data.streamers.length === 0 && <p className="text-sm text-ink-muted">Nenhum streamer cadastrado.</p>}
    </div>
  );
}
