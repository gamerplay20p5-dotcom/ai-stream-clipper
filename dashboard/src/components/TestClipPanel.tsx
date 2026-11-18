import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Clip, type Diagnostics } from "../api";
import { timecode } from "../lib/format";
import { ErrorNote } from "./ui";

const modes = [
  { key: "last30", label: "Últimos 30 s" },
  { key: "last60", label: "Últimos 60 s" },
  { key: "last90", label: "Últimos 90 s" },
  { key: "custom", label: "Intervalo personalizado" },
];

/** "Gerar corte de teste": real pipeline on a buffer range; scoring never blocks it. */
export default function TestClipPanel({ streamerId, onClose }: { streamerId: number; onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const diag = useQuery({ queryKey: ["diagnostics"], queryFn: () => api.get<Diagnostics>("/api/diagnostics") });
  const buf = diag.data?.streamers.find((s) => s.streamer.id === streamerId)?.buffer ?? null;
  const [mode, setMode] = useState("last30");
  const [start, setStart] = useState<number | "">("");
  const [end, setEnd] = useState<number | "">("");
  const create = useMutation({
    mutationFn: () =>
      api.post<Clip>(`/api/streamers/${streamerId}/test-clip`, mode === "custom" ? { mode, start, end } : { mode }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ["clips"] });
      navigate(`/cortes/${c.id}?status=`);
    },
  });

  return (
    <div className="mt-4 rounded-lg border border-suite-line bg-suite-panel p-4">
      <div className="flex items-center gap-2">
        <FlaskConical className="h-4 w-4 text-take" aria-hidden />
        <h3 className="text-sm font-semibold">Gerar corte de teste</h3>
      </div>
      <p className="mt-1 text-xs text-ink-muted">
        Usa o pipeline real (transcrição, análise visual se disponível, legendas, template e renderização 1080×1920), mas a
        pontuação não bloqueia. O corte recebe a marca TESTE MANUAL e fica fora das métricas.
      </p>
      {buf ? (
        <p className="mt-2 text-xs">
          Buffer disponível: {timecode(buf.start)} a {timecode(buf.end)} ({Math.round(buf.seconds)} s)
        </p>
      ) : (
        <p className="mt-2 text-xs text-tally">Sem vídeo no buffer deste streamer.</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2" role="radiogroup">
        {modes.map((m) => (
          <button key={m.key} type="button" role="radio" aria-checked={mode === m.key} onClick={() => setMode(m.key)}
            className={`rounded-full border px-3 py-1 text-xs ${mode === m.key ? "border-take text-take" : "border-suite-line text-ink-muted hover:text-ink"}`}>
            {m.label}
          </button>
        ))}
      </div>
      {mode === "custom" && (
        <div className="mt-3 grid max-w-sm grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="tc-start">Início (s da sessão)</label>
            <input id="tc-start" type="number" step="0.1" className="field" value={start}
              min={buf?.start} max={buf?.end} onChange={(e) => setStart(e.target.value === "" ? "" : Number(e.target.value))} />
          </div>
          <div>
            <label className="label" htmlFor="tc-end">Fim (s da sessão)</label>
            <input id="tc-end" type="number" step="0.1" className="field" value={end}
              min={buf?.start} max={buf?.end} onChange={(e) => setEnd(e.target.value === "" ? "" : Number(e.target.value))} />
          </div>
        </div>
      )}
      <ErrorNote error={create.error} />
      <div className="mt-3 flex gap-2">
        <button className="btn-take" disabled={!buf || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Criando…" : "Gerar corte de teste"}
        </button>
        <button className="btn-quiet" onClick={onClose}>Cancelar</button>
      </div>
    </div>
  );
}
