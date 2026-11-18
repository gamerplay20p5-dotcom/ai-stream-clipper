import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorNote } from "./ui";

type Sample = { id: number; start: number; end: number; text: string; confidence: number; audio: boolean };
type Group = { label: string; seconds: number; turns: number; samples: Sample[] };
type Voice = {
  session_id: number | null;
  profile: { confirmed: boolean; source: string; n: number; updated_at: string | null } | null;
  rejected: number;
  groups: Group[];
};

export function speakerName(label: string): string {
  if (label === "streamer") return "Streamer";
  if (label === "unknown") return "Não identificado";
  if (label === "overlap") return "Duas vozes juntas";
  if (label.startsWith("other:")) return `Outra voz ${label.split(":")[1]}`;
  return label || "Sem análise";
}

const mmss = (t: number) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

export default function VoicePanel({ streamerId }: { streamerId: number }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["voice", streamerId], queryFn: () => api.get<Voice>(`/api/speakers/streamers/${streamerId}`) });
  const done = () => qc.invalidateQueries({ queryKey: ["voice", streamerId] });
  const confirm = useMutation({ mutationFn: (label: string) => api.post(`/api/speakers/streamers/${streamerId}/confirm`, { session_id: q.data?.session_id, label }), onSuccess: done });
  const reject = useMutation({ mutationFn: (label: string) => api.post(`/api/speakers/streamers/${streamerId}/reject`, { session_id: q.data?.session_id, label }), onSuccess: done });
  const v = q.data;
  const choosable = (g: Group) => g.label === "streamer" || g.label.startsWith("other:");

  return (
    <div className="md:col-span-2">
      <h3 className="mb-1 text-sm font-semibold">Voz do streamer</h3>
      <p className="mb-3 text-xs text-ink-muted">
        As legendas destacam quem fala. O sistema supõe que a voz que mais fala é a do streamer (nunca a mais alta) e espera você confirmar.
        Quando não tem certeza, deixa a fala como “não identificado”.
      </p>
      <ErrorNote error={q.error || confirm.error || reject.error} />
      {v && !v.session_id && <p className="text-sm text-ink-muted">Ainda não há falas analisadas para este canal.</p>}
      {v?.session_id && (
        <>
          <p className="mb-3 text-sm">
            {v.profile === null ? "Voz do streamer ainda não definida (precisa de uns 5 minutos de fala)."
              : v.profile.confirmed ? <span className="text-take">Voz confirmada por você.</span>
              : <span className="text-tally">Hipótese automática — ouça e confirme.</span>}
            <span className="ml-2 text-xs text-ink-muted">Transmissão #{v.session_id}{v.rejected ? ` · ${v.rejected} voz(es) descartada(s)` : ""}</span>
          </p>
          <ul className="space-y-3">
            {v.groups.map((g) => (
              <li key={g.label} className="rounded-md border border-suite-line p-3">
                <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">{speakerName(g.label)} <span className="text-xs text-ink-muted">· {Math.round(g.seconds)} s de fala, {g.turns} trechos</span></span>
                  {choosable(g) && (
                    <span className="flex gap-2">
                      {!(g.label === "streamer" && v.profile?.confirmed) && (
                        <button className="btn-take px-2 py-1 text-xs" disabled={confirm.isPending} onClick={() => confirm.mutate(g.label)}>É o streamer</button>
                      )}
                      {g.label === "streamer" && (
                        <button className="btn-quiet px-2 py-1 text-xs" disabled={reject.isPending} onClick={() => reject.mutate(g.label)}>Não é o streamer</button>
                      )}
                    </span>
                  )}
                </div>
                <ul className="space-y-1">
                  {g.samples.map((s) => (
                    <li key={s.id} className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="w-12 shrink-0 text-ink-muted">{mmss(s.start)}</span>
                      <span className="min-w-0 flex-1 truncate">“{s.text}”</span>
                      {s.audio ? <audio controls preload="none" className="h-7 max-w-[220px]" src={`/api/speakers/turns/${s.id}/audio`} />
                        : <span className="text-ink-muted">sem áudio guardado</span>}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
