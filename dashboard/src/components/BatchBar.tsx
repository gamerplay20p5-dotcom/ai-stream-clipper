import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import PrivacyPicker, { initialPrivacy } from "./PrivacyPicker";
import { ErrorNote } from "./ui";

type Account = { id: number; platform: string; name: string; handle: string; status: string; meta: { privacy_options?: string[] } };
type Result = { scheduled?: { clip_id: number; scheduled_at: string | null }[]; refused?: Record<string, string[]>;
  done?: number[]; skipped?: Record<string, string> };

/** Batch actions for the selected clips: approve, reject, schedule in the next free calendar slots. */
export default function BatchBar({ ids, onClear }: { ids: number[]; onClear: () => void }) {
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ["publish-overview"], queryFn: () => api.get<{ accounts: Account[] }>("/api/publish/overview") });
  const accounts = (ov.data?.accounts ?? []).filter((a) => a.status === "connected" && a.platform !== "tiktok");  // TikTok: one by one
  const [acct, setAcct] = useState<number | "">("");
  const [rights, setRights] = useState(false);
  const [privacy, setPrivacy] = useState("");
  const chosen = accounts.find((a) => a.id === acct);
  const opts = chosen?.meta.privacy_options ?? [];
  const [res, setRes] = useState<Result | null>(null);
  const done = (r: Result) => { setRes(r); qc.invalidateQueries({ queryKey: ["clips"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); };
  const act = useMutation({ mutationFn: (action: "approve" | "reject") => api.post<Result>("/api/clips/batch", { ids, action }), onSuccess: (r) => { done(r); onClear(); } });
  const sched = useMutation({
    mutationFn: () => api.post<Result>("/api/publish/batch", { clip_ids: ids, account_id: acct, privacy, rights_confirmed: rights }),
    onSuccess: done,
  });
  const refused = Object.entries(res?.refused ?? {});
  return (
    <div className="sticky bottom-3 z-10 mt-4 rounded-lg border border-take/50 bg-suite-panel p-3 shadow-lg">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold">{ids.length} selecionado(s)</span>
        <button className="btn-take px-3 py-1.5 text-sm" disabled={act.isPending} onClick={() => act.mutate("approve")}>Aprovar</button>
        <button className="btn-quiet px-3 py-1.5 text-sm" disabled={act.isPending} onClick={() => act.mutate("reject")}>Rejeitar</button>
        <span className="mx-1 h-5 w-px bg-suite-line" />
        <select className="field w-auto py-1.5 text-sm" value={acct} aria-label="Conta" onChange={(e) => {
          const id = e.target.value ? Number(e.target.value) : "";
          setAcct(id);
          setPrivacy(initialPrivacy(accounts.find((a) => a.id === id)?.meta.privacy_options ?? []));
        }}>
          <option value="">Agendar em…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.platform} · {a.name || a.handle}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-xs">
          <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} />
          tenho direito de republicar
        </label>
        {chosen && <div className="min-w-[220px]"><PrivacyPicker options={opts} value={privacy} onChange={setPrivacy} platform={chosen.platform} /></div>}
        <button className="btn-quiet px-3 py-1.5 text-sm" disabled={!acct || !privacy || sched.isPending} onClick={() => sched.mutate()}>
          Agendar nos próximos horários
        </button>
        <button className="btn-quiet ml-auto px-2 py-1.5 text-xs" onClick={onClear}>Limpar seleção</button>
      </div>
      {res?.scheduled && <p className="mt-2 text-xs text-take">{res.scheduled.length} agendado(s). Veja em Publicação → Calendário.</p>}
      {refused.length > 0 && (
        <ul className="mt-1 text-xs text-tally">{refused.map(([k, v]) => <li key={k}>#{k}: {v.join("; ")}</li>)}</ul>
      )}
      {res?.skipped && Object.keys(res.skipped).length > 0 && (
        <p className="mt-1 text-xs text-ink-muted">Ignorados: {Object.entries(res.skipped).map(([k, v]) => `#${k} (${v})`).join(", ")}</p>
      )}
      <ErrorNote error={act.error || sched.error} />
    </div>
  );
}
