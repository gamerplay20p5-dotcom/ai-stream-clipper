import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Film, Layers, ShieldCheck, Tv } from "lucide-react";
import { useMemo, useState } from "react";
import { api, type AdminUser, type Clip, type ClipShareRow, type Streamer, type StreamerGrant } from "../api";
import { ConfirmAction, ErrorNote, Notice, Status } from "./ui";
import { shareDate } from "./SharePanel";

type Mode = "streamer" | "clips" | "role";
const MODES: { key: Mode; label: string; icon: typeof Tv }[] = [
  { key: "streamer", label: "Todos os cortes de um streamer", icon: Tv },
  { key: "clips", label: "Escolher cortes", icon: Layers },
  { key: "role", label: "Tipo de conta", icon: ShieldCheck },
];
const expiry = (day: string) => (day ? new Date(`${day}T23:59:00-03:00`).toISOString() : null);

/** More access for an existing account: by streamer, hand-picked clips, or promotion to staff. */
export default function GiveAccess({ user, shares }: { user: AdminUser; shares: ClipShareRow[] }) {
  const [mode, setMode] = useState<Mode>("streamer");
  if (!user.active) {
    return (
      <section className="give-access" aria-labelledby={`give-${user.id}`}>
        <h3 id={`give-${user.id}`} className="text-sm font-semibold">Dar mais acesso</h3>
        <p className="text-sm text-warning">Esta conta está desativada. Reative a conta para dar acesso a mais cortes.</p>
      </section>
    );
  }
  return (
    <section className="give-access" aria-labelledby={`give-${user.id}`}>
      <h3 id={`give-${user.id}`} className="text-sm font-semibold">Dar mais acesso</h3>
      <div className="view-switch" role="tablist" aria-label="Tipo de acesso">
        {MODES.filter((m) => user.role === "viewer" || m.key === "role").map(({ key, label, icon: Icon }) => (
          <button key={key} type="button" role="tab" className="view-switch-item" aria-selected={mode === key}
            aria-pressed={mode === key} onClick={() => setMode(key)}><Icon size={15} aria-hidden /> {label}</button>
        ))}
      </div>
      {mode === "streamer" && user.role === "viewer" && <ByStreamer user={user} />}
      {mode === "clips" && user.role === "viewer" && <PickClips user={user} shares={shares} />}
      {(mode === "role" || user.role === "admin") && <AccountType user={user} />}
    </section>
  );
}

function useStreamers() {
  return useQuery({ queryKey: ["streamers"], queryFn: () => api.get<Streamer[]>("/api/streamers") });
}

function ByStreamer({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const streamers = useStreamers();
  const grants = useQuery({
    queryKey: ["streamer-grants", user.id],
    queryFn: () => api.get<StreamerGrant[]>(`/api/streamer-grants?user_id=${user.id}`),
  });
  const [streamerId, setStreamerId] = useState("");
  const [download, setDownload] = useState(false);
  const [until, setUntil] = useState("");
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["streamer-grants", user.id] });
    qc.invalidateQueries({ queryKey: ["shares"] });
    qc.invalidateQueries({ queryKey: ["admin-users"] });
  };
  const give = useMutation({
    mutationFn: () => api.post<StreamerGrant & { shared_now: number }>("/api/streamer-grants", {
      email: user.email, streamer_id: Number(streamerId), can_download: download, expires_at: expiry(until),
    }),
    onSuccess: () => { setStreamerId(""); refresh(); },
  });
  const revoke = useMutation({ mutationFn: (id: number) => api.del(`/api/streamer-grants/${id}`), onSuccess: refresh });
  const chosen = (streamers.data ?? []).find((s) => String(s.id) === streamerId);
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-muted">
        A conta passa a ver todos os cortes <strong className="text-ink">aprovados</strong> do streamer, inclusive os que forem
        aprovados depois. Um corte rejeitado sai da biblioteca dela.
      </p>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_12rem] sm:items-end">
        <label><span className="label">Streamer</span>
          <select className="field" value={streamerId} onChange={(e) => setStreamerId(e.target.value)}>
            <option value="">Escolha um streamer</option>
            {(streamers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
          </select></label>
        <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" checked={download}
          onChange={(e) => setDownload(e.target.checked)} /> Permitir download</label>
        <label><span className="label">Disponível até (opcional)</span>
          <input type="date" className="field" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
      </div>
      <ConfirmAction tone="quiet" disabled={!streamerId} pending={give.isPending}
        title={`Dar acesso aos cortes de ${chosen?.display_name ?? "…"}?`}
        description={<><p><strong className="text-ink">{user.email}</strong> passa a ver todos os cortes aprovados de{" "}
          <strong className="text-ink">{chosen?.display_name}</strong>, agora e os próximos aprovados.</p>
          <p>{download ? "Download liberado." : "Só assistir (sem download)."}{until ? ` Até ${until.split("-").reverse().join("/")}.` : " Sem prazo."}</p></>}
        confirmLabel="Dar acesso" onConfirm={() => give.mutate()}>
        <Tv size={15} aria-hidden /> Dar acesso ao streamer
      </ConfirmAction>
      {give.isSuccess && <Notice>Acesso dado: {give.data.shared_now} {give.data.shared_now === 1 ? "corte aprovado entrou" : "cortes aprovados entraram"} na biblioteca agora.</Notice>}
      <ErrorNote error={give.error || revoke.error || grants.error} />
      {(grants.data ?? []).length > 0 && (
        <ul className="share-list">
          {grants.data!.map((g) => (
            <li key={g.id} className="share-row">
              <div className="min-w-0">
                <p className="font-medium">Todos os cortes aprovados de {g.streamer}</p>
                <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
                  <Status tone={g.state === "ativo" ? "success" : "neutral"}>{g.state}</Status>
                  <span>{g.active_shares} {g.active_shares === 1 ? "corte" : "cortes"}</span>
                  <span>· {g.can_download ? "pode baixar" : "só assistir"}</span>
                  {g.expires_at && <span>· até {shareDate(g.expires_at)}</span>}
                </p>
              </div>
              <ConfirmAction title="Revogar o acesso ao streamer?" label={`Revogar o acesso de ${user.email} aos cortes de ${g.streamer}`}
                description={<><p><strong className="text-ink">{user.email}</strong> deixa de ver os {g.active_shares} cortes de{" "}
                  <strong className="text-ink">{g.streamer}</strong> que vieram por este acesso, na próxima ação.</p>
                  <p>Cortes compartilhados um a um continuam disponíveis.</p></>}
                confirmLabel="Revogar acesso" pending={revoke.isPending} onConfirm={() => revoke.mutate(g.id)}>
                Revogar
              </ConfirmAction>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PickClips({ user, shares }: { user: AdminUser; shares: ClipShareRow[] }) {
  const qc = useQueryClient();
  const streamers = useStreamers();
  const [streamerId, setStreamerId] = useState("");
  const [picked, setPicked] = useState<number[]>([]);
  const [download, setDownload] = useState(false);
  const clips = useQuery({
    queryKey: ["clips", "pick", streamerId],
    queryFn: () => api.get<{ items: Clip[] }>(`/api/clips?status=approved&sort=recent&limit=120${streamerId ? `&streamer_id=${streamerId}` : ""}`),
  });
  const already = useMemo(() => new Set(shares.filter((s) => s.state === "ativo").map((s) => s.clip_id)), [shares]);
  const available = (clips.data?.items ?? []).filter((c) => c.has_video && !already.has(c.id));
  const share = useMutation({
    mutationFn: () => api.post<{ shared: number[]; refused: Record<string, string> }>("/api/shares", {
      email: user.email, clip_ids: picked, can_download: download,
    }),
    onSuccess: () => {
      setPicked([]);
      qc.invalidateQueries({ queryKey: ["shares"] });
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const toggle = (id: number) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="min-w-[12rem] flex-1"><span className="label">Streamer</span>
          <select className="field" value={streamerId} onChange={(e) => { setStreamerId(e.target.value); setPicked([]); }}>
            <option value="">Todos os streamers</option>
            {(streamers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.display_name}</option>)}
          </select></label>
        <label className="flex min-h-10 items-center gap-2 text-sm"><input type="checkbox" checked={download}
          onChange={(e) => setDownload(e.target.checked)} /> Permitir download</label>
      </div>
      <p className="text-xs text-ink-muted">Cortes aprovados que a conta ainda não tem. A conta recebe a versão de agora.</p>
      <ErrorNote error={clips.error || share.error} />
      {clips.data && available.length === 0 && <p className="text-sm text-ink-muted">Nenhum corte aprovado novo para esta conta.</p>}
      <ul className="pick-grid">
        {available.map((c) => (
          <li key={c.id}>
            <label className="pick-card" data-picked={picked.includes(c.id) || undefined}>
              <input type="checkbox" className="pick-check" checked={picked.includes(c.id)} onChange={() => toggle(c.id)}
                aria-label={`Escolher ${c.title || `Corte #${c.id}`}`} />
              <span className="pick-frame">{c.has_thumbnail
                ? <img alt="" loading="lazy" src={`/api/clips/${c.id}/thumbnail?v=${c.version}`} />
                : <Film size={18} aria-hidden />}</span>
              <span className="line-clamp-2 text-xs font-semibold leading-tight">{c.title || `Corte #${c.id}`}</span>
              <span className="text-[11px] text-ink-muted">{c.streamer?.display_name}</span>
            </label>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-take" disabled={!picked.length || share.isPending} onClick={() => share.mutate()}>
          Compartilhar {picked.length || ""} {picked.length === 1 ? "corte" : "cortes"}
        </button>
        {available.length > 0 && <button type="button" className="btn-quiet" onClick={() => setPicked(picked.length === available.length ? [] : available.map((c) => c.id))}>
          {picked.length === available.length ? "Limpar seleção" : "Selecionar todos da lista"}</button>}
        {share.isSuccess && <Notice>{share.data.shared.length} {share.data.shared.length === 1 ? "corte compartilhado" : "cortes compartilhados"}.</Notice>}
      </div>
    </div>
  );
}

function AccountType({ user }: { user: AdminUser }) {
  const qc = useQueryClient();
  const change = useMutation({
    mutationFn: (role: "viewer" | "admin") => api.patch(`/api/admin/users/${user.id}`, { role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin-users"] }),
  });
  return (
    <div className="space-y-3">
      <p className="text-sm text-ink-muted">
        Hoje esta conta é <strong className="text-ink">{user.role === "admin" ? "da equipe" : "convidada"}</strong>.
        {user.role === "admin" ? " Ela acessa o painel inteiro." : " Ela só vê a Minha Biblioteca."}
      </p>
      {user.role === "viewer" ? (
        <ConfirmAction title="Tornar esta conta da equipe?" pending={change.isPending}
          description={<><p><strong className="text-ink">{user.email}</strong> terá acesso completo à operação da HTS: aprovar e publicar
            cortes, mudar regras de publicação, conectar contas, compartilhar vídeos, criar e desativar contas e ver os servidores.</p>
            <p>As sessões abertas dessa conta terminam; ela entra de novo com a mesma senha.</p></>}
          confirmLabel="Tornar da equipe" onConfirm={() => change.mutate("admin")}>
          <ShieldCheck size={15} aria-hidden /> Tornar da equipe
        </ConfirmAction>
      ) : (
        <ConfirmAction tone="quiet" title="Tornar esta conta convidada?" pending={change.isPending}
          description={<p><strong className="text-ink">{user.email}</strong> perde o acesso ao painel e passa a ver só a Minha Biblioteca.
            As sessões abertas terminam.</p>}
          confirmLabel="Tornar convidada" onConfirm={() => change.mutate("viewer")}>
          Tornar convidada
        </ConfirmAction>
      )}
      {change.isSuccess && <Notice>Tipo de conta alterado.</Notice>}
      <ErrorNote error={change.error} />
    </div>
  );
}
