import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ClipboardCheck, Download, RotateCcw, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Clip, type Streamer } from "../api";
import AnalysisPanel, { AnalysisStatus } from "../components/AnalysisPanel";
import PublishPanel from "../components/PublishPanel";
import CaptionEditor from "../components/CaptionEditor";
import { StorageFlags } from "./Storage";
import { ClipFrame, ClipStatus, Empty, ErrorNote, Meter, PageHeader, ScoreMark } from "../components/ui";
import { dimensionLabel, platformName, seconds, timecode } from "../lib/format";

const tabs = [
  { key: "ready", label: "Para revisar" },
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
  if (status) qs.set("status", status);
  if (streamer) qs.set("streamer_id", streamer);

  const list = useQuery({
    queryKey: ["clips", status, sort, streamer],
    queryFn: () => api.get<{ items: Clip[]; total: number }>(`/api/clips?${qs}`),
    refetchInterval: 8_000,
  });
  const streamers = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers") });
  const items = list.data?.items ?? [];
  const selectedId = id ? Number(id) : null;

  function setParam(k: string, v: string) {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    if (k === "status" && !v) p.set("status", "");
    setParams(p);
  }
  const open = (cid: number | null) => navigate({ pathname: cid ? `/cortes/${cid}` : "/cortes", search: params.toString() });

  return (
    <div className={selectedId ? "lg:grid lg:grid-cols-[minmax(0,1fr)_minmax(420px,520px)] lg:gap-8" : ""}>
      <div className={`min-w-0 ${selectedId ? "hidden lg:block" : ""}`}>
        <PageHeader title="Central de cortes">
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
        <div className="mb-5 flex gap-1 overflow-x-auto border-b border-suite-line" role="tablist">
          {tabs.map((t) => (
            <button key={t.label} role="tab" aria-selected={status === t.key} onClick={() => setParam("status", t.key)}
              className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${status === t.key ? "border-take text-ink" : "border-transparent text-ink-muted hover:text-ink"}`}>
              {t.label}
            </button>
          ))}
        </div>
        <ErrorNote error={list.error} />
        {list.data && items.length === 0 && (
          <Empty title={status === "ready" ? "Tudo revisado" : "Nenhum corte nesta lista"}>
            {status === "ready" ? "Novos cortes aparecem aqui quando a IA encontra um momento com pontuação acima do mínimo." : null}
          </Empty>
        )}
        <div className={`grid gap-4 ${selectedId ? "grid-cols-2 sm:grid-cols-3 xl:grid-cols-4" : "grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-6"}`}>
          {items.map((c) => (
            <div key={c.id}>
              <ClipFrame clip={c} selected={c.id === selectedId} onOpen={() => open(c.id)} />
              <div className="mt-1.5 flex items-center justify-between text-xs">
                <ClipStatus status={c.status} />
                <span className="text-ink-faint">#{c.id}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
      {selectedId && <ReviewBench key={selectedId} clipId={selectedId} items={items} onNavigate={open} />}
    </div>
  );
}

function ReviewBench({ clipId, items, onNavigate }: { clipId: number; items: Clip[]; onNavigate: (id: number | null) => void }) {
  const qc = useQueryClient();
  const clip = useQuery({
    queryKey: ["clip", clipId],
    queryFn: () => api.get<Clip>(`/api/clips/${clipId}`),
    refetchInterval: (q) => (q.state.data && ["ready", "approved", "rejected", "failed"].includes(q.state.data.status) ? false : 3000),
  });
  const c = clip.data;
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
      if (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "j" && items[idx + 1]) onNavigate(items[idx + 1].id);
      else if (k === "k" && idx > 0) onNavigate(items[idx - 1].id);
      else if (k === "a" && c && ["ready", "rejected"].includes(c.status)) action.mutate("approve");
      else if (k === "r" && c && ["ready", "approved", "failed"].includes(c.status)) action.mutate("reject");
      else if (k === "escape") onNavigate(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [idx, items, c, action, onNavigate]);

  if (!c) return <aside className="mt-8 lg:mt-0"><ErrorNote error={clip.error} /></aside>;
  const dims = Object.entries(c.score_breakdown).filter(([k]) => k in dimensionLabel);

  return (
    <aside className="mt-10 lg:sticky lg:top-6 lg:mt-0 lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto lg:pr-1" aria-label="Revisão do corte">
      <div className="flex items-start gap-4">
        <ScoreMark score={c.score} size="lg" />
        <div className="min-w-0 flex-1">
          <h2 className="display text-lg leading-snug">{c.title || "Sem título"}</h2>
          <p className="mt-1 text-sm text-ink-muted">
            {c.streamer?.display_name} em {platformName[c.streamer?.platform ?? ""] ?? "live"}, {timecode(c.start)} a {timecode(c.end)} da sessão #{c.session_id}
          </p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            {c.is_test && <span className="rounded bg-take-dim px-1.5 py-0.5 text-xs font-semibold text-take">TESTE MANUAL</span>}
            <ClipStatus status={c.status} /><AnalysisStatus clip={c} />
          </div>
        </div>
        <button className="btn-quiet p-2" onClick={() => onNavigate(null)} aria-label="Fechar revisão (Esc)"><X className="h-4 w-4" /></button>
      </div>

      <div className="mt-5 grid gap-5 sm:grid-cols-[minmax(0,240px)_1fr]">
        <div>
          {c.has_video ? (
            <video key={c.version} data-clip-video className="aspect-short w-full rounded-lg bg-black" controls preload="metadata"
              poster={c.has_thumbnail ? `/api/clips/${c.id}/thumbnail?v=${c.version}` : undefined}
              src={`/api/clips/${c.id}/video?v=${c.version}`} />
          ) : (
            <div className="flex aspect-short w-full items-center justify-center rounded-lg bg-suite-panel p-4 text-center text-sm text-ink-muted">
              {c.status === "failed" ? "A renderização falhou." : "O vídeo aparece aqui quando a renderização terminar."}
            </div>
          )}
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button className="btn-take" disabled={!["ready", "rejected"].includes(c.status) || action.isPending} onClick={() => action.mutate("approve")}>
              <Check className="h-4 w-4" /> Aprovar
            </button>
            <button className="btn-quiet" disabled={!["ready", "approved", "failed"].includes(c.status) || action.isPending} onClick={() => action.mutate("reject")}>
              <X className="h-4 w-4" /> Rejeitar
            </button>
            <a className={`btn-quiet ${c.has_video ? "" : "pointer-events-none opacity-50"}`} href={`/api/clips/${c.id}/download`} download>
              <Download className="h-4 w-4" /> Baixar
            </a>
            <button className="btn-danger" disabled={remove.isPending}
              onClick={() => window.confirm("Excluir este corte e seus arquivos? Isso não pode ser desfeito.") && remove.mutate()}>
              <Trash2 className="h-4 w-4" /> Excluir
            </button>
          </div>
          <button className="btn-quiet mt-2 w-full" disabled={evaluate.isPending} onClick={() => evaluate.mutate()}>
            <ClipboardCheck className="h-4 w-4" /> Avaliar este corte (feedback para a IA)
          </button>
          <StorageFlags clipId={c.id} protectedFlag={c.protected} favorite={c.favorite} lifecycle={c.lifecycle} purgeAfter={c.purge_after} />
          {c.has_video && <PublishPanel clipId={c.id} isTest={c.is_test} />}
          <p className="mt-2 text-xs text-ink-faint">Atalhos: A aprova, R rejeita, J e K navegam, Esc fecha.</p>
          <ErrorNote error={action.error || remove.error || evaluate.error} />
        </div>

        <div className="min-w-0 space-y-5">
          <section>
            <h3 className="mb-2 text-sm font-semibold">Por que este momento</h3>
            <ul className="space-y-1.5 text-sm">
              {c.reasons.map((r, i) => <li key={i} className="border-l-2 border-take pl-2.5 text-ink/90">{r}</li>)}
            </ul>
          </section>
          <section>
            <h3 className="mb-2 text-sm font-semibold">Pontuação por critério</h3>
            <dl className="space-y-2">
              {dims.map(([k, v]) => (
                <div key={k} className="grid grid-cols-[8.5rem_1fr_2rem] items-center gap-2 text-xs">
                  <dt className="text-ink-muted">{dimensionLabel[k]}</dt>
                  <dd><Meter value={v} /></dd>
                  <dd className="text-right">{v === null ? "n/d" : v.toFixed(1)}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-2 text-xs text-ink-faint">Notas heurísticas de 0 a 10, não são probabilidade de viralizar. n/d: sinal indisponível.</p>
          </section>
        </div>
      </div>

      {c.error && <div className="mt-4"><ErrorNote error={c.error} /></div>}

      <section className="mt-6">
        <h3 className="mb-2 text-sm font-semibold">Transcrição</h3>
        <p className="max-h-40 overflow-y-auto rounded-md bg-suite-panel p-3 text-sm leading-relaxed text-ink/90">{c.transcript || "Sem fala detectada."}</p>
      </section>

      <AnalysisPanel clip={c} busy={action.isPending} onReanalyze={() => action.mutate("reanalyze")} />

      <CaptionEditor clipId={c.id} onSaved={invalidate} />

      <ClipEditor clip={c} onSaved={invalidate} />
    </aside>
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
    <section className="mt-6 border-t border-suite-line pt-5">
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
