"""Short release notes shown in the dashboard ("Patch notes"). Newest first. PT-BR, short.

Every release adds one entry here (kept in the code so it ships with the version it describes).
"""

from __future__ import annotations

NOTES: list[dict] = [
    {"version": "0.5.6", "date": "2026-11-18", "title": "Operação editorial", "items": [
        "Aprovar, rejeitar e agendar vários cortes de uma vez na Central de Cortes.",
        "Calendário editorial dos próximos 14 dias, com troca de horário.",
        "Modo semiautomático: a IA propõe os melhores cortes nos horários e você confirma com um clique.",
        "Pausa de emergência de todas as publicações.",
        "Relatórios por plataforma, categoria da história, horário e título.",
        "Correção: reenvio de publicação com falha agora volta de fato para a fila.",
    ]},
    {"version": "0.5.5", "date": "2026-11-18", "title": "Quem está falando + legendas inteligentes", "items": [
        "Reconhece a voz do streamer e separa das outras (rádio, personagens); na dúvida fica 'não identificado'.",
        "Confirme a voz do streamer em Streamers (ouvindo trechos); até lá é só hipótese.",
        "Legendas: streamer em destaque, outras vozes menores ou ocultas; fonte, tamanho, posição, animação e cores.",
        "Correção manual das legendas no corte, com prévia sobre o vídeo.",
        "Títulos, descrições e hashtags usam a fala do streamer e a história; texto próprio por plataforma.",
        "Identidade do canal: marca d'água discreta, chamada final, hashtags e rodapé (desligados até preencher).",
        "Nova aba Patch notes com o resumo de cada versão.",
    ]},
    {"version": "0.5.4", "date": "2026-11-18", "title": "IA que reassiste", "items": [
        "Cortes duvidosos são reassistidos com mais quadros antes de renderizar.",
        "Revisão pós-live recupera histórias sem corte e momentos que quase atingiram a nota (até 5 por live).",
        "Correção: a simulação de limpeza passou a contar os vídeos renderizados.",
    ]},
    {"version": "0.5.3", "date": "2026-11-18", "title": "Memória narrativa calibrada", "items": [
        "Perseguições só contam com sinal real (urgência, colisão, fuga a pé, prisão).",
        "Menos falsas conversas engraçadas; confiança baseada em momentos fortes.",
        "Página Armazenamento com simulação da limpeza (nada é apagado sem autorização).",
    ]},
    {"version": "0.5.2", "date": "2026-11-18", "title": "Limpeza por nota (simulação)", "items": [
        "Exclusão antiga por idade desligada: registros e avaliações nunca são apagados.",
        "Quarentena recuperável, restauração, proteção e favoritos nos cortes.",
    ]},
    {"version": "0.5.1", "date": "2026-11-18", "title": "Diretor entende histórias", "items": [
        "Memória das situações da live (perseguição, tiroteio, discussão, conversa engraçada, susto).",
        "Julgamento editorial com 14 critérios, evidências e confiança.",
        "Cortes da mesma história são unidos ou separados; corte fraco aguarda o desfecho.",
        "Página Diretor IA.",
    ]},
    {"version": "0.5.0", "date": "2026-11-18", "title": "Aprendizado contínuo", "items": [
        "Aprovar/rejeitar na Central de Cortes vira aprendizado.",
        "31 decisões anteriores importadas; aba Aprendizado com perfil editorial.",
    ]},
    {"version": "0.4.6", "date": "2026-11-18", "title": "Política de privacidade completa", "items": [
        "Política ampliada para a verificação do Google; termos com responsável e contato.",
    ]},
    {"version": "0.4.5", "date": "2026-11-18", "title": "Páginas públicas e cota do YouTube", "items": [
        "Páginas de início, privacidade e termos.",
        "Cota do YouTube atualizada (100 envios/dia); vídeos públicos liberados pela API.",
    ]},
    {"version": "0.4.4", "date": "2026-11-18", "title": "Domínio clipper.howto.software", "items": [
        "Limite de login por visitante real e API interna fechada para a internet.",
    ]},
    {"version": "0.4.1–0.4.3", "date": "2026-11-18", "title": "Instagram (login unificado)", "items": [
        "Conexão do Instagram pelo fluxo novo da Meta (config_id).",
    ]},
    {"version": "0.4.0", "date": "2026-11-18", "title": "Publicação automática", "items": [
        "Publicação no YouTube, Instagram e TikTok com aprovação humana e calendário.",
        "Primeiro envio real ao YouTube validado (privado).",
    ]},
    {"version": "0.3.0–0.3.1", "date": "2026-11-18", "title": "Avaliação editorial", "items": [
        "Página Avaliação, dataset e métricas; transcrição de áudio 3x mais rápida.",
    ]},
    {"version": "0.2.x", "date": "2026-11-18", "title": "Produção nos servidores", "items": [
        "Implantação nos servidores 201, 206 e 210; IA visual nas GPUs Arc; correção do YouTube.",
    ]},
    {"version": "0.1.0", "date": "2026-11-17", "title": "Primeira versão", "items": [
        "Captura de lives, detecção de momentos, cortes verticais com legenda e painel.",
    ]},
]
