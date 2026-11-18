import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { api, type AISettings } from "../api";
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
