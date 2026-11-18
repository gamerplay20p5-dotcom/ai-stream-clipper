import { Film, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import type { Clip } from "../api";
import { seconds, statusLabel } from "../lib/format";

export function Tally({ live, label }: { live: boolean; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span
        className={`h-2 w-2 rounded-full ${live ? "tally-blink bg-tally" : "bg-ink-faint/60"}`}
        aria-hidden
      />
      <span className={live ? "font-semibold text-tally" : "text-ink-muted"}>{label ?? (live ? "Ao vivo" : "Offline")}</span>
    </span>
  );
}

/** Amber "circled take" score marker. */
export function ScoreMark({ score, size = "md" }: { score: number; size?: "md" | "lg" }) {
  const strong = score >= 70;
  const dims = size === "lg" ? "h-14 w-14 text-xl" : "h-9 w-9 text-sm";
  return (
    <span
      title={`Pontuação heurística ${score.toFixed(1)} de 100`}
      className={`display inline-flex ${dims} items-center justify-center rounded-full ${
        strong ? "bg-take text-take-ink" : "border-2 border-take text-take"
      }`}
    >
      {Math.round(score)}
    </span>
  );
}

export function Meter({ value, max = 10, tone = "take" }: { value: number | null; max?: number; tone?: "take" | "ink" | "tally" }) {
  if (value === null || value === undefined) return <div className="h-1.5 w-full rounded-full bg-suite-line/50" />;
  const pct = Math.max(0, Math.min(100, (value / max) * 100));
  const color = tone === "take" ? "bg-take" : tone === "tally" ? "bg-tally" : "bg-ink-muted";
  return (
    <div className="h-1.5 w-full rounded-full bg-suite-line" role="meter" aria-valuenow={value} aria-valuemax={max}>
      <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

const statusTone: Record<string, string> = {
  ready: "text-take",
  approved: "text-ok",
  rejected: "text-ink-faint",
  failed: "text-tally",
  rendering: "text-ink-muted",
  analyzing: "text-ink-muted",
  candidate: "text-ink-muted",
};

export function ClipStatus({ status }: { status: string }) {
  const busy = status === "rendering" || status === "analyzing" || status === "candidate";
  return (
    <span className={`inline-flex items-center gap-1 text-xs ${statusTone[status] ?? "text-ink-muted"}`}>
      {busy && <Loader2 className="h-3 w-3 animate-spin" aria-hidden />}
      {statusLabel[status] ?? status}
    </span>
  );
}

/** The 9:16 frame - the product's central object. */
export function ClipFrame({ clip, onOpen, selected }: { clip: Clip; onOpen?: () => void; selected?: boolean }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={`group relative block aspect-short w-full overflow-hidden rounded-lg bg-suite-panel text-left ring-offset-2 ring-offset-suite transition ${
        selected ? "ring-2 ring-take" : "hover:ring-1 hover:ring-ink-faint"
      }`}
    >
      {clip.has_thumbnail ? (
        <img
          src={`/api/clips/${clip.id}/thumbnail?v=${clip.version}`}
          alt=""
          loading="lazy"
          className={`h-full w-full object-cover ${clip.status === "rejected" ? "opacity-40 grayscale" : ""}`}
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-ink-faint">
          <Film className="h-8 w-8" aria-hidden />
        </div>
      )}
      <span className="absolute right-2 top-2">
        <ScoreMark score={clip.score} />
      </span>
      <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/50 to-transparent p-2.5 pt-10">
        <p className="line-clamp-2 text-sm font-medium leading-snug text-white">{clip.title || "Sem título"}</p>
        <div className="mt-1 flex items-center justify-between gap-2 text-xs text-white/70">
          <span className="truncate">{clip.streamer?.display_name ?? ""}</span>
          <span>{seconds(clip.duration)}</span>
        </div>
      </div>
    </button>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <h1 className="display text-2xl">{title}</h1>
      {children}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-suite-line px-6 py-10 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mx-auto mt-2 max-w-md text-sm text-ink-muted">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="rounded-md border border-tally/40 bg-tally-dim px-3 py-2 text-sm text-[#ffb4b6]">
      {error instanceof Error ? error.message : String(error)}
    </p>
  );
}
