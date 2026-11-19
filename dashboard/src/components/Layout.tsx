import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, CalendarDays, ChevronDown, ChevronRight, Clapperboard, ClipboardCheck, Gauge,
  HardDrive, Library, Link2, LogOut, Menu, Radio, ScrollText, Send, Server, SlidersHorizontal, Sparkles, X,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, type Stats } from "../api";
import { distributionView } from "../lib/publishing";
import { Brand } from "./Brand";
import { ErrorNote } from "./ui";

const groups = [
  { label: "Operação", items: [
    { to: "/", label: "Visão geral", icon: Gauge },
    { to: "/streamers", label: "Lives e streamers", icon: Radio },
    { to: "/diagnostico", label: "Diagnóstico", icon: Activity },
  ] },
  { label: "Conteúdo", items: [
    { to: "/cortes", label: "Central de cortes", icon: Clapperboard },
    { to: "/avaliacao", label: "Avaliação editorial", icon: ClipboardCheck },
  ] },
  { label: "Distribuição", items: [
    { to: "/publicacao", label: "Publicações", icon: Send },
    { to: "/publicacao?aba=calendario", label: "Calendário", icon: CalendarDays },
    { to: "/publicacao?aba=contas", label: "Contas conectadas", icon: Link2 },
  ] },
  { label: "Inteligência", items: [
    { to: "/diretor", label: "Diretor IA", icon: Sparkles },
    { to: "/referencias", label: "Referências", icon: Library },
    { to: "/ia", label: "Parâmetros", icon: SlidersHorizontal },
  ] },
  { label: "Administração", items: [
    { to: "/infra", label: "Infraestrutura", icon: Server },
    { to: "/armazenamento", label: "Armazenamento", icon: HardDrive },
    { to: "/patch-notes", label: "Histórico de versões", icon: ScrollText },
  ] },
];

function WorkspaceNav({ current, count, onNavigate }: { current: string; count: number; onNavigate?: () => void }) {
  const id = useId();
  const [closed, setClosed] = useState<Record<string, string>>({});
  return (
    <nav className="workspace-nav" aria-label="Navegação principal">
      {groups.map(({ label, items }, i) => {
        const groupId = `${id}-${i}`;
        const active = items.some(({ to }) => to === current);
        const expanded = closed[label] === undefined || (active && closed[label] !== current);
        return (
          <div key={label} className="nav-group">
            <button type="button" className="nav-group-toggle" data-current={active || undefined} aria-expanded={expanded} aria-controls={groupId}
              onClick={() => setClosed((previous) => {
                const next = { ...previous };
                if (expanded) next[label] = current;
                else delete next[label];
                return next;
              })}>
              {label}<ChevronDown size={14} aria-hidden />
            </button>
            <ul id={groupId} hidden={!expanded}>
              {items.map(({ to, icon: Icon, label: name }) => (
                <li key={to}>
                  <Link to={to} className="workspace-link" aria-current={to === current ? "page" : undefined} onClick={onNavigate}>
                    <Icon size={16} strokeWidth={1.75} aria-hidden />
                    <span>{name}</span>
                    {to === "/cortes" && count > 0 && <span className="nav-count" aria-label={`${count} cortes para revisar`}>{count}</span>}
                  </Link>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

export default function Layout({ email }: { email: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { pathname, search, hash } = useLocation();
  const section = `/${pathname.split("/")[1] ?? ""}`;
  const distribution = section === "/publicacao" ? distributionView(new URLSearchParams(search)) : undefined;
  const current = distribution && ["contas", "calendario"].includes(distribution.key) ? distribution.to : section;
  useEffect(() => {
    if (!hash) window.scrollTo(0, 0);
  }, [current, hash]);
  const group = groups.find(({ items }) => items.some(({ to }) => to === current));
  const page = distribution?.label ?? group?.items.find(({ to }) => to === current)?.label;
  const drawer = useRef<HTMLDialogElement>(null);
  const [logoutError, setLogoutError] = useState<unknown>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const { data: stats } = useQuery({
    queryKey: ["stats"], queryFn: () => api.get<Stats>("/api/stats"), refetchInterval: 10_000,
  });
  const toReview = stats?.clips_by_status?.ready ?? 0;
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: async () => {
      const response = await fetch("/healthz", { cache: "no-store" });
      if (!response.ok) throw new Error("API indisponível");
      return response.json() as Promise<{ status?: string; version?: string }>;
    },
    refetchInterval: 60_000,
  });
  const stale = Boolean(health.data?.version && health.data.version !== __APP_VERSION__);

  async function logout() {
    setLogoutError(null);
    setIsLoggingOut(true);
    try {
      await api.post("/api/auth/logout");
      qc.clear();
      navigate("/login");
      window.location.reload();
    } catch (error) {
      setLogoutError(error);
      setIsLoggingOut(false);
    }
  }

  return (
    <div className="workspace">
      <a className="skip-link" href="#conteudo">Ir para o conteúdo</a>
      <aside className="workspace-sidebar">
        <Link to="/" className="sidebar-brand" aria-label="AI Stream Clipper, início"><Brand /></Link>
        <WorkspaceNav current={current} count={toReview} />
        <div className="sidebar-footer">
          <Link to="/patch-notes" className="font-mono text-xs">v{__APP_VERSION__}</Link>
          <span>HowTo.Software</span>
        </div>
      </aside>

      <div className="workspace-content">
        <header className="workspace-topbar">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" className="icon-action drawer-toggle" aria-label="Abrir navegação" aria-haspopup="dialog"
              onClick={() => drawer.current?.showModal()}><Menu size={19} aria-hidden /></button>
            <div className="breadcrumb">
              <span>{group?.label}</span><ChevronRight size={14} aria-hidden /><strong>{page}</strong>
            </div>
          </div>
          <div className="flex min-w-0 items-center gap-4">
            <Link to="/infra" className="topbar-health">
              <span className={health.isError ? "bg-tally" : health.data?.status === "ok" ? "bg-ok" : "bg-ink-faint"} />
              {health.isError ? "API indisponível" : health.data?.status === "ok" ? "API online" : "Consultando API"}
            </Link>
            <details className="account-menu" onBlur={(event) => {
              if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false;
            }} onKeyDown={(event) => {
              if (event.key === "Escape") {
                event.currentTarget.open = false;
                event.currentTarget.querySelector("summary")?.focus();
              }
            }}>
              <summary aria-label="Conta e sessão">
                <span className="account-avatar" aria-hidden>{email.charAt(0).toUpperCase()}</span>
                <span className="account-label">Minha conta</span>
                <ChevronDown size={14} aria-hidden />
              </summary>
              <div className="account-popover">
                <div className="border-b border-suite-line pb-3">
                  <div className="break-all text-sm font-semibold">{email}</div>
                  <p className="mt-1 text-xs text-ink-muted">HTS / HowTo.Software</p>
                </div>
                <button type="button" onClick={logout} disabled={isLoggingOut} className="btn-quiet mt-3 w-full">
                  <LogOut size={15} aria-hidden />{isLoggingOut ? "Saindo…" : "Sair"}
                </button>
                <ErrorNote error={logoutError} />
              </div>
            </details>
          </div>
        </header>
        <main id="conteudo" tabIndex={-1} className="workspace-main">
          {stale && (
            <div role="alert" className="notice mb-5 flex-wrap items-center justify-between border-warning/60 bg-warning-dim text-warning">
              <span>O servidor está na versão {health.data?.version}. Esta aba usa a {__APP_VERSION__}.</span>
              <button className="btn-quiet" onClick={() => window.location.reload()}>Recarregar agora</button>
            </div>
          )}
          <Outlet />
        </main>
      </div>

      <dialog ref={drawer} className="workspace-drawer" aria-labelledby="drawer-title" onClick={(event) => {
        if (event.target === event.currentTarget) drawer.current?.close();
      }}>
        <div className="drawer-sheet">
          <div className="drawer-heading">
            <span id="drawer-title" className="sr-only">Menu do AI Stream Clipper</span>
            <Link to="/" onClick={() => drawer.current?.close()}><Brand /></Link>
            <button type="button" autoFocus className="icon-action" onClick={() => drawer.current?.close()} aria-label="Fechar navegação"><X size={18} aria-hidden /></button>
          </div>
          <WorkspaceNav current={current} count={toReview} onNavigate={() => drawer.current?.close()} />
        </div>
      </dialog>
    </div>
  );
}
