import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Pub, PubAccount } from "../pages/Publishing";
import { pubStatus } from "../pages/Publishing";
import { ErrorNote } from "./ui";

type Check = { problems: string[]; rights_on_file: boolean; privacy_options: string[];
  suggested: { title: string; description: string; tags: string[]; credit: string } };
const privacyLabel: Record<string, string> = {
  private: "Privado", unlisted: "Não listado", public: "Público", SELF_ONLY: "Só eu", MUTUAL_FOLLOW_FRIENDS: "Amigos",
  FOLLOWER_OF_CREATOR: "Seguidores", PUBLIC_TO_EVERYONE: "Público",
};

export default function PublishPanel({ clipId, isTest }: { clipId: number; isTest: boolean }) {
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ["publish-overview"], queryFn: () => api.get<{ accounts: PubAccount[] }>("/api/publish/overview") });
  const pubs = useQuery({ queryKey: ["publications"], queryFn: () => api.get<Pub[]>("/api/publish/publications?days=365") });
  const accounts = (ov.data?.accounts ?? []).filter((a) => a.status === "connected");
  const [accountId, setAccountId] = useState<number | null>(null);
  const acct = accounts.find((a) => a.id === accountId) ?? null;
  const [privacy, setPrivacy] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [at, setAt] = useState("");
  const [rights, setRights] = useState(false);
  const check = useQuery({
    queryKey: ["publish-check", clipId, accountId, privacy],
    queryFn: () => api.get<Check>(`/api/publish/clips/${clipId}/check?account_id=${accountId}&privacy=${privacy || "private"}`),
    enabled: accountId !== null,
  });
  useEffect(() => {
    if (!check.data) return;
    if (!title) setTitle(check.data.suggested.title);
    if (!description) setDescription(check.data.suggested.description);
    if (!tags) setTags(check.data.suggested.tags.join(", "));
  }, [check.data]);
  useEffect(() => { setPrivacy(acct?.platform === "tiktok" ? "" : "private"); setTitle(""); setDescription(""); setTags(""); }, [accountId]);
  const submit = useMutation({
    mutationFn: () => api.post<Pub>("/api/publish/publications", {
      clip_id: clipId, account_id: accountId, title, description, privacy, rights_confirmed: rights,
      tags: tags.split(/[,\s]+/).filter(Boolean), scheduled_at: at ? new Date(at).toISOString() : null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["publications"] }); },
  });
  const mine = (pubs.data ?? []).filter((p) => p.clip_id === clipId);
  if (accounts.length === 0) {
    return <p className="mt-3 text-xs text-ink-faint">Para publicar, conecte uma conta em <Link className="text-take underline" to="/publicacao">Publicação</Link>.</p>;
  }
  const blocking = (check.data?.problems ?? []).filter((p) => !p.startsWith("confirme"));
  return (
    <details className="mt-3 rounded-lg border border-suite-line p-3 text-sm">
      <summary className="cursor-pointer font-medium">Publicar nas redes</summary>
      {mine.length > 0 && (
        <ul className="mt-2 space-y-1 text-xs">
          {mine.map((p) => (
            <li key={p.id}>{accounts.find((a) => a.id === p.account_id)?.platform_name ?? p.platform}: <span className={pubStatus[p.status]?.[1]}>{pubStatus[p.status]?.[0] ?? p.status}</span>
              {p.remote_url && <> · <a className="text-take underline" href={p.remote_url} target="_blank" rel="noreferrer">abrir</a></>}
              {p.last_error && <span className="block text-[#ffb4b6]">{p.last_error}</span>}</li>
          ))}
        </ul>
      )}
      <div className="mt-3 space-y-2">
        <select className="field" value={accountId ?? ""} onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Escolha a conta…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.platform_name} — {a.name}</option>)}
        </select>
        {acct && check.data && (
          <>
            {isTest && <p className="text-xs text-take">Corte de TESTE MANUAL: só publicação manual, nunca automática.</p>}
            {blocking.length > 0 && <ul className="list-inside list-disc text-xs text-[#ffb4b6]">{blocking.map((p, i) => <li key={i}>{p}</li>)}</ul>}
            <label className="block"><span className="text-xs text-ink-muted">Privacidade {acct.platform === "tiktok" && "(escolha obrigatória)"}</span>
              <select className="field" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                {acct.platform === "tiktok" && <option value="">Escolha…</option>}
                {check.data.privacy_options.map((p) => <option key={p} value={p}>{privacyLabel[p] ?? p}</option>)}
              </select></label>
            <label className="block"><span className="text-xs text-ink-muted">Título</span><input className="field" value={title} maxLength={acct.platform === "youtube" ? 100 : 150} onChange={(e) => setTitle(e.target.value)} /></label>
            <label className="block"><span className="text-xs text-ink-muted">Descrição (inclui créditos da origem)</span><textarea className="field min-h-[90px]" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
            <label className="block"><span className="text-xs text-ink-muted">Tags</span><input className="field" value={tags} onChange={(e) => setTags(e.target.value)} /></label>
            <label className="block"><span className="text-xs text-ink-muted">Agendar (vazio = agora)</span><input className="field" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></label>
            {!check.data.rights_on_file && (
              <label className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5" checked={rights} onChange={(e) => setRights(e.target.checked)} />
                Confirmo que tenho direito de republicar este conteúdo do streamer.</label>
            )}
            <ErrorNote error={submit.error} />
            {submit.data && <p className="text-xs text-ok">Publicação #{submit.data.id} criada ({pubStatus[submit.data.status]?.[0]}). Acompanhe em <Link className="underline" to="/publicacao">Publicação</Link>.</p>}
            <button className="btn-take w-full" disabled={submit.isPending || !privacy || !title || blocking.length > 0 || (!check.data.rights_on_file && !rights)} onClick={() => submit.mutate()}>
              <Send className="h-4 w-4" /> {at ? "Agendar publicação" : "Publicar agora"} ({privacyLabel[privacy] ?? "escolha a privacidade"})
            </button>
          </>
        )}
      </div>
    </details>
  );
}
