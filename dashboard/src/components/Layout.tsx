import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Brain, Clapperboard, ClipboardCheck, Gauge, HardDrive, LogOut, Radio, Send, Server, SlidersHorizontal } from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { api, type Stats } from "../api";

const nav = [
  { to: "/", label: "Visão geral", icon: Gauge, end: true },
  { to: "/diretor", label: "Diretor IA", icon: Brain },
  { to: "/cortes", label: "Cortes", icon: Clapperboard },
  { to: "/avaliacao", label: "Avaliação", icon: ClipboardCheck },
  { to: "/publicacao", label: "Publicação", icon: Send },
  { to: "/streamers", label: "Streamers", icon: Radio },
  { to: "/ia", label: "Configurações da IA", icon: SlidersHorizontal },
  { to: "/diagnostico", label: "Diagnóstico", icon: Activity },
  { to: "/armazenamento", label: "Armazenamento", icon: HardDrive },
  { to: "/infra", label: "Infraestrutura", icon: Server },
];

export default function Layout({ email }: { email: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => api.get<Stats>("/api/stats"),
    refetchInterval: 10_000,
  });
  const toReview = stats?.clips_by_status?.ready ?? 0;
  const health = useQuery({
    queryKey: ["healthz"],
    queryFn: () => fetch("/healthz", { cache: "no-store" }).then((r) => r.json() as Promise<{ version?: string }>),
    refetchInterval: 60_000,
  });
  const stale = Boolean(health.data?.version && health.data.version !== __APP_VERSION__);

  async function logout() {
    await api.post("/api/auth/logout");
    qc.clear();
    navigate("/login");
    window.location.reload();
  }

  return (
    <div className="flex min-h-screen flex-col md:flex-row">
      <aside className="flex shrink-0 flex-row items-center gap-1 border-b border-suite-line bg-suite-panel px-3 py-2 md:sticky md:top-0 md:h-screen md:w-56 md:flex-col md:items-stretch md:border-b-0 md:border-r md:px-3 md:py-5">
        <div className="mr-3 flex items-center gap-2.5 md:mb-8 md:mr-0 md:px-2">
          <span className="relative block h-7 w-4 rounded-[3px] bg-take" aria-hidden>
            <span className="absolute left-1/2 top-1/2 h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-suite" />
          </span>
          <span className="display hidden text-base md:inline">Stream Clipper</span>
        </div>
        <nav className="flex flex-1 flex-row gap-1 overflow-x-auto md:flex-col" aria-label="Principal">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 whitespace-nowrap rounded-md px-2.5 py-2 text-sm transition-colors ${
                  isActive ? "bg-suite-raised text-ink" : "text-ink-muted hover:bg-suite-raised/60 hover:text-ink"
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden />
              <span className="hidden sm:inline">{label}</span>
              {to === "/cortes" && toReview > 0 && (
                <span className="ml-auto rounded-full bg-take px-1.5 text-xs font-semibold text-take-ink">{toReview}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="hidden border-t border-suite-line pt-4 md:block">
          <p className="truncate px-2 text-xs text-ink-muted" title={email}>
            {email}
          </p>
          <button onClick={logout} className="mt-2 flex w-full items-center gap-3 rounded-md px-2.5 py-2 text-sm text-ink-muted hover:bg-suite-raised hover:text-ink">
            <LogOut className="h-4 w-4" aria-hidden /> Sair
          </button>
        </div>
        <button onClick={logout} className="p-2 text-ink-muted md:hidden" aria-label="Sair">
          <LogOut className="h-4 w-4" />
        </button>
      </aside>
      <main className="min-w-0 flex-1 px-4 py-6 sm:px-8 lg:px-10">
        {stale && (
            <div role="alert" className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-md border border-take/50 bg-take-dim px-3 py-2 text-sm text-take">
              <span>O sistema foi atualizado para a versão {health.data?.version} e esta aba ainda roda a {__APP_VERSION__}.</span>
              <button className="btn-take" onClick={() => window.location.reload()}>Recarregar agora</button>
            </div>
          )}
          <Outlet />
      </main>
    </div>
  );
}
