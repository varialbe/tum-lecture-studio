import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { FileText, Search } from "lucide-react";
import { api, type TranscriptSummary } from "../lib/api";
import { useStore } from "../lib/store";
import { Empty, Input, PageHeader, Skeleton } from "../components/ui";
import { ago, clock, duration } from "../lib/format";

interface Hit {
  transcript_id: string;
  idx: number;
  start: number;
  snippet: string;
  title: string;
  course_name: string;
}

/** Only <mark> survives from FTS snippets; everything else is escaped. */
export function safeSnippet(s: string): string {
  const esc = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/&lt;mark&gt;/g, "<mark>").replace(/&lt;\/mark&gt;/g, "</mark>");
}

export default function Transcripts() {
  const { libraryVersion } = useStore();
  const [items, setItems] = useState<TranscriptSummary[] | null>(null);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<Hit[] | null>(null);

  useEffect(() => {
    api.get<{ transcripts: TranscriptSummary[] }>("/api/transcripts").then((r) => setItems(r.transcripts));
  }, [libraryVersion]);
  useEffect(() => {
    if (q.trim().length < 2) {
      setHits(null);
      return;
    }
    const t = setTimeout(() => {
      api.get<{ hits: Hit[] }>(`/api/transcripts/search?q=${encodeURIComponent(q)}`).then((r) => setHits(r.hits));
    }, 200);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div>
      <PageHeader title="Transcripts" sub="Search across every lecture you've transcribed." />
      <div className="relative mb-5 max-w-xl">
        <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted" aria-hidden />
        <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search words, e.g. Bijektion" className="w-full pl-9" aria-label="Search transcripts" />
      </div>

      {hits ? (
        hits.length ? (
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line">
            {hits.map((h) => (
              <li key={`${h.transcript_id}-${h.idx}`}>
                <Link to={`/transcripts/${h.transcript_id}?t=${h.start}`} className="flex gap-4 px-4 py-2.5 hover:bg-board-2">
                  <span className="tnum w-16 shrink-0 text-accent">{clock(h.start)}</span>
                  <span className="min-w-0 flex-1">
                    <span className="block" dangerouslySetInnerHTML={{ __html: safeSnippet(h.snippet) }} />
                    <span className="block truncate text-[13px] text-muted">
                      {h.title} · {h.course_name}
                    </span>
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <Empty title={`No transcript mentions "${q}"`}>Search matches word beginnings, so "Bijek" finds "Bijektion".</Empty>
        )
      ) : !items ? (
        <Skeleton className="h-40" />
      ) : items.length === 0 ? (
        <Empty icon={<FileText className="size-6" />} title="No transcripts yet">
          Download a lecture, then choose Transcribe on its row.
        </Empty>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[640px] text-left">
            <thead className="bg-board-2 text-[12px] text-muted">
              <tr>
                <th className="px-4 py-2 font-medium">Lecture</th>
                <th className="px-3 py-2 text-right font-medium">Audio</th>
                <th className="px-3 py-2 font-medium">Model</th>
                <th className="px-3 py-2 text-right font-medium">Processing</th>
                <th className="px-3 py-2 font-medium">Created</th>
              </tr>
            </thead>
            <tbody>
              {items.map((t) => (
                <tr key={t.id} className="border-t border-line hover:bg-board-2">
                  <td className="px-4 py-2.5">
                    <Link to={`/transcripts/${t.id}`} className="font-medium hover:text-accent">
                      {t.title}
                    </Link>
                    <div className="text-[13px] text-muted">{t.course_name}</div>
                  </td>
                  <td className="tnum px-3 py-2.5 text-right">{duration(t.audio_seconds)}</td>
                  <td className="px-3 py-2.5 text-chalk-2">{t.model}</td>
                  <td className="tnum px-3 py-2.5 text-right text-chalk-2">{duration(t.processing_seconds)}</td>
                  <td className="px-3 py-2.5 text-muted">{ago(t.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
