// Dependency graph (D-015): layers by longest path, plain SVG, no layout library.
// Layers run top to bottom: notebooks are deep chains and the side panel is narrow (D-025).
import type { CellJson } from "./protocol";
import { look } from "./status";
import { StateIcon } from "./icons";
import { scrollToCell } from "./Refs";

const W = 132, H = 34, GX = 14, GY = 26;

export function layers(ids: number[], edges: [number, number][]): Map<number, number> {
  // Longest path over the acyclic part (Kahn). Cycle members and whatever hangs off
  // them never become ready; they get one layer after their resolved parents, no iteration.
  const inGraph = new Set(ids);
  const es = edges.filter(([p, c]) => inGraph.has(p) && inGraph.has(c) && p !== c);
  const indeg = new Map(ids.map((id) => [id, 0]));
  for (const [, c] of es) indeg.set(c, indeg.get(c)! + 1);
  const depth = new Map<number, number>();
  const ready = ids.filter((id) => indeg.get(id) === 0);
  for (const id of ready) depth.set(id, 0);
  while (ready.length) {
    const p = ready.shift()!;
    for (const [q, c] of es) {
      if (q !== p) continue;
      depth.set(c, Math.max(depth.get(c) ?? 0, depth.get(p)! + 1));
      indeg.set(c, indeg.get(c)! - 1);
      if (indeg.get(c) === 0) ready.push(c);
    }
  }
  for (const id of ids) {
    if (indeg.get(id)! > 0) {  // on or behind a cycle
      const placed = es.filter(([q, c]) => c === id && indeg.get(q) === 0).map(([q]) => depth.get(q)! + 1);
      depth.set(id, Math.max(0, ...placed));
    }
  }
  return depth;
}

export function Graph({ cells, order, edges }: { cells: Record<number, CellJson>; order: number[]; edges: [number, number][] }) {
  const ids = order.filter((id) => cells[id] && (cells[id].kind === "python" || cells[id].kind === "mojo"));
  const depth = layers(ids, edges);
  const rows = new Map<number, number>();
  const pos = new Map<number, { x: number; y: number }>();
  for (const id of ids) {
    const d = depth.get(id)!;
    const r = rows.get(d) ?? 0;
    rows.set(d, r + 1);
    pos.set(id, { x: 8 + r * (W + GX), y: 8 + d * (H + GY) });
  }
  const width = 16 + Math.max(1, ...rows.values()) * (W + GX);
  const height = 16 + (Math.max(0, ...depth.values()) + 1) * (H + GY);
  return (
    <svg className="graph" width={width} height={height} role="img" aria-label="grafo de dependências">
      {edges.filter(([p, c]) => pos.has(p) && pos.has(c)).map(([p, c]) => {
        const a = pos.get(p)!, b = pos.get(c)!;
        return <line key={`${p}-${c}`} x1={a.x + W / 2} y1={a.y + H} x2={b.x + W / 2} y2={b.y} className="edge" markerEnd="url(#arrow)" />;
      })}
      <defs>
        <marker id="arrow" viewBox="0 0 8 8" refX="8" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" className="arrow" />
        </marker>
      </defs>
      {ids.map((id) => {
        const c = cells[id], p = pos.get(id)!, l = look(c, cells, edges);
        const chip = l.chips.find((x) => !x.outlined);
        return (
          <g key={id} transform={`translate(${p.x},${p.y})`} className={`node tone-${l.tone ?? "none"} rail-${l.rail}`}
             onClick={() => scrollToCell(id)} role="button" tabIndex={0}
             onKeyDown={(e) => { if (e.key === "Enter") scrollToCell(id); }}>
            <title>{`[${id}] ${c.kind}: ${chip ? chip.text : c.status}`}</title>
            <rect width={W} height={H} rx={6} />
            <text x={8} y={H / 2 + 4}>[{id}] {c.kind}</text>
            {chip && <foreignObject x={W - 24} y={H / 2 - 8} width={16} height={16}><StateIcon name={chip.icon} /></foreignObject>}
            {c.upstream_modified.length > 0 && <circle cx={W - 4} cy={4} r={4} className="dot-modified" />}
          </g>
        );
      })}
    </svg>
  );
}
