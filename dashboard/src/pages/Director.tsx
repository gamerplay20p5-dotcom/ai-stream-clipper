import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ClipStatus, Empty, ErrorNote, PageHeader } from "../components/ui";
import { timecode } from "../lib/format";

type SessionRow = { id: number; streamer: string; platform: string; status: string; title: string; covered_until: number;
  story_until: number | null; situations: number; open: number };
type StoryEvent = { t: number; phase: string; label: string; source: string };
type Situation = { id: number; kind: string; kind_label: string; start: number; last: number; status: string; confidence: number;
  summary: string; related_to: number | null; complete: boolean | null; outcome: string; climax_t: number | null;
  events: StoryEvent[]; participants: string[]; uncertain: string[]; changes: string[]; goals: string[]; conflicts: string[];
  clips: { id: number; status: string; title: string; start: number; end: number; score: number; prelim_score: number | null;
    editorial_score: number | null; confidence: number | null; story_action: string | null }[] };
type SessionStory = { session: { id: number; streamer: string; status: string; covered_until: number; story_until: number | null };
  situations: Situation[]; candidates: Record<string, number>;
  isolated_clips: { id: number; status: string; title: string; start: number; score: number }[] };

const phaseTone: Record<string, string> = { setup: "bg-ink-faint", development: "bg-ink-muted", climax: "bg-take", resolution: "bg-ok" };
const phaseLabel: Record<string, string> = { setup: "preparação", development: "desenvolvimento", climax: "ponto alto", resolution: "desfecho" };
const actionLabel: Record<string, string> = { story: "com contexto da história", isolated: "momento isolado", second_cut: "segundo corte da história" };
const outcomeLabel: Record<string, string> = { arrested: "foi pego", escaped: "escapou", laughter: "terminou em risada", none: "sem desfecho" };
const decisionLabel: Record<string, string> = {
  accepted: "viraram corte", below_min_score: "abaixo da nota", text_only: "só fala", duplicate: "duplicados",
  rate_limit: "limite por hora", too_short: "curtos demais", story_merged: "unidos a uma história", story_duplicate: "repetiam uma história",
};

export default function Director() {
  const sessions = useQuery({ queryKey: ["director-sessions"], queryFn: () => api.get<SessionRow[]>("/api/director/sessions"), refetchInterval: 20_000 });
  const [sel, setSel] = useState<number | null>(null);
  const current = sel ?? sessions.data?.find((s) => s.status === "live")?.id ?? sessions.data?.[0]?.id ?? null;
  const story = useQuery({
    queryKey: ["director-story", current], enabled: current !== null, refetchInterval: 15_000,
    queryFn: () => api.get<SessionStory>(`/api/director/sessions/${current}`),
  });
  return (
    <div>
      <PageHeader title="Diretor IA" />
      <p className="-mt-3 mb-6 max-w-3xl text-sm text-ink-muted">
        Memória narrativa de cada live. O diretor acompanha situações que duram minutos, como discussões, perseguições, tiroteios e conversas engraçadas, e liga cada corte à história a que ele pertence. Cada etapa tem horário e fonte verificáveis.
      </p>
      <ErrorNote error={sessions.error ?? story.error} />
      <div className="lg:grid lg:grid-cols-[260px_minmax(0,1fr)] lg:gap-6">
        <aside className="mb-6 lg:mb-0">
          <ul className="space-y-1">
            {(sessions.data ?? []).map((s) => (
              <li key={s.id}>
                <button onClick={() => setSel(s.id)} className={`w-full rounded-md px-3 py-2 text-left text-sm ${s.id === current ? "bg-suite-raised ring-1 ring-take" : "hover:bg-suite-panel"}`}>
                  <div className="flex justify-between gap-2"><span className="truncate font-medium">{s.streamer}</span>
                    <span className={`text-xs ${s.status === "live" ? "text-tally" : "text-ink-muted"}`}>{s.status === "live" ? "ao vivo" : "encerrada"}</span></div>
                  <div className="text-xs text-ink-muted">{s.situations} situações{s.open ? ` · ${s.open} em andamento` : ""} · {timecode(s.covered_until)}</div>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section className="min-w-0 space-y-4">
          {story.data && (
            <>
              <div className="flex flex-wrap gap-2 text-xs text-ink-muted">
                {Object.entries(story.data.candidates).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
                  <span key={k} className="rounded bg-suite-panel px-2 py-1">{n} {decisionLabel[k] ?? k}</span>
                ))}
                {story.data.session.story_until !== null && <span className="rounded bg-suite-panel px-2 py-1">memória até {timecode(story.data.session.story_until ?? 0)}</span>}
              </div>
              {story.data.situations.length === 0 && (
                <Empty title="Nenhuma situação narrativa registrada nesta live">
                  A memória começa a ser montada a partir da versão 0.5.1. Lives anteriores ou trechos sem história reconhecível não têm situações.
                </Empty>
              )}
              {[...story.data.situations].reverse().map((s) => <SituationCard key={s.id} s={s} />)}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

function SituationCard({ s }: { s: Situation }) {
  const span = Math.max(1, s.last - s.start);
  return (
    <article className="rounded-lg border border-suite-line p-4">
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold">{s.kind_label}</span>
          <span className="text-xs text-ink-muted">{timecode(s.start)}–{timecode(s.last)}</span>
          {s.status === "ongoing" && <span className="rounded bg-tally-dim px-1.5 py-0.5 text-[11px] text-[#ffb4b6]">em andamento</span>}
          {s.complete && <span className="rounded bg-ok/15 px-1.5 py-0.5 text-[11px] text-ok">história completa</span>}
          {s.outcome && <span className="text-[11px] text-ink-muted">{outcomeLabel[s.outcome] ?? s.outcome}</span>}
        </div>
        <span className="text-xs text-ink-muted">confiança {(s.confidence * 100).toFixed(0)}%{s.related_to ? ` · continua a situação #${s.related_to}` : ""}</span>
      </header>
      <p className="mb-3 text-sm">{s.summary}</p>
      <div className="relative mb-1 h-6 rounded bg-suite-panel" aria-label="Linha do tempo da situação">
        {s.events.map((e, i) => (
          <span key={i} title={`${timecode(e.t)} · ${phaseLabel[e.phase] ?? e.phase}: ${e.label} (${e.source})`}
            className={`absolute top-1 h-4 w-1.5 rounded ${phaseTone[e.phase] ?? "bg-ink-muted"}`}
            style={{ left: `${Math.min(99, Math.max(0, ((e.t - s.start) / span) * 100))}%` }} />
        ))}
      </div>
      <p className="mb-3 text-[11px] text-ink-faint">■ preparação · desenvolvimento · <span className="text-take">ponto alto</span> · <span className="text-ok">desfecho</span></p>
      <ul className="mb-3 space-y-0.5 text-xs">
        {s.events.filter((e) => e.phase !== "development").slice(0, 8).map((e, i) => (
          <li key={i}><span className="text-ink-muted">{timecode(e.t)}</span> · {phaseLabel[e.phase]}: {e.label} <span className="text-ink-faint">({e.source})</span></li>
        ))}
      </ul>
      {(s.changes.length > 0 || s.participants.length > 0 || s.uncertain.length > 0) && (
        <p className="mb-3 text-xs text-ink-muted">
          {s.changes.length > 0 && <>Mudanças: {s.changes.slice(0, 4).join("; ")}. </>}
          {s.participants.length > 0 && <>Participantes: {s.participants.join(", ")}. </>}
          {s.uncertain.length > 0 && <>Incerto: {s.uncertain.join(", ")}.</>}
        </p>
      )}
      {s.clips.length > 0 ? (
        <ul className="space-y-1 border-t border-suite-line/50 pt-2 text-sm">
          {s.clips.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2">
              <Link className="truncate text-take hover:underline" to={`/cortes/${c.id}?status=`}>#{c.id} {c.title || "corte"}</Link>
              <span className="flex items-center gap-2 text-xs text-ink-muted">
                {actionLabel[c.story_action ?? ""] ?? ""} · {Math.round(c.end - c.start)} s · nota {c.score.toFixed(0)}
                {c.editorial_score !== null ? ` · editorial ${c.editorial_score.toFixed(0)}` : c.prelim_score !== null ? ` · preliminar ${c.prelim_score.toFixed(0)}` : ""}
                <ClipStatus status={c.status} />
              </span>
            </li>
          ))}
        </ul>
      ) : <p className="border-t border-suite-line/50 pt-2 text-xs text-ink-faint">Nenhum corte desta situação ainda.</p>}
    </article>
  );
}
