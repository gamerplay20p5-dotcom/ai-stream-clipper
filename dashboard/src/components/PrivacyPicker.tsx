import { t } from "../i18n";
export const privacyLabel: Record<string, string> = {
  private: t("Privado"), unlisted: t("Não listado"), public: t("Público"), SELF_ONLY: t("Só eu (TikTok)"),
  MUTUAL_FOLLOW_FRIENDS: t("Amigos"), FOLLOWER_OF_CREATOR: t("Seguidores"), PUBLIC_TO_EVERYONE: t("Público (TikTok)"),
};
const PUBLIC = new Set(["public", "PUBLIC_TO_EVERYONE"]);

/** The value the form should start with: the current one if the account offers it, else "private"
 *  when available, else "" (the person must choose). Never a silent public choice. */
export function initialPrivacy(options: string[], current = ""): string {
  if (current && options.includes(current)) return current;
  if (options.includes("private")) return "private";
  return "";
}

export const publicOnly = (options: string[]) => options.length > 0 && options.every((o) => PUBLIC.has(o));

/** What is shown is always exactly what will be sent: an invalid or empty value shows a
 *  placeholder, and a public-only account (Instagram Reels) needs an explicit confirmation. */
export default function PrivacyPicker({ options, value, onChange, platform }: {
  options: string[]; value: string; onChange: (v: string) => void; platform?: string;
}) {
  const valid = options.includes(value);
  if (publicOnly(options)) {
    return (
      <label className="flex items-start gap-2 rounded-md border border-tally/50 bg-tally/10 p-2 text-xs">
        <input type="checkbox" className="mt-0.5" checked={valid} aria-label={t("Confirmar publicação pública")}
          onChange={(e) => onChange(e.target.checked ? options[0] : "")} />
        <span>{platform === "instagram" ? t("Reels do Instagram são sempre públicos. ") : t("Esta conta só permite publicação pública. ")}
          <b>{t("Confirmo que este vídeo será publicado como PÚBLICO.")}</b></span>
      </label>
    );
  }
  return (
    <select className="field" value={valid ? value : ""} onChange={(e) => onChange(e.target.value)} aria-label={t("Privacidade")}>
      <option value="" disabled>{t("Escolha a privacidade…")}</option>
      {options.map((p) => <option key={p} value={p}>{privacyLabel[p] ?? p}</option>)}
    </select>
  );
}
