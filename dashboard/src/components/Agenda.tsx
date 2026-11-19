import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, ExternalLink, Pause, Play } from "lucide-react";
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { platformName } from "../lib/format";
import { decisionHint, decisionLabel, decisionOf, type Decision } from "../lib/publishing";
import type { CalendarData } from "./EditorialOps";
import { DecisionChip, Empty, ErrorNote, Notice, Skeleton, Status } from "./ui";

type Item = CalendarData["items"][number];
type Control = { paused: boolean; reason?: string; by?: string; at?: string };

const TZ = "America/Sao_Paulo";
const STATUS: Record<string, [string, "neutral" | "success" | "danger" | "warning" | "processing"]> = {
  uploading: ["Enviando", "processing"], processing: ["Processando na plataforma", "processing"], published: ["Publicado", "success"],
  failed: ["Falhou", "danger"], uncertain: ["Resultado incerto", "warning"], expired: ["Expirou sem confirmação", "neutral"],
};
const short: Record<Decision, string> = { sugestao: "Sugestão", confirmado: "Confirmado", automatico: "Automático" };
const PRIVACY: Record<string, string> = { private: "Privado", unlisted: "Não listado", public: "Público" };
const weekday = new Intl.DateTimeFormat("pt-BR", { weekday: "short", timeZone: "UTC" });
const longDay = new Intl.DateTimeFormat("pt-BR", { weekday: "long", day: "numeric", month: "long", timeZone: "UTC" });

/** Local calendar day (YYYY-MM-DD) in Brasília for an instant. */
const localDay = (d: Date) => new Intl.DateTimeFormat("sv-SE", { timeZone: TZ }).format(d);
const addDays = (day: string, n: number) => {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const mondayOf = (day: string) => addDays(day, -((new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7));

/** Week calendar of publications. Suggestion, confirmed and automatic are told apart by shape and text, not colour. */
export default function Agenda() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["calendar", 21], queryFn: () => api.get<CalendarData>("/api/publish/calendar?days=21"), refetchInterval: 20_000 });
  const today = localDay(new Date());
  const [week, setWeek] = useState(0);
  const [sel, setSel] = useState<number[]>([]);
  const [open, setOpen] = useState<Item | null>(null);
  const [filter, setFilter] = useState<Decision | "todas">("todas");
  const dialog = useRef<HTMLDialogElement>(null);
  const done = () => {
    setSel([]);
    qc.invalidateQueries({ queryKey: ["calendar"] });
    qc.invalidateQueries({ queryKey: ["publications"] });
  };
  const confirm = useMutation({
    mutationFn: (ids: number[]) => api.post<{ confirmed: number[]; refused: Record<string, string[]> }>("/api/publish/proposals/confirm", { ids }),
    onSuccess: done,
  });
  const dismiss = useMutation({ mutationFn: (ids: number[]) => api.post("/api/publish/proposals/dismiss", { ids }), onSuccess: done });

  const items = q.data?.items ?? [];
  const proposals = items.filter((i) => i.status === "proposed");
  const visible = items.filter((i) => filter === "todas" || decisionOf(i) === filter);
  const start = addDays(mondayOf(today), week * 7);
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i));
  const byDay = (day: string) => visible.filter((i) => (i.when ?? today).slice(0, 10) === day);
  const refused = Object.entries(confirm.data?.refused ?? {});
  const show = (item: Item) => {
    setOpen(item);
    dialog.current?.showModal();
  };

  return (
    <div className="space-y-4">
      <PauseControl />
      {proposals.length > 0 && (
        <section className="proposal-bar" aria-labelledby="proposal-title">
          <div className="min-w-0">
            <h2 id="proposal-title" className="font-semibold">
              {proposals.length === 1 ? "1 sugestão da IA aguarda você" : `${proposals.length} sugestões da IA aguardam você`}
            </h2>
            <p className="text-xs text-ink-muted">Nada é enviado sem a sua confirmação. Sugestões não confirmadas expiram no horário.</p>
            {proposals.some((p) => p.privacy === "public") && <p className="mt-1 text-xs font-semibold text-tally">Há sugestões públicas: confirmar publica para todos.</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-take" disabled={confirm.isPending} onClick={() => confirm.mutate(sel.length ? sel : proposals.map((p) => p.id))}>
              {sel.length ? `Confirmar ${sel.length} ${sel.length === 1 ? "selecionada" : "selecionadas"}` : "Confirmar todas"}
            </button>
            <button className="btn-quiet" disabled={!sel.length || dismiss.isPending} onClick={() => dismiss.mutate(sel)}>Recusar selecionadas</button>
          </div>
        </section>
      )}
      {refused.length > 0 && <Notice tone="danger"><ul>{refused.map(([k, v]) => <li key={k}>#{k}: {v.join("; ")}</li>)}</ul></Notice>}
      {confirm.isSuccess && confirm.data.confirmed.length > 0 && <Notice>{confirm.data.confirmed.length === 1 ? "Sugestão confirmada." : `${confirm.data.confirmed.length} sugestões confirmadas.`}</Notice>}
      {dismiss.isSuccess && <Notice>Sugestões recusadas.</Notice>}
      <ErrorNote error={q.error || confirm.error || dismiss.error} />

      <div className="agenda-toolbar">
        <div className="flex items-center gap-1">
          <button type="button" className="icon-action" disabled={week <= 0} onClick={() => setWeek(week - 1)} aria-label="Semana anterior"><ChevronLeft size={17} aria-hidden /></button>
          <button type="button" className="icon-action" disabled={week >= 2} onClick={() => setWeek(week + 1)} aria-label="Próxima semana"><ChevronRight size={17} aria-hidden /></button>
          <h2 className="ml-2 text-sm font-semibold">
            {week === 0 ? "Esta semana" : week === 1 ? "Próxima semana" : "Daqui a duas semanas"}
            <span className="ml-2 font-normal text-ink-muted">{days[0].slice(8, 10)}/{days[0].slice(5, 7)} a {days[6].slice(8, 10)}/{days[6].slice(5, 7)}</span>
          </h2>
        </div>
        <fieldset className="agenda-legend">
          <legend className="sr-only">Mostrar</legend>
          {(["todas", "sugestao", "confirmado", "automatico"] as const).map((k) => (
            <label key={k} className="agenda-filter" data-active={filter === k || undefined}>
              <input type="radio" name="agenda-filter" className="sr-only" checked={filter === k} onChange={() => setFilter(k)} />
              {k === "todas" ? "Todas" : <DecisionChip kind={k} label={decisionLabel[k]} />}
            </label>
          ))}
        </fieldset>
      </div>

      {q.isLoading && <Skeleton label="Carregando a agenda" />}
      {q.data && items.length === 0 && <Empty title="Nenhuma publicação nas próximas semanas">
        Agende um corte pela Central de cortes, na aba Publicação, ou ative uma regra em Contas e regras.</Empty>}

      {q.data && items.length > 0 && (
        <ol className="week-grid" aria-label="Publicações da semana, de segunda a domingo">
          {days.map((day) => {
            const list = byDay(day);
            return (
              <li key={day} className="week-day" data-today={day === today || undefined} data-empty={!list.length || undefined}>
                <h3 className="week-day-head">
                  <span className="capitalize">{weekday.format(new Date(`${day}T12:00:00Z`)).replace(".", "")}</span>
                  <strong>{day.slice(8, 10)}</strong>
                  {day === today && <span className="sr-only">(hoje)</span>}
                  <span className="sr-only">{longDay.format(new Date(`${day}T12:00:00Z`))}</span>
                </h3>
                {list.length === 0 ? <p className="week-none">Nada agendado</p> : (
                  <ul className="space-y-2">
                    {list.map((it) => {
                      const kind = decisionOf(it);
                      const status = STATUS[it.status];
                      return (
                        <li key={it.id} className="agenda-event" data-kind={kind ?? "other"}>
                          {it.status === "proposed" && (
                            <input type="checkbox" className="agenda-check" checked={sel.includes(it.id)} aria-label={`Selecionar sugestão: ${it.title}`}
                              onChange={() => setSel(sel.includes(it.id) ? sel.filter((x) => x !== it.id) : [...sel, it.id])} />
                          )}
                          <button type="button" className="agenda-event-body" onClick={() => show(it)}>
                            <span className="font-mono text-xs font-semibold">{it.when ? it.when.slice(11, 16) : "agora"}</span>
                            <span className="agenda-event-title">{it.title || `Corte #${it.clip_id}`}</span>
                            <span className="text-xs text-ink-muted">{platformName[it.platform] ?? it.platform}</span>
                            <span className="mt-1 flex flex-wrap gap-1">
                              {kind && <DecisionChip kind={kind} label={short[kind]} />}
                              {status && <Status tone={status[1]}>{status[0]}</Status>}
                            </span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </li>
            );
          })}
        </ol>
      )}

      <dialog ref={dialog} className="confirmation-modal agenda-dialog" aria-labelledby="agenda-item-title" onClose={() => setOpen(null)}>
        {open && <EventDetail key={open.id} item={open} timezone={q.data?.timezone ?? TZ} onClose={() => dialog.current?.close()} onChanged={done} />}
      </dialog>
    </div>
  );
}

function EventDetail({ item, timezone, onClose, onChanged }: { item: Item; timezone: string; onClose: () => void; onChanged: () => void }) {
  const kind = decisionOf(item);
  const move = useMutation({
    mutationFn: (at: string) => api.patch(`/api/publish/publications/${item.id}`, { scheduled_at: at }),
    onSuccess: () => { onChanged(); onClose(); },
  });
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3">
        {item.thumb && item.clip_id && <img src={`/api/clips/${item.clip_id}/thumbnail?v=${item.version}`} alt="" className="h-20 w-12 shrink-0 rounded object-cover" />}
        <div className="min-w-0">
          <h2 id="agenda-item-title" className="display text-lg">{item.title || `Corte #${item.clip_id}`}</h2>
          <p className="mt-1 text-sm text-ink-muted">
            {platformName[item.platform] ?? item.platform} · {item.account} · {PRIVACY[item.privacy] ?? item.privacy}
            {item.score != null && ` · nota ${Math.round(item.score)}`}
          </p>
        </div>
      </div>
      {kind && <div className="space-y-1"><DecisionChip kind={kind} label={decisionLabel[kind]} /><p className="text-sm text-ink-muted">{decisionHint[kind]}</p></div>}
      {item.last_error && <Notice tone="danger">{item.last_error}</Notice>}
      {["proposed", "scheduled"].includes(item.status) && (
        <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => {
          event.preventDefault();
          const value = String(new FormData(event.currentTarget).get("when"));
          if (!value || value === item.when?.slice(0, 16)) return;
          const zone = new Intl.DateTimeFormat("en", { timeZone: timezone, timeZoneName: "longOffset" });
          const offset = zone.formatToParts(new Date(value + "Z")).find((part) => part.type === "timeZoneName")?.value.slice(3) || "+00:00";
          move.mutate(value + offset);
        }}>
          <label className="min-w-0 flex-1"><span className="label">Horário (Brasília)</span>
            <input type="datetime-local" name="when" required className="field" defaultValue={item.when?.slice(0, 16) ?? ""} /></label>
          <button className="btn-quiet" disabled={move.isPending}>Mudar horário</button>
        </form>
      )}
      <ErrorNote error={move.error} />
      <div className="flex flex-wrap justify-end gap-2">
        {item.clip_id && <Link className="btn-quiet" to={`/cortes/${item.clip_id}?status=`} onClick={onClose}><ExternalLink size={15} aria-hidden /> Abrir o corte</Link>}
        <button type="button" className="btn-take" onClick={onClose}>Fechar</button>
      </div>
    </div>
  );
}

/** Compact pause indicator; pausing asks for confirmation in a dialog instead of a field on every screen. */
export function PauseControl() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["publish-control"], queryFn: () => api.get<Control>("/api/publish/control"), refetchInterval: 20_000 });
  const [reason, setReason] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const set = useMutation({
    mutationFn: (paused: boolean) => api.post<Control>("/api/publish/control", { paused, reason }),
    onSuccess: () => {
      dialog.current?.close();
      setReason("");
      qc.invalidateQueries({ queryKey: ["publish-control"] });
      qc.invalidateQueries({ queryKey: ["calendar"] });
    },
  });
  const c = q.data;
  if (!c) return q.isLoading ? null : <ErrorNote error={q.error} />;
  if (c.paused) return (
    <div role="alert" className="pause-banner">
      <Pause size={16} aria-hidden />
      <p className="min-w-0 flex-1"><strong>Publicações pausadas</strong> por {c.by}{c.reason ? `: ${c.reason}` : ""}. Nada novo é enviado; o que já estava sendo enviado termina.</p>
      <button className="btn-take" disabled={set.isPending} onClick={() => set.mutate(false)}><Play size={15} aria-hidden /> Retomar envios</button>
      <ErrorNote error={set.error} />
    </div>
  );
  return (
    <div className="pause-status">
      <span className="flex items-center gap-2"><span className="size-2 rounded-full bg-ok" aria-hidden /> Envios ativos</span>
      <button type="button" className="btn-quiet" onClick={() => dialog.current?.showModal()}><Pause size={14} aria-hidden /> Pausar tudo</button>
      <dialog ref={dialog} className="confirmation-modal" aria-labelledby="pause-title">
        <h2 id="pause-title" className="display text-xl">Pausar todas as publicações?</h2>
        <p className="mt-2 text-sm text-ink-muted">Nada novo é enviado até você retomar. O que já está sendo enviado termina. A agenda não muda.</p>
        <label className="mt-4 block"><span className="label">Motivo (opcional)</span>
          <input className="field" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Ex.: revisar legendas antes do envio" /></label>
        <ErrorNote error={set.error} />
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button type="button" className="btn-quiet" onClick={() => dialog.current?.close()}>Voltar</button>
          <button type="button" className="btn-danger" disabled={set.isPending} onClick={() => set.mutate(true)}>Pausar agora</button>
        </div>
      </dialog>
    </div>
  );
}
