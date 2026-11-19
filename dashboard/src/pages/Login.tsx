import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Brand } from "../components/Brand";
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
    <main className="login-layout">
      <div className="login-art" aria-hidden="true"><div className="login-aperture" /></div>
      <div className="login-access">
      <header><Brand /></header>
      <div className="login-form">
        <h1 className="display text-3xl">Acesse o painel</h1>
        <p className="mb-7 mt-2 text-sm text-ink-muted">Monitore lives, revise cortes e acompanhe publicações.</p>
        <form onSubmit={submit} className="space-y-4" aria-busy={busy}>
          <div>
            <label className="label" htmlFor="email">E-mail</label>
            <input id="email" className="field" type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="password">Senha</label>
            <input id="password" className="field" type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </div>
          <ErrorNote error={error} />
          <button className="btn-take min-h-11 w-full" disabled={busy}>
            {busy ? "Entrando…" : "Entrar"}
          </button>
          {cfg.data?.oidc_enabled && (
            <p className="text-xs text-ink-muted">O servidor tem Authentik (OIDC) configurado. Nesta versão, use seu e-mail e senha.</p>
          )}
        </form>
      </div>
      <footer className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-muted">
        <a className="hover:text-take" href="/privacidade">Privacidade</a>
        <a className="hover:text-take" href="/termos">Termos de uso</a>
        <span className="ml-auto font-mono">v{__APP_VERSION__}</span>
      </footer>
      </div>
    </main>
  );
}