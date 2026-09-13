import type { Source } from "../api/client";

// Renders the answer's Markdown subset (headings, bullets, bold, paragraphs) without a
// Markdown library, and turns every verified `[source_id]` citation into a link chip.

const INLINE = /(\[[a-z_]+:[^\]\s]+\]|\*\*[^*]+\*\*)/g;

function Inline({ text, sources }: { text: string; sources: Map<string, Source> }) {
  const parts = text.split(INLINE).filter((p) => p.length > 0);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("[") && part.endsWith("]")) {
          const id = part.slice(1, -1);
          const source = sources.get(id);
          const label = source ? citationLabel(source) : id;
          return source ? (
            <a
              key={i}
              className="cite"
              href={source.url}
              target="_blank"
              rel="noreferrer"
              title={`${source.title} (${id})`}
            >
              {label}
            </a>
          ) : (
            <span key={i} className="cite unknown" title="not among this run's sources">
              {id}
            </span>
          );
        }
        if (part.startsWith("**") && part.endsWith("**")) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

export function citationLabel(source: Source) {
  const first = source.authors[0]?.split(" ").pop();
  if (first) return source.published_year ? `${first} ${source.published_year}` : first;
  const title = source.title.length <= 28 ? source.title : `${source.title.slice(0, 27).trimEnd()}…`;
  return `${title} (${source.kind})`;
}

export default function AnswerText({ text, sources }: { text: string; sources: Source[] }) {
  const byId = new Map(sources.map((s) => [s.source_id, s]));
  const blocks = text.replace(/\r/g, "").split(/\n{2,}/);
  return (
    <div className="answer">
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim().length > 0);
        if (lines.length === 0) return null;
        if (lines.every((l) => /^\s*([-*]|\d+\.)\s+/.test(l))) {
          return (
            <ul key={i}>
              {lines.map((l, j) => (
                <li key={j}>
                  <Inline text={l.replace(/^\s*([-*]|\d+\.)\s+/, "")} sources={byId} />
                </li>
              ))}
            </ul>
          );
        }
        const heading = /^(#{1,3})\s+(.*)$/.exec(lines[0]);
        if (heading && lines.length === 1) {
          return (
            <h3 key={i}>
              <Inline text={heading[2]} sources={byId} />
            </h3>
          );
        }
        return (
          <p key={i}>
            <Inline text={lines.join(" ")} sources={byId} />
          </p>
        );
      })}
    </div>
  );
}
