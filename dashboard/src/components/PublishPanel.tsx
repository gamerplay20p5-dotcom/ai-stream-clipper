import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { Pub, PubAccount } from "../pages/Publishing";
import { pubStatus } from "../pages/Publishing";
import PrivacyPicker, { initialPrivacy, privacyLabel } from "./PrivacyPicker";
import { Empty, ErrorNote, Notice, Skeleton } from "./ui";

type Check = { problems: string[]; rights_on_file: boolean; privacy_options: string[];
  suggested: { title: string; description: string; tags: string[]; credit: string } };

export default function PublishPanel({ clipId, isTest }: { clipId: number; isTest: boolean }) {
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ["publish-overview"], queryFn: () => api.get<{ accounts: PubAccount[] }>("/api/publish/overview") });
  const pubs = useQuery({ queryKey: ["publications", 365], queryFn: () => api.get<Pub[]>("/api/publish/publications?days=365") });
  const accounts = (ov.data?.accounts ?? []).filter((a) => a.status === "connected");
  const [accountId, setAccountId] = useState<number | null>(null);
  const acct = accounts.find((a) => a.id === accountId) ?? null;
  const [privacy, setPrivacy] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [at, setAt] = useState("");
  const [rights, setRights] = useState(false);
  const options = acct?.meta.privacy_options ?? [];
  const check = useQuery({
    queryKey: ["publish-check", clipId, accountId, privacy],
    queryFn: () => api.get<Check>(`/api/publish/clips/${clipId}/check?account_id=${accountId}` +
      (privacy ? `&privacy=${encodeURIComponent(privacy)}` : "")),
    enabled: accountId !== null,
  });
  useEffect(() => {
    if (!check.data) return;
    if (!title) setTitle(check.data.suggested.title);
    if (!description) setDescription(check.data.suggested.description);
    if (!tags) setTags(check.data.suggested.tags.join(", "));
  }, [check.data]);
  // Bug fixed in 0.6.0-hotfix: this used to force "private" for every platform except TikTok. Instagram
  // only offers "public", so the select showed "Público" while "private" was sent and refused.
  useEffect(() => {
    setPrivacy(acct?.platform === "tiktok" ? "" : initialPrivacy(options));
    setTitle(""); setDescription(""); setTags(""); setRights(false);
  }, [accountId]);
  const submit = useMutation({
    mutationFn: () => api.post<Pub>("/api/publish/publications", {
      clip_id: clipId, account_id: accountId, title, description, privacy, rights_confirmed: rights,
      tags: tags.split(/[,\s]+/).filter(Boolean), scheduled_at: at ? new Date(at).toISOString() : null,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["publications"] }); qc.invalidateQueries({ queryKey: ["calendar"] }); },
  });
  const mine = (pubs.data ?? []).filter((p) => p.clip_id === clipId);
  const problems = check.data?.problems ?? [];
  const rightsProblem = problems.find((p) => p.startsWith("confirme"));
  // privacy problems disappear once a valid choice is made; until then the picker says what to do
  const blocking = problems.filter((p) => !p.startsWith("confirme") && !(privacy === "" && p.startsWith("privacidade")));
  const missing: string[] = [];
  if (!privacy) missing.push(acct?.platform === "instagram" ? "confirme que o Reel será público" : "escolha a privacidade");
  if (!title) missing.push("preencha o título");
  if (check.data && !check.data.rights_on_file && !rights) missing.push("confirme o direito de republicar");
  return (
    <section className="space-y-4" aria-label="Publicar nas redes">
      <h2 className="display text-lg">Publicar nas redes</h2>
      <ErrorNote error={ov.error ?? pubs.error} />
      {ov.isLoading && <Skeleton label="Carregando contas disponíveis" />}
      {ov.data && accounts.length === 0 && (
        <Empty title="Nenhuma conta disponível"><Link className="text-take hover:underline" to="/publicacao?aba=contas">Gerenciar contas conectadas</Link></Empty>
      )}
      {mine.length > 0 && (
        <ul className="divide-y divide-suite-line rounded-md border border-suite-line px-3 text-sm" aria-label="Envios deste corte">
          {mine.map((p) => (
            <li key={p.id} className="break-words py-2">{accounts.find((a) => a.id === p.account_id)?.platform_name ?? p.platform}: <span className={pubStatus[p.status]?.[1]}>{pubStatus[p.status]?.[0] ?? p.status}</span>
              {p.remote_url && <> · <a className="text-take underline" href={p.remote_url} target="_blank" rel="noreferrer">abrir</a></>}
              {p.last_error && <span className="block text-tally">{p.last_error}</span>}</li>
          ))}
        </ul>
      )}
      {accounts.length > 0 && <div className="space-y-3">
        <label className="label" htmlFor="clip-publication-account">Conta de destino</label>
        <select id="clip-publication-account" className="field" value={accountId ?? ""} onChange={(e) => setAccountId(e.target.value ? Number(e.target.value) : null)}>
          <option value="">Escolha a conta…</option>
          {accounts.map((a) => <option key={a.id} value={a.id}>{a.platform_name} — {a.name}</option>)}
        </select>
        {acct && <ErrorNote error={check.error} />}
        {acct && check.isLoading && <Skeleton label="Verificando os requisitos de envio" />}
        {acct && check.data && (
          <>
            {isTest && <p className="text-xs text-take">Corte de TESTE MANUAL: só publicação manual, nunca automática.</p>}
            {blocking.length > 0 && (
              <div className="rounded-md border border-tally/40 p-2 text-xs text-tally">
                <p className="font-semibold">Não é possível publicar enquanto:</p>
                <ul className="list-inside list-disc">{blocking.map((p, i) => <li key={i}>{p}</li>)}</ul>
              </div>
            )}
            {rightsProblem && <p className="text-xs text-take">Direitos: {rightsProblem}.</p>}
            <div className="block"><span className="text-xs text-ink-muted">Privacidade {acct.platform === "tiktok" && "(escolha obrigatória)"}</span>
              <PrivacyPicker options={check.data.privacy_options} value={privacy} onChange={setPrivacy} platform={acct.platform} /></div>
            <label className="block"><span className="text-xs text-ink-muted">Título</span><input className="field" value={title} maxLength={acct.platform === "youtube" ? 100 : 150} onChange={(e) => setTitle(e.target.value)} /></label>
            <label className="block"><span className="text-xs text-ink-muted">Descrição (inclui créditos da origem)</span><textarea className="field min-h-[90px]" value={description} onChange={(e) => setDescription(e.target.value)} /></label>
            <label className="block"><span className="text-xs text-ink-muted">Tags</span><input className="field" value={tags} onChange={(e) => setTags(e.target.value)} /></label>
            <label className="block"><span className="text-xs text-ink-muted">Agendar (vazio = agora)</span><input className="field" type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} /></label>
            {!check.data.rights_on_file && (
              <label className="flex items-start gap-2 text-xs"><input type="checkbox" className="mt-0.5" checked={rights} onChange={(e) => setRights(e.target.checked)} />
                Confirmo que tenho direito de republicar este conteúdo do streamer.</label>
            )}
            <ErrorNote error={submit.error} />
            {submit.data && <Notice>Publicação #{submit.data.id} criada ({pubStatus[submit.data.status]?.[0]}). <Link className="underline" to="/publicacao">Acompanhar envio</Link></Notice>}
            {missing.length > 0 && blocking.length === 0 && <p className="text-xs text-ink-muted">Falta: {missing.join(", ")}.</p>}
            <button className="btn-take w-full" disabled={submit.isPending || blocking.length > 0 || missing.length > 0} onClick={() => submit.mutate()}
              data-privacy={privacy}>
              <Send className="h-4 w-4" /> {at ? "Agendar publicação" : "Publicar agora"} ({privacy ? privacyLabel[privacy] ?? privacy : "privacidade não escolhida"})
            </button>
          </>
        )}
      </div>}
    </section>
  );
}
