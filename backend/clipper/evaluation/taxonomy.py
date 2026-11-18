"""Labels used by the review UI, the dataset export and the metrics (PT-BR texts)."""

from __future__ import annotations

RATINGS = {
    "excellent": "Excelente",
    "good": "Bom",
    "fair": "Regular",
    "bad": "Ruim",
    "should_not_exist": "Não deveria ter sido gerado",
}
POSITIVE = frozenset({"excellent", "good"})  # worth a Short
NEGATIVE = frozenset({"bad", "should_not_exist"})
# "fair" is neither: excluded from binary metrics/training, reported separately
RATING_VALUE = {"excellent": 4, "good": 3, "fair": 2, "bad": 1, "should_not_exist": 0}

EDITORIAL_REASONS = {
    "nothing_interesting": "Não aconteceu nada interessante",
    "missing_context": "Falta contexto",
    "started_late": "Começou tarde",
    "started_early": "Começou cedo demais",
    "ended_early": "Terminou antes do desfecho",
    "too_long": "Vídeo muito longo",
    "too_short": "Vídeo muito curto",
    "duplicate": "Corte duplicado",
    "false_reaction": "Reação falsa ou mal interpretada",
    "undervalued": "Acontecimento relevante não foi valorizado",
}
TECHNICAL_ISSUES = {
    "bad_framing": "Edição ou enquadramento ruim",
    "wrong_captions": "Legendas incorretas",
    "audio_problem": "Áudio com problema",
    "video_glitch": "Falha de vídeo ou renderização",
}
CATEGORIES = {
    "funny": "Situação engraçada",
    "crash": "Acidente ou colisão inesperada",
    "police_chase": "Perseguição policial",
    "discovery": "Descoberta surpreendente",
    "argument": "Discussão ou interação engraçada",
    "fail": "Falha absurda do jogador",
    "reaction": "Reação espontânea",
    "skill": "Jogada habilidosa",
    "conversation": "Conversa sem acontecimento interessante",
    "noise_no_reaction": "Explosão ou barulho sem reação relevante",
    "no_context": "Parece interessante, mas falta contexto",
    "other": "Outro",
}
# why an example was put in the review queue (active learning strata)
STRATA = {
    "accepted": "Corte gerado automaticamente",
    "near_threshold": "Perto da nota mínima",
    "rejected_reaction": "Rejeitado, mas com reação (risada, grito, surpresa)",
    "noise_no_reaction": "Barulho do jogo sem reação",
    "conversation": "Conversa comum (amostra)",
    "low_score": "Nota baixa (amostra)",
    "disagreement": "Áudio e visão discordam",
    "manual_test": "Corte de teste manual",
}
PROVIDERS = {
    "multimodal": "Análise multimodal real",
    "audio_fallback": "Fallback em áudio",
    "heuristic": "Heurística",
    "ranker": "Classificador treinado",
}
