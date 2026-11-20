import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Star, Upload } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, upload } from "../api";
import { ConfirmAction, Disclosure, Empty, ErrorNote, Notice, PageHeader, Skeleton, Status } from "../components/ui";
import { t } from "../i18n";

type Item = { id: number; platform: string; kind: string; url: string; handle: string; title: string; category: string;
  games: string[]; humor: string[]; formats: string[]; tags: string[]; why: string; notes: string; favorite: boolean;
  has_image: boolean; api_data: Record<string, unknown>; api_source: string; api_fetched_at: string | null;
  api_expires_at: string | null; collections: number[] };
type Collection = { id: number; name: string; description: string; items: number };
type Cap = { platform: string; what: string; status: string; how: string; source: string };
type Params = {
  layout: "gameplay" | "split" | "action";
  webcam: { position: "top" | "bottom"; height_pct: number };
  captions: { position: "low" | "middle" | "high"; size_pct: number; style: "bold" | "clean" | "boxed" | "none"; uppercase: boolean; active_color: string; emphasis_color: string };
  badge: { enabled: boolean; position: "top_left" | "top_right" | "bottom_left" | "bottom_right"; shape: "circle"; name: string };
  palette?: string[]; contrast?: number; sources?: Record<string, string>; confidence?: Record<string, number>; notes?: string[];
};
type Profile = { id: number; name: string; item_id: number | null; status: string; error: string; favorite: boolean;
  params: Params; has_image: boolean; has_avatar: boolean; analysis: { vlm?: Record<string, string>; vlm_error?: string };
  measured: { split?: number | null; caption?: { center: number } | null; badge?: string | null; faces?: unknown[] } };

const TABS = [["refs", t("Exemplos guardados")], ["formats", t("Perfis de formato")], ["cols", t("Coleções")], ["caps", t("O que cada plataforma permite")]] as const;
const list = (s: string) => s.split(/[,;]+/).map((x) => x.trim()).filter(Boolean);
const capabilityTone = { comprovado: "success", parcial: "warning", indisponível: "danger", proibido: "danger" } as const;
const formatStatus = {
  draft: [t("Aguardando print"), "neutral"],
  queued: [t("Analisando"), "processing"],
  ready: [t("Pronto"), "success"],
  failed: [t("Falhou"), "danger"],
} as const;

/** Three steps with real progress (counts from the API), so a first-time user knows where to start. */
function Guide({ onGo }: { onGo: (tab: (typeof TABS)[number][0]) => void }) {
  const refs = useQuery({ queryKey: ["refs", ""], queryFn: () => api.get<Item[]>("/api/refs/items?") });
  const formats = useQuery({ queryKey: ["formats"], queryFn: () => api.get<Profile[]>("/api/refs/formats") });
  const nRefs = refs.data?.length ?? 0;
  const nProfiles = formats.data?.length ?? 0;
  const ready = (formats.data ?? []).filter((f) => f.status === "ready").length;
  const steps = [
    { done: nRefs > 0, title: t("Guarde um exemplo"), text: nRefs ? `${nRefs} ${nRefs === 1 ? "exemplo guardado" : "exemplos guardados"}.` : t("Cole o link de um corte que funciona e anote por quê."), tab: "refs" as const, action: t("Ir para exemplos") },
    { done: ready > 0, title: t("Ensine um formato"), text: ready ? `${ready} ${ready === 1 ? "perfil pronto" : "perfis prontos"}.` : nProfiles ? t("Envie o print de um perfil para a IA analisar.") : t("Crie um perfil e envie um print autorizado."), tab: "formats" as const, action: t("Ir para perfis") },
    { done: false, title: t("Teste num corte seu"), text: ready ? t("Escolha um corte no perfil pronto e gere a prévia.") : t("Disponível quando um perfil estiver pronto."), tab: "formats" as const, action: t("Ir para perfis"), locked: ready === 0 },
  ];
  if (refs.isLoading || formats.isLoading) return null;
  return (
    <ol className="guide-steps" aria-label={t("Como usar o laboratório")}>
      {steps.map((st, i) => (
        <li key={st.title} data-done={st.done || undefined}>
          <span className="guide-number" aria-hidden>{st.done ? "✓" : i + 1}</span>
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{st.title}{st.done && <span className="sr-only"> {t("(feito)")}</span>}</p>
            <p className="text-xs text-ink-muted">{st.text}</p>
          </div>
          <button type="button" className="btn-quiet" disabled={st.locked} onClick={() => onGo(st.tab)}>{st.action}</button>
        </li>
      ))}
    </ol>
  );
}

export default function References() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("refs");
  return (
    <div>
      <PageHeader title={t("Laboratório de referências")} description={t("Guarde exemplos de cortes que funcionam, deixe a IA aprender o formato visual de um print e teste esse formato num corte seu.")} />
      <Guide onGo={setTab} />
      <nav className="view-switch mb-5" aria-label={t("Áreas do laboratório")}>
        {TABS.map(([key, label]) => (
          <button key={key} type="button" className="view-switch-item" aria-pressed={tab === key}
            onClick={() => setTab(key)}>{label}</button>
        ))}
      </nav>
      {tab === "refs" && <Refs />}
      {tab === "cols" && <Collections />}
      {tab === "formats" && <Formats />}
      {tab === "caps" && <Capabilities />}
    </div>
  );
}

function ImageInput({ label, disabled, onSelect }: { label: string; disabled?: boolean; onSelect: (file: File) => void }) {
  return (
    <label className="btn-quiet relative cursor-pointer focus-within:outline focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-take">
      <Upload size={15} aria-hidden />{label}
      <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" disabled={disabled}
        onChange={(event) => {
          const file = event.currentTarget.files?.[0];
          if (file) onSelect(file);
          event.currentTarget.value = "";
        }} />
    </label>
  );
}

function Refs() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState({ platform: "", collection: "", q: "" });
  const qs = new URLSearchParams();
  if (filter.platform) qs.set("platform", filter.platform);
  if (filter.collection) qs.set("collection_id", filter.collection);
  if (filter.q) qs.set("q", filter.q);
  const items = useQuery({ queryKey: ["refs", qs.toString()], queryFn: () => api.get<Item[]>(`/api/refs/items?${qs}`) });
  const cols = useQuery({ queryKey: ["ref-cols"], queryFn: () => api.get<Collection[]>("/api/refs/collections") });
  const [f, setF] = useState({ url: "", title: "", category: "GTA RP", games: "GTA V", humor: "", formats: "", tags: "", why: "" });
  const done = () => qc.invalidateQueries({ queryKey: ["refs"] });
  const add = useMutation({
    mutationFn: () => api.post<Item>("/api/refs/items", { url: f.url, title: f.title, category: f.category, games: list(f.games),
      humor: list(f.humor), formats: list(f.formats), tags: list(f.tags), why: f.why }),
    onSuccess: () => { setF({ ...f, url: "", title: "", why: "", tags: "" }); done(); },
  });
  const refresh = useMutation({ mutationFn: (id: number) => api.post(`/api/refs/items/${id}/refresh`), onSuccess: done });
  const patch = useMutation({ mutationFn: ({ id, b }: { id: number; b: object }) => api.patch(`/api/refs/items/${id}`, b), onSuccess: done });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/refs/items/${id}`), onSuccess: done });
  const img = useMutation({ mutationFn: ({ id, file }: { id: number; file: File }) => upload(`/api/refs/items/${id}/image`, file), onSuccess: done });
  const member = useMutation({
    mutationFn: ({ cid, id, on }: { cid: number; id: number; on: boolean }) =>
      on ? api.post(`/api/refs/collections/${cid}/items`, { item_id: id }) : api.del(`/api/refs/collections/${cid}/items/${id}`),
    onSuccess: () => { done(); qc.invalidateQueries({ queryKey: ["ref-cols"] }); },
  });
  return (
    <div className="space-y-5">
      <Disclosure title={t("Adicionar referência")}>
        <form className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3" onSubmit={(event) => { event.preventDefault(); add.mutate(); }}>
          {([
            ["url", t("Endereço de origem"), t("Link do canal, perfil ou vídeo")],
            ["title", t("Título"), t("Obrigatório para referências sem link")],
            ["category", t("Categoria"), ""],
            ["games", t("Jogos"), t("Separe por vírgulas")],
            ["humor", t("Tipo de humor"), t("Separe por vírgulas")],
            ["formats", t("Formato"), t("Ex.: gancho imediato, webcam em cima")],
            ["tags", t("Tags"), t("Separe por vírgulas")],
            ["why", t("Por que é relevante?"), ""],
          ] as const).map(([key, label, placeholder]) => (
            <label key={key} className="min-w-0">
              <span className="label">{label}</span>
              <input className="field" placeholder={placeholder} value={f[key]} onChange={(e) => setF({ ...f, [key]: e.target.value })} />
            </label>
          ))}
          <div className="flex flex-wrap items-center gap-3 sm:col-span-2 xl:col-span-3">
            <button className="btn-take" disabled={add.isPending || (!f.url.trim() && !f.title.trim())}>
              {add.isPending ? t("Adicionando…") : t("Adicionar referência")}
            </button>
            <p className="text-sm text-ink-muted">{t("Você pode enviar um print após cadastrar a referência.")}</p>
          </div>
          <div className="sm:col-span-2 xl:col-span-3"><ErrorNote error={add.error} /></div>
        </form>
      </Disclosure>
      {add.isSuccess && <Notice>{t("Referência cadastrada.")}</Notice>}
      <section className="library-filters" aria-labelledby="reference-filters">
        <div><h2 id="reference-filters" className="display text-lg">{t("Buscar na biblioteca")}</h2></div>
        <div className="flex flex-wrap gap-2">
          <input className="field w-auto grow" type="search" aria-label={t("Buscar referências")} placeholder={t("Buscar por termo")} value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
          <select className="field w-auto" aria-label={t("Filtrar plataforma")} value={filter.platform} onChange={(e) => setFilter({ ...filter, platform: e.target.value })}>
            <option value="">{t("Todas as plataformas")}</option><option value="youtube">{t("YouTube")}</option><option value="instagram">{t("Instagram")}</option><option value="other">{t("Por imagem")}</option>
          </select>
          <select className="field w-auto" aria-label={t("Filtrar coleção")} value={filter.collection} onChange={(e) => setFilter({ ...filter, collection: e.target.value })}>
            <option value="">{t("Todas as coleções")}</option>{(cols.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
      </section>
      {items.isLoading && <Skeleton label={t("Carregando referências")} />}
      {items.data?.length === 0 && (filter.q || filter.platform || filter.collection
        ? <Empty title={t("Nenhum exemplo com esses filtros")}>{t("Limpe a busca ou escolha outra plataforma ou coleção.")}</Empty>
        : <Empty title={t("Nenhum exemplo guardado ainda")}>{t("Abra “Adicionar referência” acima e cole o link de um corte que você admira. Só o link e as suas anotações são guardados; o vídeo não é baixado.")}</Empty>)}
      <ErrorNote error={items.error || cols.error || refresh.error || patch.error || del.error || img.error || member.error} />
      <ul className="grid gap-3 md:grid-cols-2">
        {(items.data ?? []).map((it) => (
          <li key={it.id} className="panel panel-body text-sm">
            <div className="flex items-start gap-3">
              {it.has_image && <img src={`/api/refs/items/${it.id}/image`} alt="" className="reference-still" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="display min-w-0 break-words text-lg">{it.title || it.handle || it.url || t("Referência sem título")}</h3>
                  <button type="button" className="icon-action" aria-label={t("Favorito")} aria-pressed={it.favorite} disabled={patch.isPending}
                    onClick={() => patch.mutate({ id: it.id, b: { favorite: !it.favorite } })}>
                    <Star size={16} className={it.favorite ? "fill-take text-take" : ""} aria-hidden />
                  </button>
                </div>
                <p className="text-xs text-ink-muted">{it.platform} · {it.kind}{it.category ? ` · ${it.category}` : ""}{it.url && <> · <a className="inline-flex items-center gap-1 text-take hover:underline" href={it.url} target="_blank" rel="noreferrer">{t("Abrir fonte")} <ExternalLink size={12} aria-hidden /></a></>}</p>
                {it.why && <p className="mt-2 text-sm">{it.why}</p>}
                <p className="mt-1 text-[11px] text-ink-faint">{[...it.games, ...it.humor, ...it.formats, ...it.tags].join(" · ")}</p>
                {Object.keys(it.api_data).length > 0 && (
                  <p className="mt-1 text-[11px] text-ink-muted">
                    {t("API ({api_source}): {join} — atualizado em {slice}, apagado em {slice2} se não atualizar", { api_source: it.api_source, join: Object.entries(it.api_data).filter(([k]) => ["subscribers", "views", "videos", "likes", "comments", "duration_s", "short"].includes(k))
                      .map(([k, v]) => `${k}: ${v ?? t("oculto")}`).join(" · "), slice: it.api_fetched_at?.slice(0, 10), slice2: it.api_expires_at?.slice(0, 10) })}
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  {it.platform === "youtube" && <button className="btn-quiet px-2 py-1" disabled={refresh.isPending} onClick={() => refresh.mutate(it.id)}>{t("Consultar API")}</button>}
                  {it.platform === "instagram" && <span className="text-ink-faint">{t("Instagram: cadastro manual (a API não permite consultar outras contas)")}</span>}
                  <ImageInput label={t("Enviar print")} disabled={img.isPending} onSelect={(file) => img.mutate({ id: it.id, file })} />
                  <ConfirmAction title={t("Remover esta referência?")} description={t("A referência e o print associado serão removidos do laboratório.")}
                    confirmLabel={t("Remover referência")} pending={del.isPending} onConfirm={() => del.mutate(it.id)}>{t("Remover")}</ConfirmAction>
                </div>
                {(cols.data ?? []).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                    {(cols.data ?? []).map((c) => (
                      <label key={c.id} className="flex items-center gap-1">
                        <input type="checkbox" checked={it.collections.includes(c.id)} disabled={member.isPending}
                          onChange={(e) => member.mutate({ cid: c.id, id: it.id, on: e.target.checked })} />{c.name}</label>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Collections() {
  const qc = useQueryClient();
  const cols = useQuery({ queryKey: ["ref-cols"], queryFn: () => api.get<Collection[]>("/api/refs/collections") });
  const [name, setName] = useState("");
  const add = useMutation({ mutationFn: () => api.post("/api/refs/collections", { name }), onSuccess: () => { setName(""); qc.invalidateQueries({ queryKey: ["ref-cols"] }); } });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/api/refs/collections/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["ref-cols"] }) });
  return (
    <div className="max-w-4xl space-y-4">
      <form className="panel panel-body flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
        <input className="field w-auto grow" aria-label={t("Nome da coleção")} required placeholder={t("Nome da coleção")} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-take" disabled={add.isPending || !name.trim()}>{add.isPending ? t("Criando…") : t("Criar coleção")}</button>
      </form>
      <ErrorNote error={cols.error || add.error || del.error} />
      {add.isSuccess && <Notice>{t("Coleção criada.")}</Notice>}
      {cols.isLoading && <Skeleton label={t("Carregando coleções")} />}
      {cols.data?.length === 0 && <Empty title={t("Nenhuma coleção criada")}>{t("Crie uma coleção e associe referências na biblioteca.")}</Empty>}
      {!!cols.data?.length && <ul className="panel divide-y divide-suite-line text-sm">
        {(cols.data ?? []).map((c) => (
          <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
            <div className="min-w-0"><h2 className="break-words font-semibold">{c.name}</h2><p className="text-xs text-ink-muted">{c.items} {t("referência(s)")}</p></div>
            <ConfirmAction title={t("Excluir esta coleção?")} description={t("As referências continuam disponíveis na biblioteca.")}
              confirmLabel={t("Excluir coleção")} pending={del.isPending} onConfirm={() => del.mutate(c.id)}>{t("Excluir")}</ConfirmAction>
          </li>
        ))}
      </ul>}
    </div>
  );
}

function Formats() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["formats"], queryFn: () => api.get<Profile[]>("/api/refs/formats"),
    refetchInterval: (x) => ((x.state.data ?? []).some((p) => p.status === "queued") ? 4000 : false) });
  const [name, setName] = useState("");
  const done = () => qc.invalidateQueries({ queryKey: ["formats"] });
  const add = useMutation({ mutationFn: () => api.post("/api/refs/formats", { name }), onSuccess: () => { setName(""); done(); } });
  return (
    <div className="space-y-4">
      <form className="panel panel-body flex flex-wrap gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
        <input className="field w-auto grow" aria-label={t("Nome do perfil de formato")} required placeholder={t("Nome do perfil de formato")} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-take" disabled={add.isPending || !name.trim()}>{add.isPending ? t("Criando…") : t("Criar perfil")}</button>
      </form>
      <p className="text-xs text-ink-muted">{t("Envie um print de um vídeo vertical: a IA mede a organização (divisão da tela, webcam, legenda, cores, selo) e cria parâmetros que você pode ajustar e testar num corte nosso. O selo usa a")} <b>{t("nossa")}</b> {t("foto e o")} <b>{t("nosso")}</b> {t("nome.")}</p>
      <ErrorNote error={add.error || q.error} />
      {q.isLoading && <Skeleton label={t("Carregando perfis de formato")} />}
      {q.data?.length === 0 && <Empty title={t("Nenhum perfil criado")}>{t("Crie um perfil para extrair o enquadramento e a hierarquia visual de um print autorizado.")}</Empty>}
      {(q.data ?? []).map((p) => <FormatCard key={p.id} p={p} onChange={done} />)}
    </div>
  );
}

function FormatCard({ p, onChange }: { p: Profile; onChange: () => void }) {
  const [draft, setDraft] = useState<Params | null>(null);
  const params = draft ?? p.params;
  const [clip, setClip] = useState("");
  const [previewClip, setPreviewClip] = useState<number | null>(null);
  const shot = useMutation({ mutationFn: (f: File) => upload(`/api/refs/formats/${p.id}/image`, f), onSuccess: () => { setDraft(null); onChange(); } });
  const avatar = useMutation({ mutationFn: (f: File) => upload(`/api/refs/formats/${p.id}/avatar`, f), onSuccess: onChange });
  const save = useMutation({ mutationFn: () => api.put(`/api/refs/formats/${p.id}`, { params }), onSuccess: () => { setDraft(null); onChange(); } });
  const fav = useMutation({ mutationFn: () => api.put(`/api/refs/formats/${p.id}`, { favorite: !p.favorite }), onSuccess: onChange });
  const del = useMutation({ mutationFn: () => api.del(`/api/refs/formats/${p.id}`), onSuccess: onChange });
  const preview = useMutation({
    mutationFn: (clipId: number) => api.post<{ label: string; badge: boolean }>(`/api/refs/formats/${p.id}/preview`, { clip_id: clipId }),
    onSuccess: (_result, clipId) => setPreviewClip(clipId),
  });
  const src = p.params.sources ?? {};
  const s = (k: string) => (src[k] ? <span className="text-[10px] text-ink-faint"> ({src[k]})</span> : null);
  const set = <K extends keyof Params>(k: K, v: Params[K]) => setDraft({ ...params, [k]: v });
  const [status, tone] = formatStatus[p.status as keyof typeof formatStatus] ?? [p.status, "neutral"];
  return (
    <article className="panel">
      <header className="panel-heading">
        <div className="min-w-0">
          <h2 className="display break-words text-lg">{p.name}</h2>
          <Status tone={tone}>{status}</Status>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className="icon-action" aria-label={t("Favorito")} aria-pressed={p.favorite} disabled={fav.isPending} onClick={() => fav.mutate()}>
            <Star size={16} className={p.favorite ? "fill-take text-take" : ""} aria-hidden />
          </button>
          <ConfirmAction title={t("Excluir este perfil?")} description={t("O perfil de formato e o print enviado serão removidos.")}
            confirmLabel={t("Excluir perfil")} pending={del.isPending} onConfirm={() => del.mutate()}>{t("Excluir")}</ConfirmAction>
        </div>
      </header>
      <div className="panel-body space-y-4">
        <ErrorNote error={p.error} />
        <div className="grid grid-cols-1 items-start gap-5 md:grid-cols-[minmax(150px,0.55fr)_minmax(0,1.45fr)]">
          <section className="space-y-2" aria-label={t("Imagem de referência")}>
            <div className="media-stage min-h-28">
              {p.has_image ? <img src={`/api/refs/formats/${p.id}/image?u=${p.status}`} alt={t("Print enviado para análise")} className="max-h-80 w-full object-contain" />
                : <p className="px-3 py-4 text-sm text-ink-muted">{t("Aguardando print vertical")}</p>}
            </div>
            <div className="flex flex-wrap gap-2">
              <ImageInput label={t("Enviar print")} disabled={shot.isPending} onSelect={(file) => shot.mutate(file)} />
              <ImageInput label={p.has_avatar ? t("Trocar foto do canal") : t("Enviar foto do canal")} disabled={avatar.isPending} onSelect={(file) => avatar.mutate(file)} />
            </div>
          </section>
          <section className="grid min-w-0 gap-3 sm:grid-cols-2" aria-label={t("Parâmetros do formato")}>
            <h3 className="text-sm font-semibold sm:col-span-2">{t("Parâmetros do formato")}</h3>
          <label className="text-xs">{t("Organização")}{s("layout")}
            <select className="field" value={params.layout} onChange={(e) => set("layout", e.target.value as Params["layout"])}>
              <option value="gameplay">{t("Gameplay inteira")}</option><option value="split">{t("Webcam + gameplay")}</option><option value="action">{t("Recorte que acompanha a ação")}</option>
            </select></label>
          {params.layout === "split" && (
            <div className="text-xs">{t("Webcam")}{s("webcam.position")}
              <div className="flex gap-2">
                <select className="field" aria-label={t("Posição da webcam")} value={params.webcam.position} onChange={(e) => set("webcam", { ...params.webcam, position: e.target.value as "top" | "bottom" })}>
                  <option value="top">{t("em cima")}</option><option value="bottom">{t("embaixo")}</option></select>
                <input type="number" min={20} max={60} className="field w-20" aria-label={t("Altura da webcam em porcentagem")} value={params.webcam.height_pct}
                  onChange={(e) => set("webcam", { ...params.webcam, height_pct: Number(e.target.value) })} />%
              </div></div>
          )}
          <div className="text-xs">{t("Legenda")}{s("captions.position")}
            <div className="flex gap-2">
              <select className="field" aria-label={t("Posição da legenda")} value={params.captions.position} onChange={(e) => set("captions", { ...params.captions, position: e.target.value as Params["captions"]["position"] })}>
                <option value="high">{t("alta")}</option><option value="middle">{t("meio")}</option><option value="low">{t("baixa")}</option></select>
              <select className="field" aria-label={t("Estilo da legenda")} value={params.captions.style} onChange={(e) => set("captions", { ...params.captions, style: e.target.value as Params["captions"]["style"] })}>
                <option value="bold">{t("destaque")}</option><option value="boxed">{t("com fundo")}</option><option value="clean">{t("limpa")}</option><option value="none">{t("sem legenda")}</option></select>
            </div></div>
          <div className="text-xs">{t("Tamanho e caixa")}{s("captions.size_pct")}
            <div className="flex flex-wrap items-center gap-2">
              <input type="number" min={60} max={160} className="field w-20" aria-label={t("Tamanho da legenda em porcentagem")} value={params.captions.size_pct}
                onChange={(e) => set("captions", { ...params.captions, size_pct: Number(e.target.value) })} />%
              <label className="flex items-center gap-1"><input type="checkbox" checked={params.captions.uppercase}
                onChange={(e) => set("captions", { ...params.captions, uppercase: e.target.checked })} /> {t("MAIÚSCULAS")}</label>
              <input type="color" aria-label={t("Cor da palavra falada")} value={params.captions.active_color || "#FFE500"}
                onChange={(e) => set("captions", { ...params.captions, active_color: e.target.value.toUpperCase() })} />
            </div></div>
          <div className="text-xs sm:col-span-2">{t("Selo de identificação (nossa foto + nosso nome)")}{s("badge.position")}
            <div className="flex flex-wrap items-center gap-2">
              <input type="checkbox" aria-label={t("Ativar selo")} checked={params.badge.enabled} onChange={(e) => set("badge", { ...params.badge, enabled: e.target.checked })} />
              <select className="field w-auto" aria-label={t("Posição do selo")} value={params.badge.position} onChange={(e) => set("badge", { ...params.badge, position: e.target.value as Params["badge"]["position"] })}>
                <option value="top_left">{t("canto superior esquerdo")}</option><option value="top_right">{t("canto superior direito")}</option>
                <option value="bottom_left">{t("canto inferior esquerdo")}</option><option value="bottom_right">{t("canto inferior direito")}</option></select>
              <input className="field w-48" maxLength={32} aria-label={t("Nome no selo")} placeholder={t("Nome do canal")} value={params.badge.name}
                onChange={(e) => set("badge", { ...params.badge, name: e.target.value })} />
            </div></div>
          {p.params.palette && <div className="text-xs sm:col-span-2">{t("Paleta medida:")} {p.params.palette.map((c) => <span key={c} className="mr-1 inline-block h-3 w-6 rounded align-middle" style={{ background: c }} title={c} />)} {p.params.contrast != null && `· contraste ${p.params.contrast}`}</div>}
          {(p.params.notes ?? []).length > 0 && <ul className="text-[11px] text-ink-muted sm:col-span-2">{p.params.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul>}
          {p.analysis.vlm_error && <p className="text-[11px] text-ink-faint sm:col-span-2">{t("IA visual indisponível nesta análise ({vlm_error}); valeu a medição.", { vlm_error: p.analysis.vlm_error })}</p>}
            <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
              <button className="btn-take" disabled={!draft || save.isPending} onClick={() => save.mutate()}>
                {save.isPending ? t("Salvando…") : t("Salvar parâmetros")}
              </button>
              {draft && <button className="btn-quiet" onClick={() => setDraft(null)} disabled={save.isPending}>{t("Descartar mudanças")}</button>}
            </div>
          </section>
        </div>
        {save.isSuccess && !draft && <Notice>{t("Parâmetros salvos.")}</Notice>}
        <section className="border-t border-suite-line pt-4">
          <h3 className="mb-2 text-sm font-semibold">{t("Gerar prévia a partir de um corte próprio")}</h3>
          <form className="flex flex-wrap items-center gap-2" onSubmit={(event) => { event.preventDefault(); if (clip) preview.mutate(Number(clip)); }}>
            <input className="field w-32" inputMode="numeric" aria-label={t("Número do corte")} placeholder={t("Corte #")} value={clip}
              onChange={(e) => setClip(e.target.value.replace(/\D/g, ""))} />
            <button className="btn-quiet" disabled={!clip || preview.isPending}>{preview.isPending ? t("Solicitando…") : t("Gerar variante de formato")}</button>
          </form>
          {preview.data && previewClip !== null && (
            <div className="mt-2"><Notice>
              {t("Variante “{label}” solicitada{v}.", { label: preview.data.label, v: preview.data.badge ? "" : t(" sem selo") })}{" "}
              <Link to={`/cortes/${previewClip}?status=&aba=versoes`} className="underline">{t("Abrir corte #")}{previewClip}</Link>
            </Notice></div>
          )}
        </section>
        <ErrorNote error={shot.error || avatar.error || save.error || del.error || preview.error || fav.error} />
      </div>
    </article>
  );
}

function Capabilities() {
  const q = useQuery({ queryKey: ["ref-caps"], queryFn: () => api.get<{ measured_at: string; items: Cap[] }>("/api/refs/capabilities") });
  return (
    <div className="space-y-4">
      <ErrorNote error={q.error} />
      {q.isLoading && <Skeleton label={t("Carregando capacidades das APIs")} />}
      {q.data && (
        <section className="panel">
          <header className="panel-heading">
            <h2 className="display text-lg">{t("Recursos verificados")}</h2>
            <p className="text-xs text-ink-muted">{t("Medição em")} {q.data.measured_at}</p>
          </header>
          <ul className="divide-y divide-suite-line">
            {q.data.items.map((capability, index) => (
              <li key={index} className="grid grid-cols-1 gap-2 px-4 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)] sm:px-5">
                <div className="min-w-0">
                  <h3 className="break-words font-semibold">{capability.what}</h3>
                  <p className="text-sm text-ink-muted">{capability.platform}</p>
                </div>
                <div>
                  <Status tone={capabilityTone[capability.status as keyof typeof capabilityTone]}>{capability.status}</Status>
                  <p className="mt-1 break-words text-sm text-ink-muted">{capability.how}</p>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
