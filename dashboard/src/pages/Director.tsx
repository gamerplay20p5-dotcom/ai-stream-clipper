import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { ClipStatus, Disclosure, Empty, ErrorNote, Meter, PageHeader, Skeleton, Status, Tally } from "../components/ui";
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
      <PageHeader title="Diretor IA" description="Acompanhe as situações identificadas em cada live, as evidências e os cortes associados." />
      <ErrorNote error={sessions.error ?? story.error} />
      {sessions.isLoading && <Skeleton label="Carregando sessões" />}
      {sessions.data?.length === 0 && <Empty title="Nenhuma live analisada"><Link to="/streamers" className="text-take hover:underline">Ver canais monitorados</Link></Empty>}
      {!!sessions.data?.length && <div className="grid items-start gap-5 lg:grid-cols-[250px_minmax(0,1fr)]">
        <aside className="panel min-w-0">
          <header className="panel-heading"><h2 className="text-sm font-semibold">Sessões monitoradas</h2></header>
          <ul className="max-h-[19rem] divide-y divide-suite-line overflow-y-auto lg:max-h-[70vh]">
            {(sessions.data ?? []).map((s) => (
              <li key={s.id}>
                <button onClick={() => setSel(s.id)} aria-pressed={s.id === current} className={`w-full px-4 py-3 text-left text-sm ${s.id === current ? "bg-take-dim text-take" : "hover:bg-suite-panel"}`}>
                  <div className="flex justify-between gap-2"><span className="truncate font-medium">{s.streamer}</span>
                    <Tally live={s.status === "live"} label={s.status === "live" ? "Ao vivo" : "Encerrada"} /></div>
                  <div className="text-xs text-ink-muted">{s.situations} situações{s.open ? ` · ${s.open} em andamento` : ""} · {timecode(s.covered_until)}</div>
                </button>
              </li>
            ))}
          </ul>
        </aside>
        <section className="min-w-0 space-y-4">
          {story.isLoading && <Skeleton label="Carregando a narrativa da live" />}
          {story.data && (
            <>
              <section className="panel panel-body">
                <h2 className="display mb-2 text-lg">Decisões da sessão</h2>
                <div className="flex flex-wrap gap-2 text-xs">
                {Object.entries(story.data.candidates).sort((a, b) => b[1] - a[1]).map(([k, n]) => (
                  <span key={k} className="rounded bg-suite-raised px-2 py-1">{n} {decisionLabel[k] ?? k}</span>
                ))}
                {story.data.session.story_until !== null && <span className="rounded bg-suite-raised px-2 py-1">memória até {timecode(story.data.session.story_until ?? 0)}</span>}
                </div>
              </section>
              {story.data.situations.length === 0 && (
                <Empty title="Nenhuma situação narrativa registrada nesta live">
                  A memória começa a ser montada a partir da versão 0.5.1. Lives anteriores ou trechos sem história reconhecível não têm situações.
                </Empty>
              )}
              {[...story.data.situations].reverse().map((s) => <SituationCard key={s.id} s={s} />)}
              {story.data.isolated_clips.length > 0 && (
                <Disclosure title="Cortes isolados">
                  <ul className="divide-y divide-suite-line">
                    {story.data.isolated_clips.map((clip) => (
                      <li key={clip.id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                        <Link className="text-take hover:underline" to={`/cortes/${clip.id}?status=`}>{clip.title || `Corte #${clip.id}`}</Link>
                        <span className="text-xs text-ink-muted">{timecode(clip.start)} / nota {Math.round(clip.score)}</span>
                        <ClipStatus status={clip.status} />
                      </li>
                    ))}
                  </ul>
                </Disclosure>
              )}
            </>
          )}
        </section>
      </div>}
    </div>
  );
}

function SituationCard({ s }: { s: Situation }) {
  const span = Math.max(1, s.last - s.start);
  return (
    <article className="panel panel-body">
      <header className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="display text-lg">{s.kind_label}</h3>
          <span className="text-xs text-ink-muted">{timecode(s.start)}–{timecode(s.last)}</span>
          {s.status === "ongoing" && <span className="rounded bg-tally-dim px-1.5 py-0.5 text-[11px] text-tally">em andamento</span>}
          {s.complete && <span className="rounded bg-ok/15 px-1.5 py-0.5 text-[11px] text-ok">história completa</span>}
          {s.outcome && <span className="text-[11px] text-ink-muted">{outcomeLabel[s.outcome] ?? s.outcome}</span>}
        </div>
        <Status tone="accent">{(s.confidence * 100).toFixed(0)}% de confiança</Status>
      </header>
      <p className="mb-3 text-sm">{s.summary}</p>
      <div className="mb-3 max-w-xs"><Meter value={s.confidence} max={1} label="Confiança do Diretor IA" /></div>
      {s.related_to && <p className="mb-3 text-xs text-ink-muted">Continua a situação #{s.related_to}</p>}
      <div className="relative mb-1 h-6 rounded bg-suite-panel" aria-label="Linha do tempo da situação">
        {s.events.map((e, i) => (
          <span key={i} title={`${timecode(e.t)} · ${phaseLabel[e.phase] ?? e.phase}: ${e.label} (${e.source})`}
            className={`absolute top-1 h-4 w-1.5 rounded ${phaseTone[e.phase] ?? "bg-ink-muted"}`}
            style={{ left: `${Math.min(99, Math.max(0, ((e.t - s.start) / span) * 100))}%` }} />
        ))}
      </div>
      <p className="mb-3 text-[11px] text-ink-faint">■ preparação · desenvolvimento · <span className="text-take">ponto alto</span> · <span className="text-ok">desfecho</span></p>
      {s.events.length > 0 && (
        <div className="mb-3">
          <Disclosure title={`Evidências (${s.events.length})`}>
            <ol className="divide-y divide-suite-line">
              {s.events.map((event, index) => (
                <li key={index} className="grid grid-cols-[auto_minmax(0,1fr)] gap-3 py-2 text-sm">
                  <span className="font-mono text-xs text-take">{timecode(event.t)}</span>
                  <span>
                    <strong>{phaseLabel[event.phase] ?? event.phase}</strong> / {event.label}
                    <span className="block text-xs text-ink-muted">Fonte: {event.source}</span>
                  </span>
                </li>
              ))}
            </ol>
          </Disclosure>
        </div>
      )}
      {[
        ["Participantes", s.participants], ["Mudanças", s.changes], ["Objetivos", s.goals], ["Conflitos", s.conflicts], ["Incertezas", s.uncertain],
      ].map(([label, values]) => Array.isArray(values) && values.length > 0 && (
        <p key={String(label)} className="mb-2 break-words text-xs text-ink-muted"><strong className="text-ink">{label}</strong> / {values.join(", ")}</p>
      ))}
      {s.clips.length > 0 ? (
        <ul className="space-y-1 border-t border-suite-line/50 pt-2 text-sm">
          {s.clips.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-2">
              <Link className="min-w-0 break-words text-take hover:underline" to={`/cortes/${c.id}?status=`}>#{c.id} {c.title || "corte"}</Link>
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
