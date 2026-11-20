import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, FlaskConical, RefreshCw, Square, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import VoicePanel from "../components/VoicePanel";
import { Link } from "react-router-dom";
import { api, type Streamer } from "../api";
import TestClipPanel from "../components/TestClipPanel";
import { Disclosure, Empty, ErrorNote, Notice, PageHeader, Skeleton, Tally } from "../components/ui";
import { ago, platformName, timecode } from "../lib/format";
import { t } from "../i18n";

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
      <PageHeader title={t("Lives e streamers")} description={t("Gerencie os canais monitorados, o estado da captura e as preferências de corte.")} />


      {add.isSuccess && <Notice>{t("Canal adicionado ao monitoramento.")}</Notice>}
      <ErrorNote error={list.error} />
      <MonitorAlert streamers={list.data ?? []} />
      {list.isLoading && <Skeleton label={t("Carregando canais monitorados")} />}
      {list.data && list.data.length > 0 && <section className="panel">
      <header className="panel-heading">
        <h2 className="display text-lg">{t("Canais cadastrados")}</h2>
        <span className="text-sm text-ink-muted">{list.data.filter((channel) => channel.live_session).length} {t("ao vivo / {length} cadastrados", { length: list.data.length })}</span>
      </header>
      <ul className="divide-y divide-suite-line">
        {list.data.map((channel) => <StreamerRow key={channel.id} s={channel} />)}
      </ul>
      </section>}
      <Disclosure title={t("Adicionar canal")}>
      <form onSubmit={submit} className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
        <div>
          <label className="label" htmlFor="url">{t("Link do canal")}</label>
          <input id="url" className="field" required placeholder={t("https://www.twitch.tv/canal ou youtube.com/@canal")} value={url}
            onChange={(e) => setUrl(e.target.value)} list="test-media" />
          {media.data?.enabled && (
            <datalist id="test-media">{media.data.files.map((f) => <option key={f} value={`file://${f}`} />)}</datalist>
          )}
        </div>
        <div>
          <label className="label" htmlFor="name">{t("Nome exibido (opcional)")}</label>
          <input id="name" className="field" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="game">{t("Jogo")}</label>
          <input id="game" className="field" value={game} onChange={(e) => setGame(e.target.value)} />
        </div>
        <button className="btn-take" disabled={add.isPending}>{add.isPending ? t("Adicionando…") : t("Começar a monitorar")}</button>
        <div className="space-y-2 sm:col-span-2 xl:col-span-4">
          <ErrorNote error={add.error} />
          <p className="text-xs text-ink-muted">
            {t("Aceita canais públicos da Twitch e do YouTube. O sistema só analisa transmissões abertas; publicar os cortes exige autorização do criador.")}
            {media.data?.enabled && t(" Vídeos de teste controlados podem ser usados com file://nome-do-arquivo.")}
          </p>
        </div>
      </form>
      </Disclosure>
      {list.data?.length === 0 && <Empty title={t("Nenhum canal cadastrado")}>{t("Abra Adicionar canal e informe o link da transmissão.")}</Empty>}

    </div>
  );
}

function StreamerRow({ s }: { s: Streamer }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: ["streamers"] });
  const patch = useMutation({ mutationFn: (b: object) => api.patch(`/api/streamers/${s.id}`, b), onSuccess: refresh });
  // Manual check: "sent" -> "accepted" (server time) -> "done" only when a result is recorded after acceptance.
  const [clickedAt, setClickedAt] = useState<number | null>(null);
  const check = useMutation({
    mutationFn: () => api.post<{ ok: boolean; requested_at?: string }>(`/api/streamers/${s.id}/check`),
    onMutate: () => setClickedAt(Date.now()),  // the attempt is shown even if the request is refused
    onSuccess: refresh,
  });
  const acceptedAt = check.data?.requested_at ? Date.parse(check.data.requested_at) : null;
  const checkedAt = s.last_checked_at ? Date.parse(s.last_checked_at) : null;
  const done = check.isSuccess && acceptedAt !== null && checkedAt !== null && checkedAt >= acceptedAt && !s.monitor?.check_requested;
  const waiting = check.isSuccess && !done;
  useEffect(() => {  // poll while an accepted check is pending; give up after 3 minutes
    if (!waiting) return;
    const t = setInterval(refresh, 3000);
    const stopAt = setTimeout(() => clearInterval(t), 180_000);
    return () => { clearInterval(t); clearTimeout(stopAt); };
  }, [waiting]);
  const pendingFor = clickedAt ? Math.round((Date.now() - clickedAt) / 1000) : 0;
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
          <MonitorBadge s={s} live={live} />
          <div className="text-xs text-ink-faint">{t("verificado")} {ago(s.last_checked_at)}</div>
        </div>
        <Link to={`/cortes?status=&streamer=${s.id}`} className="w-20 text-sm text-ink-muted hover:text-ink">{t(s.clip_count === 1 ? "{n} corte" : "{n} cortes", { n: s.clip_count })}</Link>
        <label className="flex cursor-pointer items-center gap-2 text-sm">
          <input type="checkbox" className="h-4 w-4 " checked={s.enabled}
            onChange={(e) => patch.mutate({ enabled: e.target.checked })} />
          {t("Monitorar")}
        </label>
        <div className="flex gap-1">
          <button className="btn-quiet p-2" title={t("Gerar corte de teste")} aria-label={t("Gerar corte de teste")} onClick={() => setTesting(!testing)}><FlaskConical className="h-4 w-4" /></button>
          <button className="btn-quiet p-2" title={t("Verificar agora")} aria-label={t("Verificar agora")} disabled={check.isPending} onClick={() => check.mutate()}><RefreshCw className="h-4 w-4" /></button>
          {live && (
            <button className="btn-quiet p-2" title={t("Parar a captura desta transmissão")} aria-label={t("Parar captura")}
              onClick={() => stop.mutate(s.live_session!.id)}><Square className="h-4 w-4" /></button>
          )}
          <button className="btn-quiet p-2" aria-expanded={open} aria-label={t("Preferências e histórico")} onClick={() => setOpen(!open)}>
            <ChevronDown className={`h-4 w-4 transition-transform ${open ? "rotate-180" : ""}`} />
          </button>
        </div>
      </div>
      {s.last_error && s.status === "error" && <p className="mt-2 text-xs text-tally">{s.last_error}</p>}
      {s.monitor?.state === "stale" && (
        <p className="mt-2 text-xs text-warning">
          {t("Monitoramento atrasado: {v}. Não foi possível confirmar se o canal está ao vivo{v2}.", { v: s.last_checked_at ? t("última verificação {ago}", { ago: ago(s.last_checked_at) }) : t("este canal nunca foi verificado"), v2: s.status === "live" || live ? t(" (o último resultado conhecido era ao vivo)") : "" })}
        </p>
      )}
      {clickedAt !== null && (
        <p className="mt-2 text-xs" role="status">
          {check.isPending ? <span className="text-ink-muted">{t("Enviando a solicitação…")}</span>
            : check.isError ? <span className="text-tally">{t("A solicitação não foi aceita ({v}). Nenhuma verificação foi feita.", { v: check.error instanceof Error ? check.error.message : t("erro") })}</span>
            : done ? <span className="text-ok">{t("Verificação concluída: {v}.", { v: s.status === "error" ? t("erro na verificação") : live || s.status === "live" ? t("ao vivo") : t("offline") })}</span>
            : pendingFor < 90 ? <span className="text-ink-muted">{t("Verificação solicitada, aguardando o monitor…")}</span>
            : <span className="text-warning">{t("Solicitada há {pendingFor} s e ainda não executada. O monitoramento pode estar parado.", { pendingFor: pendingFor })}</span>}
        </p>
      )}
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
            <h3 className="text-sm font-semibold">{t("Preferências de corte")}</h3>
            <div><label className="label">{t("Nome exibido")}</label><input name="display_name" className="field" defaultValue={s.display_name} /></div>
            <div><label className="label">{t("Jogo (usado em títulos e hashtags)")}</label><input name="game" className="field" defaultValue={prefs.game ?? ""} /></div>
            <div><label className="label">{t("Enquadramento padrão")}</label>
              <select name="layout" className="field" defaultValue={prefs.layout ?? "auto"}>
                <option value="auto">{t("Automático (detecta webcam)")}</option><option value="gameplay">{t("Gameplay inteira")}</option>
                <option value="zoom">{t("Gameplay ampliada")}</option><option value="split">{t("Webcam em cima")}</option>
              </select></div>
            <div><label className="label">{t("Pontuação mínima deste canal (vazio usa o padrão)")}</label>
              <input name="min_score" type="number" min={0} max={100} className="field" defaultValue={prefs.min_score ?? ""} /></div>
            <ErrorNote error={patch.error} />
            <div className="flex gap-2">
              <button className="btn-take">{t("Salvar preferências")}</button>
              <button type="button" className="btn-danger" onClick={() => window.confirm(t("Remover {display_name} e todos os cortes? Isso não pode ser desfeito.", { display_name: s.display_name })) && del.mutate()}>
                <Trash2 className="h-4 w-4" /> {t("Remover canal")}
              </button>
            </div>
          </form>
          <div>
            <h3 className="mb-2 text-sm font-semibold">{t("Histórico de transmissões")}</h3>
            {detail.data?.sessions?.length ? (
              <ul className="space-y-2 text-sm">
                {detail.data.sessions.map((x) => (
                  <li key={x.id} className="flex justify-between gap-3">
                    <span className="truncate">#{x.id} {x.title || t("Sem título")}</span>
                    <span className="shrink-0 text-ink-muted">
                      {x.status === "live" ? <Tally live /> : `${timecode(x.covered_until)}, ${x.status === "failed" ? "falhou" : "encerrada"} ${ago(x.ended_at)}`}
                    </span>
                  </li>
                ))}
              </ul>
            ) : <p className="text-sm text-ink-muted">{t("Nenhuma transmissão capturada ainda.")}</p>}
          </div>
          <VoicePanel streamerId={s.id} />
        </div>
      )}
    </li>
  );
}


const minutes = (sec: number | null) => (sec === null ? "nunca" : sec < 3600 ? t("há {round} min", { round: Math.round(sec / 60) }) : t("há {replace} h", { replace: (sec / 3600).toFixed(1).replace(".", ",") }));

/** Current monitoring state; the last known result is shown separately and never as current when stale. */
function MonitorBadge({ s, live }: { s: Streamer; live: boolean }) {
  const st = s.monitor?.state;
  if (st === "stale") return <span className="text-xs font-semibold text-warning" title={t("A verificação periódica não aconteceu no prazo")}>{t("Monitoramento atrasado")}</span>;
  if (st === "checking") return <span className="text-xs text-ink-muted">{t("Verificando…")}</span>;
  if (st === "paused" || !s.enabled) return <Tally live={false} label={t("Monitoramento pausado")} />;
  if (st === "error" || s.status === "error") return <span className="text-xs text-tally" title={s.last_error}>{t("Erro na verificação")}</span>;
  return <Tally live={live} label={live ? `Ao vivo, ${timecode(s.live_session!.covered_until)}` : t("Offline")} />;
}

/** Page-level alert: monitoring can be late even while every worker heartbeat looks healthy. */
function MonitorAlert({ streamers }: { streamers: Streamer[] }) {
  const stale = streamers.filter((x) => x.enabled && x.monitor?.state === "stale");
  if (!stale.length) return null;
  const never = stale.some((x) => x.monitor?.last_checked_age_s == null);
  const oldest = Math.max(...stale.map((x) => x.monitor?.last_checked_age_s ?? 0));
  return (
    <div role="alert" className="mb-4 rounded-md border border-warning/60 px-3 py-2 text-sm text-warning">
      <strong>{t("Monitoramento atrasado")}</strong> {t("em {length} {v}: {v2}. Os estados abaixo são o último resultado conhecido e podem não ser atuais.", { length: stale.length, v: stale.length === 1 ? t("canal") : t("canais"), v2: never ? t("há canal que nunca foi verificado") : t("a verificação mais antiga foi {minutes}", { minutes: minutes(oldest) }) })}
    </div>
  );
}
