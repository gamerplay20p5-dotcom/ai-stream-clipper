import { useQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Brand } from "../components/Brand";
import { ErrorNote, LanguagePicker } from "../components/ui";
import { t } from "../i18n";
import { ForgotPassword } from "./Password";

export default function Login({ onDone, signup: startWithSignup = false }: { onDone: () => void; signup?: boolean }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [forgot, setForgot] = useState(false);
  const [signup, setSignup] = useState(startWithSignup);
  const cfg = useQuery({
    queryKey: ["auth-config"],
    queryFn: () => api.get<{ oidc_enabled: boolean; email_enabled?: boolean; signup_enabled?: boolean }>("/api/auth/config"),
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
        <h1 className="display text-3xl">{t("Acesse o painel")}</h1>
        <p className="mb-7 mt-2 text-sm text-ink-muted">{t("Monitore lives, revise cortes e acompanhe publicações.")}</p>
        {signup ? <Signup enabled={!!cfg.data?.signup_enabled} onClose={() => setSignup(false)} /> : forgot ? <ForgotPassword emailEnabled={!!cfg.data?.email_enabled} onClose={() => setForgot(false)} /> : (
        <form onSubmit={submit} className="space-y-4" aria-busy={busy}>
          <div>
            <label className="label" htmlFor="email">{t("E-mail")}</label>
            <input id="email" className="field" type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="password">{t("Senha")}</label>
            <input id="password" className="field" type="password" autoComplete="current-password" required value={password}
              onChange={(e) => setPassword(e.target.value)} />
          </div>
          <ErrorNote error={error} />
          <button className="btn-take min-h-11 w-full" disabled={busy}>
            {busy ? t("Entrando…") : t("Entrar")}
          </button>
          {cfg.data?.oidc_enabled && (
            <p className="text-xs text-ink-muted">{t("O servidor tem Authentik (OIDC) configurado. Nesta versão, use seu e-mail e senha.")}</p>
          )}
          <button type="button" className="text-sm text-take hover:underline" onClick={() => setForgot(true)}>{t("Esqueci minha senha")}</button>
          {cfg.data?.signup_enabled && (
            <p className="text-sm text-ink-muted">{t("Ainda não tem conta?")}{" "}
              <button type="button" className="text-take hover:underline" onClick={() => setSignup(true)}>{t("Criar conta")}</button></p>
          )}
        </form>)}
      </div>
      <footer className="flex flex-wrap gap-x-5 gap-y-2 text-xs text-ink-muted">
        <a className="hover:text-take" href="/privacidade">{t("Privacidade")}</a>
        <a className="hover:text-take" href="/termos">{t("Termos de uso")}</a>
        <LanguagePicker />
        <span className="ml-auto font-mono">{t("v")}{__APP_VERSION__}</span>
      </footer>
      </div>
    </main>
  );
}
/** Self sign-up: the account only works after the e-mail owner opens the single-use link and creates a password. */
function Signup({ enabled, onClose }: { enabled: boolean; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/signup", { email });
      setDone(true);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }
  if (!enabled) {
    return <div className="space-y-4"><p className="text-sm text-ink-muted">{t("O cadastro está indisponível no momento.")}</p>
      <button type="button" className="text-sm text-take hover:underline" onClick={onClose}>{t("Voltar para o login")}</button></div>;
  }
  if (done) {
    return <div className="space-y-4" role="status"><p className="text-sm">{t("Se este e-mail puder ser usado, enviamos um link para confirmar o endereço e criar sua senha. Ele vale por 72 horas.")}</p>
      <button type="button" className="text-sm text-take hover:underline" onClick={onClose}>{t("Voltar para o login")}</button></div>;
  }
  return (
    <form onSubmit={submit} className="space-y-4" aria-busy={busy}>
      <h2 className="text-lg font-semibold">{t("Criar conta")}</h2>
      <div>
        <label className="label" htmlFor="signup-email">{t("E-mail")}</label>
        <input id="signup-email" className="field" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </div>
      <p className="text-xs text-ink-muted">{t("Enviaremos um link para confirmar que o e-mail é seu. Só depois disso a conta pode assinar um plano.")}</p>
      <ErrorNote error={error} />
      <button className="btn-take min-h-11 w-full" disabled={busy}>{busy ? t("Enviando…") : t("Enviar link de confirmação")}</button>
      <button type="button" className="text-sm text-take hover:underline" onClick={onClose}>{t("Voltar para o login")}</button>
    </form>
  );
}
