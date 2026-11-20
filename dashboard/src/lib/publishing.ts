import { t } from "../i18n";
/** Distribution views. "agenda" (week calendar) is the default; "contas" and "regras" are configuration. */
export const distributionViews = [
  { key: "agenda", label: t("Agenda"), to: "/publicacao" },
  { key: "envios", label: t("Histórico de envios"), to: "/publicacao?aba=envios" },
  { key: "relatorios", label: t("Resultados"), to: "/publicacao?aba=relatorios" },
  { key: "contas", label: t("Contas conectadas"), to: "/publicacao?aba=contas" },
  { key: "regras", label: t("Regras e direitos"), to: "/publicacao?aba=regras" },
] as const;

const legacy: Record<string, string> = { calendario: "agenda", publicacoes: "envios" };

export function distributionView(search: URLSearchParams) {
  const raw = search.get("aba") ?? (search.has("conectado") || search.has("erro") ? "contas" : null);
  const key = raw ? legacy[raw] ?? raw : null;
  return distributionViews.find((view) => view.key === key) ?? distributionViews[0];
}

/**
 * Who decided a publication, in the words the team uses:
 * - sugestao: the AI proposed it (semi-automatic rule) and it waits for a person; nothing is sent without confirmation;
 * - confirmado: a person scheduled it, or confirmed an AI suggestion;
 * - automatico: an automatic rule scheduled it without asking (only when the admin enabled that rule).
 */
export type Decision = "sugestao" | "confirmado" | "automatico";
export function decisionOf(p: { status: string; origin: string }): Decision | null {
  if (p.status === "proposed") return "sugestao";
  if (!["scheduled", "uploading", "processing", "published", "uncertain", "failed"].includes(p.status)) return null;
  return p.origin === "auto" ? "automatico" : "confirmado";
}
export const decisionLabel: Record<Decision, string> = {
  sugestao: t("Sugestão da IA"), confirmado: t("Confirmado"), automatico: t("Automático"),
};
export const decisionHint: Record<Decision, string> = {
  sugestao: t("Aguarda sua confirmação. Se ninguém confirmar, expira no horário."),
  confirmado: t("Agendado ou confirmado por uma pessoa da equipe."),
  automatico: t("Agendado por uma regra automática, sem confirmação manual."),
};
