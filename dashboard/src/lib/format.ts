import { t } from "../i18n";
export function timecode(s: number): string {
  const t = Math.max(0, s);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const sec = Math.floor(t % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(sec).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

export function seconds(s: number): string {
  return `${s.toFixed(s < 10 ? 1 : 0)} s`;
}

export function bytes(n: number): string {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(u.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${u[i]}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "nunca";
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 45) return t("agora");
  if (d < 3600) return t("há {round} min", { round: Math.round(d / 60) });
  if (d < 86400) return t("há {round} h", { round: Math.round(d / 3600) });
  return t("há {round} d", { round: Math.round(d / 86400) });
}

export const platformName: Record<string, string> = { twitch: t("Twitch"), youtube: t("YouTube"), file: t("Vídeo de teste"), instagram: t("Instagram"), tiktok: t("TikTok") };

export const dimensionLabel: Record<string, string> = {
  intensity: t("Intensidade"),
  surprise: t("Surpresa"),
  humor: t("Humor"),
  reaction: t("Reação"),
  narrative: t("Clareza narrativa"),
  context: t("Contexto"),
  uniqueness: t("Singularidade"),
  audience: t("Interesse do público"),
  visual: t("Relevância visual"),
  chat: t("Chat"),
};

export const statusLabel: Record<string, string> = {
  candidate: t("Candidato"),
  analyzing: t("Análise visual"),
  waiting: t("Aguardando contexto"),
  rendering: t("Renderizando"),
  ready: t("Para revisar"),
  approved: t("Aprovado"),
  rejected: t("Rejeitado"),
  failed: t("Falhou"),
};

export const jobLabel: Record<string, string> = {
  capture: t("Captura"),
  audio_analyze: t("Áudio e transcrição"),
  score: t("Pontuação"),
  vision_analyze: t("Análise visual"),
  render: t("Renderização"),
};
