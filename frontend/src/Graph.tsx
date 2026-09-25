// Dependency graph (D-015): layers by longest path, plain SVG, no layout library.
import type { CellJson } from "./protocol";
import { look } from "./status";
import { StateIcon } from "./icons";
import { scrollToCell } from "./Refs";

const W = 132, H = 34, GX = 40, GY = 14;

export function layers(ids: number[], edges: [number, number][]): Map<number, number> {
  const depth = new Map(ids.map((id) => [id, 0]));
  for (let i = 0; i < ids.length; i++) {  // longest path; bounded passes also terminate on cycles
    let moved = false;
    for (const [p, c] of edges) {
      if (depth.has(p) && depth.has(c) && depth.get(c)! < depth.get(p)! + 1 && depth.get(p)! < ids.length) {
        depth.set(c, depth.get(p)! + 1);
        moved = true;
      }
    }
    if (!moved) break;
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
    pos.set(id, { x: 8 + d * (W + GX), y: 8 + r * (H + GY) });
  }
  const width = 16 + (Math.max(0, ...depth.values()) + 1) * (W + GX);
  const height = 16 + Math.max(1, ...rows.values()) * (H + GY);
  return (
    <svg className="graph" width={width} height={height} role="img" aria-label="grafo de dependências">
      {edges.filter(([p, c]) => pos.has(p) && pos.has(c)).map(([p, c]) => {
        const a = pos.get(p)!, b = pos.get(c)!;
        return <line key={`${p}-${c}`} x1={a.x + W} y1={a.y + H / 2} x2={b.x} y2={b.y + H / 2} className="edge" markerEnd="url(#arrow)" />;
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
