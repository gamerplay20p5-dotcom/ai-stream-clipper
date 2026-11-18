import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { api, type AISettings, type ModelInfo } from "../api";
import { ErrorNote, PageHeader } from "../components/ui";
import { dimensionLabel } from "../lib/format";

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="grid gap-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,260px)] sm:items-center sm:gap-6">
      <div>
        <div className="text-sm">{label}</div>
        {hint && <div className="text-xs text-ink-muted">{hint}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="py-2">
      <h2 className="display mb-1 text-lg">{title}</h2>
      <div className="divide-y divide-suite-line">{children}</div>
    </section>
  );
}

export default function Settings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<AISettings>("/api/settings") });
  const models = useQuery({ queryKey: ["ai-models"], queryFn: () => api.get<ModelInfo[]>("/api/ai/models") });
  const where = (key: string) => {
    const w = (models.data ?? []).find((m) => m.key === key)?.workers.filter((x) => x.online) ?? [];
    return w.length ? `instalado em ${w.map((x) => x.host_label || x.worker).join(", ")}` : "não instalado em nenhum worker online";
  };
  const [s, setS] = useState<AISettings | null>(null);
  useEffect(() => { if (q.data) setS(q.data); }, [q.data]);
  const save = useMutation({
    mutationFn: (b: AISettings) => api.put<AISettings>("/api/settings", b),
    onSuccess: (d) => { qc.setQueryData(["settings"], d); setS(d); },
  });
  if (!s) return <ErrorNote error={q.error} />;
  const dirty = JSON.stringify(s) !== JSON.stringify(q.data);
  const num = (k: keyof AISettings) => (e: React.ChangeEvent<HTMLInputElement>) => setS({ ...s, [k]: Number(e.target.value) });
  const sel = (k: keyof AISettings) => (e: React.ChangeEvent<HTMLSelectElement>) => setS({ ...s, [k]: e.target.value });
  const bool = (k: keyof AISettings) => (e: React.ChangeEvent<HTMLInputElement>) => setS({ ...s, [k]: e.target.checked });

  return (
    <div className="max-w-3xl pb-24">
      <PageHeader title="Configurações da IA" />
      <Section title="Detecção">
        <Row label="Sensibilidade" hint="Mais alta encontra mais momentos, com mais falsos positivos.">
          <div className="flex items-center gap-3">
            <input type="range" min={0} max={1} step={0.05} value={s.sensitivity} onChange={num("sensitivity")} className="w-full accent-[#F2B33D]" />
            <span className="w-10 text-right text-sm">{Math.round(s.sensitivity * 100)}%</span>
          </div>
        </Row>
        <Row label="Pontuação mínima para virar corte" hint="De 0 a 100.">
          <input type="number" className="field" min={0} max={100} value={s.min_score} onChange={num("min_score")} />
        </Row>
        <Row label="Idioma das transmissões">
          <select className="field" value={s.language} onChange={sel("language")}>
            <option value="pt">Português</option><option value="en">Inglês</option><option value="es">Espanhol</option><option value="auto">Detectar automaticamente</option>
          </select>
        </Row>
        <Row label="Modelo de transcrição" hint="Maior é mais preciso e usa mais CPU no servidor 206.">
          <select className="field" value={s.whisper_model} onChange={sel("whisper_model")}>
            <option value="tiny">Whisper tiny</option><option value="base">Whisper base</option><option value="small">Whisper small</option>
            <option value="medium">Whisper medium</option><option value="large-v3">Whisper large-v3</option>
          </select>
        </Row>
        <Row label="Limite de cortes por hora de live">
          <input type="number" className="field" min={1} max={120} value={s.max_clips_per_hour} onChange={num("max_clips_per_hour")} />
        </Row>
      </Section>
      <Section title="Duração e contexto">
        <Row label="Duração mínima (s)"><input type="number" className="field" value={s.min_clip_seconds} onChange={num("min_clip_seconds")} /></Row>
        <Row label="Duração máxima (s)"><input type="number" className="field" value={s.max_clip_seconds} onChange={num("max_clip_seconds")} /></Row>
        <Row label="Contexto antes do acontecimento (s)"><input type="number" className="field" value={s.pre_context_seconds} onChange={num("pre_context_seconds")} /></Row>
        <Row label="Contexto depois do acontecimento (s)"><input type="number" className="field" value={s.post_context_seconds} onChange={num("post_context_seconds")} /></Row>
      </Section>
      <Section title="Análise visual">
        <Row label="Analisar os quadros dos momentos candidatos">
          <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.vision_enabled} onChange={bool("vision_enabled")} />
        </Row>
        <Row label="Provedor" hint="O modelo multimodal exige GPU Intel validada (servidores 201 e 210).">
          <select className="field" value={s.vision_provider} onChange={sel("vision_provider")}>
            <option value="heuristic">Heurístico (OpenCV, CPU)</option>
            <option value="openvino_vlm">Modelo multimodal OpenVINO (experimental, GPU)</option>
          </select>
        </Row>
        <Row label="Intervalo entre quadros (s)" hint="Perto do pico a amostragem é três vezes mais densa.">
          <input type="number" step={0.1} className="field" value={s.vision_frame_interval} onChange={num("vision_frame_interval")} />
        </Row>
        <Row label="Esperar o worker visual por até (s)" hint="Depois disso o corte segue sem análise visual.">
          <input type="number" className="field" value={s.vision_timeout_seconds} onChange={num("vision_timeout_seconds")} />
        </Row>
        <Row label="Modelo multimodal" hint={where(s.vision_model)}>
          <select className="field" value={s.vision_model} onChange={sel("vision_model")}>
            <option value="qwen3-vl-2b-int4">Qwen3-VL 2B INT4 (padrão)</option>
            <option value="qwen3-vl-4b-int4">Qwen3-VL 4B INT4 (oficial, mais pesado)</option>
            <option value="internvl2-1b-int4">InternVL2 1B INT4 (leve, para CPU)</option>
          </select>
        </Row>
        <Row label="Contexto extra mostrado à IA (s)" hint="Antes e depois do candidato, para achar começo e desfecho.">
          <input type="number" className="field" min={0} max={30} value={s.analysis_context_seconds} onChange={num("analysis_context_seconds")} />
        </Row>
      </Section>
      <Section title="Diretor e narrativa">
        <Row label="Usar o diretor automático" hint="Combina fala, som e visão para decidir início, fim e se o corte fica.">
          <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.director_enabled} onChange={bool("director_enabled")} />
        </Row>
        <Row label="Modelo de texto (justificativa e títulos)" hint={s.narrative_model.startsWith("qwen3-") ? where(s.narrative_model) : "Sem modelo extra na memória"}>
          <select className="field" value={s.narrative_model} onChange={sel("narrative_model")}>
            <option value="qwen3-1.7b-int4">Qwen3 1.7B INT4 (padrão)</option>
            <option value="qwen3-0.6b-int4">Qwen3 0.6B INT4 (mais rápido, mais fraco)</option>
            <option value="same_as_vision">Reusar o modelo multimodal</option>
            <option value="template">Modelos de frase locais</option>
          </select>
        </Row>
      </Section>
      <Section title="Edição automática">
        <Row label="Template">
          <select className="field" value={s.edit_template} onChange={sel("edit_template")}>
            <option value="padrao">Padrão: legenda destacada, zoom leve</option>
            <option value="dinamico">Dinâmico: zoom maior, transições curtas</option>
            <option value="minimalista">Minimalista: legenda limpa, sem zoom nem título</option>
          </select>
        </Row>
        <Row label="Intensidade do zoom no pico" hint="0 desliga. O zoom nunca passa de 1,35x.">
          <div className="flex items-center gap-3">
            <input type="range" min={0} max={1.5} step={0.1} value={s.zoom_intensity} onChange={num("zoom_intensity")} className="w-full accent-[#F2B33D]" />
            <span className="w-10 text-right text-sm">{s.zoom_intensity.toFixed(1)}</span>
          </div>
        </Row>
        <Row label="Normalizar o volume" hint="Ganho linear até -14 LUFS sem passar de -1 dBTP: não distorce nem comprime.">
          <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.normalize_audio} onChange={bool("normalize_audio")} />
        </Row>
      </Section>
      <Section title="Exportação">
        <Row label="Qualidade (CRF)" hint="Menor é melhor e gera arquivos maiores. 18 a 23 é o usual.">
          <input type="number" className="field" min={14} max={32} value={s.export_crf} onChange={num("export_crf")} />
        </Row>
        <Row label="Velocidade do codificador">
          <select className="field" value={s.export_preset} onChange={sel("export_preset")}>
            {["ultrafast", "veryfast", "faster", "fast", "medium", "slow"].map((p) => <option key={p}>{p}</option>)}
          </select>
        </Row>
        <Row label="Quadros por segundo"><input type="number" className="field" min={24} max={60} value={s.export_fps} onChange={num("export_fps")} /></Row>
        <Row label="Estilo de legenda padrão">
          <select className="field" value={s.caption_style} onChange={sel("caption_style")}>
            <option value="bold">Destaque</option><option value="clean">Limpa</option><option value="boxed">Com fundo</option><option value="none">Sem legenda</option>
          </select>
        </Row>
        <Row label="Mostrar o título no vídeo">
          <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.show_title_overlay} onChange={bool("show_title_overlay")} />
        </Row>
      </Section>
      <Section title="Quality Gate (seleção em 4 dimensões)">
        <Row label="Usar o Quality Gate" hint="Separa a qualidade do acontecimento da qualidade do corte: um bom momento mal cortado é reeditado antes de ser descartado.">
          <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.gate_enabled} onChange={bool("gate_enabled")} />
        </Row>
        <Row label="Acontecimento mínimo (0–10)" hint="Abaixo disso (e fora da margem de dúvida) o momento é rejeitado.">
          <input type="number" step={0.1} min={0} max={10} className="field" value={s.gate_event_min} onChange={num("gate_event_min")} />
        </Row>
        <Row label="Narrativa mínima para 'pronto' (0–10)">
          <input type="number" step={0.1} min={0} max={10} className="field" value={s.gate_narrative_min} onChange={num("gate_narrative_min")} />
        </Row>
        <Row label="Potencial mínimo para reeditar (0–10)">
          <input type="number" step={0.1} min={0} max={10} className="field" value={s.gate_recovery_min} onChange={num("gate_recovery_min")} />
        </Row>
        <Row label="Reedições automáticas por corte" hint="Limite para não processar sem fim.">
          <input type="number" min={0} max={5} className="field" value={s.gate_max_rework} onChange={num("gate_max_rework")} />
        </Row>
      </Section>
      <Section title="Legendas inteligentes">
        <Row label="Reconhecer quem está falando" hint="Separa a voz do streamer das outras (rádio, personagens). Confirme a voz na página Streamers.">
          <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.speakers_enabled} onChange={bool("speakers_enabled")} />
        </Row>
        <Row label="O que legendar" hint="“Só o streamer” vale apenas depois que você confirmar a voz; antes disso as outras vozes aparecem menores.">
          <select className="field" value={s.caption_mode} onChange={sel("caption_mode")}>
            <option value="streamer_highlight">Streamer em destaque, outras vozes menores</option>
            <option value="streamer_only">Só a fala do streamer</option>
            <option value="all">Todas as falas no mesmo estilo</option>
          </select>
        </Row>
        <Row label="Fonte">
          <select className="field" value={s.caption_font} onChange={sel("caption_font")}>
            <option value="">Padrão (Noto Sans)</option><option>Noto Sans</option><option>DejaVu Sans</option><option>Noto Serif</option>
          </select>
        </Row>
        <Row label="Tamanho" hint="100% é o tamanho atual.">
          <div className="flex items-center gap-3">
            <input type="range" min={60} max={160} step={5} value={s.caption_size_pct} onChange={num("caption_size_pct")} className="w-full accent-[#F2B33D]" />
            <span className="w-12 text-right text-sm">{s.caption_size_pct}%</span>
          </div>
        </Row>
        <Row label="Posição" hint="Sempre dentro da área segura dos botões do Shorts, Reels e TikTok.">
          <select className="field" value={s.caption_position} onChange={sel("caption_position")}>
            <option value="low">Embaixo</option><option value="middle">Meio</option><option value="high">Alto</option>
          </select>
        </Row>
        <Row label="Animação">
          <select className="field" value={s.caption_animation} onChange={sel("caption_animation")}>
            <option value="highlight">Palavra falada em destaque</option><option value="pop">Destaque com pulo</option><option value="none">Sem animação</option>
          </select>
        </Row>
        <Row label="Cores" hint="Palavra falada · palavra de ênfase · outras vozes. Vazio usa a cor do estilo.">
          <div className="flex items-center gap-3">
            <input type="color" aria-label="Cor da palavra falada" value={s.caption_active_color || "#FFE500"} onChange={(e) => setS({ ...s, caption_active_color: e.target.value.toUpperCase() })} />
            <input type="color" aria-label="Cor de ênfase" value={s.caption_emphasis_color || "#7AFF66"} onChange={(e) => setS({ ...s, caption_emphasis_color: e.target.value.toUpperCase() })} />
            <input type="color" aria-label="Cor das outras vozes" value={s.caption_other_color} onChange={(e) => setS({ ...s, caption_other_color: e.target.value.toUpperCase() })} />
            <button type="button" className="btn-quiet px-2 py-1 text-xs" onClick={() => setS({ ...s, caption_active_color: "", caption_emphasis_color: "", caption_other_color: "#9AD7FF" })}>Restaurar</button>
          </div>
        </Row>
      </Section>
      <Section title="Identidade do canal">
        <Row label="Nome do canal" hint="Usado na marca d'água.">
          <input className="field" maxLength={40} value={s.brand_name} onChange={(e) => setS({ ...s, brand_name: e.target.value })} />
        </Row>
        <Row label="Marca d'água discreta" hint="Texto pequeno e semitransparente, longe das legendas.">
          <div className="flex flex-wrap items-center gap-3">
            <input type="checkbox" className="h-4 w-4 accent-[#F2B33D]" checked={s.brand_watermark} onChange={bool("brand_watermark")} />
            <select className="field max-w-[180px]" value={s.brand_watermark_position} onChange={sel("brand_watermark_position")}>
              <option value="top_right">Canto superior direito</option><option value="top_left">Canto superior esquerdo</option><option value="bottom_left">Canto inferior esquerdo</option>
            </select>
            <input type="range" min={0.1} max={1} step={0.05} value={s.brand_watermark_opacity} onChange={num("brand_watermark_opacity")} className="w-28 accent-[#F2B33D]" aria-label="Opacidade" />
          </div>
        </Row>
        <Row label="Chamada no final do vídeo" hint="Ex.: “Siga para mais cortes”. Vazio não adiciona nada.">
          <div className="flex items-center gap-3">
            <input className="field" maxLength={80} value={s.brand_cta} onChange={(e) => setS({ ...s, brand_cta: e.target.value })} />
            <input type="number" className="field w-20" min={1.5} max={6} step={0.5} value={s.brand_cta_seconds} onChange={num("brand_cta_seconds")} aria-label="Segundos" />
          </div>
        </Row>
        <Row label="Hashtags do canal" hint="Separadas por espaço; entram depois das hashtags do corte.">
          <input className="field" key={s.brand_hashtags.join(" ")} defaultValue={s.brand_hashtags.join(" ")} onBlur={(e) => setS({ ...s, brand_hashtags: e.target.value.split(/[\s,]+/).map((t) => t.replace(/^#/, "")).filter(Boolean).slice(0, 8) })} />
        </Row>
        <Row label="Rodapé da descrição" hint="Divulgação cruzada: outras redes, agenda das lives…">
          <textarea className="field min-h-[70px]" maxLength={500} value={s.brand_description_footer} onChange={(e) => setS({ ...s, brand_description_footer: e.target.value })} />
        </Row>
      </Section>
      <Section title="Limites de processamento">
        <Row label="Renderizações em paralelo (todos os servidores)"><input type="number" className="field" min={1} max={16} value={s.max_parallel_renders} onChange={num("max_parallel_renders")} /></Row>
        <Row label="Análises de áudio em paralelo"><input type="number" className="field" min={1} max={32} value={s.max_parallel_audio} onChange={num("max_parallel_audio")} /></Row>
      </Section>
      <Section title="Retenção de vídeos">
        <Row label="Apagar rejeitados após (dias)" hint="0 mantém para sempre."><input type="number" className="field" min={0} value={s.retention_rejected_days} onChange={num("retention_rejected_days")} /></Row>
        <Row label="Apagar não revisados após (dias)"><input type="number" className="field" min={0} value={s.retention_unreviewed_days} onChange={num("retention_unreviewed_days")} /></Row>
        <Row label="Apagar aprovados após (dias)"><input type="number" className="field" min={0} value={s.retention_approved_days} onChange={num("retention_approved_days")} /></Row>
      </Section>
      <Section title="Peso de cada critério">
        {Object.entries(s.weights).map(([k, v]) => (
          <Row key={k} label={dimensionLabel[k] ?? k}>
            <div className="flex items-center gap-3">
              <input type="range" min={0} max={3} step={0.1} value={v} className="w-full accent-[#F2B33D]"
                onChange={(e) => setS({ ...s, weights: { ...s.weights, [k]: Number(e.target.value) } })} />
              <span className="w-8 text-right text-sm">{v.toFixed(1)}</span>
            </div>
          </Row>
        ))}
      </Section>
      <p className="mt-4 text-sm text-ink-muted">Publicação automática nas redes é um módulo futuro e continua desativada.</p>

      <div className={`fixed inset-x-0 bottom-0 border-t border-suite-line bg-suite-panel/95 px-4 py-3 backdrop-blur transition-transform md:left-56 ${dirty ? "translate-y-0" : "translate-y-full"}`}>
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3">
          <ErrorNote error={save.error} />
          <span className="text-sm text-ink-muted">{save.error ? "" : "Alterações não salvas"}</span>
          <div className="flex gap-2">
            <button className="btn-quiet" onClick={() => setS(q.data!)}>Descartar</button>
            <button className="btn-take" disabled={save.isPending} onClick={() => save.mutate(s)}>Salvar configurações</button>
          </div>
        </div>
      </div>
    </div>
  );
}
