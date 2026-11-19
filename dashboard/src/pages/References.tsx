import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, upload } from "../api";
import { ErrorNote, PageHeader } from "../components/ui";

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

const TABS = [["refs", "Referências"], ["cols", "Coleções"], ["formats", "Formatos"], ["caps", "O que as APIs permitem"]] as const;
const list = (s: string) => s.split(/[,;]+/).map((x) => x.trim()).filter(Boolean);
const STATUS: Record<string, string> = { comprovado: "text-ok", parcial: "text-take", indisponível: "text-tally", proibido: "text-tally" };

export default function References() {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("refs");
  return (
    <div className="max-w-5xl pb-16">
      <PageHeader title="Viral Intelligence · Referências" />
      <p className="-mt-3 mb-4 max-w-3xl text-sm text-ink-muted">
        Canais, perfis e prints que inspiram nossos cortes. Só fontes legítimas: APIs com a autorização que já temos,
        e o que você cadastra ou envia. Nenhum vídeo de terceiros é baixado; dos prints, a IA extrai só parâmetros
        abstratos (posições, tamanhos, cores), nunca logotipos, textos ou artes.
      </p>
      <div className="mb-5 flex gap-1 border-b border-suite-line" role="tablist">
        {TABS.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={tab === k} onClick={() => setTab(k)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${tab === k ? "border-take text-ink" : "border-transparent text-ink-muted hover:text-ink"}`}>{label}</button>
        ))}
      </div>
      {tab === "refs" && <Refs />}
      {tab === "cols" && <Collections />}
      {tab === "formats" && <Formats />}
      {tab === "caps" && <Capabilities />}
    </div>
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
      <form className="grid gap-2 rounded-lg border border-suite-line p-3 md:grid-cols-3" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className="field md:col-span-2" placeholder="Link do canal, vídeo, Short, perfil ou Reel (ou deixe vazio e use um título + print)"
          value={f.url} onChange={(e) => setF({ ...f, url: e.target.value })} />
        <input className="field" placeholder="Título (opcional)" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} />
        <input className="field" placeholder="Categoria" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })} />
        <input className="field" placeholder="Jogos (vírgula)" value={f.games} onChange={(e) => setF({ ...f, games: e.target.value })} />
        <input className="field" placeholder="Tipo de humor (vírgula)" value={f.humor} onChange={(e) => setF({ ...f, humor: e.target.value })} />
        <input className="field" placeholder="Formato (ex.: gancho imediato, webcam em cima)" value={f.formats} onChange={(e) => setF({ ...f, formats: e.target.value })} />
        <input className="field" placeholder="Tags: perseguição, reação, suspense…" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} />
        <input className="field" placeholder="Por que esta referência é interessante?" value={f.why} onChange={(e) => setF({ ...f, why: e.target.value })} />
        <button className="btn-take md:col-span-3" disabled={add.isPending}>Adicionar referência</button>
        <div className="md:col-span-3"><ErrorNote error={add.error} /></div>
      </form>
      <div className="flex flex-wrap gap-2 text-sm">
        <select className="field w-auto" value={filter.platform} onChange={(e) => setFilter({ ...filter, platform: e.target.value })}>
          <option value="">Todas as plataformas</option><option value="youtube">YouTube</option><option value="instagram">Instagram</option><option value="other">Por imagem</option>
        </select>
        <select className="field w-auto" value={filter.collection} onChange={(e) => setFilter({ ...filter, collection: e.target.value })}>
          <option value="">Todas as coleções</option>{(cols.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        <input className="field w-auto" placeholder="Buscar" value={filter.q} onChange={(e) => setFilter({ ...filter, q: e.target.value })} />
      </div>
      <ErrorNote error={items.error || refresh.error || patch.error || del.error || img.error || member.error} />
      <ul className="grid gap-3 md:grid-cols-2">
        {(items.data ?? []).map((it) => (
          <li key={it.id} className="rounded-lg border border-suite-line p-3 text-sm">
            <div className="flex items-start gap-3">
              {it.has_image && <img src={`/api/refs/items/${it.id}/image`} alt="" className="h-20 w-12 rounded object-cover" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-2">
                  <b className="truncate">{it.title || it.handle || it.url || `Referência #${it.id}`}</b>
                  <button className="text-take" title="Favorito" onClick={() => patch.mutate({ id: it.id, b: { favorite: !it.favorite } })}>{it.favorite ? "★" : "☆"}</button>
                </div>
                <p className="text-xs text-ink-muted">{it.platform} · {it.kind}{it.category ? ` · ${it.category}` : ""}{it.url && <> · <a className="underline" href={it.url} target="_blank" rel="noreferrer">abrir</a></>}</p>
                {it.why && <p className="mt-1 text-xs">{it.why}</p>}
                <p className="mt-1 text-[11px] text-ink-faint">{[...it.games, ...it.humor, ...it.formats, ...it.tags].join(" · ")}</p>
                {Object.keys(it.api_data).length > 0 && (
                  <p className="mt-1 text-[11px] text-ink-muted">
                    API ({it.api_source}): {Object.entries(it.api_data).filter(([k]) => ["subscribers", "views", "videos", "likes", "comments", "duration_s", "short"].includes(k))
                      .map(([k, v]) => `${k}: ${v ?? "oculto"}`).join(" · ")} — atualizado em {it.api_fetched_at?.slice(0, 10)}, apagado em {it.api_expires_at?.slice(0, 10)} se não atualizar
                  </p>
                )}
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  {it.platform === "youtube" && <button className="btn-quiet px-2 py-1" disabled={refresh.isPending} onClick={() => refresh.mutate(it.id)}>Consultar API</button>}
                  {it.platform === "instagram" && <span className="text-ink-faint">Instagram: cadastro manual (a API não permite consultar outras contas)</span>}
                  <label className="btn-quiet cursor-pointer px-2 py-1">Enviar print
                    <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
                      onChange={(e) => e.target.files?.[0] && img.mutate({ id: it.id, file: e.target.files[0] })} /></label>
                  <button className="btn-quiet px-2 py-1" onClick={() => window.confirm("Remover esta referência (só do laboratório)?") && del.mutate(it.id)}>Remover</button>
                </div>
                {(cols.data ?? []).length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
                    {(cols.data ?? []).map((c) => (
                      <label key={c.id} className="flex items-center gap-1">
                        <input type="checkbox" className="accent-[#F2B33D]" checked={it.collections.includes(c.id)}
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
    <div className="max-w-xl space-y-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
        <input className="field" placeholder="Nova coleção (ex.: Perseguições com bom gancho)" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-take" disabled={add.isPending}>Criar</button>
      </form>
      <ErrorNote error={add.error || del.error} />
      <ul className="divide-y divide-suite-line rounded-md border border-suite-line text-sm">
        {(cols.data ?? []).map((c) => (
          <li key={c.id} className="flex items-center justify-between px-3 py-2">
            <span>{c.name} <span className="text-xs text-ink-muted">· {c.items} referência(s)</span></span>
            <button className="btn-quiet px-2 py-1 text-xs" onClick={() => window.confirm("Apagar a coleção? As referências continuam.") && del.mutate(c.id)}>Apagar</button>
          </li>
        ))}
        {!cols.data?.length && <li className="px-3 py-2 text-ink-muted">Nenhuma coleção. Crie uma e marque as referências na aba Referências.</li>}
      </ul>
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
      <form className="flex max-w-xl gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) add.mutate(); }}>
        <input className="field" placeholder="Nome do perfil de formato" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn-take" disabled={add.isPending}>Criar perfil</button>
      </form>
      <p className="text-xs text-ink-muted">Envie um print de um vídeo vertical: a IA mede a organização (divisão da tela, webcam, legenda, cores, selo) e cria parâmetros que você pode ajustar e testar num corte nosso. O selo usa a <b>nossa</b> foto e o <b>nosso</b> nome.</p>
      <ErrorNote error={add.error || q.error} />
      {(q.data ?? []).map((p) => <FormatCard key={p.id} p={p} onChange={done} />)}
    </div>
  );
}

function FormatCard({ p, onChange }: { p: Profile; onChange: () => void }) {
  const [params, setParams] = useState<Params>(p.params);
  const [clip, setClip] = useState("");
  const [msg, setMsg] = useState("");
  const shot = useMutation({ mutationFn: (f: File) => upload(`/api/refs/formats/${p.id}/image`, f), onSuccess: onChange });
  const avatar = useMutation({ mutationFn: (f: File) => upload(`/api/refs/formats/${p.id}/avatar`, f), onSuccess: onChange });
  const save = useMutation({ mutationFn: () => api.put(`/api/refs/formats/${p.id}`, { params }), onSuccess: onChange });
  const fav = useMutation({ mutationFn: () => api.put(`/api/refs/formats/${p.id}`, { favorite: !p.favorite }), onSuccess: onChange });
  const del = useMutation({ mutationFn: () => api.del(`/api/refs/formats/${p.id}`), onSuccess: onChange });
  const preview = useMutation({
    mutationFn: () => api.post<{ label: string; badge: boolean }>(`/api/refs/formats/${p.id}/preview`, { clip_id: Number(clip) }),
    onSuccess: (d) => setMsg(`Variante "${d.label}" em renderização no corte #${clip}${d.badge ? "" : " (sem selo: envie a nossa foto de perfil)"}. Compare em Cortes → Versões.`),
  });
  const src = p.params.sources ?? {};
  const s = (k: string) => (src[k] ? <span className="text-[10px] text-ink-faint"> ({src[k]})</span> : null);
  const set = <K extends keyof Params>(k: K, v: Params[K]) => setParams({ ...params, [k]: v });
  return (
    <div className="rounded-lg border border-suite-line p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <b>{p.name}</b>
        <span className="text-xs text-ink-muted">{{ draft: "sem análise", queued: "analisando…", ready: "pronto", failed: "falhou" }[p.status] ?? p.status}</span>
        {p.error && <span className="text-xs text-tally">{p.error}</span>}
        <button className="ml-auto text-take" onClick={() => fav.mutate()}>{p.favorite ? "★ favorito" : "☆"}</button>
        <button className="btn-quiet px-2 py-1 text-xs" onClick={() => window.confirm("Apagar este perfil e o print enviado?") && del.mutate()}>Apagar</button>
      </div>
      <div className="grid gap-3 md:grid-cols-[120px_1fr]">
        <div className="space-y-2 text-xs">
          {p.has_image ? <img src={`/api/refs/formats/${p.id}/image?u=${p.status}`} alt="print enviado" className="w-full rounded" /> : <div className="flex aspect-[9/16] items-center justify-center rounded bg-suite-panel text-ink-faint">sem print</div>}
          <label className="btn-quiet block cursor-pointer px-2 py-1 text-center">Enviar print
            <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && shot.mutate(e.target.files[0])} /></label>
          <label className="btn-quiet block cursor-pointer px-2 py-1 text-center">{p.has_avatar ? "Trocar nossa foto" : "Nossa foto de perfil"}
            <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && avatar.mutate(e.target.files[0])} /></label>
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs">Organização{s("layout")}
            <select className="field" value={params.layout} onChange={(e) => set("layout", e.target.value as Params["layout"])}>
              <option value="gameplay">Gameplay inteira</option><option value="split">Webcam + gameplay</option><option value="action">Recorte que acompanha a ação</option>
            </select></label>
          {params.layout === "split" && (
            <label className="text-xs">Webcam{s("webcam.position")}
              <div className="flex gap-2">
                <select className="field" value={params.webcam.position} onChange={(e) => set("webcam", { ...params.webcam, position: e.target.value as "top" | "bottom" })}>
                  <option value="top">em cima</option><option value="bottom">embaixo</option></select>
                <input type="number" min={20} max={60} className="field w-20" value={params.webcam.height_pct}
                  onChange={(e) => set("webcam", { ...params.webcam, height_pct: Number(e.target.value) })} />%
              </div></label>
          )}
          <label className="text-xs">Legenda{s("captions.position")}
            <div className="flex gap-2">
              <select className="field" value={params.captions.position} onChange={(e) => set("captions", { ...params.captions, position: e.target.value as Params["captions"]["position"] })}>
                <option value="high">alta</option><option value="middle">meio</option><option value="low">baixa</option></select>
              <select className="field" value={params.captions.style} onChange={(e) => set("captions", { ...params.captions, style: e.target.value as Params["captions"]["style"] })}>
                <option value="bold">destaque</option><option value="boxed">com fundo</option><option value="clean">limpa</option><option value="none">sem legenda</option></select>
            </div></label>
          <label className="text-xs">Tamanho e caixa{s("captions.size_pct")}
            <div className="flex items-center gap-2">
              <input type="number" min={60} max={160} className="field w-20" value={params.captions.size_pct}
                onChange={(e) => set("captions", { ...params.captions, size_pct: Number(e.target.value) })} />%
              <label className="flex items-center gap-1"><input type="checkbox" checked={params.captions.uppercase}
                onChange={(e) => set("captions", { ...params.captions, uppercase: e.target.checked })} /> MAIÚSCULAS</label>
              <input type="color" aria-label="Cor da palavra falada" value={params.captions.active_color || "#FFE500"}
                onChange={(e) => set("captions", { ...params.captions, active_color: e.target.value.toUpperCase() })} />
            </div></label>
          <label className="text-xs sm:col-span-2">Selo de identificação (nossa foto + nosso nome){s("badge.position")}
            <div className="flex flex-wrap items-center gap-2">
              <input type="checkbox" checked={params.badge.enabled} onChange={(e) => set("badge", { ...params.badge, enabled: e.target.checked })} />
              <select className="field w-auto" value={params.badge.position} onChange={(e) => set("badge", { ...params.badge, position: e.target.value as Params["badge"]["position"] })}>
                <option value="top_left">canto superior esquerdo</option><option value="top_right">canto superior direito</option>
                <option value="bottom_left">canto inferior esquerdo</option><option value="bottom_right">canto inferior direito</option></select>
              <input className="field w-48" maxLength={32} placeholder="Nosso nome (ex.: Malaio Cortes)" value={params.badge.name}
                onChange={(e) => set("badge", { ...params.badge, name: e.target.value })} />
            </div></label>
          {p.params.palette && <div className="text-xs sm:col-span-2">Paleta medida: {p.params.palette.map((c) => <span key={c} className="mr-1 inline-block h-3 w-6 rounded align-middle" style={{ background: c }} title={c} />)} {p.params.contrast != null && `· contraste ${p.params.contrast}`}</div>}
          {(p.params.notes ?? []).length > 0 && <ul className="text-[11px] text-ink-muted sm:col-span-2">{p.params.notes!.map((n, i) => <li key={i}>{n}</li>)}</ul>}
          {p.analysis.vlm_error && <p className="text-[11px] text-ink-faint sm:col-span-2">IA visual indisponível nesta análise ({p.analysis.vlm_error}); valeu a medição.</p>}
          <div className="flex flex-wrap items-center gap-2 sm:col-span-2">
            <button className="btn-take px-3 py-1" disabled={save.isPending} onClick={() => save.mutate()}>Salvar parâmetros</button>
            <input className="field w-28" placeholder="Corte #" value={clip} onChange={(e) => setClip(e.target.value.replace(/\D/g, ""))} />
            <button className="btn-quiet px-3 py-1" disabled={!clip || preview.isPending} onClick={() => preview.mutate()}>Testar num corte nosso</button>
          </div>
          {msg && <p className="text-xs text-ok sm:col-span-2">{msg}</p>}
          <div className="sm:col-span-2"><ErrorNote error={shot.error || avatar.error || save.error || del.error || preview.error || fav.error} /></div>
        </div>
      </div>
    </div>
  );
}

function Capabilities() {
  const q = useQuery({ queryKey: ["ref-caps"], queryFn: () => api.get<{ measured_at: string; items: Cap[] }>("/api/refs/capabilities") });
  return (
    <div>
      <p className="mb-3 text-xs text-ink-muted">Testado com as nossas credenciais em {q.data?.measured_at}, sem pedir permissões novas.</p>
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-ink-muted"><tr><th>Plataforma</th><th>Recurso</th><th>Situação</th><th>Como / alternativa</th></tr></thead>
        <tbody>{(q.data?.items ?? []).map((c, i) => (
          <tr key={i} className="border-t border-suite-line/50 align-top">
            <td className="py-1 pr-2">{c.platform}</td><td className="pr-2">{c.what}</td>
            <td className={`pr-2 font-medium ${STATUS[c.status] ?? ""}`}>{c.status}</td><td className="text-xs text-ink-muted">{c.how}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}
