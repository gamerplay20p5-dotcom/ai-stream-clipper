import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowUpRight, Clapperboard, Crosshair, Layers3, Radio } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, type Clip } from "../api";
import { ClipStatus, Disclosure, Empty, ErrorNote, Meter, PageHeader, Skeleton, Tally } from "../components/ui";
import { platformName, timecode } from "../lib/format";

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

const actionLabel: Record<string, string> = { story: "com contexto da história", isolated: "momento isolado", second_cut: "segundo corte da história" };
const outcomeLabel: Record<string, string> = { arrested: "foi pego", escaped: "escapou", laughter: "terminou em risada", none: "sem desfecho" };
const decisionLabel: Record<string, string> = {
  accepted: "para corte", below_min_score: "abaixo da nota", text_only: "só fala", duplicate: "por duplicidade",
  rate_limit: "limite por hora", too_short: "curtos demais", story_merged: "unidos a uma história", story_duplicate: "repetiam uma história",
};

const phases = [
  { key: "climax", label: "Clímax" }, { key: "development", label: "Desenvolvimento" },
  { key: "setup", label: "Preparação" }, { key: "resolution", label: "Desfecho" },
];
const phaseName = (phase: string) => phases.find(p => p.key === phase)?.label ?? phase;
const countLabel = (n: number, one: string, many: string) => n + " " + (n === 1 ? one : many);

export default function Director() {
  const sessions = useQuery({ queryKey: ["director-sessions"], queryFn: () => api.get<SessionRow[]>("/api/director/sessions"), refetchInterval: 20_000 });
  const [id, select] = useState<number | null>(null);
  const rows = sessions.data ?? [];
  const row = rows.find(s => s.id === id) ?? rows.find(s => s.status === "live") ?? rows[0];
  const story = useQuery({
    queryKey: ["director-story", row?.id], enabled: Boolean(row), refetchInterval: 15_000,
    queryFn: () => api.get<SessionStory>(`/api/director/sessions/${row!.id}`),
  });
  return <div className="director">
    <PageHeader title="Diretor IA" description="O que a IA entendeu da live, em histórias: o que aconteceu, por que importa e o que ela fez com isso.">
      {row && <label className="session-select"><Radio size={17} aria-hidden /><select aria-label="Live selecionada" value={row.id} onChange={e => select(Number(e.target.value))}>
        {rows.map(s => <option key={s.id} value={s.id}>{s.streamer || "Live"} / sessão #{s.id}</option>)}
      </select></label>}
    </PageHeader>
    <ErrorNote error={sessions.error ?? story.error} />
    {(sessions.isLoading || (row && story.isLoading)) && <Skeleton label="Carregando a narrativa da live" />}
    {sessions.data?.length === 0 && <Empty title="Nenhuma live analisada"><Link className="text-take" to="/streamers">Ver canais monitorados</Link></Empty>}
    {row && story.data && <Session key={row.id} row={row} story={story.data} />}
  </div>;
}

function Session({ row, story }: { row: SessionRow; story: SessionStory }) {
  const [selection, select] = useState<number | null>(null);
  const [marked, setMarked] = useState<{ situation: number; event: StoryEvent } | null>(null);
  const situations = [...story.situations].reverse();
  const current = situations.find(s => s.id === selection) ?? situations[0];
  const events = [...(current?.events ?? [])].sort((a,b) => a.t - b.t);
  const mark = marked && marked.situation === current?.id ? marked.event : null;
  const selectedEvent = events.find(e => mark && e.t === mark.t && e.phase === mark.phase
    && e.label === mark.label && e.source === mark.source)
    ?? events.find(e => e.phase === "climax") ?? events[0];
  const decisions = Object.entries(story.candidates).sort((a, b) => b[1] - a[1]);
  return <>
    <section className="session-band" aria-label="Monitoramento da sessão">
      <div className="session-identity">
        <div className="source-symbol"><Radio aria-hidden /></div>
        <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><Tally live={story.session.status === "live"} label={story.session.status === "live" ? "Ao vivo" : "Encerrada"} />
          <span className="text-xs text-ink-muted">{platformName[row.platform] ?? row.platform}</span></div>
          <h2>{row.title || row.streamer || `Sessão #${row.id}`}</h2>
          {row.title && <p className="text-sm text-ink-muted">{row.streamer}</p>}
        </div>
      </div>
      <dl className="session-stats">
        <div><dt>Tempo analisado</dt><dd>{story.session.story_until == null ? "—" : timecode(story.session.story_until)}</dd></div>
        <div><dt>Situações</dt><dd>{row.situations}</dd></div>
        <div><dt>Decisões</dt><dd>{decisions.reduce((total, [, n]) => total + n, 0)}</dd></div>
      </dl>
    </section>
    <p className="director-summary">{plainSession(decisions, row.open)}</p>
    <Disclosure title="Avançado: decisões por motivo">
      <div className="session-readout"><Activity size={15} aria-hidden/><span>Captura até {timecode(story.session.covered_until)}</span>
        <span>{row.open} em andamento</span>{decisions.map(([key,n]) => <span key={key}>{n} {decisionLabel[key] ?? key}</span>)}</div>
    </Disclosure>
    {current ? <div className="narrative-desk">
      <div className="min-w-0 space-y-4">
        {situations.length > 1 && <label className="story-picker"><span>História</span><select aria-label="História" value={current.id} onChange={e => select(Number(e.target.value))}>
          {situations.map(s => <option key={s.id} value={s.id}>{timecode(s.start)} · {s.kind_label}</option>)}
        </select></label>}
        <PlainStory key={current.id} s={current} events={events} />
        <Disclosure title="Avançado: linha do tempo, evidências e confiança">
          <div className="space-y-5">
            <EventSheet s={current} events={events} selected={selectedEvent} onSelect={event => setMarked({ situation: current.id, event })} />
            <Facts s={current} evidence={selectedEvent} />
          </div>
        </Disclosure>
      </div>
      <Inspector key={current.id} s={current} />
    </div> : <Empty title="Nenhuma situação narrativa registrada nesta live">A memória é criada quando o diretor reconhece uma história na transmissão.</Empty>}
    {story.isolated_clips.length > 0 && <Disclosure title={`Cortes isolados (${story.isolated_clips.length})`}>
      <ul>{story.isolated_clips.map(c => <li key={c.id} className="flex flex-wrap items-center gap-3 border-b border-suite-line py-2">
        <Link className="text-take" to={`/cortes/${c.id}?status=`}>{c.title || `Corte #${c.id}`}</Link>
        <span>{timecode(c.start)} / nota {Math.round(c.score)}</span><ClipStatus status={c.status}/></li>)}</ul></Disclosure>}
  </>;
}

function EventSheet({ s, events, selected, onSelect }: {
  s: Situation; events: StoryEvent[]; selected?: StoryEvent; onSelect: (event: StoryEvent) => void;
}) {
  const first = Math.min(s.start, s.climax_t ?? s.start, ...events.map(e => e.t));
  const last = Math.max(s.last, s.climax_t ?? first, ...events.map(e => e.t));
  const position = (t: number) => Math.max(0, Math.min(100, 100 * (t - first) / Math.max(1,last - first)));
  const lanes = [...phases, ...Array.from(new Set(events.map(e => e.phase))).filter(k => !phases.some(p => p.key === k)).map(key => ({key,label:key}))];
  const y = (phase: string) => 12 + 24 * lanes.findIndex(p => p.key === phase);
  return <section className="event-sheet" aria-label="Acontecimentos da situação">
    <div className="scope">
      <div className="scope-caption"><strong>{s.kind_label}</strong><span>{timecode(first)} – {timecode(last)}</span></div>
      <div className="scope-track">
        <div className="lane-names">{lanes.map(p => <span key={p.key}>{p.label}</span>)}</div>
        <div className="scope-grid" style={{height: lanes.length * 24}}>
          <svg viewBox={`0 0 100 ${lanes.length * 24}`} preserveAspectRatio="none" aria-hidden>
            <polyline points={events.map(e => position(e.t)+","+y(e.phase)).join(" ")} fill="none" stroke="currentColor" strokeWidth="2" vectorEffect="non-scaling-stroke"/>
          </svg>
          {s.climax_t != null && <i className="climax-cursor" style={{left:position(s.climax_t)+"%"}} aria-hidden />}
          {events.map((e,i) => <i key={i} aria-hidden className="scope-point" data-selected={e === selected} style={{left:position(e.t)+"%",top:y(e.phase)}} />)}
        </div>
        <div className="scope-ruler">{[0,0.5,1].map(f => <span key={f}>{timecode(first+f*(last-first))}</span>)}</div>
      </div>
      {selected && <p className="scope-reading"><Crosshair size={15} aria-hidden/><time>{timecode(selected.t)}</time> {selected.label}</p>}
    </div>
    <div className="desk-heading mt-5"><Layers3 aria-hidden/><h3>Acontecimentos</h3><span>{events.length}</span></div>
    {events.length ? <ol className="event-log">{events.map((e,i) => <li key={i}>
      <button type="button" onClick={() => onSelect(e)} aria-pressed={e === selected}>
        <time>{timecode(e.t)}</time><span className="event-copy"><small>{phaseName(e.phase)}</small><strong>{e.label}</strong></span>
        <span className="event-source">{e.source}</span>
      </button>
    </li>)}</ol> : <p className="quiet-copy">Ainda não há evidências com marcação de tempo.</p>}
  </section>;
}

const confidenceWord = (v: number) => (v >= 0.75 ? "alta" : v >= 0.5 ? "média" : "baixa");
const clipAction: Record<string, string> = {
  story: "corte com o contexto da história", isolated: "corte só do momento", second_cut: "segundo corte da mesma história",
};
const clipStatusWord: Record<string, string> = {
  ready: "esperando revisão", approved: "aprovado", rejected: "rejeitado", failed: "falhou ao renderizar",
  rendering: "renderizando", analyzing: "em análise", candidate: "em análise", waiting: "aguardando contexto",
};

function plainSession(decisions: [string, number][], open: number) {
  const total = decisions.reduce((sum, [, n]) => sum + n, 0);
  if (!total) return open ? `${countLabel(open, "história em andamento", "histórias em andamento")}. Nenhum momento foi avaliado ainda.` : "Nenhum momento foi avaliado nesta live ainda.";
  const accepted = decisions.find(([k]) => k === "accepted")?.[1] ?? 0;
  const merged = decisions.filter(([k]) => k === "story_merged" || k === "story_duplicate").reduce((sum, [, n]) => sum + n, 0);
  return `A IA avaliou ${total} ${total === 1 ? "momento" : "momentos"} desta live e transformou ${accepted} em corte.`
    + (merged ? ` Juntou ${merged} a uma história que já estava acompanhando.` : "")
    + (open ? ` ${countLabel(open, "história segue", "histórias seguem")} em andamento.` : "");
}

/** The four questions, answered only with fields the director recorded (no generated prose). */
function PlainStory({ s, events }: { s: Situation; events: StoryEvent[] }) {
  const climax = events.find(e => e.phase === "climax");
  const steps = events.slice(0, 6);
  const meaning = s.complete ? "Tem começo, meio e fim claros." : s.status === "ongoing" ? "Ainda está acontecendo na live."
    : s.complete === false ? "A IA não viu como terminou." : "";
  return <article className="plain-story" aria-labelledby={`story-${s.id}`}>
    <header>
      <h2 id={`story-${s.id}`} className="display text-xl">{s.kind_label}</h2>
      <p className="text-sm text-ink-muted">{timecode(s.start)} a {timecode(s.last)} da live{s.participants.length > 0 && ` · com ${s.participants.join(", ")}`}</p>
    </header>
    <section>
      <h3>O que aconteceu?</h3>
      {steps.length ? <ol className="plain-steps">{steps.map((e, i) => <li key={i}><time>{timecode(e.t)}</time> {e.label}</li>)}</ol>
        : <p>{s.summary || "A IA ainda não marcou acontecimentos com horário."}</p>}
    </section>
    <section>
      <h3>Por que importa?</h3>
      <p>{[meaning, climax && `O ponto alto foi “${climax.label}” (${timecode(climax.t)}).`,
        s.goals.length > 0 && `Objetivo: ${s.goals.join(", ")}.`, s.conflicts.length > 0 && `Conflito: ${s.conflicts.join(", ")}.`,
        s.related_to && "Continua uma história anterior desta live."].filter(Boolean).join(" ") || "A IA não registrou por que esta história se destaca."}</p>
    </section>
    <section>
      <h3>O que a IA decidiu?</h3>
      {s.clips.length ? <ul>{s.clips.map(c => <li key={c.id}>
        <Link className="text-take hover:underline" to={`/cortes/${c.id}?status=`}>{c.title || `Corte #${c.id}`}</Link>
        {": "}{clipAction[c.story_action ?? ""] ?? "corte"}, {clipStatusWord[c.status] ?? c.status}.</li>)}</ul>
        : <p>Ainda não fez corte desta história.</p>}
    </section>
    <section>
      <h3>Resultado</h3>
      <p>{s.outcome && s.outcome !== "none" ? `Desfecho: ${outcomeLabel[s.outcome] ?? s.outcome}.` : "Sem desfecho registrado."}
        {" "}Certeza da leitura: {confidenceWord(s.confidence)}.</p>
      {s.uncertain.length > 0 && <p className="mt-1 text-sm text-warning">A IA não tem certeza sobre: {s.uncertain.join(", ")}.</p>}
    </section>
  </article>;
}

function Facts({ s, evidence }: { s: Situation; evidence?: StoryEvent }) {
  const criteria = [["Participantes",s.participants],["Mudanças",s.changes],["Objetivos",s.goals],["Conflitos",s.conflicts],["Incertezas",s.uncertain]] as const;
  return <section aria-label="Ficha técnica da situação" className="space-y-3">
    <p className="text-xs text-ink-muted">Situação #{s.id} · {s.summary}</p>
    <div className="confidence-label"><span>Confiança da leitura</span><strong>{Math.round(s.confidence*100)}%</strong></div>
    <Meter value={s.confidence} max={1} label="Confiança do Diretor IA" />
    <dl className="decision-facts">
      {evidence && <div><dt>Evidência</dt><dd>{evidence.label}<span className="block text-xs text-ink-muted">{timecode(evidence.t)} / {evidence.source}</span></dd></div>}
      {s.climax_t != null && <div><dt>Clímax</dt><dd>{timecode(s.climax_t)}</dd></div>}
      {s.outcome && <div><dt>Desfecho</dt><dd>{outcomeLabel[s.outcome] ?? s.outcome}</dd></div>}
      {criteria.map(([name,values]) => values.length > 0 && <div key={name}><dt>{name}</dt><dd>{values.join(", ")}</dd></div>)}
    </dl>
  </section>;
}

function Inspector({ s }: { s: Situation }) {
  const leadId = s.clips[0]?.id;
  const cover = useQuery({
    queryKey: ["clip", leadId], enabled: leadId != null, staleTime: 30_000,
    queryFn: () => api.get<Clip>(`/api/clips/${leadId}`),
  });
  return <aside className="story-inspector" aria-label="Cortes desta história">
    <div className="desk-heading"><Clapperboard aria-hidden/><h2>Cortes desta história</h2></div>
    <section className="inspector-clips">
      {s.clips.length === 0 && <p className="quiet-copy">Nenhum corte ainda.</p>}
      {s.clips.map(c => <Link key={c.id} to={`/cortes/${c.id}?status=`} className="linked-cut" data-media={c.id === leadId && Boolean(cover.data?.has_thumbnail)}>
        {c.id === leadId && cover.data?.has_thumbnail && <img className="linked-still" alt="" loading="lazy"
          src={`/api/clips/${c.id}/thumbnail?v=${cover.data.version}`}/>}
        <strong>{c.title || `Corte #${c.id}`} <ArrowUpRight size={14} aria-hidden/></strong>
        <span>{Math.round(c.end-c.start)} s · nota {Math.round(c.editorial_score ?? c.score)}</span>
        <span>{actionLabel[c.story_action ?? ""]}</span>
        <ClipStatus status={c.status}/></Link>)}
    </section>
  </aside>;
}
