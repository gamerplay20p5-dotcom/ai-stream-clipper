import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Share2, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type AdminUser, type ClipShareRow } from "../api";
import { ConfirmAction, ErrorNote, Notice, Status } from "./ui";

const day = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeZone: "America/Sao_Paulo" });
export const shareDate = (iso: string | null) => (iso ? day.format(new Date(iso)) : "");

/** One share row with the actions that keep sense for it: download permission, re-approval and revocation. */
export function ShareRow({ share, showClip, showEmail }: { share: ClipShareRow; showClip?: boolean; showEmail?: boolean }) {
  const qc = useQueryClient();
  const done = () => {
    qc.invalidateQueries({ queryKey: ["shares"] });
    qc.invalidateQueries({ queryKey: ["admin-users"] });
  };
  const patch = useMutation({ mutationFn: (b: object) => api.patch(`/api/shares/${share.id}`, b), onSuccess: done });
  const reapprove = useMutation({ mutationFn: () => api.post(`/api/shares/${share.id}/reapprove`), onSuccess: done });
  const revoke = useMutation({ mutationFn: () => api.del(`/api/shares/${share.id}`), onSuccess: done });
  const active = share.state === "ativo";
  const who = share.email ?? `conta #${share.user_id}`;
  const what = share.clip_title || `Corte #${share.clip_id}`;
  return (
    <li className="share-row">
      <div className="min-w-0">
        <p className="truncate font-medium">
          {showClip && (share.clip_title || `Corte #${share.clip_id}`)}
          {showClip && showEmail && " · "}
          {showEmail && share.email}
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
          <Status tone={active ? "success" : share.state === "expirado" ? "warning" : "neutral"}>{share.state}</Status>
          <span>{share.can_download ? "pode baixar" : "só assistir"}</span>
          {share.expires_at && <span>· até {shareDate(share.expires_at)}</span>}
          <span>· {share.follow_latest ? "acompanha a versão mais recente" : `versão ${share.shared_version ?? "—"}`}</span>
          {share.grant_id != null && <span>· via acesso ao streamer</span>}
        </p>
        {active && share.outdated && (
          <p className="mt-1.5 text-xs text-warning">
            O corte foi reeditado. A conta ainda assiste à versão aprovada antes.
          </p>
        )}
      </div>
      {active && (
        <div className="flex flex-wrap items-center gap-2">
          {share.outdated && (
            <ConfirmAction tone="quiet" title="Enviar a versão atual?" label={`Enviar a versão atual de ${what} para ${who}`}
              description={<><p><strong className="text-ink">{who}</strong> passa a assistir à versão atual de <strong className="text-ink">{what}</strong>.</p>
                <p>A versão aprovada antes deixa de aparecer para essa conta.</p></>}
              confirmLabel="Enviar a versão atual" pending={reapprove.isPending} onConfirm={() => reapprove.mutate()}>
              <RefreshCw size={14} aria-hidden /> Enviar a versão atual
            </ConfirmAction>
          )}
          <label className="inline-flex items-center gap-2 text-xs">
            <input type="checkbox" checked={share.can_download} disabled={patch.isPending}
              onChange={(e) => patch.mutate({ can_download: e.target.checked })} />
            Permitir download
          </label>
          <ConfirmAction title="Revogar o acesso?" label={`Revogar o acesso de ${who} a ${what}`}
            description={<><p><strong className="text-ink">{who}</strong> deixa de ver <strong className="text-ink">{what}</strong> na próxima ação, inclusive com a página aberta.</p>
              <p>O registro fica no histórico. Para liberar de novo, compartilhe outra vez.</p></>}
            confirmLabel="Revogar acesso" pending={revoke.isPending} onConfirm={() => revoke.mutate()}>
            <X size={14} aria-hidden /> Revogar
          </ConfirmAction>
        </div>
      )}
      <div className="basis-full"><ErrorNote error={patch.error || reapprove.error || revoke.error} /></div>
    </li>
  );
}

/** Share this clip with an external account. Default: exact approved version, watch only, no expiry. */
export default function SharePanel({ clipId, clipTitle }: { clipId: number; clipTitle: string }) {
  const qc = useQueryClient();
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<AdminUser[]>("/api/admin/users") });
  const shares = useQuery({
    queryKey: ["shares", "clip", clipId], queryFn: () => api.get<ClipShareRow[]>(`/api/shares?clip_id=${clipId}`),
  });
  const [email, setEmail] = useState("");
  const [download, setDownload] = useState(false);
  const [variants, setVariants] = useState(false);
  const [follow, setFollow] = useState(false);
  const [expires, setExpires] = useState("");
  const create = useMutation({
    mutationFn: () => api.post<{ shared: number[] }>("/api/shares", {
      email, clip_ids: [clipId], can_download: download, can_variants: variants, follow_latest: follow,
      expires_at: expires ? new Date(`${expires}T23:59:00-03:00`).toISOString() : null,
    }),
    onSuccess: () => {
      setEmail("");
      qc.invalidateQueries({ queryKey: ["shares"] });
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const viewers = (users.data ?? []).filter((u) => u.role === "viewer" && u.active);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <section className="space-y-5" aria-labelledby="share-title">
      <div>
        <h3 id="share-title" className="display text-lg">Compartilhar “{clipTitle}”</h3>
        <p className="mt-1 max-w-[62ch] text-sm text-ink-muted">
          A conta convidada vê só este vídeo na Minha Biblioteca. Ela recebe a versão de agora. Uma reedição não muda o que
          ela assiste até você enviar a versão nova.
        </p>
      </div>
      <form className="share-form" onSubmit={submit}>
        <div className="sm:col-span-2">
          <label className="label" htmlFor="share-email">E-mail da conta convidada</label>
          <input id="share-email" className="field" type="email" required list="share-accounts" value={email}
            onChange={(e) => setEmail(e.target.value)} placeholder="nome@empresa.com" autoComplete="off" />
          <datalist id="share-accounts">{viewers.map((u) => <option key={u.id} value={u.email} />)}</datalist>
          <p className="mt-1.5 text-xs text-ink-muted">A conta precisa existir. Crie em Pessoas e acesso.</p>
        </div>
        <label className="share-option"><input type="checkbox" checked={download} onChange={(e) => setDownload(e.target.checked)} />
          <span><strong>Permitir download</strong><small>Sem isso, a conta só assiste no navegador.</small></span></label>
        <label className="share-option"><input type="checkbox" checked={variants} onChange={(e) => setVariants(e.target.checked)} />
          <span><strong>Mostrar outras versões</strong><small>Original e formatos que já estavam prontos.</small></span></label>
        <label className="share-option"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          <span><strong>Sempre a versão mais recente</strong><small>Reedições passam a valer para a conta sem nova aprovação.</small></span></label>
        <div>
          <label className="label" htmlFor="share-expires">Disponível até (opcional)</label>
          <input id="share-expires" className="field" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
        </div>
        <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
          <button className="btn-take" disabled={create.isPending || !email}><Share2 size={16} aria-hidden /> Compartilhar</button>
          {create.isSuccess && <Notice>Compartilhado. O vídeo já aparece na Minha Biblioteca da conta.</Notice>}
        </div>
        <div className="sm:col-span-2"><ErrorNote error={create.error} /></div>
      </form>
      <div>
        <h4 className="mb-2 text-sm font-semibold">Quem tem acesso</h4>
        <ErrorNote error={shares.error} />
        {shares.data?.length === 0 && <p className="text-sm text-ink-muted">Ninguém de fora da equipe tem acesso a este corte.</p>}
        <ul className="share-list">{shares.data?.map((s) => <ShareRow key={s.id} share={s} showEmail />)}</ul>
      </div>
    </section>
  );
}
