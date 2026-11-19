"""Short release notes shown in the dashboard ("Patch notes"). Newest first. PT-BR, short.

Every release adds one entry here (kept in the code so it ships with the version it describes).
"""

from __future__ import annotations

NOTES: list[dict] = [
    {"version": "0.7.1", "date": "2026-11-19", "title": "Painel organizado por tarefa", "items": [
        "Nova tela Hoje: mostra primeiro o que precisa de você (cortes, sugestões de publicação, envios incertos e falhas).",
        "Central de cortes: barra Aprovar/Rejeitar/Próximo sempre à mão no celular, “Por que este corte?” em linguagem simples e atalhos com ?.",
        "Agenda semanal: sugestão da IA, confirmado e automático têm formatos diferentes; a pausa de emergência pede confirmação.",
        "Diretor IA responde: o que aconteceu, por que importa, o que a IA decidiu e o resultado. O técnico fica em Avançado.",
        "Pessoas e acesso, aba Compartilhar e Minha Biblioteca para contas convidadas.",
    ]},
    {"version": "0.7.0", "date": "2026-11-19", "title": "Contas e vídeos compartilhados", "items": [
        "O administrador cria contas (sem cadastro público e sem envio de e-mail) e compartilha cortes renderizados com elas.",
        "Contas externas só assistem ao que foi liberado; download e variantes são permissões separadas.",
        "Revogar, expirar, desativar ou mudar o papel tem efeito imediato, inclusive em sessões abertas.",
        "Todo o painel interno passou a ser exclusivo da equipe (administradores).",
    ]},
    {"version": "0.6.3", "date": "2026-11-19", "title": "Viral Intelligence: laboratório de referências", "items": [
        "Nova página Viral Intelligence: referências do YouTube e do Instagram, coleções, categorias, jogos, humor e formato.",
        "Metadados públicos do YouTube pela API autorizada (canal, vídeo, Short); Instagram por cadastro manual com o motivo.",
        "Envie um print: a IA mede a organização da tela (webcam, legenda, cores, selo) e cria um perfil de formato ajustável.",
        "Teste o perfil num corte nosso: a prévia vira uma variante para comparar; o vídeo principal não muda sozinho.",
        "Selo de identificação com a nossa foto e o nosso nome.",
    ]},
    {"version": "0.6.2", "date": "2026-11-19", "title": "Editor estrutural", "items": [
        "Cinco estruturas para o mesmo acontecimento: gancho imediato, suspense, humor de reação, história completa e momento rápido.",
        "Corte de pausas que nunca remove reações, o ponto alto nem o desfecho; só trechos reais, na ordem real.",
        "Prévia opcional do clímax no início, sempre marcada como 'Daqui a pouco...'.",
        "Modo sombra: o vídeo principal continua o original e a versão reestruturada fica ao lado para comparar.",
        "Comparação lado a lado, inclusive às cegas, e opção de usar a variante como vídeo principal.",
        "Cada publicação envia exatamente o vídeo aprovado no agendamento, mesmo que o corte mude depois.",
    ]},
    {"version": "0.6.1", "date": "2026-11-19", "title": "Correção da publicação no Instagram", "items": [
        "A tela mostrava 'Público' mas enviava 'Privado' para o Instagram, e o botão ficava bloqueado: corrigido.",
        "Reels exigem confirmação explícita de que serão públicos; nada é escolhido em silêncio.",
        "Regras e agendamento em lote validam a privacidade de cada conta; regras inválidas aparecem marcadas.",
        "O painel mostra todos os motivos de bloqueio, inclusive o direito de republicação.",
        "Envio do vídeo ao Instagram em partes (sem carregar o arquivo inteiro na memória) e erros da Meta classificados.",
        "Proteção contra publicar o mesmo Reel duas vezes.",
    ]},
    {"version": "0.6.0", "date": "2026-11-18", "title": "Quality Gate 2.0", "items": [
        "Cada corte recebe 4 notas separadas: acontecimento, narrativa do corte, potencial de reedição e técnica.",
        "Bom momento mal cortado é reeditado automaticamente (começar antes, incluir o desfecho, aproximar do ponto alto) antes de ser descartado; no máximo 2 vezes.",
        "Novos resultados: pronto, reeditar, aguardando contexto, revisão humana e rejeitado.",
        "Aba 'Revisão humana' na Central de Cortes para os casos de dúvida ou de decisões em conflito.",
        "Limiares ajustáveis em Configurações da IA.",
    ]},
    {"version": "0.5.7", "date": "2026-11-18", "title": "Correção do reconhecimento de voz", "items": [
        "Sons avulsos (música, efeitos do jogo) não ocupam mais todas as vagas de vozes: novas vozes, inclusive a do streamer, voltam a ser reconhecidas.",
    ]},
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
