/** Distribution views. "agenda" (week calendar) is the default; "contas" and "regras" are configuration. */
export const distributionViews = [
  { key: "agenda", label: "Agenda", to: "/publicacao" },
  { key: "envios", label: "Histórico de envios", to: "/publicacao?aba=envios" },
  { key: "relatorios", label: "Resultados", to: "/publicacao?aba=relatorios" },
  { key: "contas", label: "Contas conectadas", to: "/publicacao?aba=contas" },
  { key: "regras", label: "Regras e direitos", to: "/publicacao?aba=regras" },
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
  sugestao: "Sugestão da IA", confirmado: "Confirmado", automatico: "Automático",
};
export const decisionHint: Record<Decision, string> = {
  sugestao: "Aguarda sua confirmação. Se ninguém confirmar, expira no horário.",
  confirmado: "Agendado ou confirmado por uma pessoa da equipe.",
  automatico: "Agendado por uma regra automática, sem confirmação manual.",
};
