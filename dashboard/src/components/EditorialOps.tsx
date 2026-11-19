import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { ErrorNote, Skeleton } from "./ui";

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

const n = (v: number) => v.toLocaleString("pt-BR");

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
