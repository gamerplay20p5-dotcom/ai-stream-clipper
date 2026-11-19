import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FlaskConical, RefreshCw, Square, Trash2 } from "lucide-react";
import { useState, type FormEvent } from "react";
import VoicePanel from "../components/VoicePanel";
import { Link } from "react-router-dom";
import { api, type Streamer } from "../api";
import TestClipPanel from "../components/TestClipPanel";
import { Disclosure, Empty, ErrorNote, Notice, PageHeader, Skeleton, Tally } from "../components/ui";
import { ago, platformName, timecode } from "../lib/format";

export default function Streamers() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers"), refetchInterval: 8_000 });
  const media = useQuery({ queryKey: ["test-media"], queryFn: () => api.get<{ enabled: boolean; files: string[] }>("/api/test-media") });
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [game, setGame] = useState("GTA V");
  const add = useMutation({
    mutationFn: () => api.post<Streamer>("/api/streamers", { url, display_name: name, preferences: { game } }),
    onSuccess: () => {
      setUrl("");
      setName("");
      qc.invalidateQueries({ queryKey: ["streamers"] });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    add.mutate();
  }

  return (
    <div className="space-y-5">
      <PageHeader title="Lives e streamers" description="Gerencie os canais monitorados, o estado da captura e as preferências de corte." />


      {add.isSuccess && <Notice>Canal adicionado ao monitoramento.</Notice>}
      <ErrorNote error={list.error} />
      {list.isLoading && <Skeleton label="Carregando canais monitorados" />}
      {list.data && list.data.length > 0 && <section className="panel">
      <header className="panel-heading">
        <h2 className="display text-lg">Canais cadastrados</h2>
        <span className="text-sm text-ink-muted">{list.data.filter((channel) => channel.live_session).length} ao vivo / {list.data.length} cadastrados</span>
      </header>
      <ul className="divide-y divide-suite-line">
        {list.data.map((channel) => <StreamerRow key={channel.id} s={channel} />)}
      </ul>
      </section>}
      <Disclosure title="Adicionar canal">
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
        <div>
          <label className="label" htmlFor="url">Link do canal</label>
          <input id="url" className="field" required placeholder="https://www.twitch.tv/canal ou youtube.com/@canal" value={url}
            onChange={(e) => setUrl(e.target.value)} list="test-media" />
          {media.data?.enabled && (
            <datalist id="test-media">{media.data.files.map((f) => <option key={f} value={`file://${f}`} />)}</datalist>
          )}
        </div>
        <div>
          <label className="label" htmlFor="name">Nome exibido (opcional)</label>
          <input id="name" className="field" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="game">Jogo</label>
          <input id="game" className="field" value={game} onChange={(e) => setGame(e.target.value)} />
        </div>
        <button className="btn-take" disabled={add.isPending}>{add.isPending ? "Adicionando…" : "Começar a monitorar"}</button>
        <div className="space-y-2 sm:col-span-2 xl:col-span-4">
          <ErrorNote error={add.error} />
          <p className="text-xs text-ink-muted">
            Aceita canais públicos da Twitch e do YouTube. O sistema só analisa transmissões abertas; publicar os cortes exige autorização do criador.
            {media.data?.enabled && " Vídeos de teste controlados podem ser usados com file://nome-do-arquivo."}
          </p>
        </div>
      </form>
      </Disclosure>
      {list.data?.length === 0 && <Empty title="Nenhum canal cadastrado">Abra Adicionar canal e informe o link da transmissão.</Empty>}

    </div>
  );
}

function StreamerRow({ s }: { s: Streamer }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["streamers"] });
  const patch = useMutation({ mutationFn: (b: object) => api.patch(`/api/streamers/${s.id}`, b), onSuccess: refresh });
  const check = useMutation({ mutationFn: () => api.post(`/api/streamers/${s.id}/check`), onSuccess: refresh });
  const del = useMutation({ mutationFn: () => api.del(`/api/streamers/${s.id}`), onSuccess: refresh });
  const stop = useMutation({ mutationFn: (sid: number) => api.post(`/api/sessions/${sid}/stop`), onSuccess: refresh });
  const detail = useQuery({ queryKey: ["streamer", s.id], queryFn: () => api.get<Streamer>(`/api/streamers/${s.id}`), enabled: open });
  const live = !!s.live_session;
  const prefs = s.preferences as { game?: string; min_score?: number; layout?: string };

  return (
    <li className="px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div className="min-w-0 basis-full sm:flex-1">
          <div className="flex items-center gap-3">
            <span className="truncate font-medium">{s.display_name}</span>
            <span className="text-xs text-ink-muted">{platformName[s.platform]}</span>
          </div>
          <a href={s.platform === "file" ? undefined : s.url} target="_blank" rel="noreferrer noopener" className="block truncate text-xs text-ink-muted hover:text-take">{s.url}</a>
        </div>
        <div className="text-sm sm:w-40">
          {s.status === "error" ? (
            <span className="text-xs text-tally" title={s.last_error}>Erro na verificação</span>
          ) : (
            <Tally live={live} label={live ? `Ao vivo, ${timecode(s.live_session!.covered_until)}` : s.enabled ? "Offline" : "Pausado"} />
          )}
          <div className="text-xs text-ink-faint">verificado {ago(s.last_checked_at)}</div>
        </div>
        <Link to={`/cortes?status=&streamer=${s.id}`} className="w-20 text-sm text-ink-muted hover:text-ink">{s.clip_count} {s.clip_count === 1 ? "corte" : "cortes"}</Link>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 " checked={s.enabled}
            onChange={(e) => patch.mutate({ enabled: e.target.checked })} />
          Monitorar
        </label>
        <div className="flex gap-1">
          <button className="btn-quiet p-2" title="Gerar corte de teste" aria-label="Gerar corte de teste" onClick={() => setTesting(!testing)}><FlaskConical className="h-4 w-4" /></button>
          <button className="btn-quiet p-2" title="Verificar agora" aria-label="Verificar agora" disabled={check.isPending} onClick={() => check.mutate()}><RefreshCw className="h-4 w-4" /></button>
          {live && (
            <button className="btn-quiet p-2" title="Parar a captura desta transmissão" aria-label="Parar captura"
              onClick={() => stop.mutate(s.live_session!.id)}><Square className="h-4 w-4" /></button>
          )}
          <button className="btn-quiet p-2" aria-expanded={open} aria-label="Preferências e histórico" onClick={() => setOpen(!open)}>
            <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>
      {s.last_error && s.status === "error" && <p className="mt-2 text-xs text-tally">{s.last_error}</p>}
      {testing && <TestClipPanel streamerId={s.id} onClose={() => setTesting(false)} />}
      {open && (
        <div className="mt-4 grid min-w-0 gap-6 rounded-lg border border-suite-line bg-suite p-4 md:grid-cols-2">
          <form className="space-y-3" onSubmit={(e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            patch.mutate({
              display_name: String(f.get("display_name")),
              preferences: { game: String(f.get("game")), layout: String(f.get("layout")),
                min_score: f.get("min_score") ? Number(f.get("min_score")) : undefined },
            });
          }}>
            <h3 className="text-sm font-semibold">Preferências de corte</h3>
            <div><label className="label">Nome exibido</label><input name="display_name" className="field" defaultValue={s.display_name} /></div>
            <div><label className="label">Jogo (usado em títulos e hashtags)</label><input name="game" className="field" defaultValue={prefs.game ?? ""} /></div>
            <div><label className="label">Enquadramento padrão</label>
              <select name="layout" className="field" defaultValue={prefs.layout ?? "auto"}>
                <option value="auto">Automático (detecta webcam)</option><option value="gameplay">Gameplay inteira</option>
                <option value="zoom">Gameplay ampliada</option><option value="split">Webcam em cima</option>
              </select></div>
            <div><label className="label">Pontuação mínima deste canal (vazio usa o padrão)</label>
              <input name="min_score" type="number" min={0} max={100} className="field" defaultValue={prefs.min_score ?? ""} /></div>
            <ErrorNote error={patch.error} />
            <div className="flex gap-2">
              <button className="btn-take">Salvar preferências</button>
              <button type="button" className="btn-danger" onClick={() => window.confirm(`Remover ${s.display_name} e todos os cortes? Isso não pode ser desfeito.`) && del.mutate()}>
                <Trash2 className="h-4 w-4" /> Remover canal
              </button>
            </div>
          </form>
          <div>
            <h3 className="mb-2 text-sm font-semibold">Histórico de transmissões</h3>
            {detail.data?.sessions?.length ? (
              <ul className="space-y-2 text-sm">
                {detail.data.sessions.map((x) => (
                  <li key={x.id} className="flex justify-between gap-3">
                    <span className="truncate">#{x.id} {x.title || "Sem título"}</span>
                    <span className="shrink-0 text-ink-muted">
                      {x.status === "live" ? <Tally live /> : `${timecode(x.covered_until)}, ${x.status === "failed" ? "falhou" : "encerrada"} ${ago(x.ended_at)}`}
                    </span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-ink-muted">Nenhuma transmissão capturada ainda.</p>}
          </div>
          <VoicePanel streamerId={s.id} />
        </div>
      )}
    </li>
  );
}
