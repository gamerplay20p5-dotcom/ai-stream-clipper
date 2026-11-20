import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, KeyRound, LogOut, UserPlus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, type AdminUser, type ClipShareRow } from "../api";
import GiveAccess from "../components/GiveAccess";
import { ShareRow, shareDate } from "../components/SharePanel";
import { ConfirmAction, Empty, ErrorNote, Notice, PageHeader, Skeleton, Status } from "../components/ui";

type Secret = { email: string; initial_password: string; note: string };

/** Accounts and access (multi-user stage 1). No public sign-up and no e-mail: the admin hands the password over. */
export default function People() {
  const users = useQuery({ queryKey: ["admin-users"], queryFn: () => api.get<AdminUser[]>("/api/admin/users") });
  const [selected, setSelected] = useState<number | null>(null);
  const [secret, setSecret] = useState<Secret | null>(null);
  const list = users.data ?? [];
  const current = list.find((u) => u.id === selected) ?? null;
  return (
    <div>
      <PageHeader title="Pessoas e acesso"
        description="Crie contas para quem é de fora da equipe e escolha quais cortes cada uma vê. Contas convidadas só enxergam a Minha Biblioteca." />
      <div className="section-flow">
        {secret && <OneTimePassword secret={secret} onClose={() => setSecret(null)} />}
        <CreateAccount onCreated={setSecret} />
        <section aria-labelledby="accounts-title">
          <h2 id="accounts-title" className="display mb-3 text-xl">Contas</h2>
          <ErrorNote error={users.error} />
          {users.isLoading && <Skeleton label="Carregando contas" />}
          {users.data && list.length === 0 && <Empty title="Nenhuma conta cadastrada" />}
          {list.length > 0 && (
            <ul className="people-list">
              {list.map((u) => (
                <li key={u.id} data-current={u.id === selected || undefined}>
                  <button type="button" className="people-row" aria-expanded={u.id === selected}
                    onClick={() => setSelected(u.id === selected ? null : u.id)}>
                    <span className="min-w-0">
                      <strong className="block truncate">{u.email}</strong>
                      <span className="mt-1 block text-xs text-ink-muted">
                        {u.last_login_at ? `Último acesso em ${shareDate(u.last_login_at)}` : "Ainda não entrou"}
                      </span>
                    </span>
                    <span className="flex flex-wrap items-center justify-end gap-1.5">
                      <Status tone={u.role === "admin" ? "accent" : "neutral"}>{u.role === "admin" ? "Equipe" : "Convidado"}</Status>
                      {!u.active && <Status tone="warning">Desativada</Status>}
                      {u.role === "viewer" && <span className="text-xs text-ink-muted">{u.active_shares} {u.active_shares === 1 ? "corte" : "cortes"}</span>}
                    </span>
                  </button>
                  {u.id === selected && current && <AccountDetail user={current} onSecret={setSecret} />}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}

function CreateAccount({ onCreated }: { onCreated: (s: Secret) => void }) {
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"viewer" | "admin">("viewer");
  const [fullAccess, setFullAccess] = useState(false);
  const create = useMutation({
    mutationFn: () => api.post<AdminUser & Secret>("/api/admin/users", { email, role }),
    onSuccess: (r) => {
      setEmail("");
      setRole("viewer");
      setFullAccess(false);
      onCreated(r);
      qc.invalidateQueries({ queryKey: ["admin-users"] });
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  return (
    <form className="panel panel-body grid gap-4 sm:grid-cols-[minmax(0,1fr)_14rem_auto] sm:items-end" onSubmit={submit}>
      <div>
        <label className="label" htmlFor="new-email">Nova conta</label>
        <input id="new-email" type="email" required className="field" value={email} placeholder="nome@empresa.com"
          onChange={(e) => setEmail(e.target.value)} autoComplete="off" />
      </div>
      <div>
        <label className="label" htmlFor="new-role">Tipo</label>
        <select id="new-role" className="field" value={role} onChange={(e) => { setRole(e.target.value as "viewer" | "admin"); setFullAccess(false); }}>
          <option value="viewer">Convidado (só a biblioteca)</option>
          <option value="admin">Equipe (painel completo)</option>
        </select>
      </div>
      <button className="btn-take" disabled={create.isPending || !email || (role === "admin" && !fullAccess)}><UserPlus size={16} aria-hidden /> Criar conta</button>
      {role === "admin" && (
        <label className="admin-ack sm:col-span-3">
          <input type="checkbox" checked={fullAccess} onChange={(e) => setFullAccess(e.target.checked)} />
          <span><strong>Esta conta terá acesso completo à operação da HTS.</strong> Ela poderá aprovar e publicar cortes, mudar regras de
            publicação, conectar contas, compartilhar vídeos, criar e desativar outras contas e ver os servidores. Confirmo que é uma pessoa da equipe.</span>
        </label>
      )}
      {create.error && <div className="sm:col-span-3"><ErrorNote error={create.error} /></div>}
    </form>
  );
}

function OneTimePassword({ secret, onClose }: { secret: Secret; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="one-time" role="status" aria-labelledby="otp-title">
      <h2 id="otp-title" className="display text-lg">Senha inicial de {secret.email}</h2>
      <p className="text-sm text-ink-muted">{secret.note} Ela aparece só agora.</p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="one-time-code">{secret.initial_password}</code>
        <button type="button" className="btn-quiet" onClick={async () => {
          try { await navigator.clipboard.writeText(secret.initial_password); setCopied(true); } catch { setCopied(false); }
        }}><Copy size={15} aria-hidden /> {copied ? "Copiada" : "Copiar"}</button>
        <button type="button" className="btn-quiet" onClick={onClose}>Já entreguei, ocultar</button>
      </div>
    </section>
  );
}

function AccountDetail({ user, onSecret }: { user: AdminUser; onSecret: (s: Secret) => void }) {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: ["admin-users"] });
  const shares = useQuery({
    queryKey: ["shares", "user", user.id],
    queryFn: () => api.get<ClipShareRow[]>(`/api/admin/users/${user.id}/shares?include_revoked=true`),
  });
  const patch = useMutation({ mutationFn: (b: object) => api.patch(`/api/admin/users/${user.id}`, b), onSuccess: refresh });
  const reset = useMutation({
    mutationFn: () => api.post<Secret>(`/api/admin/users/${user.id}/reset-password`),
    onSuccess: (r) => { onSecret(r); refresh(); },
  });
  const end = useMutation({ mutationFn: () => api.post(`/api/admin/users/${user.id}/end-sessions`), onSuccess: refresh });
  const active = (shares.data ?? []).filter((s) => s.state === "ativo");
  const past = (shares.data ?? []).filter((s) => s.state !== "ativo");
  return (
    <div className="people-detail">
      <div className="flex flex-wrap gap-2">
        <ConfirmAction title="Gerar uma nova senha?" description="A senha atual deixa de funcionar e as sessões abertas desta conta terminam."
          confirmLabel="Gerar nova senha" pending={reset.isPending} onConfirm={() => reset.mutate()}>
          <KeyRound size={15} aria-hidden /> Nova senha
        </ConfirmAction>
        <button type="button" className="btn-quiet" disabled={end.isPending} onClick={() => end.mutate()}>
          <LogOut size={15} aria-hidden /> Encerrar sessões
        </button>
        {user.active
          ? <ConfirmAction title="Desativar esta conta?" description="A pessoa sai na próxima ação e não entra mais. Os compartilhamentos ficam guardados."
              confirmLabel="Desativar" pending={patch.isPending} onConfirm={() => patch.mutate({ active: false })}>Desativar conta</ConfirmAction>
          : <button type="button" className="btn-quiet" disabled={patch.isPending} onClick={() => patch.mutate({ active: true })}>Reativar conta</button>}
      </div>
      {end.isSuccess && <Notice>Sessões encerradas.</Notice>}
      <ErrorNote error={patch.error || reset.error || end.error} />
      <GiveAccess user={user} shares={shares.data ?? []} />
      {user.role === "viewer" && (
        <div>
          <h3 className="mb-2 text-sm font-semibold">Cortes compartilhados</h3>
          <ErrorNote error={shares.error} />
          {shares.data && active.length === 0 && (
            <p className="text-sm text-ink-muted">Nenhum corte ativo. Use “Dar mais acesso” acima ou a aba Compartilhar da Central de cortes.</p>
          )}
          <ul className="share-list">{active.map((s) => <ShareRow key={s.id} share={s} showClip />)}</ul>
          {past.length > 0 && (
            <details className="mt-3 text-sm">
              <summary className="text-ink-muted">Histórico ({past.length})</summary>
              <ul className="share-list mt-2">{past.map((s) => <ShareRow key={s.id} share={s} showClip />)}</ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
