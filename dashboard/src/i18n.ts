/**
 * Interface language (PT-BR source, English translation).
 *
 * The Portuguese text in the code IS the key: `t("Aprovar")` shows "Aprovar" in Portuguese and the entry
 * of `i18n/en.ts` in English. Placeholders use `{name}`: `t("{n} cortes", { n })`.
 * The language comes from the saved choice, else from the browser (pt* -> Portuguese, anything else ->
 * English). Changing it reloads the page, so module-level labels are evaluated in the right language.
 * `npm run check:i18n` fails when a t("...") text has no English entry.
 */
import en from "./i18n/en";
import enServer from "./i18n/en-server";

export type Lang = "pt" | "en";
const KEY = "asc.lang";

function detect(): Lang {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "pt" || saved === "en") return saved;
  } catch {
    /* storage blocked: fall back to the browser */
  }
  const nav = typeof navigator !== "undefined" ? navigator.language || "" : "";
  return nav.toLowerCase().startsWith("pt") ? "pt" : "en";
}

export const lang: Lang = detect();
/** BCP 47 locale for dates and numbers in the chosen language. */
export const locale = lang === "pt" ? "pt-BR" : "en-US";
if (typeof document !== "undefined") document.documentElement.lang = locale;

const dict: Record<string, string> = en;

export function t(text: string, vars?: Record<string, string | number | null | undefined>): string {
  let out = lang === "en" ? dict[text] ?? text : text;
  if (vars) out = out.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k] ?? "") : m));
  return out;
}

/** Fixed labels that the SERVER sends in Portuguese (stage names, host roles, story kinds).
 * Unknown text (AI explanations, typed content) is shown as it came. Entries live in i18n/en-server.ts. */
export function tx(text: string | null | undefined): string {
  if (!text) return "";
  return lang === "en" ? enServer[text] ?? text : text;
}

export function setLang(next: Lang) {
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* not persisted: still switch for this load */
  }
  window.location.reload();
}

export const languages: { code: Lang; label: string }[] = [
  { code: "pt", label: "Português" },
  { code: "en", label: "English" },
];
