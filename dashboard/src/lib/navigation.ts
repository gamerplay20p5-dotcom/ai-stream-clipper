import {
  Activity, AudioLines, CalendarDays, Clapperboard, ClipboardCheck, HardDrive, Library, Link2, Radio, ScrollText,
  Briefcase, Server, SlidersHorizontal, Sun, Users, type LucideIcon,
} from "lucide-react";
import { t } from "../i18n";

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
  { key: "dia", label: t("Trabalho do dia"), items: [
    { to: "/", label: t("Hoje"), icon: Sun, match: (p) => p === "/" },
    { to: "/cortes", label: t("Central de cortes"), icon: Clapperboard, match: at("/cortes"), badge: "review" },
    { to: "/publicacao", label: t("Agenda de publicações"), icon: CalendarDays,
      match: (p, aba) => at("/publicacao")(p) && !publishTabs.includes(aba ?? "") },
    { to: "/diretor", label: t("Diretor IA"), icon: AudioLines, match: at("/diretor") },
  ] },
  { key: "acervo", label: t("Acervo e pessoas"), items: [
    { to: "/referencias", label: t("Referências"), icon: Library, match: at("/referencias") },
    { to: "/pessoas", label: t("Pessoas e acesso"), icon: Users, match: at("/pessoas") },
    { to: "/clientes", label: t("Clientes"), icon: Briefcase, match: at("/clientes") },
  ] },
  { key: "config", label: t("Configuração"), collapsible: true, items: [
    { to: "/streamers", label: t("Canais monitorados"), icon: Radio, match: at("/streamers") },
    { to: "/ia", label: t("Parâmetros da IA"), icon: SlidersHorizontal, match: at("/ia") },
    { to: "/publicacao?aba=contas", label: t("Contas e regras"), icon: Link2,
      match: (p, aba) => at("/publicacao")(p) && publishTabs.includes(aba ?? "") },
  ] },
  { key: "sistema", label: t("Sistema"), collapsible: true, items: [
    { to: "/avaliacao", label: t("Avaliação editorial"), icon: ClipboardCheck, match: at("/avaliacao") },
    { to: "/diagnostico", label: t("Diagnóstico"), icon: Activity, match: at("/diagnostico") },
    { to: "/infra", label: t("Servidores"), icon: Server, match: at("/infra") },
    { to: "/armazenamento", label: t("Armazenamento"), icon: HardDrive, match: at("/armazenamento") },
    { to: "/patch-notes", label: t("Histórico de versões"), icon: ScrollText, match: at("/patch-notes") },
  ] },
];

export function currentNav(path: string, aba: string | null) {
  for (const group of staffNav) {
    const item = group.items.find((i) => i.match(path, aba));
    if (item) return { group, item };
  }
  return undefined;
}
