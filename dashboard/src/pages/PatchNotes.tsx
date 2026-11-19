import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Empty, ErrorNote, PageHeader, Skeleton, Status } from "../components/ui";

type Note = { version: string; date: string; title: string; items: string[] };

export default function PatchNotes() {
  const q = useQuery({ queryKey: ["patch-notes"], queryFn: () => api.get<{ current: string; notes: Note[] }>("/api/patch-notes") });
  return (
    <div className="max-w-3xl">
      <PageHeader title="Histórico de versões" description="Notas de lançamento do AI Stream Clipper por HTS.">
        {q.data && <Status tone="accent">Versão em uso: v{q.data.current}</Status>}
      </PageHeader>
      <ErrorNote error={q.error} />
      {q.isLoading && <Skeleton label="Carregando versões" />}
      {q.data?.notes.length === 0 && <Empty title="Nenhuma versão registrada" />}
      <ol className="border-l border-suite-outline">
        {(q.data?.notes ?? []).map((n) => (
          <li key={n.version} className="relative mb-5 ml-4 pl-1 before:absolute before:-left-[1.32rem] before:top-4 before:size-2 before:rounded-full before:bg-take">
            <article className="panel panel-body">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <span className="font-mono text-sm font-semibold text-take">v{n.version}</span>
              <time dateTime={n.date} className="text-xs text-ink-muted">{new Date(n.date + "T12:00:00").toLocaleDateString("pt-BR")}</time>
            </div>
            <h2 className="display mb-3 text-lg">{n.title}</h2>
            <ul className="list-inside list-disc space-y-0.5 text-sm text-ink-muted">
              {n.items.map((it, i) => <li key={i}>{it}</li>)}
            </ul>
            </article>
          </li>
        ))}
      </ol>
    </div>
  );
}
