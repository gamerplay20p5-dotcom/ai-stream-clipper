import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { ErrorNote } from "../components/ui";

export default function Login({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const cfg = useQuery({
    queryKey: ["auth-config"],
    queryFn: () => api.get<{ oidc_enabled: boolean }>("/api/auth/config"),
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/login", { email, password });
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="grid w-full max-w-3xl items-center gap-10 md:grid-cols-[180px_1fr]">
        <div className="mx-auto hidden aspect-short w-[180px] flex-col justify-end rounded-xl bg-take p-4 text-take-ink md:flex" aria-hidden>
          <p className="display text-lg leading-tight">Os melhores momentos da live, em pé.</p>
        </div>
        <form onSubmit={submit} className="w-full max-w-sm space-y-4">
          <div>
            <h1 className="display text-2xl">Stream Clipper</h1>
            <p className="mt-1 text-sm text-ink-muted">Entre para revisar os cortes encontrados nas transmissões.</p>
          </div>
          <div>
            <label className="label" htmlFor="email">E-mail</label>
            <input id="email" className="field" type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="password">Senha</label>
            <input id="password" className="field" type="password" autoComplete="current-password" required
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <ErrorNote error={error} />
          <button className="btn-take w-full" disabled={busy}>{busy ? "Entrando…" : "Entrar"}</button>
          {cfg.data?.oidc_enabled && (
            <p className="text-xs text-ink-muted">Login via Authentik (OIDC) está configurado no servidor, mas ainda não habilitado nesta versão.</p>
          )}
        </form>
      </div>
    </div>
  );
}
