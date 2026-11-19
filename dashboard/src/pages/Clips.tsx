import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronLeft, ChevronRight, ClipboardCheck, Download, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Clip, type Streamer } from "../api";
import AnalysisPanel, { AnalysisStatus } from "../components/AnalysisPanel";
import PublishPanel from "../components/PublishPanel";
import CaptionEditor from "../components/CaptionEditor";
import BatchBar from "../components/BatchBar";
import VersionsPanel from "../components/VersionsPanel";
import { StorageFlags } from "./Storage";
import { ClipFrame, ClipStatus, ClipVideo, ConfirmAction, Disclosure, Empty, ErrorNote, Meter, Notice, PageHeader, ScoreMark, Skeleton, Status } from "../components/ui";
import { dimensionLabel, platformName, seconds, timecode } from "../lib/format";

const tabs = [
  { key: "ready", label: "Para revisar" },
  { key: "gate:HUMAN_REVIEW", label: "Revisão humana" },
  { key: "approved", label: "Aprovados" },
  { key: "candidate,analyzing,rendering,waiting", label: "Em processamento" },
  { key: "rejected", label: "Rejeitados" },
  { key: "failed", label: "Com falha" },
  { key: "", label: "Todos" },
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
  const [picked, setPicked] = useState<number[]>([]);
  const toggle = (cid: number) => setPicked((p) => (p.includes(cid) ? p.filter((x) => x !== cid) : [...p, cid]));

  function setParam(k: string, v: string) {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    if (k === "status" && !v) p.set("status", "");
    setParams(p);
  }
  const open = (cid: number | null) => {
    const search = new URLSearchParams(params);
    if (cid === null) search.delete("aba");
    navigate({ pathname: cid ? `/cortes/${cid}` : "/cortes", search: search.toString() });
  };

  return (
    <div>
      <div hidden={Boolean(selectedId)}>
        <PageHeader title="Central de cortes" description="Filtre por etapa e abra um corte para assistir, revisar ou editar.">
          <div className="flex flex-wrap gap-2">
            <select className="field w-auto" value={streamer} onChange={(e) => setParam("streamer", e.target.value)} aria-label="Filtrar por streamer">
              <option value="">Todos os streamers</option>
              {(streamers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
            </select>
            <select className="field w-auto" value={sort} onChange={(e) => setParam("sort", e.target.value)} aria-label="Ordenar">
              <option value="score">Maior pontuação</option>
              <option value="recent">Mais recentes</option>
            </select>
          </div>
        </PageHeader>
        <nav className="view-switch mb-4" aria-label="Estados dos cortes">
          {tabs.map((t) => (
            <button key={t.label} type="button" className="view-switch-item" aria-pressed={status === t.key}
              onClick={() => setParam("status", t.key)}>{t.label}</button>
          ))}
        </nav>
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-ink-muted">
            {list.data && (list.data.total > items.length ? `${items.length} de ${list.data.total} cortes` : `${items.length} ${items.length === 1 ? "corte" : "cortes"}`)}
          </p>
          {items.length > 0 && picked.length === 0 && (
            <button className="btn-quiet py-1 text-xs" onClick={() => setPicked(items.map((c) => c.id))}>Selecionar todos desta lista</button>
          )}
        </div>
        <ErrorNote error={list.error} />
        {list.isLoading && <Skeleton label="Carregando cortes" />}
        {list.data && items.length === 0 && (
          <Empty title={status === "ready" ? "Tudo revisado" : "Nenhum corte nesta lista"}>
            {status === "ready" ? "Novos cortes aparecem aqui quando a IA encontra um momento com pontuação acima do mínimo." : null}
          </Empty>
        )}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
          {items.map((c) => (
            <div key={c.id}>
              <ClipFrame clip={c} selected={c.id === selectedId} onOpen={() => open(c.id)} />
              <div className="mt-2 flex flex-wrap items-center justify-between gap-1 text-xs">
                <ClipStatus status={c.status} />
                <label className="flex cursor-pointer items-center gap-1 text-ink-faint">
                  <input type="checkbox" checked={picked.includes(c.id)} onChange={() => toggle(c.id)}
                    aria-label={`Selecionar corte ${c.id}`} />#{c.id}
                </label>
              </div>
            </div>
          ))}
        </div>
        {picked.length > 0 && <BatchBar ids={picked} onClear={() => setPicked([])} />}
      </div>
      {selectedId && <ReviewBench key={selectedId} clipId={selectedId} items={items} onNavigate={open} startInVersions={params.get("aba") === "versoes"} />}
    </div>
  );
}

const reviewViews = [
  { key: "review", label: "Revisão" },
  { key: "versions", label: "Versões e comparação" },
  { key: "edit", label: "Edição e legendas" },
  { key: "publish", label: "Publicação" },
] as const;

function ReviewBench({ clipId, items, onNavigate, startInVersions }: {
  clipId: number; items: Clip[]; onNavigate: (id: number | null) => void; startInVersions: boolean;
}) {
  const [requestedView, setView] = useState<(typeof reviewViews)[number]["key"]>(startInVersions ? "versions" : "review");
  const bench = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const qc = useQueryClient();
  const clip = useQuery({
    queryKey: ["clip", clipId],
    queryFn: () => api.get<Clip>(`/api/clips/${clipId}`),
    refetchInterval: (q) => (q.state.data && ["ready", "approved", "rejected", "failed"].includes(q.state.data.status) ? false : 3000),
  });
  const c = clip.data;
  const view = c?.has_video || requestedView === "edit" ? requestedView : "review";
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
      if (k === "j" && items[idx + 1]) onNavigate(items[idx + 1].id);
      else if (k === "k" && idx > 0) onNavigate(items[idx - 1].id);
      else if (k === "a" && !action.isPending && c && ["ready", "rejected"].includes(c.status)) action.mutate("approve");
      else if (k === "r" && !action.isPending && c && ["ready", "approved", "failed"].includes(c.status)) action.mutate("reject");
      else if (k === "escape") onNavigate(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [idx, items, c, action, onNavigate]);

  const loaded = Boolean(c);
  useEffect(() => {
    document.getElementById("conteudo")?.scrollIntoView({ block: "start" });
    heading.current?.focus({ preventScroll: true });
  }, [clipId, loaded]);

  function changeView(next: typeof view) {
    if (next === view) return;
    bench.current?.querySelectorAll("video").forEach((video) => video.pause());
    setView(next);
  }

  const dims = Object.entries(c?.score_breakdown ?? {}).filter(([k]) => k in dimensionLabel);

  return (
    <div ref={bench}>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <button className="btn-quiet" onClick={() => onNavigate(null)}><ArrowLeft size={16} aria-hidden /> Voltar aos cortes</button>
        <div className="flex items-center gap-2">
          <button className="icon-action" disabled={idx <= 0} onClick={() => onNavigate(items[idx - 1].id)} aria-label="Corte anterior (K)">
            <ChevronLeft size={17} aria-hidden />
          </button>
          {idx >= 0 && <span className="font-mono text-xs text-ink-muted">{idx + 1} / {items.length}</span>}
          <button className="icon-action" disabled={idx < 0 || idx + 1 >= items.length}
            onClick={() => onNavigate(items[idx + 1].id)} aria-label="Próximo corte (J)">
            <ChevronRight size={17} aria-hidden />
          </button>
        </div>
      </div>
      <ErrorNote error={clip.error} />
      {!c ? (clip.isLoading && <Skeleton label="Carregando corte" />) : (
        <>
          <header className="mb-4 flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h1 ref={heading} tabIndex={-1} className="display break-words text-2xl outline-none sm:text-3xl">{c.title || "Sem título"}</h1>
              <p className="mt-2 text-sm text-ink-muted">
                {c.streamer?.display_name} / {platformName[c.streamer?.platform ?? ""] ?? "Live"} / Sessão #{c.session_id}
                <span className="ml-2 font-mono text-xs">{timecode(c.start)} a {timecode(c.end)}</span>
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {c.is_test && <Status tone="warning">Teste manual</Status>}
                <ClipStatus status={c.status} /><AnalysisStatus clip={c} />
              </div>
            </div>
            <ScoreMark score={c.score} size="lg" />
          </header>

          <nav className="view-switch mb-5" aria-label="Áreas do corte">
            {reviewViews.filter((item) => c.has_video || !["versions", "publish"].includes(item.key)).map((item) => (
              <button type="button" key={item.key} className="view-switch-item" aria-pressed={item.key === view}
                onClick={() => changeView(item.key)}>{item.label}</button>
            ))}
          </nav>

          {(action.error || remove.error || evaluate.error || c.error || action.isSuccess) && <div className="mb-4 space-y-2">
            <ErrorNote error={action.error || remove.error || evaluate.error || c.error} />
            {action.isSuccess && <Notice>
              {{ approve: "Corte aprovado.", reject: "Corte rejeitado.", rerender: "Renderização solicitada.", reanalyze: "Nova análise solicitada." }[action.variables!]}
            </Notice>}
          </div>}

          <div className="grid items-start gap-6 lg:grid-cols-[minmax(260px,0.9fr)_minmax(0,1.1fr)] lg:gap-8">
            <section hidden={view === "versions"} className="min-w-0 lg:sticky lg:top-[calc(var(--topbar-height)+1rem)]" aria-label="Prévia do corte">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><h2 className="display text-lg">Prévia</h2><span className="font-mono text-xs text-ink-muted">9:16 / {seconds(c.duration)}</span></div>
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2">
                  <button className="btn-take" disabled={!["ready", "rejected"].includes(c.status) || action.isPending} onClick={() => action.mutate("approve")}>
                    <Check size={16} aria-hidden /> Aprovar
                  </button>
                  <button className="btn-quiet" disabled={!["ready", "approved", "failed"].includes(c.status) || action.isPending} onClick={() => action.mutate("reject")}>
                    <X size={16} aria-hidden /> Rejeitar
                  </button>
                </div>
                {c.has_video ? (
                  <ClipVideo className="review-player" data-clip-video key={c.version} aria-label="Vídeo do corte"
                    poster={c.has_thumbnail ? `/api/clips/${c.id}/thumbnail?v=${c.version}` : undefined}
                    src={`/api/clips/${c.id}/video?v=${c.version}`} />
                ) : (
                  <div className="media-stage min-h-56 px-6 text-center text-sm text-ink-muted">
                    {c.status === "failed" ? "A renderização falhou." : "O vídeo aparece aqui quando a renderização terminar."}
                  </div>
                )}
                <Disclosure title="Arquivo e ações">
                  <div className="flex flex-wrap gap-2">
                    {c.has_video && <a className="btn-quiet" href={`/api/clips/${c.id}/download`} download><Download size={16} aria-hidden /> Baixar</a>}
                    <ConfirmAction title="Excluir este corte?" description="O corte e seus arquivos serão removidos permanentemente."
                      confirmLabel="Excluir corte" pending={remove.isPending} onConfirm={() => remove.mutate()}>
                      <Trash2 size={16} aria-hidden /> Excluir
                    </ConfirmAction>
                  </div>
                  <StorageFlags clipId={c.id} protectedFlag={c.protected} favorite={c.favorite} lifecycle={c.lifecycle} purgeAfter={c.purge_after} />
                </Disclosure>
                <p className="text-xs text-ink-muted">A aprova, R rejeita, J e K navegam, Esc volta à fila.</p>
              </div>
            </section>

            <div hidden={view !== "review"} className="min-w-0 space-y-4">
              <section className="min-w-0">
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-suite-line pb-3">
                  <h2 className="display text-lg">Leitura editorial</h2>
                  <button className="btn-quiet" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
                    <ClipboardCheck size={15} aria-hidden /> Avaliar para a IA
                  </button>
                </div>
                <div className="space-y-4 py-4">
                  {c.reasons.length > 0 && <ul className="space-y-2 border-l border-suite-outline pl-3 text-sm">{c.reasons.map((reason, index) => <li key={index}>{reason}</li>)}</ul>}
                  {dims.length > 0 && (
                    <section>
                      <h3 className="mb-3 text-sm font-semibold">Pontuação por critério</h3>
                      <dl className="space-y-2">
                        {dims.map(([key, value]) => (
                          <div key={key} className="grid grid-cols-[minmax(6.7rem,1fr)_minmax(2rem,1fr)_2rem] items-center gap-2 text-xs">
                            <dt className="text-ink-muted">{dimensionLabel[key]}</dt>
                            <dd><Meter value={value} label={dimensionLabel[key]} /></dd>
                            <dd className="text-right">{value === null ? "n/d" : value.toFixed(1)}</dd>
                          </div>
                        ))}
                      </dl>
                      <p className="mt-2 text-xs text-ink-muted">Notas heurísticas de 0 a 10. n/d indica sinal indisponível.</p>
                    </section>
                  )}
                </div>
              </section>
              <AnalysisPanel clip={c} busy={action.isPending} onReanalyze={() => action.mutate("reanalyze")} />
              <Disclosure title="Transcrição">
                <p className="max-h-64 overflow-y-auto text-sm leading-relaxed">{c.transcript || "Sem fala detectada."}</p>
              </Disclosure>
            </div>

            {c.has_video && <div hidden={view !== "versions"} className="min-w-0 lg:col-span-2">
              <VersionsPanel clipId={c.id} version={c.version} onChanged={invalidate} />
            </div>}
            <div hidden={view !== "edit"} className="panel panel-body min-w-0">
              <ClipEditor clip={c} onSaved={invalidate} />
              <CaptionEditor clipId={c.id} videoVersion={c.version} onSaved={invalidate} />
            </div>
            {c.has_video && <div hidden={view !== "publish"} className="panel panel-body min-w-0">
              <PublishPanel clipId={c.id} isTest={c.is_test} />
            </div>}
          </div>
        </>
      )}
    </div>
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
      <h3 className="mb-3 text-sm font-semibold">Editar e exportar</h3>
      <div className="space-y-3">
        <div>
          <label className="label" htmlFor="title">Título</label>
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
          <label className="label" htmlFor="desc">Descrição</label>
          <textarea id="desc" rows={4} className="field" value={form.description} onChange={(e) => set("description", e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="label" htmlFor="start">Início na sessão (s)</label>
            <input id="start" type="number" step="0.1" min={0} className="field" value={form.start}
              onChange={(e) => set("start", Number(e.target.value))} />
          </div>
          <div>
            <label className="label" htmlFor="end">Fim na sessão (s)</label>
            <input id="end" type="number" step="0.1" min={0} className="field" value={form.end}
              onChange={(e) => set("end", Number(e.target.value))} />
          </div>
          <div>
            <label className="label" htmlFor="style">Estilo da legenda</label>
            <select id="style" className="field" value={form.caption_style} onChange={(e) => set("caption_style", e.target.value)}>
              <option value="bold">Destaque (contorno grosso)</option>
              <option value="clean">Limpa</option>
              <option value="boxed">Com fundo</option>
              <option value="none">Sem legenda</option>
            </select>
          </div>
          <div>
            <label className="label" htmlFor="layout">Enquadramento</label>
            <select id="layout" className="field" value={form.layout} onChange={(e) => set("layout", e.target.value)}>
              <option value="auto">Automático</option>
              <option value="gameplay">Gameplay inteira</option>
              <option value="zoom">Gameplay ampliada</option>
              <option value="split">Webcam em cima</option>
            </select>
          </div>
        </div>
        <p className="text-xs text-ink-muted">Duração: {seconds(Math.max(0, form.end - form.start))}. Mudanças de tempo, título, legenda ou enquadramento exigem nova renderização.</p>
        <ErrorNote error={save.error} />
        <div className="flex flex-wrap gap-2">
          <button className="btn-take" disabled={!needsRender || busy || save.isPending} onClick={() => save.mutate(true)}>
            <RotateCcw className="h-4 w-4" /> Salvar e renderizar de novo
          </button>
          <button className="btn-quiet" disabled={!dirty || save.isPending} onClick={() => save.mutate(false)}>Salvar sem renderizar</button>
          {clip.status === "failed" && (
            <button className="btn-quiet" disabled={save.isPending} onClick={() => save.mutate(true)}>Tentar renderizar de novo</button>
          )}
        </div>
      </div>
    </section>
  );
}
