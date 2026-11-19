import {
  Activity, AudioLines, CalendarDays, Clapperboard, ClipboardCheck, HardDrive, Library, Link2, Radio, ScrollText,
  Server, SlidersHorizontal, Sun, Users, type LucideIcon,
} from "lucide-react";

export type NavItem = {
  to: string;
  label: string;
  icon: LucideIcon;
  /** True when this entry represents the current page. */
  match: (path: string, aba: string | null) => boolean;
  badge?: "review";
};
export type NavGroup = { key: string; label: string; items: NavItem[]; collapsible?: boolean };

const at = (prefix: string) => (path: string) => path === prefix || path.startsWith(prefix + "/");
const publishTabs = ["contas", "regras"];

/** Staff navigation, ordered by how often each task happens: daily work first, configuration and system last. */
export const staffNav: NavGroup[] = [
  { key: "dia", label: "Trabalho do dia", items: [
    { to: "/", label: "Hoje", icon: Sun, match: (p) => p === "/" },
    { to: "/cortes", label: "Central de cortes", icon: Clapperboard, match: at("/cortes"), badge: "review" },
    { to: "/publicacao", label: "Agenda de publicações", icon: CalendarDays,
      match: (p, aba) => at("/publicacao")(p) && !publishTabs.includes(aba ?? "") },
    { to: "/diretor", label: "Diretor IA", icon: AudioLines, match: at("/diretor") },
  ] },
  { key: "acervo", label: "Acervo e pessoas", items: [
    { to: "/referencias", label: "Referências", icon: Library, match: at("/referencias") },
    { to: "/pessoas", label: "Pessoas e acesso", icon: Users, match: at("/pessoas") },
  ] },
  { key: "config", label: "Configuração", collapsible: true, items: [
    { to: "/streamers", label: "Canais monitorados", icon: Radio, match: at("/streamers") },
    { to: "/ia", label: "Parâmetros da IA", icon: SlidersHorizontal, match: at("/ia") },
    { to: "/publicacao?aba=contas", label: "Contas e regras", icon: Link2,
      match: (p, aba) => at("/publicacao")(p) && publishTabs.includes(aba ?? "") },
  ] },
  { key: "sistema", label: "Sistema", collapsible: true, items: [
    { to: "/avaliacao", label: "Avaliação editorial", icon: ClipboardCheck, match: at("/avaliacao") },
    { to: "/diagnostico", label: "Diagnóstico", icon: Activity, match: at("/diagnostico") },
    { to: "/infra", label: "Servidores", icon: Server, match: at("/infra") },
    { to: "/armazenamento", label: "Armazenamento", icon: HardDrive, match: at("/armazenamento") },
    { to: "/patch-notes", label: "Histórico de versões", icon: ScrollText, match: at("/patch-notes") },
  ] },
];

export function currentNav(path: string, aba: string | null) {
  for (const group of staffNav) {
    const item = group.items.find((i) => i.match(path, aba));
    if (item) return { group, item };
  }
  return undefined;
}
