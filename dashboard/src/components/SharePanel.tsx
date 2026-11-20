import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Share2, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type AdminUser, type ClipShareRow } from "../api";
import { ConfirmAction, ErrorNote, Notice, Status } from "./ui";
import { t, locale } from "../i18n";

const day = new Intl.DateTimeFormat(locale, { dateStyle: "short", timeZone: "America/Sao_Paulo" });
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
  const who = share.email ?? t("conta #{user_id}", { user_id: share.user_id });
  const what = share.clip_title || t("Corte #{clip_id}", { clip_id: share.clip_id });
  return (
    <li className="share-row">
      <div className="min-w-0">
        <p className="truncate font-medium">
          {showClip && (share.clip_title || t("Corte #{clip_id}", { clip_id: share.clip_id }))}
          {showClip && showEmail && " · "}
          {showEmail && share.email}
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-ink-muted">
          <Status tone={active ? "success" : share.state === "expirado" ? "warning" : "neutral"}>{share.state}</Status>
          <span>{share.can_download ? t("pode baixar") : t("só assistir")}</span>
          {share.expires_at && <span>{t("· até")} {shareDate(share.expires_at)}</span>}
          <span>· {share.follow_latest ? t("acompanha a versão mais recente") : t("versão {v}", { v: share.shared_version ?? "—" })}</span>
          {share.grant_id != null && <span>{t("· via acesso ao streamer")}</span>}
        </p>
        {active && share.outdated && (
          <p className="mt-1.5 text-xs text-warning">
            {t("O corte foi reeditado. A conta ainda assiste à versão aprovada antes.")}
          </p>
        )}
      </div>
      {active && (
        <div className="flex flex-wrap items-center gap-2">
          {share.outdated && (
            <ConfirmAction tone="quiet" title={t("Enviar a versão atual?")} label={t("Enviar a versão atual de {what} para {who}", { what: what, who: who })}
              description={<><p><strong className="text-ink">{who}</strong> {t("passa a assistir à versão atual de")} <strong className="text-ink">{what}</strong>.</p>
                <p>{t("A versão aprovada antes deixa de aparecer para essa conta.")}</p></>}
              confirmLabel={t("Enviar a versão atual")} pending={reapprove.isPending} onConfirm={() => reapprove.mutate()}>
              <RefreshCw size={14} aria-hidden /> {t("Enviar a versão atual")}
            </ConfirmAction>
          )}
          <label className="inline-flex items-center gap-2 text-xs">
            <input type="checkbox" checked={share.can_download} disabled={patch.isPending}
              onChange={(e) => patch.mutate({ can_download: e.target.checked })} />
            {t("Permitir download")}
          </label>
          <ConfirmAction title={t("Revogar o acesso?")} label={t("Revogar o acesso de {who} a {what}", { who: who, what: what })}
            description={<><p><strong className="text-ink">{who}</strong> {t("deixa de ver")} <strong className="text-ink">{what}</strong> {t("na próxima ação, inclusive com a página aberta.")}</p>
              <p>{t("O registro fica no histórico. Para liberar de novo, compartilhe outra vez.")}</p></>}
            confirmLabel={t("Revogar acesso")} pending={revoke.isPending} onConfirm={() => revoke.mutate()}>
            <X size={14} aria-hidden /> {t("Revogar")}
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
        <h3 id="share-title" className="display text-lg">{t("Compartilhar “{clipTitle}”", { clipTitle: clipTitle })}</h3>
        <p className="mt-1 max-w-[62ch] text-sm text-ink-muted">
          {t("A conta convidada vê só este vídeo na Minha Biblioteca. Ela recebe a versão de agora. Uma reedição não muda o que ela assiste até você enviar a versão nova.")}
        </p>
      </div>
      <form className="share-form" onSubmit={submit}>
        <div className="sm:col-span-2">
          <label className="label" htmlFor="share-email">{t("E-mail da conta convidada")}</label>
          <input id="share-email" className="field" type="email" required list="share-accounts" value={email}
            onChange={(e) => setEmail(e.target.value)} placeholder={t("nome@empresa.com")} autoComplete="off" />
          <datalist id="share-accounts">{viewers.map((u) => <option key={u.id} value={u.email} />)}</datalist>
          <p className="mt-1.5 text-xs text-ink-muted">{t("A conta precisa existir. Crie em Pessoas e acesso.")}</p>
        </div>
        <label className="share-option"><input type="checkbox" checked={download} onChange={(e) => setDownload(e.target.checked)} />
          <span><strong>{t("Permitir download")}</strong><small>{t("Sem isso, a conta só assiste no navegador.")}</small></span></label>
        <label className="share-option"><input type="checkbox" checked={variants} onChange={(e) => setVariants(e.target.checked)} />
          <span><strong>{t("Mostrar outras versões")}</strong><small>{t("Original e formatos que já estavam prontos.")}</small></span></label>
        <label className="share-option"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          <span><strong>{t("Sempre a versão mais recente")}</strong><small>{t("Reedições passam a valer para a conta sem nova aprovação.")}</small></span></label>
        <div>
          <label className="label" htmlFor="share-expires">{t("Disponível até (opcional)")}</label>
          <input id="share-expires" className="field" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
        </div>
        <div className="sm:col-span-2 flex flex-wrap items-center gap-3">
          <button className="btn-take" disabled={create.isPending || !email}><Share2 size={16} aria-hidden /> {t("Compartilhar")}</button>
          {create.isSuccess && <Notice>{t("Compartilhado. O vídeo já aparece na Minha Biblioteca da conta.")}</Notice>}
        </div>
        <div className="sm:col-span-2"><ErrorNote error={create.error} /></div>
      </form>
      <div>
        <h4 className="mb-2 text-sm font-semibold">{t("Quem tem acesso")}</h4>
        <ErrorNote error={shares.error} />
        {shares.data?.length === 0 && <p className="text-sm text-ink-muted">{t("Ninguém de fora da equipe tem acesso a este corte.")}</p>}
        <ul className="share-list">{shares.data?.map((s) => <ShareRow key={s.id} share={s} showEmail />)}</ul>
      </div>
    </section>
  );
}
