import type { Claim, Cluster, Resolution, RunDetail, Source } from "../api/client";
import { citationLabel } from "./AnswerText";
import { StanceBadge, formatPct } from "./Badges";

function SourceRow({ s }: { s: Source }) {
  return (
    <tr>
      <td>
        <code className="id">{s.source_id}</code>
      </td>
      <td>
        <a href={s.url} target="_blank" rel="noreferrer">
          {s.title}
        </a>
        <div className="muted small">
          {s.authors.slice(0, 3).join(", ")}
          {s.authors.length > 3 ? " et al." : ""}
        </div>
      </td>
      <td>{s.kind}</td>
      <td className="num">{s.published_year ?? "—"}</td>
      <td className="num">{s.authority.toFixed(2)}</td>
      <td className="num">{s.sub_question_index !== null ? s.sub_question_index + 1 : "—"}</td>
    </tr>
  );
}

function ClaimList({ claims, sources }: { claims: Claim[]; sources: Map<string, Source> }) {
  if (claims.length === 0) return <p className="muted">none</p>;
  return (
    <ul className="claims">
      {claims.map((c) => {
        const s = sources.get(c.source_id);
        return (
          <li key={c.claim_id}>
            <StanceBadge stance={c.stance} />
            <span className="claim-text">{c.text}</span>
            <span className="muted small">
              {" "}
              {s ? (
                <a href={s.url} target="_blank" rel="noreferrer">
                  {citationLabel(s)}
                </a>
              ) : (
                c.source_id
              )}{" "}
              · {c.evidence_type} · {formatPct(c.confidence)}
              {c.sub_question_index !== null ? ` · q${c.sub_question_index + 1}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ResolutionCard({ r, sources }: { r: Resolution; sources: Map<string, Source> }) {
  const total = r.support.score + r.refute.score || 1;
  const pct = Math.round((r.support.score / total) * 100);
  return (
    <div className="card sub">
      <div className="row-between">
        <strong>
          q{r.question_index + 1}: {r.question}
        </strong>
        <span>
          <StanceBadge stance={r.winner} /> <span className="muted small">{r.method.split("_").join(" ")}</span>
        </span>
      </div>
      <div className="score-bar" title={`support ${r.support.score.toFixed(2)} vs refute ${r.refute.score.toFixed(2)}`}>
        <div className="score-support" style={{ width: `${pct}%` }} />
        <div className="score-refute" style={{ width: `${100 - pct}%` }} />
      </div>
      <div className="muted small">
        for: {r.support.claims} claims from {r.support.sources} sources
        {r.support.newest_year ? ` (newest ${r.support.newest_year})` : ""} · against: {r.refute.claims} claims from{" "}
        {r.refute.sources} sources{r.refute.newest_year ? ` (newest ${r.refute.newest_year})` : ""} · margin{" "}
        {r.margin >= 0 ? "+" : ""}
        {r.margin.toFixed(2)} · confidence {formatPct(r.confidence)}
      </div>
      <p>{r.reasoning}</p>
      {r.minority_report && (
        <div className="minority">
          <strong>Minority view ({r.minority_report.stance}):</strong> {r.minority_report.summary}
          <div className="muted small">
            sources:{" "}
            {r.minority_report.source_ids.map((id) => {
              const s = sources.get(id);
              return s ? (
                <a key={id} href={s.url} target="_blank" rel="noreferrer" className="cite">
                  {citationLabel(s)}
                </a>
              ) : (
                <code key={id} className="id">
                  {id}
                </code>
              );
            })}
          </div>
        </div>
      )}
      {r.superseded.length > 0 && (
        <div className="muted small">
          supersedes: {r.superseded.map(([newer, older]) => `${newer} → ${older}`).join(", ")}
        </div>
      )}
    </div>
  );
}

function ClusterRow({ c }: { c: Cluster }) {
  const contested = c.supports > 0 && c.refutes > 0;
  return (
    <tr className={contested ? "row-warn" : ""}>
      <td>{c.label}</td>
      <td className="num">{c.claim_ids.length}</td>
      <td className="num">{c.source_ids.length}</td>
      <td className="num">{c.supports}</td>
      <td className="num">{c.refutes}</td>
      <td className="num">{c.neutral}</td>
      <td>{contested ? <span className="badge warn">contested</span> : ""}</td>
    </tr>
  );
}

export default function EvidencePanel({ run }: { run: RunDetail }) {
  const sources = new Map(run.sources.map((s) => [s.source_id, s]));
  const by = (stance: string) => run.claims.filter((c) => c.stance === stance);
  return (
    <div className="stack">
      <section className="card">
        <h2>Sub-questions</h2>
        <ol className="subq">
          {run.sub_questions.map((q, i) => (
            <li key={i}>{q}</li>
          ))}
        </ol>
      </section>

      <section className="card">
        <h2>
          Sources <span className="muted small">({run.sources.length})</span>
        </h2>
        {run.sources.length === 0 ? (
          <p className="muted">No sources were retrieved.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Id</th>
                <th>Title</th>
                <th>Kind</th>
                <th className="num">Year</th>
                <th className="num">Authority</th>
                <th className="num">Sub-q</th>
              </tr>
            </thead>
            <tbody>
              {run.sources.map((s) => (
                <SourceRow key={s.source_id} s={s} />
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>
          Claims <span className="muted small">({run.claims.length})</span>
        </h2>
        <h3>Supporting</h3>
        <ClaimList claims={by("supports")} sources={sources} />
        <h3>Refuting</h3>
        <ClaimList claims={by("refutes")} sources={sources} />
        <h3>Context (neutral)</h3>
        <ClaimList claims={by("neutral")} sources={sources} />
      </section>

      <section className="card">
        <h2>
          Conflicts <span className="muted small">({run.resolutions.length})</span>
        </h2>
        {run.resolutions.length === 0 ? (
          <p className="muted">No sub-question had evidence on both sides from different sources.</p>
        ) : (
          run.resolutions.map((r) => <ResolutionCard key={r.question_index} r={r} sources={sources} />)
        )}
      </section>

      <section className="card">
        <h2>
          Topic clusters <span className="muted small">({run.clusters.length})</span>
        </h2>
        {run.clusters.length === 0 ? (
          <p className="muted">none</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Topic</th>
                <th className="num">Claims</th>
                <th className="num">Sources</th>
                <th className="num">For</th>
                <th className="num">Against</th>
                <th className="num">Neutral</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {run.clusters.map((c) => (
                <ClusterRow key={c.cluster_id} c={c} />
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
