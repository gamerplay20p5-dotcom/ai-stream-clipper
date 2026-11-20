import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Download, Film, LogOut, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type LibraryItem } from "../api";
import { Brand } from "../components/Brand";
import { Empty, ErrorNote, LanguagePicker, Notice, Skeleton, Status } from "../components/ui";
import { seconds } from "../lib/format";
import { t, locale } from "../i18n";

const day = new Intl.DateTimeFormat(locale, { day: "numeric", month: "long", timeZone: "America/Sao_Paulo" });
const fmt = (iso: string | null) => (iso ? day.format(new Date(iso)) : "");

/** Status of a library media URL, asking for a single byte. The backend decides; the page only reacts. */
async function mediaStatus(url: string): Promise<number> {
  try {
    const res = await fetch(url, { headers: { Range: "bytes=0-0" }, cache: "no-store", credentials: "same-origin" });
    void res.body?.cancel();
    return res.status;
  } catch {
    return 0; // network failure: not an authorization answer
  }
}
const slug = (name: string) => name.toLowerCase().normalize("NFD").replace(/[^a-z0-9]+/g, "-");
const LOST = t("Seu acesso a este vídeo terminou: a equipe revogou o compartilhamento ou o prazo acabou.");

/** The whole product for an external (non-staff) account: only what was shared with it. */
export default function LibraryApp({ email }: { email: string }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["library"], queryFn: () => api.get<{ items: LibraryItem[] }>("/api/library"), refetchInterval: 60_000,
  });
  const items = list.data?.items ?? [];
  // the library is organised by streamer (largest first); a filter shows one streamer at a time
  const groups = Object.entries(items.reduce<Record<string, LibraryItem[]>>((acc, it) => {
    const k = it.streamer || t("Outros");
    (acc[k] ??= []).push(it);
    return acc;
  }, {})).sort((x, y) => y[1].length - x[1].length || x[0].localeCompare(y[0], locale));
  const [streamer, setStreamer] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const open = items.find((i) => i.clip_id === openId) ?? null;
  const [leaving, setLeaving] = useState(false);
  const [notice, setNotice] = useState("");
  const lose = (message = LOST) => {
    setNotice(message);
    setOpenId(null);
    void qc.invalidateQueries({ queryKey: ["library"] });
  };
  // The list refreshed and the video on screen is no longer shared with this account.
  useEffect(() => {
    if (openId !== null && list.data && !list.data.items.some((i) => i.clip_id === openId)) lose();
  }, [list.data, openId]);

  async function logout() {
    setLeaving(true);
    try {
      await api.post("/api/auth/logout");
    } finally {
      qc.clear();
      window.location.assign("/login");
    }
  }

  return (
    <div className="library-shell">
      <a className="skip-link" href="#conteudo">{t("Ir para o conteúdo")}</a>
      <header className="library-topbar">
        <Brand />
        <div className="flex min-w-0 items-center gap-3">
          <span className="hidden truncate text-xs text-ink-muted sm:inline">{email}</span>
          <button type="button" className="btn-quiet" onClick={logout} disabled={leaving}>
            <LogOut size={15} aria-hidden />{leaving ? t("Saindo…") : t("Sair")}
          </button>
        </div>
      </header>
      <section className="library-hero" aria-labelledby="library-title">
        <div className="library-hero-inner">
          <div>
            <h1 id="library-title" className="library-title">{t("Minha biblioteca")}</h1>
            <p className="mt-2 max-w-[52ch] text-sm text-ink-muted">
              {t("Cortes que a equipe da HTS compartilhou com você. Cada vídeo fica disponível enquanto o acesso estiver ativo.")}
            </p>
          </div>
          {list.data && <p className="text-sm text-ink-muted">
            <strong className="text-ink">{items.length}</strong> {items.length === 1 ? t("vídeo") : t("vídeos")}
          </p>}
        </div>
      </section>
      <main id="conteudo" tabIndex={-1} className="library-main">

        <ErrorNote error={list.error} />
        {list.isLoading && <Skeleton label={t("Carregando sua biblioteca")} />}
        {list.data && items.length === 0 && (
          <Empty title={t("Nenhum vídeo compartilhado ainda")}>
            {t("Quando a equipe compartilhar um corte com o seu e-mail, ele aparece aqui. Não é preciso recarregar a página.")}
          </Empty>
        )}

        {notice && <div className="flex flex-wrap items-center gap-2"><Notice tone="danger">{notice}</Notice>
          <button type="button" className="btn-quiet" onClick={() => setNotice("")}>{t("Ok")}</button></div>}
        {open && <Viewer key={open.clip_id} item={open} onClose={() => setOpenId(null)} onLost={lose}
          onRefresh={() => void qc.invalidateQueries({ queryKey: ["library"] })} />}

        {items.length > 0 && (
          <>
            {groups.length > 1 && (
              <nav className="library-streamers" aria-label={t("Streamers")}>
                <button type="button" aria-pressed={streamer === ""} onClick={() => setStreamer("")}>
                  {t("Todos")} <span>{items.length}</span></button>
                {groups.map(([name, list]) => (
                  <button key={name} type="button" aria-pressed={streamer === name} onClick={() => setStreamer(name)}>
                    {name} <span>{list.length}</span></button>
                ))}
              </nav>
            )}
            {groups.filter(([name]) => !streamer || name === streamer).map(([name, list]) => (
              <section key={name} className="library-group" aria-labelledby={`grp-${slug(name)}`}>
                <h2 id={`grp-${slug(name)}`} className="library-group-title">{name}
                  <span>{list.length} {list.length === 1 ? t("vídeo") : t("vídeos")}</span></h2>
                <ul className="library-grid" aria-label={t("Vídeos de {name}", { name: name })}>
                  {list.map((item) => (
                    <li key={item.clip_id}>
                      <button type="button" className="library-card" aria-current={item.clip_id === openId || undefined}
                        onClick={() => { setNotice(""); setOpenId(item.clip_id); }}>
                        <span className="library-frame">
                          <img src={item.thumbnail_url} alt="" loading="lazy" onError={(e) => { e.currentTarget.hidden = true; }} />
                          <Film className="library-frame-fallback" aria-hidden />
                          <span className="library-play" aria-hidden><Play size={16} fill="currentColor" /></span>
                          <span className="library-duration">{seconds(item.duration_s)}</span>
                        </span>
                        <span className="mt-2.5 block text-sm font-semibold leading-tight">{item.title}</span>
                        <span className="mt-2 flex flex-wrap gap-1.5">
                          {item.permissions.download ? <Status tone="success">{t("Download liberado")}</Status> : <Status>{t("Só assistir")}</Status>}
                          {item.expires_at && <Status tone="warning"><Clock size={12} aria-hidden /> {t("até")} {fmt(item.expires_at)}</Status>}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </>
        )}
      </main>
      <footer className="library-footer">
        <span>{t("AI Stream Clipper by HTS · HowTo.Software")}</span>
        <a href="/privacidade">{t("Privacidade")}</a>
        <a href="/termos">{t("Termos de uso")}</a>
        <a href="/conta/senha">{t("Trocar senha")}</a>
        <LanguagePicker />
      </footer>
    </div>
  );
}

function Viewer({ item, onClose, onLost, onRefresh }: {
  item: LibraryItem; onClose: () => void; onLost: (message?: string) => void; onRefresh: () => void;
}) {
  const [source, setSource] = useState(item.video_url);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  /** Map the backend's answer for this media to what the person can do next. */
  async function react(url: string): Promise<boolean> {
    const status = await mediaStatus(url);
    if (status === 401) {
      window.dispatchEvent(new Event("asc:unauthorized")); // session ended: back to sign-in
      return false;
    }
    if (status === 404) {
      onLost();
      return false;
    }
    if (status === 403) {
      setProblem(t("O download deste vídeo não está mais liberado. Você ainda pode assistir enquanto o acesso estiver ativo."));
      onRefresh();
      return false;
    }
    if (status < 200 || status >= 300) {
      setProblem(t("Não foi possível carregar o vídeo agora. Verifique a conexão e tente de novo."));
      return false;
    }
    return true;
  }

  async function download(url: string) {
    setBusy(true);
    setProblem("");
    try {
      if (await react(url)) window.location.assign(url);
    } finally {
      setBusy(false);
    }
  }
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus({ preventScroll: false });
  }, []);
  return (
    <section className="library-viewer" aria-labelledby="viewer-title">
      <video key={source} src={source} controls playsInline preload="metadata" poster={item.thumbnail_url}
        className="library-video" aria-label={t("Vídeo: {title}", { title: item.title })} onError={() => void react(source)}
        onPlay={onRefresh /* re-check access whenever playback starts: a buffered video would otherwise keep playing */} />
      <div className="min-w-0 space-y-4">
        <div>
          <h2 id="viewer-title" ref={heading} tabIndex={-1} className="display text-xl outline-none">{item.title}</h2>
          <p className="mt-1 text-sm text-ink-muted">{item.streamer} · {seconds(item.duration_s)}</p>
        </div>
        <dl className="library-facts">
          <div><dt>{t("Compartilhado em")}</dt><dd>{fmt(item.shared_at) || "—"}</dd></div>
          <div><dt>{t("Disponível até")}</dt><dd>{item.expires_at ? fmt(item.expires_at) : t("Sem prazo definido")}</dd></div>
          <div><dt>{t("Versão")}</dt><dd>{item.version === "atual" ? t("Sempre a mais recente") : t("A versão aprovada pela equipe")}</dd></div>
        </dl>
        {item.variants.length > 0 && (
          <fieldset className="space-y-2">
            <legend className="label">{t("Outras versões")}</legend>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-quiet" aria-pressed={source === item.video_url} onClick={() => setSource(item.video_url)}>{t("Principal")}</button>
              {item.variants.map((v) => (
                <button key={v.name} type="button" className="btn-quiet" aria-pressed={source === v.video_url}
                  onClick={() => setSource(v.video_url)}>{v.label}</button>
              ))}
            </div>
          </fieldset>
        )}
        {problem && <Notice tone="danger">{problem}</Notice>}
        <div className="flex flex-wrap gap-2">
          {item.download_url
            ? <button type="button" className="btn-take" disabled={busy} onClick={() => void download(item.download_url!)}>
                <Download size={16} aria-hidden /> {busy ? t("Verificando…") : t("Baixar vídeo")}</button>
            : <p className="text-sm text-ink-muted">{t("A equipe liberou este vídeo só para assistir.")}</p>}
          <button type="button" className="btn-quiet" onClick={onClose}>{t("Fechar")}</button>
        </div>
      </div>
    </section>
  );
}
