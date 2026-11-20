import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorNote, Notice, Skeleton } from "./ui";

type Control = { paused: boolean; reason?: string; by?: string; at?: string };
type Item = { id: number; status: string; platform: string; origin: string; account: string; privacy: string; when: string | null;
  title: string; clip_id: number | null; score: number | null; thumb: boolean; version: number; last_error: string };
export type CalendarData = { timezone: string; items: Item[] };
type Report = {
  days: number; note: string;
  platforms: Record<string, { published: number; views: number; likes: number; comments: number; failed: number; with_metrics: number }>;
  categories: { category: string; published: number; views: number; avg_views: number }[];
  hours: { hour: number; published: number; views: number; avg_views: number }[];
  top: { id: number; title: string; platform: string; views: number; likes: number; url: string }[];
  proposals: { confirmed: number; dismissed: number; expired: number; pending: number };
};

const ST: Record<string, [string, string]> = {
  proposed: ["Proposta", "text-take"], scheduled: ["Agendado", "text-ink"], uploading: ["Enviando", "text-take"],
  processing: ["Processando", "text-take"], published: ["Publicado", "text-ok"], expired: ["Expirou", "text-ink-faint"],
  failed: ["Falhou", "text-tally"], uncertain: ["Resultado incerto", "text-warning"],
};
const PRIV: Record<string, string> = { private: "privado", unlisted: "não listado", public: "PÚBLICO" };
const n = (v: number) => v.toLocaleString("pt-BR");

export function PauseBanner() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["publish-control"], queryFn: () => api.get<Control>("/api/publish/control"), refetchInterval: 20_000 });
  const [reason, setReason] = useState("");
  const set = useMutation({
    mutationFn: (paused: boolean) => api.post<Control>("/api/publish/control", { paused, reason }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["publish-control"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); },
  });
  const c = q.data;
  if (!c) return q.isLoading ? <Skeleton label="Consultando estado dos envios" lines={1} /> : <ErrorNote error={q.error} />;
  return c.paused ? (
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-tally/60 bg-tally/10 px-3 py-2 text-sm">
      <b className="text-tally">Publicação PAUSADA</b>
      <span className="text-ink-muted">por {c.by}{c.reason ? `: ${c.reason}` : ""}. Nada novo é enviado; o que já estava sendo enviado termina.</span>
      <button className="btn-take ml-auto px-3 py-1 text-sm" disabled={set.isPending} onClick={() => set.mutate(false)}>Retomar publicações</button>
      <ErrorNote error={set.error} />
    </div>
  ) : (
    <div className="panel flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
      <p className="mr-auto font-semibold">Envios habilitados</p>
      <input aria-label="Motivo da pausa" className="field max-w-xs py-1 text-sm" placeholder="Motivo (opcional)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button className="btn-danger px-3 py-1 text-sm" disabled={set.isPending}
        onClick={() => window.confirm("Pausar TODAS as publicações agora?") && set.mutate(true)}>Pausa de emergência</button>
      <ErrorNote error={set.error} />
    </div>
  );
}

export function EditorialCalendar() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["calendar"], queryFn: () => api.get<CalendarData>("/api/publish/calendar?days=14"), refetchInterval: 20_000 });
  const [sel, setSel] = useState<number[]>([]);
  const done = () => { setSel([]); qc.invalidateQueries({ queryKey: ["calendar"] }); qc.invalidateQueries({ queryKey: ["publications"] }); };
  const confirm = useMutation({ mutationFn: (ids: number[]) => api.post<{ confirmed: number[]; refused: Record<string, string[]> }>("/api/publish/proposals/confirm", { ids }), onSuccess: done });
  const dismiss = useMutation({ mutationFn: (ids: number[]) => api.post("/api/publish/proposals/dismiss", { ids }), onSuccess: done });
  const move = useMutation({ mutationFn: ({ id, at }: { id: number; at: string | null }) => api.patch(`/api/publish/publications/${id}`, { scheduled_at: at }), onSuccess: done });
  const items = q.data?.items ?? [];
  const proposals = items.filter((i) => i.status === "proposed");
  const days = new Map<string, Item[]>();
  for (const it of items) {
    const d = it.when ? it.when.slice(0, 10) : "sem data";
    days.set(d, [...(days.get(d) ?? []), it]);
  }
  const refused = Object.entries(confirm.data?.refused ?? {});
  return (
    <div className="space-y-4">
      {proposals.length > 0 && (
        <div className="panel panel-body border-take/50">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <b className="text-sm">{proposals.length} proposta(s) do modo semiautomático</b>
            <span className="text-xs text-ink-muted">Nada é enviado sem a sua confirmação; propostas não confirmadas expiram no horário.</span>
            <button className="btn-take ml-auto px-3 py-1 text-sm" disabled={confirm.isPending}
              onClick={() => confirm.mutate(sel.length ? sel : proposals.map((p) => p.id))}>
              Confirmar {sel.length ? `${sel.length} selecionada(s)` : "todas"}
            </button>
            <button className="btn-quiet px-3 py-1 text-sm" disabled={!sel.length || dismiss.isPending} onClick={() => dismiss.mutate(sel)}>Recusar selecionadas</button>
          </div>
          {proposals.some((p) => p.privacy === "public") && <p className="mb-2 text-xs text-tally">Atenção: há propostas PÚBLICAS. Confirmar publica para todos.</p>}
          {refused.length > 0 && <ul className="mb-2 text-xs text-tally">{refused.map(([k, v]) => <li key={k}>#{k}: {v.join("; ")}</li>)}</ul>}
        </div>
      )}
      <ErrorNote error={q.error || confirm.error || dismiss.error || move.error} />
      {q.isLoading && <Skeleton label="Carregando calendário editorial" />}
      {q.data && items.length === 0 && <Empty title="Calendário vazio">Propostas e publicações agendadas aparecem nesta agenda.</Empty>}
      {confirm.isSuccess && confirm.data.confirmed.length > 0 && <Notice>{confirm.data.confirmed.length} proposta(s) confirmada(s).</Notice>}
      {dismiss.isSuccess && <Notice>Propostas recusadas.</Notice>}
      {move.isSuccess && <Notice>Horário atualizado.</Notice>}
      {Array.from(days.entries()).map(([d, list]) => (
        <section key={d} className="agenda-day">
          <h2 className="agenda-date">
            {d === "sem data" ? "Imediato" : <time dateTime={d}><strong>{d.slice(8,10)}</strong> {new Date(d + "T12:00:00").toLocaleDateString("pt-BR", { weekday:"short", month:"short", year:"numeric" })}</time>}
          </h2>
          <ul className="divide-y divide-suite-line">
            {list.map((it) => (
              <li key={it.id} className="flex flex-wrap items-start gap-3 px-4 py-3 text-sm sm:px-5">
                {it.status === "proposed" && (
                  <input type="checkbox" checked={sel.includes(it.id)} aria-label={`Selecionar proposta ${it.id}`}
                    onChange={() => setSel(sel.includes(it.id) ? sel.filter((x) => x !== it.id) : [...sel, it.id])} />
                )}
                <span className="w-12 font-mono text-sm font-semibold">{it.when ? it.when.slice(11, 16) : "agora"}</span>
                {it.thumb && it.clip_id && <img src={`/api/clips/${it.clip_id}/thumbnail?v=${it.version}`} alt="" className="h-10 w-6 rounded object-cover" />}
                <div className="min-w-[9rem] flex-1">
                  {it.clip_id ? <Link className="break-words font-semibold hover:text-take hover:underline"
                    to={`/cortes/${it.clip_id}?status=`}>{it.title || `Corte #${it.clip_id}`}</Link>
                    : <p className="break-words font-semibold">{it.title || "Sem título"}</p>}
                  <p className="mt-1 break-words text-xs text-ink-muted">{it.platform} / {it.account} / {PRIV[it.privacy] ?? it.privacy}{it.score != null ? ` / nota ${Math.round(it.score)}` : ""}</p>
                </div>
                <span className={`rounded bg-suite-raised px-2 py-1 text-xs font-semibold ${ST[it.status]?.[1] ?? "text-ink-muted"}`}>{ST[it.status]?.[0] ?? it.status}</span>
                {["proposed", "scheduled"].includes(it.status) && (
                  <details className="w-full pl-0 sm:pl-14">
                    <summary className="py-1 text-sm text-take">Reagendar</summary>
                    <form key={it.when} className="mt-2 flex flex-wrap gap-2" onSubmit={(event) => {
                      event.preventDefault();
                      const value = String(new FormData(event.currentTarget).get("when"));
                      if (!value || value === it.when?.slice(0, 16)) return;
                      const zone = new Intl.DateTimeFormat("en", { timeZone: q.data?.timezone, timeZoneName: "longOffset" });
                      const offset = zone.formatToParts(new Date(value + "Z")).find((part) => part.type === "timeZoneName")?.value.slice(3) || "+00:00";
                      move.mutate({ id: it.id, at: value + offset });
                    }}>
                      <input type="datetime-local" name="when" required className="field w-auto text-xs" aria-label="Novo horário em Brasília"
                        defaultValue={it.when?.slice(0, 16) ?? ""} />
                      <button className="btn-quiet" disabled={move.isPending}>Salvar horário</button>
                    </form>
                  </details>
                )}
                {it.last_error && ["failed", "uncertain"].includes(it.status) && <span className="w-full break-words text-xs text-tally">{it.last_error}</span>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function Reports() {
  const [days, setDays] = useState(30);
  const q = useQuery({ queryKey: ["publish-report", days], queryFn: () => api.get<Report>(`/api/publish/report?days=${days}`) });
  const r = q.data;
  return (
    <div>
      <div className="mb-3 flex items-center gap-2 text-sm">
        <label htmlFor="report-period">Período</label>
        <select id="report-period" className="field w-auto py-1" value={days} onChange={(e) => setDays(Number(e.target.value))}>
          <option value={7}>7 dias</option><option value={30}>30 dias</option><option value={90}>90 dias</option>
        </select>
      </div>
      <ErrorNote error={q.error} />
      {q.isLoading && <Skeleton label="Carregando relatório de publicações" />}
      {r && (
        <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
          <div>
            <h2 className="display mb-2 text-lg">Por plataforma</h2>
            <div className="table-scroll" tabIndex={0} role="region" aria-label="Relatório por plataforma"><table className="w-full min-w-[24rem] text-sm"><thead className="text-xs text-ink-muted"><tr><th className="text-left">Plataforma</th><th>Publicados</th><th>Views</th><th>Curtidas</th><th>Falhas</th></tr></thead>
              <tbody>{Object.entries(r.platforms).map(([p, v]) => <tr key={p} className="text-center"><td className="text-left">{p}</td><td>{v.published}</td><td>{n(v.views)}</td><td>{n(v.likes)}</td><td>{v.failed}</td></tr>)}</tbody></table></div>
            {!Object.keys(r.platforms).length && <p className="text-xs text-ink-muted">Sem publicações no período.</p>}
          </div>
          <div>
            <h2 className="display mb-2 text-lg">Por categoria da história</h2>
            <ul className="text-sm">{r.categories.map((c) => <li key={c.category} className="flex justify-between"><span>{c.category}</span><span className="text-ink-muted">{c.published} vídeo(s) · média {n(c.avg_views)} views</span></li>)}</ul>
          </div>
          <div>
            <h2 className="display mb-2 text-lg">Por horário de publicação (Brasília)</h2>
            <ul className="text-sm">{r.hours.map((h) => <li key={h.hour} className="flex justify-between"><span>{String(h.hour).padStart(2, "0")}h</span><span className="text-ink-muted">{h.published} vídeo(s) · média {n(h.avg_views)} views</span></li>)}</ul>
          </div>
          <div>
            <h2 className="display mb-2 text-lg">Títulos com mais views</h2>
            <ol className="list-inside list-decimal text-sm">{r.top.map((t) => <li key={t.id} className="truncate">{t.url ? <a className="hover:underline" href={t.url} target="_blank" rel="noreferrer">{t.title}</a> : t.title} <span className="text-ink-muted">· {n(t.views)}</span></li>)}</ol>
          </div>
          <p className="text-xs text-ink-muted md:col-span-2">
            Propostas semiautomáticas: {r.proposals.confirmed} confirmadas, {r.proposals.dismissed} recusadas, {r.proposals.expired} expiradas, {r.proposals.pending} pendentes. {r.note}
          </p>
        </div>
      )}
    </div>
  );
}
