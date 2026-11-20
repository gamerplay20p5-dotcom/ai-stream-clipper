import { AlertCircle, ChevronDown, Film, Languages, Loader2, Play } from "lucide-react";
import { useId, useRef, type ReactNode, type VideoHTMLAttributes } from "react";
import type { Clip } from "../api";
import { seconds, statusLabel } from "../lib/format";
import { lang, languages, setLang, t, type Lang } from "../i18n";

export function Status({ tone = "neutral", children }: {
  tone?: "neutral" | "accent" | "success" | "danger" | "warning" | "processing";
  children: ReactNode;
}) {
  const tones = {
    neutral: "bg-suite-raised text-ink-muted",
    accent: "bg-suite-raised text-ink",
    success: "bg-ok/10 text-ok",
    danger: "bg-tally-dim text-tally",
    warning: "bg-warning-dim text-warning",
    processing: "bg-processing-dim text-processing",
  };
  return <span className={`inline-flex max-w-full items-center gap-1.5 rounded px-2 py-0.5 text-xs font-semibold ${tones[tone]}`}>{children}</span>;
}

export function Tally({ live, label }: { live: boolean; label?: string }) {
  return (
    <Status tone={live ? "danger" : "neutral"}>
      <span className={`size-1.5 shrink-0 rounded-full ${live ? "bg-tally" : "bg-ink-faint"}`} aria-hidden />
      {label ?? (live ? t("Ao vivo") : t("Offline"))}
    </Status>
  );
}

export function ScoreMark({ score, size = "md" }: { score: number; size?: "md" | "lg" }) {
  return (
    <span title={t("Pontuação heurística {toFixed} de 100", { toFixed: score.toFixed(1) })}
      aria-label={t("Pontuação {toFixed} de 100", { toFixed: score.toFixed(1) })}
      className={`inline-flex shrink-0 items-center rounded-md bg-suite-media/95 font-mono font-semibold text-ink ${size === "lg" ? "gap-1 px-2.5 py-2 text-lg" : "gap-0.5 px-1.5 text-xs"}`}>
      {Math.round(score)}<span className="text-[10px] text-ink-muted" aria-hidden>/100</span>
    </span>
  );
}

export function Meter({ value, max = 10, tone = "take", label = t("Medição") }: {
  value: number | null; max?: number; tone?: "take" | "ink" | "tally"; label?: string;
}) {
  if (value === null || value === undefined) return <div className="h-1.5 w-full rounded-full bg-suite-line/50" />;
  const current = Math.max(0, Math.min(max, value));
  const color = tone === "take" ? "bg-take" : tone === "tally" ? "bg-tally" : "bg-ink-muted";
  return (
    <div className="h-1.5 w-full rounded-full bg-suite-line" role="meter" aria-label={label} aria-valuenow={current} aria-valuemin={0} aria-valuemax={max}>
      <div className={`h-1.5 rounded-full transition-[width] duration-200 ${color}`} style={{ width: `${(current / max) * 100}%` }} />
    </div>
  );
}

export function ClipStatus({ status }: { status: string }) {
  const busy = ["rendering", "analyzing", "candidate", "waiting"].includes(status);
  const tone = busy ? "processing" : status === "ready" ? "accent" : status === "approved" ? "success" : status === "failed" ? "danger" : "neutral";
  return (
    <Status tone={tone}>
      {busy && <Loader2 className="size-3 shrink-0 motion-safe:animate-spin" aria-hidden />}
      {statusLabel[status] ?? status}
    </Status>
  );
}

export function ClipFrame({ clip, onOpen, selected }: { clip: Clip; onOpen: () => void; selected?: boolean }) {
  const title = clip.title || t("Sem título");
  return (
    <button type="button" onClick={onOpen} aria-current={selected ? "true" : undefined}
      className={`group block w-full min-w-0 rounded-lg text-left ${selected ? "ring-1 ring-take" : ""}`}>
      <span className="relative grid aspect-short max-h-72 w-full place-items-center overflow-hidden rounded-lg border border-suite-line bg-suite-media transition-colors group-hover:border-suite-outline">
        {clip.has_thumbnail ? (
          <img src={`/api/clips/${clip.id}/thumbnail?v=${clip.version}`} alt="" loading="lazy"
            className={`absolute inset-0 size-full object-cover ${clip.status === "rejected" ? "opacity-60" : ""}`} />
        ) : <Film className="size-7 text-ink-faint" aria-hidden />}
        <span className="absolute left-2 top-2 text-xs font-semibold">
          {clip.is_test ? <Status tone="warning">{t("Teste manual")}</Status> : <ScoreMark score={clip.score} />}
        </span>
        <span className="pointer-events-none absolute grid size-10 place-items-center rounded-full bg-suite-media/90 text-ink opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
          <Play size={16} fill="currentColor" aria-hidden />
        </span>
        <span className="absolute bottom-2 right-2">
          <span className="rounded bg-suite-media/95 px-1.5 py-0.5 font-mono text-xs text-ink">{seconds(clip.duration)}</span>
        </span>
      </span>
      <span className="block pb-1 pt-2.5 group-hover:text-take">
        <span className="line-clamp-2 text-sm font-semibold leading-tight" title={title}>{title}</span>
        {clip.streamer && <span className="mt-1 block truncate text-xs text-ink-muted">{clip.streamer.display_name}</span>}
      </span>
    </button>
  );
}

export function ClipVideo({ className = "", ...props }: VideoHTMLAttributes<HTMLVideoElement>) {
  return (
    <div className={`media-stage ${className}`}>
      <video controls playsInline preload="metadata" className="media-video" {...props} />
    </div>
  );
}

export function PageHeader({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <header className="page-heading">
      <div className="min-w-0">
        <h1 className="display text-2xl sm:text-3xl">{title}</h1>
        {description && <p className="mt-2 max-w-[66ch] text-sm text-ink-muted">{description}</p>}
      </div>
      {children}
    </header>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-state">
      <p className="font-semibold">{title}</p>
      {children && <div className="max-w-md text-sm text-ink-muted">{children}</div>}
    </div>
  );
}

export function Skeleton({ label = t("Carregando"), lines = 3 }: { label?: string; lines?: number }) {
  return (
    <div className="panel panel-body min-h-28 space-y-3" role="status">
      <span className="sr-only">{label}</span>
      <div className="loading-shape h-4 w-2/5" aria-hidden />
      {Array.from({ length: lines }, (_, index) => <div className="loading-shape h-3 w-full opacity-65" key={index} aria-hidden />)}
    </div>
  );
}

export function Notice({ tone = "success", children }: { tone?: "success" | "danger"; children: ReactNode }) {
  return (
    <div role={tone === "danger" ? "alert" : "status"}
      className={`notice break-words ${tone === "danger" ? "border-tally/40 bg-tally-dim text-tally" : "border-ok/40 bg-ok/10 text-ok"}`}>
      {tone === "danger" && <AlertCircle size={16} className="mt-0.5 shrink-0" aria-hidden />}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <Notice tone="danger">{error instanceof Error ? error.message : String(error)}</Notice>;
}

export function Disclosure({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="disclosure">
      <summary>{title}<ChevronDown size={17} className="shrink-0 text-ink-muted" aria-hidden /></summary>
      <div className="panel-body">{children}</div>
    </details>
  );
}

/** Opens a dialog and runs `onConfirm` only after the explicit second click. Backwards compatible defaults. */
export function ConfirmAction({ title, description, confirmLabel, pending, disabled, tone = "danger", onConfirm, children, label }: {
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pending?: boolean;
  disabled?: boolean;
  tone?: "danger" | "quiet";
  onConfirm: () => void;
  children: ReactNode;
  /** Accessible name of the trigger when its visible text is not specific enough (e.g. several "Revogar" in a list). */
  label?: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const id = useId();
  return (
    <>
      <button type="button" className={tone === "danger" ? "btn-danger" : "btn-quiet"} disabled={pending || disabled} aria-label={label}
        onClick={() => dialog.current?.showModal()}>
        {children}
      </button>
      <dialog ref={dialog} className="confirmation-modal" aria-labelledby={id} aria-describedby={id + "-description"}>
        <h2 id={id} className="display text-xl">{title}</h2>
        <div id={id + "-description"} className="mt-2 space-y-2 text-sm text-ink-muted">{description}</div>
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={() => dialog.current?.close()}>{t("Voltar")}</button>
          <button type="button" className={tone === "danger" ? "btn-danger" : "btn-take"} disabled={pending} onClick={() => {
            dialog.current?.close();
            onConfirm();
          }}>{confirmLabel}</button>
        </div>
      </dialog>
    </>
  );
}

const decisionTone = { sugestao: "decision-suggestion", confirmado: "decision-confirmed", automatico: "decision-auto" } as const;
/** Suggestion, confirmed or automatic: always icon shape + text, never colour alone. */
export function DecisionChip({ kind, label }: { kind: "sugestao" | "confirmado" | "automatico"; label: string }) {
  return <span className={`decision-chip ${decisionTone[kind]}`}><span className="decision-mark" aria-hidden />{label}</span>;
}

/** Interface language. The label is bilingual so anyone can find it; changing it reloads the page. */
export function LanguagePicker({ className = "" }: { className?: string }) {
  return (
    <label className={`inline-flex items-center gap-1.5 text-xs text-ink-muted ${className}`}>
      <Languages size={14} aria-hidden />
      <span className="sr-only">Idioma / Language</span>
      <select className="field min-h-0 w-auto py-1 text-xs" value={lang} aria-label="Idioma / Language"
        onChange={(e) => setLang(e.target.value as Lang)}>
        {languages.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
      </select>
    </label>
  );
}
