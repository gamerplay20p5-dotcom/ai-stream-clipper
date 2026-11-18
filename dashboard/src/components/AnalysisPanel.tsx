import { AudioLines, Clapperboard, Eye, FileText, Sparkles } from "lucide-react";
import type { Clip, Editorial } from "../api";
import { Meter } from "./ui";
import { seconds, timecode } from "../lib/format";

const sourceIcon: Record<string, typeof Eye> = {
  visual: Eye,
  acoustic: AudioLines,
  audio: AudioLines,
  transcript: FileText,
  narrative: Sparkles,
  heuristic: Clapperboard,
};

const sourceLabel: Record<string, string> = {
  visual: "Visão",
  acoustic: "Som",
  audio: "Áudio",
  transcript: "Fala",
  narrative: "Narrativa",
  heuristic: "Heurística",
};

const layoutLabel: Record<string, string> = {
  gameplay: "gameplay inteira",
  zoom: "gameplay ampliada",
  split: "webcam em cima",
  action: "recorte que acompanha a ação",
};

const timingLabel: Record<string, string> = {
  fetch_s: "busca da mídia",
  heuristic_vision_s: "visão heurística",
  vlm_s: "modelo multimodal",
  narrative_s: "texto narrativo",
  total_s: "total da análise",
};

export function AnalysisStatus({ clip }: { clip: Clip }) {
  const m = clip.analysis_meta;
  if (!m) {
    return <span className="text-xs text-ink-faint">{clip.vision_state === "pending" ? "Análise em andamento" : "Sem análise da Fase 2"}</span>;
  }
  if (m.vision_fallback) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-suite-line px-2 py-0.5 text-xs text-ink-muted" title={m.fallback_reason}>
        <AudioLines className="h-3 w-3" aria-hidden /> Fallback: áudio + heurística
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-take-dim px-2 py-0.5 text-xs text-take">
      <Eye className="h-3 w-3" aria-hidden /> {m.vision_model} em {m.vision_device}
    </span>
  );
}

export default function AnalysisPanel({ clip, onReanalyze, busy }: { clip: Clip; onReanalyze: () => void; busy: boolean }) {
  const m = clip.analysis_meta;
  const d = clip.decision;
  const v = clip.analysis?.visual;
  const n = clip.analysis?.narrative;
  const p = clip.edit_plan;
  const hb = clip.heuristic_bounds;
  const canReanalyze = !["rendering", "analyzing", "candidate"].includes(clip.status);

  return (
    <>
    {d?.editorial && <EditorialBlock e={d.editorial} />}
    <section className="mt-6 border-t border-suite-line pt-5" aria-label="Análise da IA">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Análise da IA</h3>
        <AnalysisStatus clip={clip} />
      </div>

      {!d ? (
        <p className="text-sm text-ink-muted">
          {clip.vision_state === "pending" ? "O diretor ainda está analisando este momento." : "Este corte foi criado antes da Fase 2."}
        </p>
      ) : (
        <div className="space-y-5 text-sm">
          {m?.vision_fallback && m.fallback_reason && (
            <p className="rounded-md bg-suite-panel px-3 py-2 text-xs text-ink-muted">
              Modelo multimodal não usado: {m.fallback_reason}
            </p>
          )}

          {v && !v.fallback && (
            <div>
              <h4 className="mb-1 text-xs text-ink-muted">O que a IA visual viu</h4>
              <p>{v.summary}</p>
              {v.key_event && <p className="mt-1 text-ink-muted">Destaque: {v.key_event}</p>}
              <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
                <div>inesperado <span className="text-ink">{v.unexpected.toFixed(0)}</span></div>
                <div>engraçado <span className="text-ink">{v.funny.toFixed(0)}</span></div>
                <div>ação <span className="text-ink">{v.action.toFixed(0)}</span></div>
                <div>confiança <span className="text-ink">{Math.round(v.confidence * 100)}%</span></div>
                <div>{v.frame_times.length} quadros analisados</div>
              </dl>
              {v.events.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs">
                  {v.events.map((e, i) => (
                    <li key={i}>
                      <span className="text-ink-muted">{timecode(v.window_abs[0] + e.t)}</span> {e.label}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <div>
            <h4 className="mb-1 text-xs text-ink-muted">Decisão do diretor</h4>
            <p>
              {d.mode === "multimodal" ? "Áudio, fala e visão combinados." : "Áudio e fala (sem modelo visual)."}{" "}
              Nota {d.base_score.toFixed(0)} → <span className="text-take">{d.score.toFixed(0)}</span>
              {hb && (
                <>
                  , trecho {timecode(hb[0])}–{timecode(hb[1])} → {timecode(d.start_abs)}–{timecode(d.end_abs)} (
                  {seconds(d.end_abs - d.start_abs)})
                </>
              )}
              .
            </p>
            {d.adjustments.length > 0 && (
              <ul className="mt-2 space-y-1 text-xs">
                {d.adjustments.map((a, i) => <li key={i} className="border-l-2 border-suite-line pl-2 text-ink-muted">{a}</li>)}
              </ul>
            )}
          </div>

          {d.evidence.length > 0 && (
            <div>
              <h4 className="mb-1 text-xs text-ink-muted">Evidências</h4>
              <ul className="space-y-1.5 text-xs">
                {d.evidence.map((e, i) => {
                  const Icon = sourceIcon[e.source] ?? Clapperboard;
                  return (
                    <li key={i} className="flex gap-2">
                      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink-muted" aria-label={sourceLabel[e.source] ?? e.source} />
                      <span>
                        {e.t_abs !== null && <span className="text-ink-muted">{timecode(e.t_abs)} </span>}
                        {e.detail}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {n && !n.fallback && n.justification && (
            <div>
              <h4 className="mb-1 text-xs text-ink-muted">Justificativa editorial ({n.provider.split(":")[1]?.split("@")[0]})</h4>
              <p>{n.justification}</p>
            </div>
          )}

          {p && (
            <div>
              <h4 className="mb-1 text-xs text-ink-muted">Plano de edição ({p.source === "ai" ? "IA" : p.source === "user" ? "seu ajuste" : "heurística"})</h4>
              <p className="text-xs">
                Template {p.template}, {layoutLabel[p.layout] ?? p.layout}
                {p.zooms.length > 0 && `, zoom ${p.zooms[0].scale.toFixed(2)}x no pico`}
                {p.normalize_audio && `, áudio normalizado para ${p.target_lufs} LUFS sem distorção`}
                {p.emphasis_words.length > 0 && `, destaque em: ${p.emphasis_words.slice(0, 5).join(", ")}`}.
              </p>
              {p.rationale.length > 0 && <p className="mt-1 text-xs text-ink-muted">{p.rationale.join(" ")}</p>}
            </div>
          )}

          {m?.timings && (
            <div>
              <h4 className="mb-1 text-xs text-ink-muted">Tempo de processamento</h4>
              <dl className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-xs sm:grid-cols-3">
                {Object.entries(m.timings).map(([k, val]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt className="text-ink-muted">{timingLabel[k] ?? k}</dt>
                    <dd>{seconds(val)}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-1 text-xs text-ink-faint">
                {m.worker && `Worker ${m.worker}. `}
                {m.narrative_provider && `Texto: ${m.narrative_provider}.`}
              </p>
            </div>
          )}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button className="btn-quiet" disabled={!canReanalyze || busy} onClick={onReanalyze}>
          <Sparkles className="h-4 w-4" /> Analisar de novo com a IA
        </button>
        <span className="text-xs text-ink-faint">
          {clip.analysis_runs > 0 ? `${clip.analysis_runs} análise(s). ` : ""}Editar e renderizar de novo não repete a análise.
        </span>
      </div>
    </section>
    </>
  );
}

function EditorialBlock({ e }: { e: Editorial }) {
  const crit = Object.entries(e.criteria);
  return (
    <section className="mt-6 border-t border-suite-line pt-5" aria-label="Julgamento editorial">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Julgamento editorial</h3>
        <span className="text-xs text-ink-muted">
          preliminar {e.preliminary.toFixed(0)}
          {e.final !== null && <> · final <b className="text-ink">{e.final.toFixed(0)}</b></>} · confiança {(e.confidence * 100).toFixed(0)}%
        </span>
      </div>
      <p className="mb-2 text-[11px] text-ink-faint">A confiança mede quantas evidências independentes existem, não a chance de viralizar.</p>
      {e.waiting_context && (
        <p className="mb-2 rounded border border-take/50 bg-take-dim px-2 py-1 text-xs text-take">
          A situação ainda estava em andamento: o corte aguarda o desfecho antes da decisão final.
        </p>
      )}
      {e.story && <p className="mb-2 text-sm"><span className="text-ink-muted">História: </span>{e.story.summary}</p>}
      <dl className="mb-3 space-y-1 text-sm">
        {e.why_selected && <div><dt className="inline text-ink-muted">Por que foi selecionado: </dt><dd className="inline">{e.why_selected}</dd></div>}
        {e.best_part && <div><dt className="inline text-ink-muted">Parte mais interessante: </dt><dd className="inline">{e.best_part}</dd></div>}
        {e.needed_context && <div><dt className="inline text-ink-muted">Contexto: </dt><dd className="inline">{e.needed_context}</dd></div>}
        {e.weaknesses.length > 0 && <div><dt className="inline text-ink-muted">O que enfraquece: </dt><dd className="inline">{e.weaknesses.join("; ")}</dd></div>}
        {e.edit_suggestions.length > 0 && <div><dt className="inline text-ink-muted">Sugestões de edição: </dt><dd className="inline">{e.edit_suggestions.join("; ")}</dd></div>}
        {e.rejection_reason && <div><dt className="inline text-ink-muted">Motivo de rejeição: </dt><dd className="inline">{e.rejection_reason}</dd></div>}
      </dl>
      <details>
        <summary className="cursor-pointer text-xs text-ink-muted">Os 14 critérios e suas evidências</summary>
        <ul className="mt-2 space-y-2">
          {crit.map(([k, c]) => (
            <li key={k} className="text-xs">
              <div className="flex items-center gap-2">
                <span className="w-40 shrink-0 sm:w-56">{c.label}</span>
                <div className="flex-1"><Meter value={c.value} /></div>
                <span className="w-8 text-right">{c.value === null ? "n/d" : c.value.toFixed(0)}</span>
              </div>
              {c.evidence.length > 0 && <p className="mt-0.5 text-[11px] text-ink-faint">{c.evidence.join(" · ")}</p>}
            </li>
          ))}
        </ul>
      </details>
    </section>
  );
}
