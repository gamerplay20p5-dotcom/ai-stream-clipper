import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorNote, PageHeader } from "../components/ui";

type Note = { version: string; date: string; title: string; items: string[] };

export default function PatchNotes() {
  const q = useQuery({ queryKey: ["patch-notes"], queryFn: () => api.get<{ current: string; notes: Note[] }>("/api/patch-notes") });
  return (
    <div className="max-w-3xl">
      <PageHeader title="Patch notes" />
      <ErrorNote error={q.error} />
      {q.data && <p className="-mt-3 mb-6 text-sm text-ink-muted">Versão em uso: <b className="text-ink">{q.data.current}</b></p>}
      <ol className="space-y-4">
        {(q.data?.notes ?? []).map((n) => (
          <li key={n.version} className={`rounded-lg border p-4 ${n.version === q.data?.current ? "border-take/60" : "border-suite-line"}`}>
            <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="font-semibold"><span className="text-take">v{n.version}</span> · {n.title}</h2>
              <span className="text-xs text-ink-muted">{new Date(n.date + "T12:00:00").toLocaleDateString("pt-BR")}</span>
            </div>
            <ul className="list-inside list-disc space-y-0.5 text-sm text-ink-muted">
              {n.items.map((it, i) => <li key={i}>{it}</li>)}
            </ul>
          </li>
        ))}
      </ol>
    </div>
  );
}
