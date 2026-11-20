import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, LogOut, Menu, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { api, type Stats } from "../api";
import { currentNav, staffNav } from "../lib/navigation";
import { Brand } from "./Brand";
import { ErrorNote } from "./ui";

function readOpen(): Record<string, boolean> {
  try { return JSON.parse(localStorage.getItem("asc:nav-open") ?? "{}"); } catch { return {}; }
}

function WorkspaceNav({ path, aba, count, onNavigate }: { path: string; aba: string | null; count: number; onNavigate?: () => void }) {
  const id = useId();
  const [open, setOpen] = useState<Record<string, boolean>>(readOpen);
  const toggle = (key: string, value: boolean) => setOpen((previous) => {
    const next = { ...previous, [key]: value };
    try { localStorage.setItem("asc:nav-open", JSON.stringify(next)); } catch { /* per-viewer convenience only */ }
    return next;
  });
  return (
    <nav className="workspace-nav" aria-label="Navegação principal">
      {staffNav.map((group) => {
        const groupId = `${id}-${group.key}`;
        const active = group.items.some((item) => item.match(path, aba));
        const expanded = !group.collapsible || active || Boolean(open[group.key]);
        return (
          <div key={group.key} className="nav-group" data-secondary={group.collapsible || undefined}>
            {group.collapsible ? (
              <button type="button" className="nav-group-toggle" data-current={active || undefined} aria-expanded={expanded}
                aria-controls={groupId} onClick={() => !active && toggle(group.key, !expanded)}>
                {group.label}<ChevronDown size={14} aria-hidden />
              </button>
            ) : <p className="nav-group-label">{group.label}</p>}
            <ul id={groupId} hidden={!expanded}>
              {group.items.map(({ to, icon: Icon, label, match, badge }) => {
                const current = match(path, aba);
                return (
                  <li key={to}>
                    <Link to={to} className="workspace-link" aria-current={current ? "page" : undefined} onClick={onNavigate}>
                      <Icon size={16} strokeWidth={1.75} aria-hidden />
                      <span>{label}</span>
                      {badge === "review" && count > 0 && <span className="nav-count" aria-label={`${count} cortes para revisar`}>{count}</span>}
                    </Link>
                  </li>
                );
              })}
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
  const aba = new URLSearchParams(search).get("aba");
  const where = currentNav(pathname, aba);
  const section = pathname.split("/")[1] ?? "";
  useEffect(() => {
    if (!hash) window.scrollTo(0, 0);
  }, [section, aba, hash]);
  const group = where?.group;
  const page = where?.item.label;
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
        <WorkspaceNav path={pathname} aba={aba} count={toReview} />
        <div className="sidebar-footer">
          <Link to="/patch-notes" className="font-mono text-xs">v{__APP_VERSION__}</Link>
          <span>HowTo.Software</span>
        </div>
      </aside>

      <div className="workspace-content">
        <header className="workspace-topbar">
          <div className="flex min-w-0 items-center gap-3">
            <Link to="/" className="console-brand" aria-label="AI Stream Clipper, início"><Brand /></Link>
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
                <h2 className="display text-lg">Minha conta</h2>
                <div className="border-b border-suite-line pb-3">
                  <div className="break-all text-sm font-medium">{email}</div>
                  <p className="mt-1 text-xs text-ink-muted">Administrador da equipe · HTS</p>
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
          <WorkspaceNav path={pathname} aba={aba} count={toReview} onNavigate={() => drawer.current?.close()} />
        </div>
      </dialog>
    </div>
  );
}
