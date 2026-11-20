import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ListVideo, Film, ArrowLeft, Check, ChevronLeft, ChevronRight, ClipboardCheck, Download, Keyboard, Minus, Plus, RotateCcw, SlidersHorizontal, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Clip, type Streamer } from "../api";
import AnalysisPanel, { AnalysisStatus } from "../components/AnalysisPanel";
import PublishPanel from "../components/PublishPanel";
import CaptionEditor from "../components/CaptionEditor";
import BatchBar from "../components/BatchBar";
import VersionsPanel from "../components/VersionsPanel";
import SharePanel from "../components/SharePanel";
import { StorageFlags } from "./Storage";
import { ClipStatus, ClipVideo, ConfirmAction, Disclosure, Empty, ErrorNote, Meter, Notice, PageHeader, ScoreMark, Skeleton, Status } from "../components/ui";
import { dimensionLabel, platformName, seconds, timecode } from "../lib/format";
import { t, tx } from "../i18n";

const tabs = [
  { key: "ready", label: t("Para revisar") },
  { key: "gate:HUMAN_REVIEW", label: t("Revisão humana") },
  { key: "approved", label: t("Aprovados") },
  { key: "candidate,analyzing,rendering,waiting", label: t("Em processamento") },
  { key: "rejected", label: t("Rejeitados") },
  { key: "failed", label: t("Com falha") },
  { key: "", label: t("Todos") },
];

export default function Clips() {
  const [params, setParams] = useSearchParams();
  const { id } = useParams();
  const navigate = useNavigate();
  const status = params.get("status") ?? "ready";
  const sort = params.get("sort") ?? "score";
  const streamer = params.get("streamer") ?? "";
  const qs = new URLSearchParams({ sort, limit: "120" });
  if (status.startsWith("gate:")) { qs.set("gate", status.slice(5)); qs.set("status", "ready"); }
  else if (status) qs.set("status", status);
  if (streamer) qs.set("streamer_id", streamer);

  const list = useQuery({
    queryKey: ["clips", status, sort, streamer],
    queryFn: () => api.get<{ items: Clip[]; total: number }>(`/api/clips?${qs}`),
    refetchInterval: 8_000,
  });
  const streamers = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers") });
  const items = list.data?.items ?? [];
  const selectedId = id ? Number(id) : null;
  const [queueOpen, setQueueOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [picked, setPicked] = useState<number[]>([]);
  const toggle = (cid: number) => setPicked((p) => (p.includes(cid) ? p.filter((x) => x !== cid) : [...p, cid]));

  const open = (cid: number | null) => {
    const search = new URLSearchParams(params);
    if (cid === null) search.delete("aba");
    navigate({ pathname: cid ? `/cortes/${cid}` : "/cortes", search: search.toString() });
  };

  const monitorId = selectedId ?? items[0]?.id;
  function filter(k: string, v: string) {
    const next = new URLSearchParams(params);
    next.delete("aba");
    if (v || k === "status") next.set(k, v);
    else next.delete(k);
    if (selectedId) navigate({ pathname: "/cortes", search: next.toString() });
    else setParams(next);
  }
  return <div className="cut-room" data-focus={selectedId !== null || undefined}>
    <PageHeader title={t("Central de cortes")}>
      <button type="button" className="btn-quiet filters-toggle" aria-expanded={filtersOpen} aria-controls="cut-filters"
        onClick={() => setFiltersOpen(!filtersOpen)}><SlidersHorizontal size={15} aria-hidden /> {t("Filtros")}</button>
      <div id="cut-filters" className="cut-filters" data-open={filtersOpen || undefined}>
        <select className="field w-auto" value={streamer} onChange={(e) => filter("streamer", e.target.value)} aria-label={t("Filtrar por streamer")}>
          <option value="">{t("Todos os streamers")}</option>
          {(streamers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
        </select>
        <select className="field w-auto" value={sort} onChange={(e) => filter("sort", e.target.value)} aria-label={t("Ordenar")}>
          <option value="score">{t("Maior pontuação")}</option>
          <option value="recent">{t("Mais recentes")}</option>
        </select>
      </div>
    </PageHeader>
    <label className="compact-status">{t("Etapa")}
      <select className="field" aria-label={t("Etapa dos cortes")} value={status} onChange={e => filter("status", e.target.value)}>
        {tabs.map(t => <option key={t.label} value={t.key}>{t.label}</option>)}
      </select></label>
    <nav className="cut-states view-switch mb-4" aria-label={t("Estados dos cortes")}>
      {tabs.map((t) => (
        <button key={t.label} type="button" className="view-switch-item" aria-pressed={status === t.key}
          onClick={() => filter("status", t.key)}>{t.label}</button>
      ))}
    </nav>
    <ErrorNote error={list.error}/>
    {list.isLoading && <Skeleton label={t("Carregando cortes")}/>}
    {list.data && !items.length && !selectedId && <Empty title={status === "ready" ? t("Tudo revisado") : t("Nenhum corte nesta lista")}>
      {status === "ready" && t("Novos cortes aparecem aqui quando a IA identifica um momento acima da pontuação mínima.")}
    </Empty>}
    {monitorId != null && <div className="cut-desk">
      <aside className="cut-bin" data-open={queueOpen} aria-label={t("Fila de cortes")}>
        <header className="desk-heading">
          <ListVideo aria-hidden /><h2>{t("Fila de cortes")}</h2>
          <button type="button" className="queue-toggle" aria-expanded={queueOpen} aria-controls="bin-list"
            onClick={() => setQueueOpen(!queueOpen)}>{queueOpen ? t("Recolher") : t("Ver fila")}</button>
          {list.data && <span>{items.length < list.data.total ? `${items.length} / ${list.data.total}` : items.length}</span>}
        </header>
        <ul id="bin-list">{items.map(c => <li key={c.id} data-current={monitorId === c.id}>
          <button type="button" className="bin-clip" aria-pressed={monitorId === c.id} onClick={() => { setQueueOpen(false); open(c.id); }}>
            <span className="bin-frame">{c.has_thumbnail ? <img alt="" loading="lazy" src={`/api/clips/${c.id}/thumbnail?v=${c.version}`}/> : <Film size={19} aria-hidden/>}</span>
            <span className="bin-caption"><small>#{c.id} · {seconds(c.duration)} · {Math.round(c.score)}/100</small><strong>{c.title || t("Sem título")}</strong>
            <small>{c.is_test ? t("Teste manual") : c.streamer?.display_name}</small>
            {!status && <ClipStatus status={c.status} />}</span></button>
          <label className="bin-check"><input type="checkbox" checked={picked.includes(c.id)} onChange={() => toggle(c.id)} aria-label={t("Selecionar corte {id}", { id: c.id })}/></label>
        </li>)}</ul>
        {!items.length && <p className="quiet-copy px-3">{t("Fila vazia neste filtro.")}</p>}
        {items.length > 0 && picked.length === 0 && <button type="button" className="bin-all" onClick={() => setPicked(items.map(c => c.id))}>{t("Selecionar todos desta lista")}</button>}
      </aside>
      <ReviewBench key={monitorId} clipId={monitorId} items={items} onNavigate={open} explicit={selectedId !== null} startInVersions={params.get("aba") === "versoes"}/>
    </div>}
    {picked.length > 0 && <BatchBar ids={picked} onClear={() => setPicked([])} />}
  </div>;
}

const reviewViews = [
  { key: "review", label: t("Revisar") },
  { key: "versions", label: t("Comparar versões") },
  { key: "edit", label: t("Editar") },
  { key: "publish", label: t("Publicar") },
  { key: "share", label: t("Compartilhar") },
] as const;
const needsVideo: string[] = ["versions", "publish", "share"];

function ReviewBench({ clipId, items, onNavigate, startInVersions, explicit }: {
  clipId: number; items: Clip[]; onNavigate: (id: number | null) => void; startInVersions: boolean; explicit: boolean;
}) {
  const [requestedView, setView] = useState<(typeof reviewViews)[number]["key"]>(startInVersions ? "versions" : "review");
  const [visited, setVisited] = useState<Set<string>>(() => new Set([startInVersions ? "versions" : "review"]));
  const bench = useRef<HTMLDivElement>(null);
  const help = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const qc = useQueryClient();
  const clip = useQuery({
    queryKey: ["clip", clipId],
    queryFn: () => api.get<Clip>(`/api/clips/${clipId}`),
    refetchInterval: (q) => (q.state.data && ["ready", "approved", "rejected", "failed"].includes(q.state.data.status) ? false : 3000),
  });
  const c = clip.data;
  const view = c?.has_video || !needsVideo.includes(requestedView) ? requestedView : "review";
  const idx = items.findIndex((x) => x.id === clipId);
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["clips"] });
    qc.invalidateQueries({ queryKey: ["clip", clipId] });
    qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const action = useMutation({
    mutationFn: (a: "approve" | "reject" | "rerender" | "reanalyze") => api.post<Clip>(`/api/clips/${clipId}/${a}`),
    onSuccess: (_d, a) => {
      invalidate();
      if ((a === "approve" || a === "reject") && idx >= 0 && items[idx + 1]) onNavigate(items[idx + 1].id);
    },
  });
  const navigate = useNavigate();
  const evaluate = useMutation({
    mutationFn: () => api.post<{ id: number }>(`/api/review/clips/${clipId}/example`),
    onSuccess: (ex) => navigate(`/avaliacao?aba=revisar&ex=${ex.id}`),
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/api/clips/${clipId}`),
    onSuccess: () => {
      invalidate();
      onNavigate(items[idx + 1]?.id ?? null);
    },
  });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select, video, [contenteditable], dialog") || document.querySelector("dialog[open]")
        || e.repeat || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (e.key === "?") { e.preventDefault(); help.current?.showModal(); }
      else if (k === " " && !t.closest("button, a, summary")) {
        const video = bench.current?.querySelector<HTMLVideoElement>("[data-clip-video] video");
        if (video) { e.preventDefault(); if (video.paused) void video.play(); else video.pause(); }
      }
      else if (k === "j" && idx >= 0 && items[idx + 1]) onNavigate(items[idx + 1].id);
      else if (k === "k" && idx > 0) onNavigate(items[idx - 1].id);
      else if (k === "a" && !action.isPending && c && ["ready", "rejected"].includes(c.status)) action.mutate("approve");
      else if (k === "r" && !action.isPending && c && ["ready", "approved", "failed"].includes(c.status)) action.mutate("reject");
      else if (k === "escape") onNavigate(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [idx, items, c, action, onNavigate, explicit]);

  const loaded = Boolean(c);
  useEffect(() => {
    if (!explicit) return;
    heading.current?.focus({ preventScroll: true });
  }, [clipId, loaded, explicit]);

  function changeView(next: typeof view) {
    if (next === view) return;
    if (!explicit) onNavigate(clipId);
    bench.current?.querySelectorAll("video").forEach((video) => video.pause());
    setVisited(previous => new Set(previous).add(next));
    setView(next);
  }

  const dims = Object.entries(c?.score_breakdown ?? {}).filter(([k]) => k in dimensionLabel);

  return (
    <div ref={bench} className="cut-bench">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {explicit && <button className="icon-action" title={t("Voltar à fila")} aria-label={t("Voltar à fila")} onClick={() => onNavigate(null)}><ArrowLeft size={16} aria-hidden/></button>}
          <span className="monitor-label"><Film size={16} aria-hidden/> {t("Corte")} <b>#{clipId}</b></span>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" className="icon-action hidden md:inline-grid" onClick={() => help.current?.showModal()}
            aria-label={t("Atalhos de teclado (?)")} title={t("Atalhos de teclado (?)")}><Keyboard size={16} aria-hidden /></button>
          <button className="icon-action" disabled={idx <= 0} onClick={() => onNavigate(items[idx - 1].id)} aria-label={t("Corte anterior (K)")}>
            <ChevronLeft size={17} aria-hidden />
          </button>
          {idx >= 0 && <span className="font-mono text-xs text-ink-muted">{idx + 1} / {items.length}</span>}
          <button className="icon-action" disabled={idx < 0 || idx + 1 >= items.length}
            onClick={() => onNavigate(items[idx + 1].id)} aria-label={t("Próximo corte (J)")}>
            <ChevronRight size={17} aria-hidden />
          </button>
        </div>
      </div>
      <ErrorNote error={clip.error} />
      {!c ? (clip.isLoading && <Skeleton label={t("Carregando corte")} />) : (
        <>
          <header className="mb-4 flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h2 ref={heading} tabIndex={-1} className="display break-words text-xl outline-none sm:text-2xl">{c.title || t("Sem título")}</h2>
              <p className="clip-meta mt-2 text-sm text-ink-muted">
                {c.streamer?.display_name} / {platformName[c.streamer?.platform ?? ""] ?? t("Live")} / {t("Sessão #{id}", { id: c.session_id })}
                <span className="ml-2 font-mono text-xs">{timecode(c.start)} {t("a")} {timecode(c.end)}</span>
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {c.is_test && <Status tone="warning">{t("Teste manual")}</Status>}
                <ClipStatus status={c.status} /><span className="hidden md:inline-flex"><AnalysisStatus clip={c} /></span>
              </div>
            </div>
            <ScoreMark score={c.score} size="lg" />
          </header>

          <nav className="view-switch mb-5" aria-label={t("Áreas do corte")}>
            {reviewViews.filter((item) => c.has_video || !needsVideo.includes(item.key)).map((item) => (
              <button type="button" key={item.key} className="view-switch-item" aria-pressed={item.key === view}
                onClick={() => changeView(item.key)}>{item.label}</button>
            ))}
          </nav>

          {(action.error || remove.error || evaluate.error || c.error || action.isSuccess) && <div className="mb-4 space-y-2">
            <ErrorNote error={action.error || remove.error || evaluate.error || c.error} />
            {action.isSuccess && <Notice>
              {{ approve: t("Corte aprovado."), reject: t("Corte rejeitado."), rerender: t("Renderização solicitada."), reanalyze: t("Nova análise solicitada.") }[action.variables!]}
            </Notice>}
          </div>}

          <div className="monitor-grid">
            <section hidden={view === "versions"} className="monitor-stage min-w-0" aria-label={t("Prévia do corte")}>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="display text-lg">{t("Prévia")}</h2><span className="font-mono text-xs text-ink-muted">9:16 / {seconds(c.duration)}</span></div>
              <div className="space-y-3">
                {c.has_video ? (
                  <ClipVideo className="review-player" data-clip-video key={c.version} aria-label={t("Vídeo do corte")}
                    poster={c.has_thumbnail ? `/api/clips/${c.id}/thumbnail?v=${c.version}` : undefined}
                    src={`/api/clips/${c.id}/video?v=${c.version}`} />
                ) : (
                  <div className="media-stage min-h-56 px-6 text-center text-sm text-ink-muted">
                    {c.status === "failed" ? t("A renderização falhou.") : t("O vídeo aparece aqui quando a renderização terminar.")}
                  </div>
                )}
                <div className="decision-bar" role="group" aria-label={t("Decisão sobre o corte")}>
                  <button className="btn-take" disabled={!["ready", "rejected"].includes(c.status) || action.isPending} onClick={() => action.mutate("approve")}>
                    <Check size={16} aria-hidden /> {t("Aprovar")}
                  </button>
                  <button className="btn-quiet" disabled={!["ready", "approved", "failed"].includes(c.status) || action.isPending} onClick={() => action.mutate("reject")}>
                    <X size={16} aria-hidden /> {t("Rejeitar")}
                  </button>
                  <button className="btn-quiet decision-next" disabled={idx < 0 || idx + 1 >= items.length}
                    onClick={() => onNavigate(items[idx + 1].id)} aria-label={t("Próximo corte")}>
                    <ChevronRight size={16} aria-hidden /><span className="decision-next-label">{t("Próximo")}</span>
                  </button>
                </div>
                <Disclosure title={t("Arquivo e ações")}>
                  <div className="flex flex-wrap gap-2">
                    {c.has_video && <a className="btn-quiet" href={`/api/clips/${c.id}/download`} download><Download size={16} aria-hidden /> {t("Baixar")}</a>}
                    <ConfirmAction title={t("Excluir este corte?")} description={t("O corte e seus arquivos serão removidos permanentemente.")}
                      confirmLabel={t("Excluir corte")} pending={remove.isPending} onConfirm={() => remove.mutate()}>
                      <Trash2 size={16} aria-hidden /> {t("Excluir")}
                    </ConfirmAction>
                  </div>
                  <StorageFlags clipId={c.id} protectedFlag={c.protected} favorite={c.favorite} lifecycle={c.lifecycle} purgeAfter={c.purge_after} />
                </Disclosure>
                <p className="key-guide hidden md:flex"><kbd>{t("A")}</kbd> {t("Aprovar")} <kbd>{t("R")}</kbd> {t("Rejeitar")} <kbd>{t("J")}</kbd><kbd>{t("K")}</kbd> {t("Navegar")} <kbd>?</kbd> {t("Todos os atalhos")}</p>
              </div>
            </section>

            <div hidden={view !== "review"} className="min-w-0 space-y-4">
              <WhyCard clip={c} />
              <Disclosure title={t("Detalhes técnicos da IA")}>
                <div className="space-y-5">
                  {dims.length > 0 && (
                    <section>
                      <h3 className="mb-3 text-sm font-semibold">{t("Pontuação por critério")}</h3>
                      <dl className="space-y-2">
                        {dims.map(([key, value]) => (
                          <div key={key} className="grid grid-cols-[minmax(6.7rem,1fr)_minmax(2rem,1fr)_2rem] items-center gap-2 text-xs">
                            <dt className="text-ink-muted">{dimensionLabel[key]}</dt>
                            <dd><Meter value={value} label={dimensionLabel[key]} /></dd>
                            <dd className="text-right">{value === null ? t("n/d") : value.toFixed(1)}</dd>
                          </div>
                        ))}
                      </dl>
                      <p className="mt-2 text-xs text-ink-muted">{t("Notas heurísticas de 0 a 10. n/d indica sinal indisponível.")}</p>
                    </section>
                  )}
                  <AnalysisPanel clip={c} busy={action.isPending} onReanalyze={() => action.mutate("reanalyze")} />
                  <button className="btn-quiet" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
                    <ClipboardCheck size={15} aria-hidden /> {t("Enviar para a avaliação editorial")}
                  </button>
                </div>
              </Disclosure>
              <Disclosure title={t("Transcrição")}>
                <p className="max-h-64 overflow-y-auto text-sm leading-relaxed">{c.transcript || t("Sem fala detectada.")}</p>
              </Disclosure>
            </div>

            {c.has_video && visited.has("versions") && <div hidden={view !== "versions"} className="compare-stage min-w-0">
              <VersionsPanel clipId={c.id} version={c.version} onChanged={invalidate} />
            </div>}
            {visited.has("edit") && <div hidden={view !== "edit"} className="panel panel-body min-w-0">
              <ClipEditor clip={c} onSaved={invalidate} />
              <CaptionEditor clipId={c.id} videoVersion={c.version} onSaved={invalidate} />
            </div>}
            {c.has_video && visited.has("publish") && <div hidden={view !== "publish"} className="panel panel-body min-w-0">
              <PublishPanel clipId={c.id} isTest={c.is_test} />
            </div>}
            {c.has_video && visited.has("share") && <div hidden={view !== "share"} className="panel panel-body min-w-0">
              <SharePanel clipId={c.id} clipTitle={c.title || t("Corte #{id}", { id: c.id })} />
            </div>}
          </div>
        </>
      )}
      <dialog ref={help} className="confirmation-modal" aria-labelledby="keys-title">
        <h2 id="keys-title" className="display text-xl">{t("Atalhos da Central")}</h2>
        <dl className="shortcut-list">
          <div><dt><kbd>{t("Espaço")}</kbd></dt><dd>{t("Reproduzir ou pausar o vídeo")}</dd></div>
          <div><dt><kbd>{t("A")}</kbd></dt><dd>{t("Aprovar o corte na tela e ir para o próximo")}</dd></div>
          <div><dt><kbd>{t("R")}</kbd></dt><dd>{t("Rejeitar o corte na tela e ir para o próximo")}</dd></div>
          <div><dt><kbd>{t("J")}</kbd> / <kbd>{t("K")}</kbd></dt><dd>{t("Próximo / anterior da fila")}</dd></div>
          <div><dt><kbd>{t("Esc")}</kbd></dt><dd>{t("Voltar para a fila")}</dd></div>
          <div><dt><kbd>?</kbd></dt><dd>{t("Mostrar esta lista")}</dd></div>
        </dl>
        <p className="mt-3 text-xs text-ink-muted">{t("Os atalhos não funcionam enquanto você digita em um campo.")}</p>
        <div className="mt-5 flex justify-end"><button type="button" className="btn-take" onClick={() => help.current?.close()}>{t("Entendi")}</button></div>
      </dialog>
    </div>
  );
}

const confidenceWord = (v: number) => (v >= 0.75 ? t("alta") : v >= 0.5 ? t("média") : t("baixa"));

/** "Why this clip?" in plain words, assembled only from what the pipeline recorded. Numbers live in the technical details. */
function WhyCard({ clip: c }: { clip: Clip }) {
  const g = c.gate_detail;
  const visual = c.analysis?.visual;
  const sawSomething = visual && !visual.fallback && visual.summary;
  return (
    <section className="why-card" aria-labelledby="why-title">
      <h2 id="why-title" className="display text-lg">{t("Por que este corte?")}</h2>
      {g && g.outcome && (
        <div className="why-gate" data-outcome={g.outcome}>
          <strong>{tx(g.outcome_label)}</strong>
          {g.reasons.length > 0 && <ul>{g.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}</ul>}
          {g.blocking.length > 0 && <p className="text-xs">{t("O que impede a aprovação automática:")} {g.blocking.join("; ")}</p>}
        </div>
      )}
      {c.reasons.length > 0 ? (
        <div>
          <h3 className="why-label">{t("O que chamou a atenção da IA")}</h3>
          <ul className="why-reasons">{c.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>
        </div>
      ) : <p className="text-sm text-ink-muted">{t("A IA não registrou motivos para este corte.")}</p>}
      {sawSomething && (
        <div>
          <h3 className="why-label">{t("O que a IA viu na imagem")}</h3>
          <p className="text-sm">{visual.summary}</p>
        </div>
      )}
      <dl className="why-facts">
        <div><dt>{t("Trecho da live")}</dt><dd className="font-mono">{timecode(c.start)} {t("a")} {timecode(c.end)}</dd></div>
        <div><dt>{t("Duração")}</dt><dd>{seconds(c.duration)}</dd></div>
        <div><dt>{t("Nota")}</dt><dd>{Math.round(c.editorial_score ?? c.score)} {t("de 100")}</dd></div>
        {c.confidence != null && <div><dt>{t("Certeza da IA")}</dt><dd>{confidenceWord(c.confidence)} ({Math.round(c.confidence * 100)}%)</dd></div>}
      </dl>
    </section>
  );
}

function ClipEditor({ clip, onSaved }: { clip: Clip; onSaved: () => void }) {
  const [form, setForm] = useState({
    title: clip.title,
    description: clip.description,
    start: clip.start,
    end: clip.end,
    caption_style: clip.caption_style,
    layout: clip.layout,
  });
  const dirty = useMemo(
    () => (Object.keys(form) as (keyof typeof form)[]).some((k) => form[k] !== clip[k]),
    [form, clip],
  );
  const needsRender = form.start !== clip.start || form.end !== clip.end || form.caption_style !== clip.caption_style ||
    form.layout !== clip.layout || form.title !== clip.title;
  const busy = ["rendering", "analyzing", "candidate"].includes(clip.status);
  const save = useMutation({
    mutationFn: async (rerender: boolean) => {
      await api.patch(`/api/clips/${clip.id}`, form);
      if (rerender) await api.post(`/api/clips/${clip.id}/rerender`);
    },
    onSuccess: onSaved,
  });
  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));

  return (
    <section className="space-y-3">
      <h3 className="mb-3 text-sm font-semibold">{t("Editar e exportar")}</h3>
      <div className="space-y-3">
        <div>
          <label className="label" htmlFor="title">{t("Título")}</label>
          <input id="title" className="field" value={form.title} maxLength={255} onChange={(e) => set("title", e.target.value)} />
          {clip.title_suggestions.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {clip.title_suggestions.map((t) => (
                <button key={t} type="button" onClick={() => set("title", t)}
                  className={`rounded-full border px-2.5 py-1 text-xs ${form.title === t ? "border-take text-take" : "border-suite-line text-ink-muted hover:text-ink"}`}>
                  {t}
                </button>
              ))}
            </div>
          )}
        </div>
        <div>
          <label className="label" htmlFor="desc">{t("Descrição")}</label>
          <textarea id="desc" rows={4} className="field" value={form.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="start">{t("Começa em (segundos da live)")}</label>
            <div className="nudge">
              <button type="button" className="icon-action" aria-label={t("Começar 0,5 s antes")} onClick={() => set("start", Math.max(0, +(form.start - 0.5).toFixed(1)))}><Minus size={15} aria-hidden /></button>
              <input id="start" type="number" step="0.1" min={0} className="field" value={form.start}
                onChange={(e) => set("start", Number(e.target.value))} />
              <button type="button" className="icon-action" aria-label={t("Começar 0,5 s depois")} onClick={() => set("start", +(form.start + 0.5).toFixed(1))}><Plus size={15} aria-hidden /></button>
            </div>
          </div>
          <div>
            <label className="label" htmlFor="end">{t("Termina em (segundos da live)")}</label>
            <div className="nudge">
              <button type="button" className="icon-action" aria-label={t("Terminar 0,5 s antes")} onClick={() => set("end", Math.max(0, +(form.end - 0.5).toFixed(1)))}><Minus size={15} aria-hidden /></button>
              <input id="end" type="number" step="0.1" min={0} className="field" value={form.end}
                onChange={(e) => set("end", Number(e.target.value))} />
              <button type="button" className="icon-action" aria-label={t("Terminar 0,5 s depois")} onClick={() => set("end", +(form.end + 0.5).toFixed(1))}><Plus size={15} aria-hidden /></button>
            </div>
          </div>
          <div>
            <label className="label" htmlFor="style">{t("Estilo da legenda")}</label>
            <select id="style" className="field" value={form.caption_style} onChange={(e) => set("caption_style", e.target.value)}>
              <option value="bold">{t("Destaque (contorno grosso)")}</option>
              <option value="clean">{t("Limpa")}</option>
              <option value="boxed">{t("Com fundo")}</option>
              <option value="none">{t("Sem legenda")}</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="layout">{t("Enquadramento")}</label>
            <select id="layout" className="field" value={form.layout} onChange={(e) => set("layout", e.target.value)}>
              <option value="auto">{t("Automático")}</option>
              <option value="gameplay">{t("Gameplay inteira")}</option>
              <option value="zoom">{t("Gameplay ampliada")}</option>
              <option value="split">{t("Webcam em cima")}</option>
            </select>
          </div>
        </div>
        <p className="text-xs text-ink-muted">{t("Duração: {seconds}. Mudanças de tempo, título, legenda ou enquadramento exigem nova renderização.", { seconds: seconds(Math.max(0, form.end - form.start)) })}</p>
        <ErrorNote error={save.error} />
        <div className="flex flex-wrap gap-2">
          <button className="btn-take" disabled={!needsRender || busy || save.isPending} onClick={() => save.mutate(true)}>
            <RotateCcw className="h-4 w-4" /> {t("Salvar e renderizar de novo")}
          </button>
          <button className="btn-quiet" disabled={!dirty || save.isPending} onClick={() => save.mutate(false)}>{t("Salvar sem renderizar")}</button>
          {clip.status === "failed" && (
            <button className="btn-quiet" disabled={save.isPending} onClick={() => save.mutate(true)}>{t("Tentar renderizar de novo")}</button>
          )}
        </div>
      </div>
    </section>
  );
}
