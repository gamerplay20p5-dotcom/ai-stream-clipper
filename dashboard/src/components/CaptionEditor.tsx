import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { speakerName } from "./VoicePanel";
import { ErrorNote } from "./ui";
import { t, locale } from "../i18n";

type W = { start: number; end: number; text: string; speaker: string };
type Caps = { clip_id: number; start: number; end: number; override: boolean; edited_by: string | null; edited_at: string | null;
  options: { mode: string; other_color: string; streamer_confirmed: boolean }; words: W[] };
type Line = { start: number; end: number; text: string; speaker: string; words: W[] };

const isOther = (s: string) => s.startsWith("other:");

function toLines(words: W[]): Line[] {
  const out: Line[] = [];
  for (const w of words) {
    const cur = out[out.length - 1];
    if (cur && isOther(cur.speaker) === isOther(w.speaker) && w.start - cur.end <= 0.7 && cur.words.length < 10) {
      cur.words.push(w); cur.end = w.end; cur.text += " " + w.text;
    } else out.push({ start: w.start, end: w.end, text: w.text, speaker: w.speaker, words: [w] });
  }
  return out;
}

/** Rebuild words from an edited line: same word count keeps the original timing, else spread evenly. */
function fromLine(l: Line): W[] {
  const tokens = l.text.split(/\s+/).filter(Boolean);
  if (!tokens.length) return [];
  if (tokens.length === l.words.length) return l.words.map((w, i) => ({ ...w, text: tokens[i], speaker: l.speaker }));
  const span = Math.max(0.3, l.end - l.start) / tokens.length;
  return tokens.map((t, i) => ({ start: +(l.start + i * span).toFixed(3), end: +(l.start + (i + 1) * span).toFixed(3), text: t, speaker: l.speaker }));
}

export default function CaptionEditor({ clipId, videoVersion, onSaved }: { clipId: number; videoVersion: number; onSaved: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["captions", clipId], queryFn: () => api.get<Caps>(`/api/clips/${clipId}/captions`) });
  const [lines, setLines] = useState<Line[] | null>(null);
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => { if (q.data) setLines(toLines(q.data.words)); }, [q.data]);
  useEffect(() => {
    const v = document.querySelector<HTMLVideoElement>("video[data-clip-video]");
    if (!v) return;
    const on = () => setNow(v.currentTime);
    v.addEventListener("timeupdate", on);
    return () => v.removeEventListener("timeupdate", on);
  }, [q.data, videoVersion]);
  const done = () => { qc.invalidateQueries({ queryKey: ["captions", clipId] }); onSaved(); };
  const save = useMutation({ mutationFn: (words: W[]) => api.put(`/api/clips/${clipId}/captions`, { words, rerender: true }), onSuccess: done });
  const reset = useMutation({ mutationFn: () => api.del(`/api/clips/${clipId}/captions`), onSuccess: done });
  const original = useMemo(() => (q.data ? JSON.stringify(toLines(q.data.words).map((l) => [l.text, l.speaker])) : ""), [q.data]);
  if (!q.data || !lines) return <ErrorNote error={q.error} />;
  const c = q.data;
  const dirty = JSON.stringify(lines.map((l) => [l.text, l.speaker])) !== original;
  const speakers = Array.from(new Set(["streamer", "unknown", ...c.words.map((w) => w.speaker).filter(isOther)]));
  const hidden = c.options.mode === "streamer_only" && c.options.streamer_confirmed;
  const live = now === null ? null : lines.find((l) => c.start + now >= l.start - 0.05 && c.start + now <= l.end + 0.3);
  const seek = (t: number) => { const v = document.querySelector<HTMLVideoElement>("video[data-clip-video]"); if (v) v.currentTime = Math.max(0, t - c.start); };

  return (
    <section className="mt-6">
      <h3 className="mb-1 text-sm font-semibold">{t("Legendas")}</h3>
      <p className="mb-2 text-xs text-ink-muted">
        {t("Texto fiel à fala, por quem falou. Corrija palavras ou o locutor e salve: o vídeo é renderizado de novo.")}
        {c.override && c.edited_at && <> {t("Corrigida por {edited_by} em {toLocaleString}.", { edited_by: c.edited_by, toLocaleString: new Date(c.edited_at).toLocaleString(locale) })}</>}
      </p>
      <div className="mb-3 min-h-[44px] rounded-md bg-black/80 p-2 text-center text-sm font-bold uppercase" aria-live="polite">
        {live ? <span style={{ color: isOther(live.speaker) && c.options.mode !== "all" ? c.options.other_color : "#fff",
          fontSize: isOther(live.speaker) && c.options.mode !== "all" ? "0.8em" : undefined }}>
          {hidden && isOther(live.speaker) ? <span className="text-ink-faint normal-case">{t("(outra voz — oculta)")}</span> : live.text}</span>
          : <span className="text-xs font-normal normal-case text-ink-faint">{t("Dê play no vídeo para ver a prévia aqui.")}</span>}
      </div>
      {!lines.length && <p className="text-sm text-ink-muted">{t("Sem fala neste corte.")}</p>}
      <ol className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
        {lines.map((l, i) => (
          <li key={i} className={`grid grid-cols-[3rem_minmax(0,1fr)] sm:grid-cols-[3rem_minmax(0,1fr)_7rem] items-center gap-2 rounded px-1 ${live === l ? "bg-take-dim" : ""}`}>
            <button type="button" className="text-left text-xs text-ink-muted hover:text-ink" onClick={() => seek(l.start)} title={t("Ir para este trecho")}>
              {t("{toFixed}s", { toFixed: Math.max(0, l.start - c.start).toFixed(1) })}
            </button>
            <input className="field py-1 text-sm" value={l.text} aria-label={`Legenda ${i + 1}`}
              onChange={(e) => setLines(lines.map((x, k) => (k === i ? { ...x, text: e.target.value } : x)))} />
            <select className="field col-start-2 py-1 text-xs sm:col-start-auto" value={l.speaker || "unknown"} aria-label={`Locutor ${i + 1}`}
              onChange={(e) => setLines(lines.map((x, k) => (k === i ? { ...x, speaker: e.target.value } : x)))}>
              {speakers.map((s) => <option key={s} value={s}>{speakerName(s)}</option>)}
            </select>
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn-take" disabled={!dirty || save.isPending} onClick={() => save.mutate(lines.flatMap(fromLine))}>
          {save.isPending ? t("Salvando…") : t("Salvar legendas e renderizar")}
        </button>
        {dirty && <button className="btn-quiet" onClick={() => setLines(toLines(c.words))}>{t("Desfazer")}</button>}
        {c.override && (
          <button className="btn-quiet" disabled={reset.isPending}
            onClick={() => window.confirm(t("Voltar para a transcrição automática? A sua correção deixa de ser usada.")) && reset.mutate()}>
            {t("Voltar à transcrição automática")}
          </button>
        )}
      </div>
      <ErrorNote error={save.error || reset.error} />
    </section>
  );
}
