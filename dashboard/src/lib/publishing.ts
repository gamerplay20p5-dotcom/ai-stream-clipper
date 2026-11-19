export const distributionViews = [
  { key: "publicacoes", label: "Publicações", to: "/publicacao" },
  { key: "calendario", label: "Calendário", to: "/publicacao?aba=calendario" },
  { key: "contas", label: "Contas conectadas", to: "/publicacao?aba=contas" },
  { key: "regras", label: "Regras e direitos", to: "/publicacao?aba=regras" },
  { key: "relatorios", label: "Relatórios", to: "/publicacao?aba=relatorios" },
] as const;

export function distributionView(search: URLSearchParams) {
  const key = search.get("aba") ?? (search.has("conectado") || search.has("erro") ? "contas" : null);
  return distributionViews.find((view) => view.key === key) ?? distributionViews[0];
}
