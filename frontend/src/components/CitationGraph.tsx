import { useEffect, useMemo, useState } from "react";
import { getRunGraph, type GraphEdge, type GraphNode, type GraphResponse } from "../api/client";

// Dependency-free layered layout: sub-questions on the left, claims in the middle,
// sources on the right. Stance edges run claim -> question, provenance is drawn from
// each claim's source attribute, and claim -> claim edges (extends, supersedes) arc
// inside the claims column.

const COL = { question: 150, claim: 470, source: 820 };
const ROW = 44;
const WIDTH = 1000;

const EDGE_COLOR: Record<GraphEdge["edge_type"], string> = {
  supports: "#2f9e6f",
  refutes: "#c9403d",
  extends: "#8a94a6",
  supersedes: "#d68a1d",
};

type Positioned = GraphNode & { x: number; y: number; label: string };

function truncate(text: string, n: number) {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

function layout(graph: GraphResponse): Positioned[] {
  const questions = graph.nodes.filter((n) => n.kind === "question");
  const sources = graph.nodes.filter((n) => n.kind === "source");
  const claims = graph.nodes.filter((n) => n.kind === "claim");
  const stanceRank = (s: unknown) => (s === "supports" ? 0 : s === "refutes" ? 1 : 2);
  claims.sort(
    (a, b) =>
      Number(a.question_index ?? 0) - Number(b.question_index ?? 0) ||
      stanceRank(a.stance) - stanceRank(b.stance) ||
      String(a.id).localeCompare(String(b.id)),
  );
  const height = Math.max(questions.length, claims.length, sources.length, 1) * ROW + 40;
  const place = (nodes: GraphNode[], x: number, labelOf: (n: GraphNode) => string) => {
    const gap = nodes.length > 0 ? height / (nodes.length + 1) : height;
    return nodes.map((n, i) => ({ ...n, x, y: gap * (i + 1), label: labelOf(n) }));
  };
  return [
    ...place(questions, COL.question, (n) => `q${Number(n.index) + 1}: ${truncate(String(n.text ?? ""), 34)}`),
    ...place(claims, COL.claim, (n) => truncate(String(n.text ?? n.id), 52)),
    ...place(sources, COL.source, (n) => truncate(String(n.label ?? n.title ?? n.id), 28)),
  ];
}

export default function CitationGraph({ runId }: { runId: string }) {
  const [graph, setGraph] = useState<GraphResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getRunGraph(runId)
      .then((g) => {
        if (!cancelled) setGraph(g);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [runId]);

  const nodes = useMemo(() => (graph ? layout(graph) : []), [graph]);
  const byId = useMemo(() => new Map(nodes.map((n) => [n.id, n])), [nodes]);

  if (error) return <div className="card error">Could not load the graph: {error}</div>;
  if (!graph) return <p className="muted">loading graph…</p>;
  if (graph.nodes.length === 0) return <p className="muted">This run produced no graph.</p>;

  const height = Math.max(...nodes.map((n) => n.y)) + 40;
  const related = (id: string) =>
    selected === null ||
    id === selected ||
    graph.edges.some(
      (e) => (e.source_node === selected && e.target_node === id) || (e.target_node === selected && e.source_node === id),
    ) ||
    (byId.get(id)?.kind === "source" && byId.get(selected)?.source_id === id) ||
    (byId.get(selected)?.kind === "source" && byId.get(id)?.source_id === selected);

  return (
    <div className="graph-wrap">
      <div className="legend">
        {(Object.keys(EDGE_COLOR) as GraphEdge["edge_type"][]).map((t) => (
          <span key={t}>
            <span className="legend-line" style={{ background: EDGE_COLOR[t] }} /> {t}
          </span>
        ))}
        <span>
          <span className="legend-line provenance" /> source of claim
        </span>
        <span className="muted small">click a node to focus; click again to clear</span>
      </div>
      <svg viewBox={`0 0 ${WIDTH} ${height}`} className="graph" role="img" aria-label="citation graph">
        {/* provenance: source -> claim */}
        {nodes
          .filter((n) => n.kind === "claim")
          .map((c) => {
            const s = byId.get(String(c.source_id));
            if (!s) return null;
            const dim = !(related(c.id) && related(s.id));
            return (
              <line
                key={`prov-${c.id}`}
                x1={s.x - 8}
                y1={s.y}
                x2={c.x + 8}
                y2={c.y}
                className={`edge provenance ${dim ? "dim" : ""}`}
              />
            );
          })}
        {/* argumentative edges */}
        {graph.edges.map((e, i) => {
          const a = byId.get(e.source_node);
          const b = byId.get(e.target_node);
          if (!a || !b) return null;
          const dim = !(related(a.id) && related(b.id)) || (selected !== null && a.id !== selected && b.id !== selected);
          const color = EDGE_COLOR[e.edge_type];
          const title = `${e.edge_type} (${e.weight.toFixed(2)}): ${e.explanation}`;
          if (a.kind === "claim" && b.kind === "claim") {
            const bulge = 70 + Math.abs(a.y - b.y) / 6;
            const d = `M ${a.x - 8} ${a.y} Q ${a.x - bulge} ${(a.y + b.y) / 2} ${b.x - 8} ${b.y}`;
            return (
              <path
                key={i}
                d={d}
                stroke={color}
                strokeDasharray={e.edge_type === "extends" ? "4 4" : undefined}
                className={`edge ${dim ? "dim" : ""}`}
                fill="none"
              >
                <title>{title}</title>
              </path>
            );
          }
          return (
            <line
              key={i}
              x1={a.x - 8}
              y1={a.y}
              x2={b.x + 60}
              y2={b.y}
              stroke={color}
              strokeWidth={1 + e.weight * 2}
              className={`edge ${dim ? "dim" : ""}`}
            >
              <title>{title}</title>
            </line>
          );
        })}
        {/* nodes */}
        {nodes.map((n) => {
          const dim = !related(n.id);
          const onClick = () => setSelected(selected === n.id ? null : n.id);
          const full = String(n.text ?? n.title ?? n.id);
          if (n.kind === "question") {
            return (
              <g key={n.id} className={`node ${dim ? "dim" : ""}`} onClick={onClick}>
                <title>{full}</title>
                <rect x={n.x - 130} y={n.y - 16} width={190} height={32} rx={8} className="node-question" />
                <text x={n.x - 35} y={n.y + 4} textAnchor="middle" className="node-label">
                  {n.label}
                </text>
              </g>
            );
          }
          if (n.kind === "source") {
            return (
              <g key={n.id} className={`node ${dim ? "dim" : ""}`} onClick={onClick}>
                <title>{`${full} (${n.id})`}</title>
                <rect x={n.x - 8} y={n.y - 14} width={170} height={28} rx={6} className={`node-source ${String(n.source_kind)}`} />
                <text x={n.x + 77} y={n.y + 4} textAnchor="middle" className="node-label">
                  {n.label}
                </text>
              </g>
            );
          }
          const stance = String(n.stance);
          return (
            <g key={n.id} className={`node ${dim ? "dim" : ""}`} onClick={onClick}>
              <title>{`${full}\n${n.id} · ${stance} · confidence ${Number(n.confidence ?? 0).toFixed(2)}`}</title>
              <circle cx={n.x} cy={n.y} r={7} className={`node-claim ${stance}`} />
              <text x={n.x + 12} y={n.y + 4} className="node-label small">
                {n.label}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="muted small">
        {graph.summary.claims} claims · {graph.summary.supports_edges} supports · {graph.summary.refutes_edges} refutes ·{" "}
        {graph.summary.extends_edges} extends · {graph.summary.supersedes_edges} supersedes · {graph.summary.conflicts}{" "}
        conflicts
      </div>
    </div>
  );
}
