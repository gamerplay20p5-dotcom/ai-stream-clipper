import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Heart, ShieldCheck, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Disclosure, Empty, ErrorNote, Meter, Notice, PageHeader, Skeleton } from "../components/ui";
import { ago, bytes } from "../lib/format";
import { t, locale } from "../i18n";

type Overview = { bytes: Record<string, number>; clips: Record<string, number>; recovered_bytes: number;
  disk: { total?: number; free?: number }; enabled: boolean; dry_run: boolean; enabled_at: string | null;
  retro_authorized: boolean; quota_bytes: number };
type Item = { clip_id: number; status: string; title: string; reason: string; score: number; bytes: number; files: number;
  created_at: string; protected_by?: string[]; days?: number; note?: string };
type Plan = { eligible: Item[]; protected: Item[]; waiting_period: Item[]; excluded_existing: number;
  bytes: Record<string, number>; files: number; total_bytes: number; counts: Record<string, number> };
type LogRow = { id: number; clip_id: number | null; action: string; reason: string; bytes_freed: number; dry_run: boolean; actor: string; at: string };
type Settings = Record<string, unknown>;

const FIELDS: [string, string, "num" | "bool", string?][] = [
  ["cleanup_enabled", t("Ativar limpeza automática"), "bool"],
  ["cleanup_dry_run", t("Modo simulação (não apaga nada)"), "bool"],
  ["cleanup_keep_min_score", t("Nota editorial mínima para manter um corte"), "num"],
  ["review_min_score", t("Nota mínima para revisão humana"), "num"],
  ["autopublish_min_score", t("Nota mínima para publicação automática"), "num"],
  ["cleanup_below_min_days", t("Dias até descartar cortes abaixo da nota mínima"), "num"],
  ["cleanup_rejected_days", t("Dias até descartar cortes rejeitados"), "num"],
  ["cleanup_quarantine_days", t("Dias de quarentena (recuperável)"), "num"],
  ["cleanup_disposable_quota_gb", t("Espaço máximo para arquivos descartáveis (GB)"), "num"],
  ["cleanup_purge_video", t("Excluir o MP4 renderizado"), "bool"],
  ["cleanup_purge_source", t("Excluir a mídia de origem"), "bool"],
  ["cleanup_purge_thumbnail", t("Excluir a miniatura"), "bool"],
];

export default function Storage() {
  const qc = useQueryClient();
  const ov = useQuery({ queryKey: ["storage-ov"], queryFn: () => api.get<Overview>("/api/storage/overview"), refetchInterval: 30_000 });
  const [includeExisting, setIncludeExisting] = useState(false);
  const sim = useQuery({ queryKey: ["storage-sim", includeExisting], queryFn: () => api.get<Plan>(`/api/storage/simulation?include_existing=${includeExisting}`) });
  const logq = useQuery({ queryKey: ["storage-log"], queryFn: () => api.get<LogRow[]>("/api/storage/log?limit=50") });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<Settings>("/api/settings") });
  const [form, setForm] = useState<Settings | null>(null);
  useEffect(() => { if (settings.data && !form) setForm(settings.data); }, [settings.data]);
  const save = useMutation({
    mutationFn: (b: Settings) => api.put<Settings>("/api/settings", b),
    onSuccess: () => { qc.invalidateQueries(); },
  });
  const flag = useMutation({
    mutationFn: ({ id, what, value }: { id: number; what: "protect" | "favorite"; value: boolean }) => api.post(`/api/storage/clips/${id}/${what}`, { value }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["storage-sim"] }),
  });
  const [phrase, setPhrase] = useState("");
  const retro = useMutation({ mutationFn: () => api.post("/api/storage/authorize-retroactive", { confirm: phrase }), onSuccess: () => qc.invalidateQueries() });
  const o = ov.data;
  const s = sim.data;
  const enabledNow = Boolean(form?.cleanup_enabled) && !form?.cleanup_dry_run;
  return (
    <div className="space-y-6">
      <PageHeader title={t("Armazenamento")} description={t("Acompanhe o uso do disco e simule os efeitos da política de retenção.")} />
      <ErrorNote error={ov.error ?? sim.error ?? logq.error ?? settings.error ?? save.error ?? flag.error ?? retro.error} />
      {ov.isLoading && <Skeleton label={t("Consultando uso do disco")} />}
      {save.isSuccess && <Notice>{t("Política de retenção salva.")}</Notice>}
      {o?.disk.total != null && o.disk.total > 0 && o.disk.free != null && <section className="disk-capacity">
        <div className="flex flex-wrap items-baseline justify-between gap-3"><h2 className="display text-lg">{t("Capacidade do disco")}</h2>
          <span className="text-sm text-ink-muted">{bytes(o.disk.free)} {t("livres de")} {bytes(o.disk.total)}</span></div>
        <Meter label={t("Espaço ocupado")} max={o.disk.total} value={o.disk.total-o.disk.free}/>
      </section>}
      {o && (
        <div className="panel grid grid-cols-2 divide-suite-line sm:grid-cols-3 xl:grid-cols-6 xl:divide-x">
          <Card label={t("Cortes renderizados")} value={bytes(o.bytes.video)} />
          <Card label={t("Mídias de origem")} value={bytes(o.bytes.source)} />
          <Card label={t("Miniaturas")} value={bytes(o.bytes.thumbnail)} />
          <Card label={t("Dataset de avaliação")} value={bytes(o.bytes.dataset)} />
          <Card label={t("Em quarentena")} value={bytes(o.bytes.quarantine)} hint={t("{n} cortes", { n: o.clips.quarantined ?? 0 })} />
          <Card label={t("Recuperado pela limpeza")} value={bytes(o.recovered_bytes)} hint={t("{v} cortes descartados · {v2} preservados", { v: o.clips.purged ?? 0, v2: o.clips.active ?? 0 })} />
        </div>
      )}
      {o && (
        <p className={`rounded-md border px-3 py-2 text-sm ${o.enabled && !o.dry_run ? "border-tally/50 bg-tally-dim text-tally" : "border-ok/40 bg-ok/10 text-ok"}`}>
          {!o.enabled ? t("Limpeza automática desligada: nada é apagado. A simulação abaixo mostra o que aconteceria.")
            : o.dry_run ? t("Limpeza em modo simulação: as decisões são registradas, mas nenhum arquivo é apagado.")
              : t("Limpeza ativa: cortes elegíveis vão para a quarentena e a mídia é apagada depois do prazo. Os registros e as avaliações são sempre mantidos.")}
        </p>
      )}

      <section className="panel">
        <header className="panel-heading"><h2 className="display text-lg">{t("Simulação da limpeza")}</h2></header>
        <div className="panel-body">
        <p className="mb-3 text-xs text-ink-muted">{t("Somente leitura. Mostra o que a limpeza faria com as configurações atuais. Ao mudar as configurações, salve e a simulação é atualizada.")}</p>
        <label className="mb-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={includeExisting} onChange={(e) => setIncludeExisting(e.target.checked)} />
          {t("Incluir os cortes que já existiam (prévia; apagar esses cortes exige autorização retroativa)")}</label>
        {sim.isLoading && <Skeleton label={t("Calculando simulação")} />}
        {s && (
          <>
            <div className="mb-4 grid grid-cols-2 divide-suite-line rounded-md bg-suite-raised sm:grid-cols-4 sm:divide-x">
              <Card label={t("Seriam removidos")} value={`${s.counts.eligible} cortes`} hint={`${s.files} arquivos`} />
              <Card label={t("Espaço recuperado")} value={bytes(s.total_bytes)} hint={t("vídeo {bytes} · origem {bytes2} · miniaturas {bytes3}", { bytes: bytes(s.bytes.video), bytes2: bytes(s.bytes.source), bytes3: bytes(s.bytes.thumbnail) })} />
              <Card label={t("Protegidos")} value={String(s.counts.protected)} hint={s.excluded_existing ? t("{excluded_existing} por serem anteriores à ativação", { excluded_existing: s.excluded_existing }) : ""} />
              <Card label={t("Aguardando o prazo")} value={String(s.counts.waiting_period)} />
            </div>
            <ClipTable title={t("Seriam removidos (após a quarentena)")} rows={s.eligible} onFlag={(id, what, v) => flag.mutate({ id, what, value: v })} />
            <ClipTable title={t("Protegidos e o motivo")} rows={s.protected} />
          </>
        )}
        </div>
      </section>

      {form && (
        <Disclosure title={t("Política de retenção e cotas")}>
          <div className="grid gap-3 md:grid-cols-2">
            {FIELDS.map(([k, label, kind]) => (
              <label key={k} className="flex items-center justify-between gap-3 text-sm">
                <span>{label}</span>
                {kind === "bool"
                  ? <input type="checkbox" checked={Boolean(form[k])} onChange={(e) => setForm({ ...form, [k]: e.target.checked })} />
                  : <input className="field w-24" type="number" value={Number(form[k] ?? 0)} onChange={(e) => setForm({ ...form, [k]: Number(e.target.value) })} />}
              </label>
            ))}
          </div>
          {enabledNow && <p className="mt-3 text-xs text-tally">{t("Atenção: com a simulação desligada, cortes elegíveis entram em quarentena e a mídia é apagada depois de {String} dias. Os cortes que já existiam continuam fora até você autorizar abaixo.", { String: String(form.cleanup_quarantine_days) })}</p>}
          <button className="btn-take mt-3" disabled={save.isPending} onClick={() => save.mutate(form)}>{save.isPending ? t("Salvando…") : t("Salvar configurações")}</button>
        </Disclosure>
      )}

      {o && (
        <Disclosure title={t("Limpeza retroativa")}>
          {o.retro_authorized ? <p className="text-ok">{t("Autorizada: cortes antigos elegíveis também entram no ciclo.")}</p> : (
            <>
              <p className="mb-2 text-ink-muted">{t("Os cortes existentes antes da ativação nunca são apagados sem sua autorização. Para incluí-los, revise a simulação com \"Incluir os cortes que já existiam\" marcado e digite")} <b>{t("AUTORIZO A LIMPEZA RETROATIVA")}</b>.</p>
              <div className="flex flex-wrap gap-2"><input className="field max-w-sm" aria-label={t("Frase de autorização da limpeza retroativa")} value={phrase} onChange={(e) => setPhrase(e.target.value)} />
                <button className="btn-danger" disabled={phrase.trim().toUpperCase() !== "AUTORIZO A LIMPEZA RETROATIVA" || retro.isPending} onClick={() => retro.mutate()}>{t("Autorizar")}</button></div>
            </>
          )}
        </Disclosure>
      )}

      <Disclosure title={t("Histórico de limpeza")}>
        {logq.isLoading && <Skeleton label={t("Carregando histórico")} lines={2} />}
        {logq.data?.length === 0 && <Empty title={t("Nenhuma ação registrada")} />}
        <ul className="divide-y divide-suite-line text-sm">
          {(logq.data ?? []).map((r) => (
            <li key={r.id} className="break-words py-2"><span className="text-ink-muted">{ago(r.at)}</span> · {r.dry_run ? t("simulação") : r.action}{r.clip_id ? <> · <Link className="text-take" to={`/cortes/${r.clip_id}?status=`}>#{r.clip_id}</Link></> : ""} · {r.reason}{r.bytes_freed ? ` · ${bytes(r.bytes_freed)}` : ""}</li>
          ))}
        </ul>
      </Disclosure>
    </div>
  );
}

function Card({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="min-w-0 border-suite-line p-3 sm:p-4">
      <p className="text-xs text-ink-muted">{label}</p>
      <p className="display mt-1 text-lg">{value}</p>
      {hint && <p className="mt-1 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  );
}

function ClipTable({ title, rows, onFlag }: { title: string; rows: Item[]; onFlag?: (id: number, what: "protect" | "favorite", v: boolean) => void }) {
  if (!rows.length) return null;
  return (
    <details className="mb-3">
      <summary className="cursor-pointer text-sm font-medium">{title} ({rows.length})</summary>
      <div className="table-scroll mt-2" tabIndex={0} role="region" aria-label={title}>
      <table className="w-full min-w-[30rem] text-sm">
        <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">{t("Corte")}</th><th>{t("Motivo")}</th><th>{t("Nota")}</th><th>{t("Tamanho")}</th><th /></tr></thead>
        <tbody>{rows.slice(0, 150).map((r) => (
          <tr key={r.clip_id} className="border-t border-suite-line/50">
            <td className="py-1"><Link className="text-take hover:underline" to={`/cortes/${r.clip_id}?status=`}>#{r.clip_id}</Link> <span className="text-xs text-ink-muted">{r.title}</span></td>
            <td className="text-xs">{r.protected_by ? r.protected_by.join("; ") : r.reason}</td>
            <td>{r.score.toFixed(0)}</td><td>{bytes(r.bytes)}</td>
            <td className="space-x-1 text-right">{onFlag && <>
              <button className="btn-quiet px-2 py-1 text-xs" aria-label={t("Proteger contra limpeza")} title={t("Proteger contra limpeza")} onClick={() => onFlag(r.clip_id, "protect", true)}><ShieldCheck className="h-3 w-3" /></button>
              <button className="btn-quiet px-2 py-1 text-xs" aria-label={t("Favoritar")} title={t("Favoritar")} onClick={() => onFlag(r.clip_id, "favorite", true)}><Heart className="h-3 w-3" /></button>
            </>}</td>
          </tr>))}</tbody>
      </table>
      </div>
    </details>
  );
}

export function StorageFlags({ clipId, protectedFlag, favorite, lifecycle, purgeAfter }: { clipId: number; protectedFlag?: boolean; favorite?: boolean; lifecycle?: string; purgeAfter?: string | null }) {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: (p: { path: string; body?: unknown }) => api.post(`/api/storage/clips/${clipId}/${p.path}`, p.body ?? {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["clip", clipId] }),
  });
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs">
      <button className={`btn-quiet px-2 py-1 ${protectedFlag ? "text-ok" : ""}`} disabled={m.isPending} onClick={() => m.mutate({ path: "protect", body: { value: !protectedFlag } })}>
        <ShieldCheck className="h-3.5 w-3.5" /> {protectedFlag ? t("Protegido") : t("Proteger da limpeza")}</button>
      <button className={`btn-quiet px-2 py-1 ${favorite ? "text-take" : ""}`} disabled={m.isPending} onClick={() => m.mutate({ path: "favorite", body: { value: !favorite } })}>
        <Heart className="h-3.5 w-3.5" /> {favorite ? t("Favorito") : t("Favoritar")}</button>
      {lifecycle === "quarantined" && <button className="btn-quiet px-2 py-1 text-take" disabled={m.isPending} onClick={() => m.mutate({ path: "restore" })}><Undo2 className="h-3.5 w-3.5" /> {t("Restaurar (exclusão em {v})", { v: purgeAfter ? new Date(purgeAfter).toLocaleDateString(locale) : t("breve") })}</button>}
      {lifecycle === "purged" && <span className="text-ink-faint">{t("Mídia removida pela limpeza (registro e avaliações preservados)")}</span>}
      <ErrorNote error={m.error} />
    </div>
  );
}
