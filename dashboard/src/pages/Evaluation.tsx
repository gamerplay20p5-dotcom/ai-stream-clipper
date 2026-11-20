import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Flag, Loader2, Save, SkipForward } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type Example, type Taxonomy } from "../api";
import { Disclosure, Empty, ErrorNote, PageHeader, Skeleton } from "../components/ui";
import { ago, bytes, platformName, timecode } from "../lib/format";
import { t, tx } from "../i18n";

const TABS = [
  { key: "aprendizado", label: t("Aprendizado") },
  { key: "revisar", label: t("Revisar") },
  { key: "metricas", label: t("Métricas") },
  { key: "dataset", label: t("Dataset e modelos") },
];

const providerTone: Record<string, string> = {
  multimodal: "bg-ok/15 text-ok",
  audio_fallback: "bg-tally-dim text-tally",
  heuristic: "bg-suite-raised text-ink-muted",
  ranker: "bg-take-dim text-take",
};

export function ProviderBadge({ provider, tax }: { provider?: string; tax?: Taxonomy }) {
  const p = provider || "heuristic";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${providerTone[p] ?? providerTone.heuristic}`}>
      {tax?.providers[p] ?? p}
    </span>
  );
}

const fmt = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? t("n/d") : v.toFixed(digits));
const pct = (v: number | null | undefined) => (v === null || v === undefined ? t("n/d") : `${Math.round(v * 100)}%`);

export default function Evaluation() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("aba") ?? "revisar";
  const tax = useQuery({ queryKey: ["taxonomy"], queryFn: async () => {
    // the server sends Portuguese labels; show them in the interface language
    const raw = await api.get<Taxonomy>("/api/review/taxonomy");
    return Object.fromEntries(Object.entries(raw).map(([group, labels]) =>
      [group, Object.fromEntries(Object.entries(labels as Record<string, string>).map(([k, v]) => [k, tx(v)]))])) as Taxonomy;
  }, staleTime: Infinity });
  return (
    <div>
      <PageHeader title={t("Avaliação editorial")} description={t("Revise exemplos, compare a decisão da IA com sua leitura e acompanhe o aprendizado.")} />
      <nav className="view-switch mb-5" aria-label={t("Seções da avaliação")}>
        {TABS.map((t) => (
          <button type="button" key={t.key} onClick={() => setParams({ aba: t.key })}
            className="view-switch-item" aria-pressed={tab === t.key}>{t.label}</button>
        ))}
      </nav>
      {tax.isLoading && tab !== "aprendizado" && <Skeleton label={t("Carregando critérios editoriais")} />}
      {tax.data && tab === "revisar" && <ReviewDesk tax={tax.data} />}
      {tax.data && tab === "metricas" && <Metrics tax={tax.data} />}
      {tax.data && tab === "dataset" && <DatasetPanel tax={tax.data} />}
      {tab === "aprendizado" && <LearningPanel />}
      <ErrorNote error={tax.error} />
    </div>
  );
}

// ------------------------------------------------------------------ review desk
function ReviewDesk({ tax }: { tax: Taxonomy }) {
  const [params, setParams] = useSearchParams();
  const [filter, setFilter] = useState<"queue" | "reviewed">("queue");
  const queue = useQuery({
    queryKey: ["review-queue", filter],
    queryFn: () => filter === "queue"
      ? api.get<Example[]>("/api/review/queue?limit=60")
      : api.get<Example[]>("/api/review/examples?state=reviewed&limit=100"),
    refetchInterval: 15_000,
  });
  const items = queue.data ?? [];
  const sel = params.get("ex") ? Number(params.get("ex")) : items[0]?.id ?? null;
  const select = (id: number | null) => {
    const p = new URLSearchParams(params);
    if (id) p.set("ex", String(id)); else p.delete("ex");
    setParams(p);
  };
  const next = () => {
    const i = items.findIndex((e) => e.id === sel);
    const n = items.find((e, j) => j > i && !e.reviewed_at) ?? items.find((e) => e.id !== sel && !e.reviewed_at);
    select(n?.id ?? null);
  };

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(12rem,.32fr)_minmax(0,1fr)]">
      <aside className="panel min-w-0">
        <div className="panel-heading"><h2 className="text-sm font-semibold">{t("Exemplos")}</h2></div>
        <div className="p-3">
        <div className="view-switch mb-3">
          <button type="button" className="view-switch-item px-2 text-xs" aria-pressed={filter === "queue"} onClick={() => setFilter("queue")}>{t("Fila sugerida")}</button>
          <button type="button" className="view-switch-item px-2 text-xs" aria-pressed={filter === "reviewed"} onClick={() => setFilter("reviewed")}>{t("Já avaliados")}</button>
        </div>
        <p className="mb-3 text-xs text-ink-muted">
          {filter === "queue"
            ? t("Ordenada pelo que mais ajuda a IA a aprender: perto do limiar, áudio e visão discordando, rejeitados com reação.")
            : t("Avaliações salvas. Você pode corrigir qualquer uma.")}
        </p>
        {queue.isLoading && <Skeleton label={t("Carregando exemplos")} lines={2} />}
        <ErrorNote error={queue.error} />
        {queue.data && items.length === 0 && (
          <Empty title={filter === "queue" ? t("Nada para revisar agora") : t("Nenhuma avaliação ainda")}>
            {t("Os exemplos aparecem enquanto as lives são analisadas (a mídia leva cerca de 1 minuto para ficar pronta).")}
          </Empty>
        )}
        <ul className="max-h-52 space-y-1 overflow-y-auto p-1 lg:max-h-[65vh]">
          {items.map((e) => (
            <li key={e.id}>
              <button onClick={() => select(e.id)} aria-current={e.id === sel ? "true" : undefined}
                className={`w-full rounded-md px-3 py-2 text-left text-sm ${e.id === sel ? "bg-suite-raised border-l-2 border-take" : "hover:bg-suite-panel"}`}>
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-medium">{e.streamer_name || "—"}</span>
                  <span className="shrink-0 text-xs text-ink-muted">{e.ai.decision === "accepted" ? t("corte") : t("rejeitado")} · {fmt(e.ai.score)}</span>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2 text-xs text-ink-muted">
                  <span className="truncate">{e.stratum_label}</span>
                  {e.review ? <span className="text-ok">{tax.ratings[e.review.rating]}</span> : <span>{ago(e.created_at)}</span>}
                </div>
              </button>
            </li>
          ))}
        </ul>
        </div>
      </aside>
      {sel ? <ExampleReview key={sel} id={sel} tax={tax} onDone={next} /> : null}
    </div>
  );
}

type Marks = { event_start: number | null; event_peak: number | null; event_end: number | null; short_start: number | null; short_end: number | null };

function ExampleReview({ id, tax, onDone }: { id: number; tax: Taxonomy; onDone: () => void }) {
  const qc = useQueryClient();
  const ex = useQuery({ queryKey: ["example", id], queryFn: () => api.get<Example>(`/api/review/examples/${id}`) });
  const video = useRef<HTMLVideoElement>(null);
  const [now, setNow] = useState(0);
  const [rating, setRating] = useState("");
  const [category, setCategory] = useState("");
  const [reasons, setReasons] = useState<string[]>([]);
  const [tech, setTech] = useState<string[]>([]);
  const [ctxq, setCtxq] = useState<number | null>(null);
  const [description, setDescription] = useState("");
  const [notes, setNotes] = useState("");
  const [marks, setMarks] = useState<Marks>({ event_start: null, event_peak: null, event_end: null, short_start: null, short_end: null });

  const e = ex.data;
  useEffect(() => {
    if (!e) return;
    const r = e.review;
    setRating(r?.rating ?? "");
    setCategory(r?.category ?? "");
    setReasons(r?.editorial_reasons ?? []);
    setTech(r?.technical_issues ?? []);
    setCtxq(r?.context_quality ?? null);
    setDescription(r?.description ?? "");
    setNotes(r?.notes ?? "");
    setMarks({
      event_start: r?.event_start ?? null, event_peak: r?.event_peak ?? null, event_end: r?.event_end ?? null,
      short_start: r?.short_start ?? null, short_end: r?.short_end ?? null,
    });
  }, [e?.id, e?.review?.updated_at]);

  const save = useMutation({
    mutationFn: () => api.put<Example>(`/api/review/examples/${id}/review`, {
      rating, category, editorial_reasons: reasons, technical_issues: tech, context_quality: ctxq,
      description, notes, ...marks,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["review-queue"] });
      qc.invalidateQueries({ queryKey: ["example", id] });
      qc.invalidateQueries({ queryKey: ["review-metrics"] });
      onDone();
    },
  });

  // keyboard: I/O short in/out, A/P/F event start/peak/end, space play/pause, 1-5 rating
  useEffect(() => {
    const ratings = Object.keys(tax.ratings);
    const onKey = (ev: KeyboardEvent) => {
      const target = ev.target as HTMLElement;
      if (target.closest("input, textarea, select, video, [contenteditable], dialog") || document.querySelector("dialog[open]")
        || ev.repeat || ev.altKey || ev.ctrlKey || ev.metaKey) return;
      const k = ev.key.toLowerCase();
      if (k === "i") mark("short_start");
      else if (k === "o") mark("short_end");
      else if (k === "a") mark("event_start");
      else if (k === "p") mark("event_peak");
      else if (k === "f") mark("event_end");
      else if (["1", "2", "3", "4", "5"].includes(k)) setRating(ratings[Number(k) - 1]);
      else return;
      ev.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!e) return ex.isLoading ? <Skeleton label={t("Carregando exemplo")} /> : <ErrorNote error={ex.error} />;
  const abs = (t: number) => e.media_start + t; // media time -> session time
  function mark(k: keyof Marks) {
    if (!video.current || !e) return;
    setMarks((m) => ({ ...m, [k]: Math.round(abs(video.current!.currentTime) * 10) / 10 }));
  }
  const seek = (sessionT: number | null | undefined) => {
    if (video.current && sessionT !== null && sessionT !== undefined && e) video.current.currentTime = Math.max(0, sessionT - e.media_start);
  };
  const ai = e.ai;
  const span = Math.max(1, e.media_end - e.media_start);
  const x = (t: number | null | undefined) => (t === null || t === undefined ? null : Math.max(0, Math.min(100, ((t - e.media_start) / span) * 100)));
  const humanDur = marks.short_start !== null && marks.short_end !== null ? marks.short_end - marks.short_start : null;
  const aiDur = ai.start !== null && ai.end !== null ? ai.end - ai.start : null;

  return (
    <section className="panel panel-body space-y-5">
      <header className="flex flex-wrap items-center gap-2 text-sm">
        <h2 className="display text-lg">{e.streamer_name}</h2>
        <span className="text-ink-muted">{platformName[e.platform] ?? e.platform} {t("· sessão {session_id} ·", { session_id: e.session_id })} {e.split === "val" ? t("validação") : t("treino")}</span>
        <ProviderBadge provider={ai.provider} tax={tax} />
        <span className="rounded bg-suite-raised px-1.5 py-0.5 text-[11px] text-ink-muted">{e.stratum_label}</span>
        {e.is_test && <span className="rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-semibold text-take">{t("TESTE MANUAL · nunca usado como positivo")}</span>}
      </header>

      {e.media_state === "ready" ? (
        <div className="media-stage"><video ref={video} className="aspect-video max-h-[50vh] w-full" controls playsInline preload="metadata" aria-label={t("Vídeo do exemplo")}
          src={`/api/review/examples/${e.id}/media`} onTimeUpdate={(ev) => setNow((ev.target as HTMLVideoElement).currentTime)} /></div>
      ) : (
        <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-suite-panel text-sm text-ink-muted">
          {e.media_state === "pending" ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" /> {t("Preparando a mídia…")}</> : t("Mídia indisponível: {v}", { v: e.media_error || e.media_state })}
        </div>
      )}

      {/* Faixas de seleção da IA e do operador. */}
      <div>
        <div className="relative h-10 rounded-md bg-suite-panel" aria-label={t("Linha do tempo do exemplo")}>
          {x(ai.start) !== null && x(ai.end) !== null && (
            <div className="absolute top-1 h-3 rounded bg-take/70" style={{ left: `${x(ai.start)}%`, width: `${x(ai.end)! - x(ai.start)!}%` }} title={t("Seleção da IA")} />
          )}
          {x(ai.heuristic_start) !== null && x(ai.heuristic_end) !== null && (ai.heuristic_start !== ai.start || ai.heuristic_end !== ai.end) && (
            <div className="absolute top-[18px] h-1 rounded bg-take/30" style={{ left: `${x(ai.heuristic_start)}%`, width: `${x(ai.heuristic_end)! - x(ai.heuristic_start)!}%` }} title={t("Seleção heurística original")} />
          )}
          {x(marks.short_start) !== null && x(marks.short_end) !== null && (
            <div className="absolute bottom-1 h-3 rounded bg-ok/70" style={{ left: `${x(marks.short_start)}%`, width: `${Math.max(0.5, x(marks.short_end)! - x(marks.short_start)!)}%` }} title={t("Seu Short ideal")} />
          )}
          {x(ai.peak) !== null && <div className="absolute inset-y-0 w-0.5 bg-take" style={{ left: `${x(ai.peak)}%` }} title={t("Pico (IA)")} />}
          {(["event_start", "event_peak", "event_end"] as const).map((k) => x(marks[k]) !== null && (
            <div key={k} className="absolute inset-y-0 w-0.5 bg-ok" style={{ left: `${x(marks[k])}%` }} title={k} />
          ))}
          <div className="absolute inset-y-0 w-px bg-white" style={{ left: `${(now / span) * 100}%` }} />
        </div>
        <div className="mt-1 flex justify-between text-[11px] text-ink-faint">
          <span>{timecode(e.media_start)}</span>
          <span><span className="text-take">■</span> {t("IA")} <span className="ml-2 text-ok">■</span> {t("Você")}</span>
          <span>{timecode(e.media_end)}</span>
        </div>
      </div>

      <div className="flex flex-wrap gap-2 text-sm">
        {([
          ["event_start", t("Início do acontecimento"), "A"], ["event_peak", t("Ponto principal"), "P"], ["event_end", t("Fim da situação"), "F"],
          ["short_start", t("Início do Short"), "I"], ["short_end", t("Fim do Short"), "O"],
        ] as const).map(([k, label, key]) => (
          <button key={k} className="btn-quiet" onClick={() => mark(k)} disabled={e.media_state !== "ready"}>
            {label} <kbd className="ml-1 text-[10px] text-ink-faint">{key}</kbd>
            {marks[k] !== null && <span className="ml-1 text-ok" onClick={(ev) => { ev.stopPropagation(); seek(marks[k]); }}>{timecode(marks[k]!)}</span>}
          </button>
        ))}
        <button className="btn-quiet" onClick={() => { seek(ai.start); }}>{t("Ir para início da IA")}</button>
      </div>

      <div className="grid gap-3 rounded-lg border border-suite-line p-3 text-sm sm:grid-cols-3">
        <div><p className="text-xs text-ink-muted">{t("Duração IA")}</p><p>{aiDur !== null ? `${aiDur.toFixed(0)} s` : "n/d"}</p></div>
        <div><p className="text-xs text-ink-muted">{t("Sua duração ideal")}</p><p>{humanDur !== null ? `${humanDur.toFixed(0)} s` : t("marque I e O")}</p></div>
        <div><p className="text-xs text-ink-muted">{t("Diferença de início / fim")}</p>
          <p>{marks.short_start !== null && ai.start !== null ? `${(ai.start - marks.short_start).toFixed(1)} s` : "—"} / {marks.short_end !== null && ai.end !== null ? `${(ai.end - marks.short_end).toFixed(1)} s` : "—"}</p></div>
      </div>

      <Disclosure title={t("Decisão e evidências da IA")}><AiSide e={e} tax={tax} seek={seek} /></Disclosure>

      <div>
        <p className="mb-2 text-sm font-medium">{t("Avaliação editorial")} <span className="text-xs text-ink-muted">{t("(teclas 1–5)")}</span></p>
        <div className="flex flex-wrap gap-2">
          {Object.entries(tax.ratings).map(([k, label], i) => (
            <button key={k} aria-pressed={rating === k} onClick={() => setRating(k)}
              className={`rounded-md border px-3 py-1.5 text-sm ${rating === k ? (k === "excellent" || k === "good" ? "border-ok bg-ok/15 text-ok" : k === "fair" ? "border-take bg-take-dim text-take" : "border-tally bg-tally-dim text-tally") : "border-suite-line text-ink-muted hover:text-ink"}`}>
              <span className="mr-1 text-[10px] text-ink-faint">{i + 1}</span>{label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <fieldset>
          <legend className="mb-2 text-sm font-medium">{t("Motivos editoriais")}</legend>
          <Chips options={tax.editorial_reasons} value={reasons} onChange={setReasons} />
        </fieldset>
        <fieldset>
          <legend className="mb-2 text-sm font-medium">{t("Problemas técnicos")} <span className="text-xs text-ink-muted">{t("(separados do julgamento editorial)")}</span></legend>
          <Chips options={tax.technical_issues} value={tech} onChange={setTech} />
        </fieldset>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <label className="block text-sm">
          <span className="mb-1 block font-medium">{t("Tipo de momento")}</span>
          <select className="field" value={category} onChange={(ev) => setCategory(ev.target.value)}>
            <option value="">—</option>
            {Object.entries(tax.categories).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label className="block text-sm">
          <span className="mb-1 block font-medium">{t("Qualidade do contexto:")} {ctxq ?? "—"}</span>
          <input type="range" min={0} max={10} value={ctxq ?? 5} onChange={(ev) => setCtxq(Number(ev.target.value))} className="w-full " />
        </label>
      </div>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">{t("Descrição objetiva do que acontece")}</span>
        <textarea className="field min-h-[60px]" value={description} onChange={(ev) => setDescription(ev.target.value)}
          placeholder={t("Ex.: foge da polícia de moto, bate num poste e o streamer ri da própria falha")} />
      </label>
      <label className="block text-sm">
        <span className="mb-1 block font-medium">{t("Observações")}</span>
        <textarea className="field min-h-[48px]" value={notes} onChange={(ev) => setNotes(ev.target.value)} />
      </label>

      <ErrorNote error={save.error} />
      <div className="flex flex-wrap gap-2">
        <button className="btn-take" disabled={!rating || save.isPending} onClick={() => save.mutate()}>
          <Save className="h-4 w-4" /> {t("Salvar e ir para o próximo")}
        </button>
        <button className="btn-quiet" onClick={onDone}><SkipForward className="h-4 w-4" /> {t("Pular")}</button>
      </div>

    </section>
  );
}

function Chips({ options, value, onChange }: { options: Record<string, string>; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {Object.entries(options).map(([k, label]) => {
        const on = value.includes(k);
        return (
          <button key={k} type="button" aria-pressed={on}
            onClick={() => onChange(on ? value.filter((x) => x !== k) : [...value, k])}
            className={`rounded-full border px-2.5 py-1 text-xs ${on ? "border-take bg-take-dim text-take" : "border-suite-line text-ink-muted hover:text-ink"}`}>
            {label}
          </button>
        );
      })}
    </div>
  );
}

function AiSide({ e, tax, seek }: { e: Example; tax: Taxonomy; seek: (t: number) => void }) {
  const ai = e.ai;
  const words = e.context?.words ?? [];
  const events = e.context?.acoustic_events ?? [];
  return (
    <details className="rounded-lg border border-suite-line p-3 text-sm" open>
      <summary className="cursor-pointer font-medium">{t("O que a IA viu e decidiu")}</summary>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <p>
            {t("Decisão:")} <b>{ai.decision === "accepted" ? t("virou corte") : `rejeitado (${ai.decision})`}</b> {t("· nota")} {fmt(ai.score, 1)}
            {ai.min_score !== null && ai.min_score !== undefined && <> {t("(mínimo")} {fmt(ai.min_score)})</>}
            {ai.ranker_score !== null && ai.ranker_score !== undefined && <> {t("· classificador")} {pct(ai.ranker_score)}</>}
          </p>
          <p className="flex items-center gap-2 text-xs">{t("Produzido por")} <ProviderBadge provider={ai.provider} tax={tax} /></p>
          <p className="text-xs text-ink-muted">{t("A nota é uma heurística de ordenação. Não é garantia de qualidade nem probabilidade de viralizar.")}</p>
          {ai.fallback_reason && <p className="text-xs text-tally">{t("Fallback:")} {ai.fallback_reason}</p>}
          {ai.visual?.summary && <p><span className="text-ink-muted">{t("Visão ({v}):", { v: ai.visual.model ?? t("modelo") })}</span> {ai.visual.summary}</p>}
          {e.context?.disagreement && <p className="text-take">{t("Áudio (")}{fmt(e.context.disagreement.audio_score)}{t(") e visão (")}{fmt(e.context.disagreement.visual_interest, 1)}/10) discordam.</p>}
          {(ai.reasons ?? []).length > 0 && <ul className="list-inside list-disc text-xs text-ink-muted">{ai.reasons!.slice(0, 8).map((r, i) => <li key={i}>{r}</li>)}</ul>}
          {(ai.director?.adjustments ?? []).length > 0 && <p className="text-xs text-ink-muted">{t("Diretor:")} {ai.director!.adjustments!.join("; ")}</p>}
        </div>
        <div className="space-y-2">
          <p className="text-xs text-ink-muted">{t("Eventos acústicos")}</p>
          <ul className="max-h-32 space-y-0.5 overflow-y-auto text-xs">
            {events.length === 0 && <li className="text-ink-faint">{t("nenhum")}</li>}
            {events.map((ev, i) => (
              <li key={i}><button className="text-take hover:underline" onClick={() => seek(ev.t)}>{timecode(ev.t)}</button> {ev.kind}{ev.phrase ? ` “${ev.phrase}”` : ""}{ev.source ? ` (${ev.source})` : ""}</li>
            ))}
          </ul>
          <p className="text-xs text-ink-muted">{t("Transcrição")}</p>
          <p className="max-h-32 overflow-y-auto text-xs leading-relaxed">
            {words.length === 0 ? <span className="text-ink-faint">{t("sem fala")}</span> : words.map(([t, w], i) => (
              <span key={i} className="cursor-pointer hover:text-take" onClick={() => seek(t)}>{w} </span>
            ))}
          </p>
        </div>
      </div>
      {e.provenance && (
        <p className="mt-3 break-all text-[11px] text-ink-faint">
          {t("Versões: app {String} · pontuação {String2} · config {String3}", { String: String((e.provenance as Record<string, unknown>).app ?? "?"), String2: String((e.provenance as Record<string, unknown>).scoring ?? "?"), String3: String((e.provenance as Record<string, unknown>).settings_hash ?? "?") })}
          {(e.provenance as { analysis?: { prompt_version?: string; vision_model?: string } }).analysis &&
            <> {t("· visão {String} · prompt", { String: String((e.provenance as { analysis: { vision_model?: string } }).analysis.vision_model ?? "—") })} {String((e.provenance as { analysis: { prompt_version?: string } }).analysis.prompt_version ?? "—")}</>}
        </p>
      )}
    </details>
  );
}

// ------------------------------------------------------------------ metrics
type EditorialMetrics = {
  reviewed: number; reviewed_accepted: number; reviewed_rejected: number;
  approval_rate: number | null; precision: number | null; coverage: number | null;
  false_negatives_known: number; false_positives: number; false_positives_per_hour: number | null;
  duplicates_flagged: number; hours_captured: number;
  start_error_s: { median: number | null; p95: number | null; n: number };
  end_error_s: { median: number | null; p95: number | null; n: number };
  duration_s: { ai_mean: number | null; human_ideal_mean: number | null };
  precision_at_5: Record<string, number | null>;
  editorial_reasons: Record<string, number>; technical_issues: Record<string, number>;
  false_negative_examples: { example_id: number; stratum: string; score: number | null; rating: string }[];
  false_positive_examples: { example_id: number; clip_id: number | null; score: number | null; rating: string; reasons: string[] }[];
};
type Operational = {
  stages: Record<string, { n: number; run_p50: number | null; run_p95: number | null; wait_p50: number | null; wait_p95: number | null }>;
  clip_ready_s: { p50: number | null; p95: number | null; n: number };
  vlm_s: { p50: number | null; p95: number | null; n: number };
  queue: { queued: Record<string, number>; oldest_s: Record<string, number> };
  candidates_per_hour: number; clips_per_hour: number;
};

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-suite-line p-3">
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="display mt-1 text-xl">{value}</p>
      {hint && <p className="mt-1 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  );
}

function Metrics({ tax }: { tax: Taxonomy }) {
  const [split, setSplit] = useState("");
  const m = useQuery({ queryKey: ["review-metrics", split], queryFn: () => api.get<{ editorial: EditorialMetrics; note: string }>(`/api/review/metrics${split ? `?split=${split}` : ""}`) });
  const op = useQuery({ queryKey: ["review-op"], queryFn: () => api.get<Operational>("/api/review/operational?hours=24"), refetchInterval: 30_000 });
  const [, setParams] = useSearchParams();
  const ed = m.data?.editorial;
  const open = (id: number) => setParams({ aba: "revisar", ex: String(id) });
  const stageName: Record<string, string> = { audio_analyze: t("Áudio e transcrição"), score: t("Pontuação"), vision_analyze: t("Análise multimodal"), render: t("Renderização"), dataset_media: t("Mídia do dataset") };
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-ink-muted">{t("Conjunto:")}</span>
        {[["", t("Todos")], ["train", t("Treino")], ["val", t("Validação")]].map(([k, l]) => (
          <button key={k} onClick={() => setSplit(k)} className={`rounded px-2 py-1 ${split === k ? "bg-suite-raised" : "text-ink-muted"}`}>{l}</button>
        ))}
      </div>
      <p className="text-xs text-ink-muted">{m.data?.note}</p>
      {ed && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat label={t("Avaliações")} value={String(ed.reviewed)} hint={t("{reviewed_accepted} cortes · {reviewed_rejected} rejeitados", { reviewed_accepted: ed.reviewed_accepted, reviewed_rejected: ed.reviewed_rejected })} />
            <Stat label={t("Precisão dos cortes")} value={pct(ed.precision)} hint={t("bons ou excelentes entre os cortes avaliados (sem 'regular')")} />
            <Stat label={t("Cobertura")} value={pct(ed.coverage)} hint={t("bons momentos conhecidos que viraram corte")} />
            <Stat label={t("Taxa de aprovação")} value={pct(ed.approval_rate)} />
            <Stat label={t("Falsos positivos por hora")} value={fmt(ed.false_positives_per_hour, 2)} hint={`${ed.false_positives} ruins/indevidos · ${ed.hours_captured} h capturadas`} />
            <Stat label={t("Falsos negativos conhecidos")} value={String(ed.false_negatives_known)} hint={t("rejeitados que você avaliou como bons")} />
            <Stat label={t("Erro no início (mediana / p95)")} value={`${fmt(ed.start_error_s.median, 1)} / ${fmt(ed.start_error_s.p95, 1)} s`} hint={t("{n} cortes com marcação", { n: ed.start_error_s.n })} />
            <Stat label={t("Erro no fim (mediana / p95)")} value={`${fmt(ed.end_error_s.median, 1)} / ${fmt(ed.end_error_s.p95, 1)} s`} />
            <Stat label={t("Duração média IA / ideal")} value={`${fmt(ed.duration_s.ai_mean)} / ${fmt(ed.duration_s.human_ideal_mean)} s`} />
            <Stat label={t("Duplicatas marcadas")} value={String(ed.duplicates_flagged)} />
          </div>
          <div className="rounded-lg border border-suite-line p-3">
            <p className="mb-2 text-sm font-medium">{t("Precision@5 por sessão: os melhores aparecem no topo?")}</p>
            <div className="table-scroll" role="region" tabIndex={0} aria-label={t("Qualidade editorial por recorte")}><table className="w-full min-w-[28rem] text-sm">
              <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">{t("Ordenação")}</th><th>{t("P@5")}</th></tr></thead>
              <tbody>
                <tr><td className="py-1">{t("A. Heurística (v0.2.9)")}</td><td>{pct(ed.precision_at_5.heuristic)}</td></tr>
                <tr><td className="py-1">{t("B. Julgamento contextual (diretor + visão + memória)")}</td><td>{pct(ed.precision_at_5.contextual)}</td></tr>
                <tr><td className="py-1">{t("C. Classificador treinado")}</td><td>{pct(ed.precision_at_5.ranker)}</td></tr>
              </tbody>
            </table></div>
            <p className="mt-2 text-[11px] text-ink-faint">{t("“n/d” = avaliações insuficientes na mesma sessão para comparar. Nenhuma melhoria é declarada sem esses números.")}</p>
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-lg border border-suite-line p-3">
              <p className="mb-2 text-sm font-medium">{t("Onde a IA ainda erra: falsos positivos")}</p>
              {ed.false_positive_examples.length === 0 ? <p className="text-xs text-ink-faint">{t("nenhum registrado")}</p> :
                <ul className="space-y-1 text-xs">{ed.false_positive_examples.map((f) => (
                  <li key={f.example_id}><button className="text-take hover:underline" onClick={() => open(f.example_id)}>#{f.example_id}</button> {t("nota")} {fmt(f.score)} · {tax.ratings[f.rating]} · {f.reasons.map((r) => tax.editorial_reasons[r] ?? r).join(", ")}</li>
                ))}</ul>}
            </div>
            <div className="rounded-lg border border-suite-line p-3">
              <p className="mb-2 text-sm font-medium">{t("Falsos negativos conhecidos")}</p>
              {ed.false_negative_examples.length === 0 ? <p className="text-xs text-ink-faint">{t("nenhum registrado")}</p> :
                <ul className="space-y-1 text-xs">{ed.false_negative_examples.map((f) => (
                  <li key={f.example_id}><button className="text-take hover:underline" onClick={() => open(f.example_id)}>#{f.example_id}</button> {t("{v} · nota", { v: tax.strata[f.stratum] ?? f.stratum })} {fmt(f.score)} · {tax.ratings[f.rating]}</li>
                ))}</ul>}
            </div>
          </div>
        </>
      )}
      {op.data && (
        <div className="rounded-lg border border-suite-line p-3">
          <p className="mb-2 text-sm font-medium">{t("Desempenho operacional (últimas 24 h)")}</p>
          <div className="mb-3 grid gap-3 sm:grid-cols-4">
            <Stat label={t("Short pronto (p50 / p95)")} value={`${fmt(op.data.clip_ready_s.p50)} / ${fmt(op.data.clip_ready_s.p95)} s`} />
            <Stat label={t("Inferência VLM (p50 / p95)")} value={`${fmt(op.data.vlm_s.p50, 1)} / ${fmt(op.data.vlm_s.p95, 1)} s`} />
            <Stat label={t("Candidatos por hora")} value={fmt(op.data.candidates_per_hour, 1)} />
            <Stat label={t("Cortes por hora")} value={fmt(op.data.clips_per_hour, 2)} />
          </div>
          <div className="table-scroll" role="region" tabIndex={0} aria-label={t("Desempenho por dimensão")}><table className="w-full min-w-[28rem] text-sm">
            <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">{t("Etapa")}</th><th>{t("Jobs")}</th><th>{t("Execução p50/p95")}</th><th>{t("Espera na fila p50/p95")}</th><th>{t("Na fila agora")}</th></tr></thead>
            <tbody>
              {Object.entries(op.data.stages).map(([k, s]) => (
                <tr key={k} className="border-t border-suite-line/50">
                  <td className="py-1">{stageName[k] ?? k}</td><td>{s.n}</td>
                  <td>{fmt(s.run_p50, 1)} / {fmt(s.run_p95, 1)} {t("s")}</td><td>{fmt(s.wait_p50, 1)} / {fmt(s.wait_p95, 1)} {t("s")}</td>
                  <td>{op.data!.queue.queued[k] ?? 0}{op.data!.queue.oldest_s[k] ? t(" (mais antigo {v} s)", { v: op.data!.queue.oldest_s[k] }) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </div>
      )}
      <ErrorNote error={m.error ?? op.error} />
    </div>
  );
}

// ------------------------------------------------------------------ dataset & rankers
type Stats = {
  total: number; reviewed: number; media_bytes: number; quota_bytes: number; candidates: number;
  by_stratum: Record<string, { total: number; reviewed: number }>;
  by_split: Record<string, { total: number; reviewed: number }>;
  by_media: Record<string, { total: number; reviewed: number }>;
  by_rating: Record<string, number>;
};
type Ranker = { id: number; name: string; status: string; n_train: number; n_val: number; created_at: string;
  metrics: { ranker?: { auc: number | null; p_at_5: number | null }; heuristic?: { auc: number | null; p_at_5: number | null }; beats_heuristic?: boolean; top_weights?: [string, number][] } };
type Rankers = { models: Ranker[]; labelled: Record<string, { n: number; pos: number }>; mode: string;
  requirements: { min_train: number; min_per_class: number; min_val: number } };

function DatasetPanel({ tax }: { tax: Taxonomy }) {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["review-stats"], queryFn: () => api.get<Stats>("/api/review/stats"), refetchInterval: 20_000 });
  const rk = useQuery({ queryKey: ["rankers"], queryFn: () => api.get<Rankers>("/api/review/rankers") });
  const train = useMutation({ mutationFn: () => api.post("/api/review/rankers/train"), onSuccess: () => qc.invalidateQueries({ queryKey: ["rankers"] }) });
  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: string }) => api.post(`/api/review/rankers/${id}/status`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["rankers"] }),
  });
  const backfill = useMutation({ mutationFn: () => api.post<{ created: number }>("/api/review/backfill"), onSuccess: () => qc.invalidateQueries() });
  const s = st.data;
  const useBar = s ? Math.min(100, (s.media_bytes / Math.max(1, s.quota_bytes)) * 100) : 0;
  return (
    <div className="space-y-6">
      {s && (
        <div className="grid gap-3 sm:grid-cols-4">
          <Stat label={t("Exemplos")} value={String(s.total)} hint={`${s.reviewed} avaliados`} />
          <Stat label={t("Candidatos registrados")} value={String(s.candidates)} hint={t("todos os momentos pontuados, inclusive os rejeitados")} />
          <Stat label={t("Treino / validação")} value={`${s.by_split.train?.total ?? 0} / ${s.by_split.val?.total ?? 0}`} hint={t("separados por sessão (sem vazamento)")} />
          <div className="rounded-lg border border-suite-line p-3">
            <p className="text-xs text-ink-muted">{t("Mídia do dataset")}</p>
            <p className="display mt-1 text-xl">{bytes(s.media_bytes)}</p>
            <div className="mt-2 h-1.5 rounded-full bg-suite-line"><div className="h-1.5 rounded-full bg-take" style={{ width: `${useBar}%` }} /></div>
            <p className="mt-1 text-[11px] text-ink-faint">{t("cota {bytes} · retenção em Configurações da IA", { bytes: bytes(s.quota_bytes) })}</p>
          </div>
        </div>
      )}
      {s && (
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-suite-line p-3 text-sm">
            <p className="mb-2 font-medium">{t("Por motivo de seleção")}</p>
            <ul className="space-y-1">{Object.entries(s.by_stratum).map(([k, v]) => (
              <li key={k} className="flex justify-between"><span>{tax.strata[k] ?? k}</span><span className="text-ink-muted">{v.reviewed}/{v.total}</span></li>
            ))}</ul>
          </div>
          <div className="rounded-lg border border-suite-line p-3 text-sm">
            <p className="mb-2 font-medium">{t("Avaliações por nota")}</p>
            <ul className="space-y-1">{Object.entries(tax.ratings).map(([k, label]) => (
              <li key={k} className="flex justify-between"><span>{label}</span><span className="text-ink-muted">{s.by_rating[k] ?? 0}</span></li>
            ))}</ul>
          </div>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <a className="btn-quiet" href="/api/review/export?format=jsonl"><Download className="h-4 w-4" /> {t("Exportar JSONL (avaliados)")}</a>
        <a className="btn-quiet" href="/api/review/export?format=csv"><Download className="h-4 w-4" /> {t("Exportar CSV de atributos")}</a>
        <a className="btn-quiet" href="/api/review/export?format=jsonl&split=val">{t("Só validação")}</a>
        <button className="btn-quiet" disabled={backfill.isPending} onClick={() => backfill.mutate()}><Flag className="h-4 w-4" /> {t("Incluir cortes antigos")}</button>
        {backfill.data && <span className="self-center text-xs text-ink-muted">{backfill.data.created} {t("cortes incluídos")}</span>}
      </div>

      <div className="rounded-lg border border-suite-line p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-medium">{t("Classificador de ranking (aprende com as suas avaliações)")}</p>
          <button className="btn-take" disabled={train.isPending} onClick={() => train.mutate()}>{t("Treinar nova versão")}</button>
        </div>
        {rk.data && (
          <p className="mb-3 text-xs text-ink-muted">
            {t("Modo atual:")} <b>{rk.data.mode === "shadow" ? t("sombra (calcula e mostra, não decide)") : rk.data.mode === "active" ? "ativo" : "desligado"}</b>{t(". Exemplos rotulados: treino {v} ({v2} positivos), validação {v3} ({v4} positivos). Mínimo: {min_train} no treino com ≥{min_per_class} de cada classe e {min_val} na validação.", { v: rk.data.labelled.train?.n ?? 0, v2: rk.data.labelled.train?.pos ?? 0, v3: rk.data.labelled.val?.n ?? 0, v4: rk.data.labelled.val?.pos ?? 0, min_train: rk.data.requirements.min_train, min_per_class: rk.data.requirements.min_per_class, min_val: rk.data.requirements.min_val })}
          </p>
        )}
        <ErrorNote error={train.error ?? setStatus.error} />
        <div className="table-scroll" role="region" tabIndex={0} aria-label={t("Rankers do dataset")}><table className="w-full min-w-[28rem] text-sm">
          <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">{t("Versão")}</th><th>{t("Estado")}</th><th>{t("AUC val. (modelo / heurística)")}</th><th>{t("P@5 (modelo / heurística)")}</th><th>Treino/val</th><th /></tr></thead>
          <tbody>
            {(rk.data?.models ?? []).map((m) => (
              <tr key={m.id} className="border-t border-suite-line/50">
                <td className="py-1">{m.name}</td><td>{m.status}</td>
                <td>{fmt(m.metrics.ranker?.auc, 3)} / {fmt(m.metrics.heuristic?.auc, 3)} {m.metrics.beats_heuristic ? <span className="text-ok">{t("melhor")}</span> : <span className="text-ink-faint">{t("não superou")}</span>}</td>
                <td>{pct(m.metrics.ranker?.p_at_5)} / {pct(m.metrics.heuristic?.p_at_5)}</td>
                <td>{m.n_train}/{m.n_val}</td>
                <td className="space-x-1 text-right">
                  {m.status !== "shadow" && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => setStatus.mutate({ id: m.id, status: "shadow" })}>{t("Sombra")}</button>}
                  {m.status !== "active" && m.metrics.beats_heuristic && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => setStatus.mutate({ id: m.id, status: "active" })}>{t("Ativar")}</button>}
                  {m.status !== "archived" && <button className="btn-quiet px-2 py-1 text-xs" onClick={() => setStatus.mutate({ id: m.id, status: "archived" })}>{t("Arquivar")}</button>}
                </td>
              </tr>
            ))}
            {(rk.data?.models ?? []).length === 0 && <tr><td colSpan={6} className="py-2 text-xs text-ink-faint">{t("Nenhum modelo treinado ainda. Avalie exemplos primeiro.")}</td></tr>}
          </tbody>
        </table></div>
      </div>
    </div>
  );
}


// ------------------------------------------------------------------ continuous learning
type Group = { value: string; n: number; approved: number; rate: number | null; ci95: [number, number] | null; reliable: boolean };
type Learning = {
  status: { human_labels: number; labels_last_7d: number; active_model: string; shadow_model: string | null;
    last_run: { at: string; outcome: string; detail: string; labels: number } | null };
  profile: { labels: number; approved: number; rejected: number; note: string; approval_rate: number | null;
    approval_ci95: [number, number] | null; by_source: Record<string, number>; by_signal: Group[]; by_streamer: Group[];
    by_situation: Group[]; duration_median: { approved: number | null; rejected: number | null };
    current_score_auc: number | null; start_delta_median: number | null; rejection_reasons: Record<string, number>;
    recurring_problems: string[]; diversity: string | null; sufficient_for_training: boolean };
};
const outcomeLabel: Record<string, string> = {
  profile_only: t("perfil atualizado (sem treino)"), trained_shadow: t("treinado, em sombra"), promoted: t("treinado e ativado"),
};

function GroupTable({ title, rows }: { title: string; rows: Group[] }) {
  if (!rows.length) return null;
  return (
    <div className="rounded-lg border border-suite-line p-3">
      <p className="mb-2 text-sm font-medium">{title}</p>
      <div className="table-scroll" role="region" tabIndex={0} aria-label={t("Grupos do dataset")}><table className="w-full min-w-[28rem] text-sm">
        <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">{t("Grupo")}</th><th>{t("Rótulos")}</th><th>{t("Aprovação")}</th><th>{t("IC 95%")}</th></tr></thead>
        <tbody>{rows.slice(0, 10).map((g) => (
          <tr key={g.value} className={`border-t border-suite-line/50 ${g.reliable ? "" : "text-ink-muted"}`}>
            <td className="py-1">{g.value}</td><td>{g.n}</td><td>{pct(g.rate)}</td>
            <td className="text-xs">{g.ci95 ? `${Math.round(g.ci95[0] * 100)}–${Math.round(g.ci95[1] * 100)}%` : "—"}{!g.reliable && " · poucos dados"}</td>
          </tr>))}</tbody>
      </table></div>
    </div>
  );
}

function LearningPanel() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["learning"], queryFn: () => api.get<Learning>("/api/review/learning"), refetchInterval: 30_000 });
  const backfill = useMutation({ mutationFn: () => api.post<{ labels: number }>("/api/review/learning/backfill"), onSuccess: () => qc.invalidateQueries({ queryKey: ["learning"] }) });
  const run = useMutation({ mutationFn: () => api.post("/api/review/learning/run"), onSuccess: () => qc.invalidateQueries({ queryKey: ["learning"] }) });
  const d = q.data;
  if (!d) return <ErrorNote error={q.error} />;
  const p = d.profile;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-4">
        <Stat label={t("Avaliações humanas")} value={String(d.status.human_labels)} hint={t("{labels_last_7d} nos últimos 7 dias · aprovar/rejeitar nos cortes também conta", { labels_last_7d: d.status.labels_last_7d })} />
        <Stat label={t("Modelo ativo")} value={d.status.active_model.replace(" (nenhum classificador ativo)", "")} hint={d.status.shadow_model ? t("em sombra: {model}", { model: d.status.shadow_model }) : t("nenhum modelo em sombra")} />
        <Stat label={t("Último aprendizado")} value={d.status.last_run ? ago(d.status.last_run.at) : "nunca"} hint={d.status.last_run ? t("{v} · {labels} rótulos", { v: outcomeLabel[d.status.last_run.outcome] ?? d.status.last_run.outcome, labels: d.status.last_run.labels }) : ""} />
        <Stat label={t("Taxa de aprovação")} value={pct(p.approval_rate)} hint={p.approval_ci95 ? `IC 95%: ${Math.round(p.approval_ci95[0] * 100)}–${Math.round(p.approval_ci95[1] * 100)}%` : ""} />
      </div>
      <p className={`rounded-md border px-3 py-2 text-sm ${p.labels < 30 ? "border-take/50 bg-take-dim text-take" : "border-suite-line text-ink-muted"}`}>
        {t("{labels} rótulos ({approved} aprovados, {rejected} rejeitados) — {note}.", { labels: p.labels, approved: p.approved, rejected: p.rejected, note: p.note })}
        {d.status.last_run?.detail && <span className="block text-xs opacity-80">{t("Último treino:")} {d.status.last_run.detail}</span>}
      </p>
      <div className="flex flex-wrap gap-2">
        <button className="btn-quiet" disabled={backfill.isPending} onClick={() => backfill.mutate()}>{t("Importar aprovações/rejeições anteriores")}</button>
        <button className="btn-quiet" disabled={run.isPending} onClick={() => run.mutate()}>{t("Rodar aprendizado agora (baixa prioridade)")}</button>
        {backfill.data && <span className="self-center text-xs text-ink-muted">{backfill.data.labels} {t("decisões importadas")}</span>}
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label={t("A nota atual separa bons de ruins?")} value={p.current_score_auc === null ? t("n/d") : `AUC ${p.current_score_auc}`} hint={t("0,5 = acaso · 1,0 = perfeito (só rótulos humanos)")} />
        <Stat label={t("Duração mediana")} value={`${fmt(p.duration_median.approved)} / ${fmt(p.duration_median.rejected)} s`} hint={t("aprovados / rejeitados")} />
        <Stat label={t("Início da IA vs. o seu")} value={p.start_delta_median === null ? t("n/d") : `${p.start_delta_median > 0 ? "+" : ""}${p.start_delta_median} s`} hint={t("positivo = a IA começou depois do ideal")} />
      </div>
      {(p.recurring_problems.length > 0 || p.diversity) && (
        <div className="rounded-lg border border-take/40 p-3 text-sm">
          <p className="mb-1 font-medium">{t("Padrões que o diretor está corrigindo")}</p>
          <ul className="list-inside list-disc text-ink-muted">{p.recurring_problems.map((r, i) => <li key={i}>{r}</li>)}{p.diversity && <li>{p.diversity}</li>}</ul>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        <GroupTable title={t("Por sinal detectado")} rows={p.by_signal} />
        <GroupTable title={t("Por streamer")} rows={p.by_streamer} />
        <GroupTable title={t("Por situação narrativa")} rows={p.by_situation} />
      </div>
      <ErrorNote error={backfill.error ?? run.error} />
    </div>
  );
}
