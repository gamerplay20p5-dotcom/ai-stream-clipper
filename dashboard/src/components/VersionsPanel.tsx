import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api } from "../api";
import { ClipVideo, Disclosure, Empty, ErrorNote, Notice, Skeleton, Status } from "./ui";
import { t } from "../i18n";

type Seg = { a: number; b: number; role: string };
type Metrics = { duration: number; time_to_peak: number; teaser: boolean; dead_s: number; dead_ratio: number; cuts: number;
  reactions_kept: boolean; chronological: boolean; words_kept: number; words_in_span: number;
  words_dropped_vs_original?: number; words_original?: number;
  reactions_original?: number; reactions_preserved?: number; reactions_dropped_by_bounds?: number; reactions_dropped_inside?: number;
  words_cut_at_boundaries?: number; context_lost_before_s?: number; context_lost_after_s?: number; context_lost_text?: string };
type Variant = { structure: string; label: string; reason?: string; metrics?: Metrics; original_metrics?: Metrics;
  state?: string; error?: string; segments: Seg[]; video: boolean; manual?: boolean };
type Overview = { main: { structure: string; label: string; segments: Seg[]; video: boolean; version: number };
  variants: Record<string, Variant>;
  comparisons: { id: number; left_structure: string; right_structure: string; winner: string; blind: boolean; reviewer: string; at: string }[] };
type Proposal = { structure: string; label: string; reason: string; applicable: boolean; why_not: string; metrics: Metrics; meaningful: boolean };

const LABEL: Record<string, string> = { O: t("Original"), A: t("Gancho imediato"), B: t("Suspense narrativo"), C: t("Humor de reação"), D: t("História completa"), E: t("Momento rápido") };

function Timeline({ segs, lo, hi }: { segs: Seg[]; lo: number; hi: number }) {
  const span = Math.max(1, hi - lo);
  return (
    <div className="relative h-3 w-full rounded bg-suite-line/40" aria-label={t("Trechos mantidos")}>
      {segs.map((s, i) => (
        <div key={i} title={`${s.role}: ${s.a.toFixed(1)}–${s.b.toFixed(1)}s`}
          className={`absolute top-0 h-3 rounded ${s.role === "teaser" ? "bg-tally" : s.role === "intro" ? "bg-ok" : "bg-take"}`}
          style={{ left: `${((s.a - lo) / span) * 100}%`, width: `${Math.max(0.5, ((s.b - s.a) / span) * 100)}%` }} />
      ))}
    </div>
  );
}

function MetricRows({ m }: { m?: Metrics }) {
  if (!m) return <p className="text-xs text-ink-muted">{t("sem métricas")}</p>;
  return (
    <ul className="text-xs text-ink-muted">
      <li>{t("Duração:")} <b className="text-ink">{m.duration.toFixed(1)}{t("s")}</b> {t("· {cuts} corte(s)", { cuts: m.cuts })}{m.teaser ? t(" · com prévia") : ""}</li>
      <li>{t("Ponto alto aos")} <b className="text-ink">{m.time_to_peak.toFixed(1)}{t("s")}</b></li>
      <li>{t("Tempo sem fala: {toFixed}s ({round}%)", { toFixed: m.dead_s.toFixed(0), round: Math.round(m.dead_ratio * 100) })}</li>
      <li>{m.reactions_original === undefined ? (m.reactions_kept ? t("Reações preservadas") : t("ATENÇÃO: reação removida"))
        : m.reactions_original === 0 ? t("Nenhuma reação sonora detectada no original (nada a medir)")
        : <span className={m.reactions_kept ? "" : "text-tally"}>{t("Reações: {reactions_preserved} de {reactions_original} do original{v}", { reactions_preserved: m.reactions_preserved, reactions_original: m.reactions_original, v: m.reactions_dropped_by_bounds ? t(" · {reactions_dropped_by_bounds} ficaram fora do novo início/fim", { reactions_dropped_by_bounds: m.reactions_dropped_by_bounds }) : "" })}
            {m.reactions_dropped_inside ? t(" · {reactions_dropped_inside} removidas no corte de pausas", { reactions_dropped_inside: m.reactions_dropped_inside }) : ""}</span>}
        {" · "}{m.chronological ? "ordem real" : "ordem alterada"}</li>
      {(m.context_lost_before_s || m.context_lost_after_s) ? (
        <li className="text-tally">{t("Contexto fora: {v}{v2}", { v: m.context_lost_before_s ? t("{toFixed}s antes", { toFixed: m.context_lost_before_s.toFixed(0) }) : "", v2: m.context_lost_after_s ? t(" {toFixed}s depois", { toFixed: m.context_lost_after_s.toFixed(0) }) : "" })}
          {m.context_lost_text ? <> — “{m.context_lost_text}…”</> : null}</li>) : null}
      {m.words_cut_at_boundaries ? <li className="text-tally">{m.words_cut_at_boundaries} {t("palavra(s) cortada(s) numa transição")}</li> : null}
      <li>{t("Falas mantidas: {words_kept}/", { words_kept: m.words_kept })}{m.words_in_span}{m.words_original ? t(" · {v} de {words_original} palavras do original ficaram de fora", { v: m.words_dropped_vs_original ?? 0, words_original: m.words_original }) : ""}</li>
    </ul>
  );
}

export default function VersionsPanel({ clipId, version, onChanged }: { clipId: number; version: number; onChanged: () => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["variants", clipId], queryFn: () => api.get<Overview>(`/api/clips/${clipId}/variants`),
    refetchInterval: (x) => (Object.values(x.state.data?.variants ?? {}).some((v) => v.state === "queued") ? 5000 : false) });
  const props = useQuery({ queryKey: ["structures", clipId], queryFn: () => api.get<Proposal[]>(`/api/clips/${clipId}/structures`), enabled: false });
  const [key, setKey] = useState("auto");
  const [teaser, setTeaser] = useState(false);
  const [blind, setBlind] = useState(true);
  const [flip, setFlip] = useState(() => Math.random() < 0.5);
  const [pick, setPick] = useState<string | null>(null);
  const [voted, setVoted] = useState<{ left?: string; right?: string } | null>(null);
  const done = () => { qc.invalidateQueries({ queryKey: ["variants", clipId] }); onChanged(); };
  const restructure = useMutation({ mutationFn: () => api.post(`/api/clips/${clipId}/restructure`, { structure: key, teaser }), onSuccess: () => { setVoted(null); done(); } });
  const promote = useMutation({
    mutationFn: (n: string) => api.post<{ promotion?: { note?: string } }>(`/api/clips/${clipId}/variants/${n}/promote`),
    onSuccess: (d) => { if (d.promotion?.note) window.alert(d.promotion.note); done(); },
  });
  const vote = useMutation({
    mutationFn: (w: "left" | "right" | "equal") => api.post<{ winner: string }>(`/api/clips/${clipId}/compare`,
      { left: flip ? alt : "main", right: flip ? "main" : alt, winner: w, blind }),
    onSuccess: () => { setVoted({ left: name(sides[0]), right: name(sides[1]) }); done(); },
  });
  const v = q.data;
  const ready = v ? ["format", "edit", "original"]
    .filter((n) => v.variants[n]?.video) : [];
  const altName = pick && ready.includes(pick) ? pick : ready[0];
  const alt = altName ?? "edit";
  const all = useMemo(() => (v ? [v.main.segments, ...Object.values(v.variants).map((x) => x.segments)].flat() : []), [v]);
  if (!v) return q.isLoading ? <Skeleton label={t("Carregando versões")} /> : <ErrorNote error={q.error} />;
  const lo = Math.min(...all.map((s) => s.a)), hi = Math.max(...all.map((s) => s.b));
  const sides = flip ? [alt, "main"] : ["main", alt];
  const src = (n: string) => (n === "main" ? `/api/clips/${clipId}/video?v=${version}` : `/api/clips/${clipId}/variants/${n}/video?v=${version}-${v.variants[n]?.state}`);
  const name = (n: string) => (n === "main" ? v.main.label || LABEL[v.main.structure] || t("Principal") : v.variants[n]?.label || n);

  return (
    <section className="space-y-4">
      <section className="panel" aria-labelledby="comparison-heading">
        <header className="panel-heading">
          <h2 id="comparison-heading" className="display text-lg">{t("Comparação de versões")}</h2>
          {altName && <Status tone={blind ? "accent" : "neutral"}>{blind ? t("Modo cego") : t("Identificação visível")}</Status>}
        </header>
        <div className="panel-body space-y-4">
          {!altName ? <Empty title={t("Nenhuma variante pronta")}>{t("Gere uma variante abaixo para compará-la ao vídeo principal.")}</Empty> : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
                {ready.length > 1 && (
                  <select className="field w-auto" value={alt} aria-label={t("Variante a comparar com o principal")} disabled={vote.isPending}
                    onChange={(e) => { setPick(e.target.value); setVoted(null); setFlip(Math.random() < 0.5); }}>
                    {ready.map((n) => <option key={n} value={n}>{name(n)}</option>)}
                  </select>
                )}
                <label className="flex min-h-9 items-center gap-2 text-sm">
                  <input type="checkbox" checked={blind} disabled={vote.isPending}
                    onChange={(e) => { setBlind(e.target.checked); setVoted(null); setFlip(Math.random() < 0.5); }} />
                  {t("Comparar às cegas")}
                </label>
              </div>
              <p className="text-sm text-ink-muted">{t("Assista aos dois vídeos e registre sua preferência. A avaliação de melhora considera os votos em modo cego.")}</p>
              <div className="grid grid-cols-2 gap-2.5 sm:gap-4">
                {sides.map((n, index) => {
                  const title = blind && !voted ? t("Versão {v}", { v: index + 1 }) : name(n);
                  return (
                    <figure key={`${n}-${index}`} className="min-w-0 space-y-2">
                      <figcaption className="text-sm font-semibold">{title}</figcaption>
                      <ClipVideo aria-label={title} preload="none" src={src(n)} />
                    </figure>
                  );
                })}
              </div>
              {voted ? <Notice>{t("Voto registrado. Versão 1 era “{left}” e Versão 2 era “{right}”.", { left: voted.left, right: voted.right })}</Notice> : (
                <div className="flex flex-wrap gap-2" aria-label={t("Voto de comparação")}>
                  <button className="btn-quiet" disabled={vote.isPending} onClick={() => vote.mutate("left")}>{t("Prefiro a versão 1")}</button>
                  <button className="btn-quiet" disabled={vote.isPending} onClick={() => vote.mutate("right")}>{t("Prefiro a versão 2")}</button>
                  <button className="btn-quiet" disabled={vote.isPending} onClick={() => vote.mutate("equal")}>{t("Empate")}</button>
                </div>
              )}
            </>
          )}
        </div>
      </section>

      <Disclosure title={t("Linha do tempo, métricas e promoção")}>
      <div className="space-y-3">
        <div>
          <div className="mb-1 flex justify-between text-xs"><b>{t("Principal:")} {LABEL[v.main.structure] ?? v.main.structure}</b></div>
          <Timeline segs={v.main.segments} lo={lo} hi={hi} />
        </div>
        {Object.entries(v.variants).map(([n, x]) => (
          <div key={n} className="rounded-md border border-suite-line p-2">
            <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-xs">
              <b>{t("Variante:")} {x.label ?? LABEL[x.structure]}</b>
              <span className="text-ink-muted">{x.state === "queued" ? t("Renderizando…") : x.state === "failed" ? t("Falhou") : x.state === "done" ? t("Pronta") : x.state}</span>
            </div>
            {x.state === "failed" && <ErrorNote error={x.error} />}
            <Timeline segs={x.segments} lo={lo} hi={hi} />
            {x.reason && <p className="mt-1 text-xs text-ink-muted">{t("Por quê:")} {x.reason}</p>}
            <div className="mt-2 grid gap-2 sm:grid-cols-2">
              <div><p className="text-[11px] font-semibold">{t("Esta variante")}</p><MetricRows m={x.metrics} /></div>
              {x.original_metrics && <div><p className="text-[11px] font-semibold">{t("Original")}</p><MetricRows m={x.original_metrics} /></div>}
            </div>
            {x.state === "done" && (
              <button className="btn-quiet mt-2 px-2 py-1 text-xs" disabled={promote.isPending}
                onClick={() => window.confirm(t("Usar esta variante como vídeo principal? O vídeo atual fica guardado como variante.")) && promote.mutate(n)}>
                {t("Usar como vídeo principal")}
              </button>
            )}
          </div>
        ))}
      </div>
      </Disclosure>

      <section className="panel panel-body">
        <h2 className="display mb-2 text-lg">{t("Gerar variante")}</h2>
        <p className="text-sm text-ink-muted">{t("A reedição usa trechos da live e preserva o vídeo principal até você promover uma variante.")}</p>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <select className="field w-auto py-1 text-sm" value={key} onChange={(e) => setKey(e.target.value)} aria-label={t("Estrutura")}>
          <option value="auto">{t("Estrutura automática")}</option>
          {["A", "B", "C", "D", "E"].map((k) => <option key={k} value={k}>{k} · {LABEL[k]}</option>)}
        </select>
        <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={teaser} onChange={(e) => setTeaser(e.target.checked)} />
          {t("prévia do clímax no início (marcada como “Daqui a pouco…”)")}</label>
        <button className="btn-take px-3 py-1 text-sm" disabled={restructure.isPending} onClick={() => restructure.mutate()}>{restructure.isPending ? t("Solicitando…") : t("Gerar variante")}</button>
        <button className="btn-quiet px-2 py-1 text-xs" onClick={() => props.refetch()}>{t("Ver opções")}</button>
      </div>
      {props.data && (
        <ul className="mt-2 space-y-1 text-xs">
          {props.data.map((p) => (
            <li key={p.structure} className={p.applicable ? "" : "text-ink-faint"}>
              <b>{p.structure} · {p.label}</b>: {p.applicable
                ? t("{toFixed}s, ponto alto aos {toFixed2}s, {round}% sem fala{v}", { toFixed: p.metrics.duration.toFixed(0), toFixed2: p.metrics.time_to_peak.toFixed(0), round: Math.round(p.metrics.dead_ratio * 100), v: p.meaningful ? "" : t(" (quase igual ao original)") })
                : t("não aplicável — {why_not}", { why_not: p.why_not })}
            </li>
          ))}
        </ul>
      )}
      <ErrorNote error={q.error || props.error || restructure.error || promote.error || vote.error} />
      {restructure.isSuccess && <Notice>{t("Renderização da variante solicitada.")}</Notice>}
      {v.comparisons.length > 0 && (
        <p className="mt-2 text-xs text-ink-muted">{t("Comparações deste corte:")} {v.comparisons.map((c) =>
          `${LABEL[c.left_structure] ?? c.left_structure} × ${LABEL[c.right_structure] ?? c.right_structure}: ${c.winner === "equal" ? "empate" : (c.winner === "left" ? LABEL[c.left_structure] : LABEL[c.right_structure]) ?? c.winner}${c.blind ? " (cega)" : ""}`).join("; ")}</p>
      )}
      </section>
    </section>
  );
}
