import { useMutation, useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Brand } from "../components/Brand";
import { ErrorNote, LanguagePicker, Notice, Skeleton } from "../components/ui";
import { t } from "../i18n";

/** New password + confirmation, with the minimum length shown before submitting. */
function NewPassword({ min, value, onChange, confirm, onConfirm }: {
  min: number; value: string; onChange: (v: string) => void; confirm: string; onConfirm: (v: string) => void;
}) {
  const mismatch = confirm.length > 0 && confirm !== value;
  return (
    <>
      <div>
        <label className="label" htmlFor="new-password">{t("Nova senha")}</label>
        <input id="new-password" className="field" type="password" autoComplete="new-password" required minLength={min}
          value={value} onChange={(e) => onChange(e.target.value)} aria-describedby="password-rules" />
        <p id="password-rules" className="mt-1 text-xs text-ink-muted">
          {t("Pelo menos {min} caracteres. Uma frase com palavras soltas é mais forte e mais fácil de lembrar.", { min })}</p>
      </div>
      <div>
        <label className="label" htmlFor="confirm-password">{t("Repita a nova senha")}</label>
        <input id="confirm-password" className="field" type="password" autoComplete="new-password" required
          value={confirm} onChange={(e) => onConfirm(e.target.value)} aria-invalid={mismatch || undefined} />
        {mismatch && <p className="mt-1 text-xs text-tally">{t("As senhas não são iguais.")}</p>}
      </div>
    </>
  );
}

/** Page opened from an invite or reset link: the person creates their own password. */
export function SetPassword() {
  const [params] = useSearchParams();
  const token = params.get("t") ?? "";
  const info = useQuery({
    queryKey: ["password-link", token], enabled: !!token, retry: false,
    queryFn: () => api.get<{ email: string; purpose: string; expires_at: string; password_min: number }>(
      `/api/auth/password-link?token=${encodeURIComponent(token)}`),
  });
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const save = useMutation({ mutationFn: () => api.post("/api/auth/password-link", { token, new_password: pw }) });
  const min = info.data?.password_min ?? 10;
  const submit = (e: FormEvent) => { e.preventDefault(); if (pw === pw2) save.mutate(); };
  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col gap-6 p-6">
      <header><Brand /></header>
      <section className="panel panel-body space-y-4">
        <h1 className="display text-2xl">{info.data?.purpose === "reset" ? t("Criar uma nova senha") : t("Criar a sua senha")}</h1>
        {!token && <p className="text-sm">{t("Este endereço não tem o código do link. Abra o link completo que você recebeu.")}</p>}
        {info.isLoading && <Skeleton label={t("Conferindo o link")} />}
        {info.error && <><ErrorNote error={info.error} /><p className="text-sm text-ink-muted">{t("Peça um link novo a quem administra a sua conta.")}</p></>}
        {save.isSuccess ? (
          <>
            <Notice>{t("Senha criada. Use-a para entrar.")}</Notice>
            <Link className="btn-take inline-flex" to="/login">{t("Ir para a entrada")}</Link>
          </>
        ) : info.data && (
          <form className="space-y-4" onSubmit={submit}>
            <p className="text-sm text-ink-muted">{t("Conta: {email}. O link vale uma vez só.", { email: info.data.email })}</p>
            <NewPassword min={min} value={pw} onChange={setPw} confirm={pw2} onConfirm={setPw2} />
            <ErrorNote error={save.error} />
            <button className="btn-take min-h-11 w-full" disabled={save.isPending || !pw || pw !== pw2}>
              <KeyRound size={16} aria-hidden /> {save.isPending ? t("Salvando…") : t("Salvar senha")}</button>
          </form>
        )}
      </section>
      <footer className="text-xs text-ink-muted"><LanguagePicker /></footer>
    </main>
  );
}

/** Signed-in person changes their own password (staff and guests). */
export function ChangePassword({ back }: { back: string }) {
  const cfg = useQuery({ queryKey: ["auth-config"], queryFn: () => api.get<{ password_min?: number }>("/api/auth/config") });
  const [current, setCurrent] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const save = useMutation({
    mutationFn: () => api.post("/api/auth/password", { current_password: current, new_password: pw }),
    onSuccess: () => { setCurrent(""); setPw(""); setPw2(""); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); if (pw === pw2) save.mutate(); };
  return (
    <div className="max-w-md space-y-4">
      <h1 className="display text-3xl">{t("Trocar senha")}</h1>
      <p className="text-sm text-ink-muted">{t("Depois da troca, as outras sessões abertas desta conta terminam; esta continua.")}</p>
      <form className="panel panel-body space-y-4" onSubmit={submit}>
        <div>
          <label className="label" htmlFor="current-password">{t("Senha atual")}</label>
          <input id="current-password" className="field" type="password" autoComplete="current-password" required
            value={current} onChange={(e) => setCurrent(e.target.value)} />
        </div>
        <NewPassword min={cfg.data?.password_min ?? 10} value={pw} onChange={setPw} confirm={pw2} onConfirm={setPw2} />
        <ErrorNote error={save.error} />
        {save.isSuccess && <Notice>{t("Senha trocada.")}</Notice>}
        <div className="flex flex-wrap gap-2">
          <button className="btn-take" disabled={save.isPending || !current || !pw || pw !== pw2}>
            <KeyRound size={16} aria-hidden /> {save.isPending ? t("Salvando…") : t("Trocar senha")}</button>
          <Link className="btn-quiet" to={back}>{t("Voltar")}</Link>
        </div>
      </form>
    </div>
  );
}

/** "Forgot password" on the sign-in page. Same answer for any e-mail (accounts are not revealed). */
export function ForgotPassword({ emailEnabled, onClose }: { emailEnabled: boolean; onClose: () => void }) {
  const [email, setEmail] = useState("");
  const ask = useMutation({ mutationFn: () => api.post("/api/auth/forgot", { email }) });
  if (!emailEnabled) {
    return (
      <div className="panel panel-body space-y-2 text-sm">
        <p>{t("O envio de e-mail ainda não está configurado. Peça a quem administra a sua conta um link para criar uma nova senha.")}</p>
        <button type="button" className="btn-quiet" onClick={onClose}>{t("Voltar")}</button>
      </div>
    );
  }
  return (
    <form className="panel panel-body space-y-3" onSubmit={(e) => { e.preventDefault(); ask.mutate(); }}>
      {ask.isSuccess ? (
        <Notice>{t("Se existir uma conta com este e-mail, enviamos um link para criar uma nova senha. Ele vale por 2 horas.")}</Notice>
      ) : (
        <>
          <label className="label" htmlFor="forgot-email">{t("E-mail da sua conta")}</label>
          <input id="forgot-email" className="field" type="email" required autoComplete="username" value={email}
            onChange={(e) => setEmail(e.target.value)} />
          <ErrorNote error={ask.error} />
          <button className="btn-take w-full" disabled={ask.isPending || !email}>{t("Enviar link")}</button>
        </>
      )}
      <button type="button" className="btn-quiet" onClick={onClose}>{t("Voltar")}</button>
    </form>
  );
}
