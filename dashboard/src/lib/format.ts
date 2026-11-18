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
  if (d < 45) return "agora";
  if (d < 3600) return `há ${Math.round(d / 60)} min`;
  if (d < 86400) return `há ${Math.round(d / 3600)} h`;
  return `há ${Math.round(d / 86400)} d`;
}

export const platformName: Record<string, string> = { twitch: "Twitch", youtube: "YouTube", file: "Vídeo de teste" };

export const dimensionLabel: Record<string, string> = {
  intensity: "Intensidade",
  surprise: "Surpresa",
  humor: "Humor",
  reaction: "Reação",
  narrative: "Clareza narrativa",
  context: "Contexto",
  uniqueness: "Singularidade",
  audience: "Interesse do público",
  visual: "Relevância visual",
  chat: "Chat",
};

export const statusLabel: Record<string, string> = {
  candidate: "Candidato",
  analyzing: "Análise visual",
  rendering: "Renderizando",
  ready: "Para revisar",
  approved: "Aprovado",
  rejected: "Rejeitado",
  failed: "Falhou",
};

export const jobLabel: Record<string, string> = {
  capture: "Captura",
  audio_analyze: "Áudio e transcrição",
  score: "Pontuação",
  vision_analyze: "Análise visual",
  render: "Renderização",
};
