import React, { useMemo } from "react";

const ORIGIN_COLORS = {
  explicit: "var(--explicit)",
  inferred: "var(--inferred)",
  none: "var(--none)",
  phantom: "var(--phantom)",
};

function mid(a, b) {
  return { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
}

// Convert one closed TrueType contour (on/off-curve quadratic points) into
// an SVG path string.  Mirrors how a rasterizer reconstructs implied
// on-curve points at the midpoint of two consecutive off-curves.
function contourToPath(points) {
  const n = points.length;
  if (n === 0) return "";
  if (n === 1) return `M ${points[0].x} ${points[0].y}`;
  const on = points.map((p) => p.on);

  if (!on.some(Boolean)) {
    // Entire contour is off-curve: every midpoint is an implied on-curve.
    let d = `M ${mid(points[n - 1], points[0]).x} ${mid(
      points[n - 1],
      points[0]
    ).y}`;
    for (let i = 0; i < n; i++) {
      const m = mid(points[i], points[(i + 1) % n]);
      d += ` Q ${points[i].x} ${points[i].y} ${m.x} ${m.y}`;
    }
    return d + " Z";
  }

  const startIdx = on.findIndex(Boolean);
  const ordered = [];
  for (let k = 0; k < n; k++) ordered.push(points[(startIdx + k) % n]);

  let d = `M ${ordered[0].x} ${ordered[0].y}`;
  let i = 1;
  while (i < n) {
    if (ordered[i].on) {
      d += ` L ${ordered[i].x} ${ordered[i].y}`;
      i += 1;
      continue;
    }
    const offs = [];
    while (i < n && !ordered[i].on) {
      offs.push(ordered[i]);
      i += 1;
    }
    const end = i < n ? ordered[i] : ordered[0];
    if (offs.length === 1) {
      d += ` Q ${offs[0].x} ${offs[0].y} ${end.x} ${end.y}`;
    } else {
      let m = mid(offs[0], offs[1]);
      d += ` Q ${offs[0].x} ${offs[0].y} ${m.x} ${m.y}`;
      for (let k = 1; k < offs.length - 1; k++) {
        m = mid(offs[k], offs[k + 1]);
        d += ` Q ${offs[k].x} ${offs[k].y} ${m.x} ${m.y}`;
      }
      d += ` Q ${offs[offs.length - 1].x} ${offs[offs.length - 1].y} ${end.x} ${end.y}`;
    }
  }
  return d + " Z";
}

export default function OutlineView({ data, selected, onSelect }) {
  const outlineCount = data.outlinePointCount;
  const outlinePoints = data.points.slice(0, outlineCount);

  const frame = useMemo(() => {
    const all = outlinePoints.flatMap((p) => [
      { x: p.default[0], y: p.default[1] },
      { x: p.final[0], y: p.final[1] },
    ]);
    if (all.length === 0) return null;
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const p of all) {
      minX = Math.min(minX, p.x);
      maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y);
      maxY = Math.max(maxY, p.y);
    }
    const pad = 60;
    minX -= pad; maxX += pad; minY -= pad; maxY += pad;
    // Advance phantom range informs the horizontal extent.
    const left = data.points[outlineCount];
    const right = data.points[outlineCount + 1];
    if (left && right) {
      minX = Math.min(minX, left.final[0] - 10);
      maxX = Math.max(maxX, right.final[0] + 10);
    }
    const width = maxX - minX;
    const height = maxY - minY;
    // SVG Y axis is flipped; map (x,y) -> (x - minX, maxY - y).
    const tx = (x) => x - minX;
    const ty = (y) => maxY - y;
    return { width, height, tx, ty };
  }, [data, outlineCount, outlinePoints]);

  if (!frame) {
    return (
      <div className="card">
        <h2>轮廓叠画</h2>
        <p className="muted">该字形没有轮廓点（空字形）。</p>
      </div>
    );
  }

  const ptArr = (which) =>
    outlinePoints.map((p) => ({
      x: which === "default" ? p.default[0] : p.final[0],
      y: which === "default" ? p.default[1] : p.final[1],
      on: p.onCurve,
    }));

  const basePts = ptArr("default");
  const curPts = ptArr("final");

  const pathsFor = (pts) =>
    data.contours.map((contour, ci) => ({
      ci,
      d: contourToPath(contour.map((idx) => pts[idx])),
    }));

  const left = data.points[outlineCount];
  const right = data.points[outlineCount + 1];

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column" }}>
      <h2>轮廓叠画 · 虚线=基准默认实例，实线=当前实例</h2>
      <div className="legend">
        <span className="dot"><i style={{ background: ORIGIN_COLORS.explicit }} />显式 delta</span>
        <span className="dot"><i style={{ background: ORIGIN_COLORS.inferred }} />IUP 推断</span>
        <span className="dot"><i style={{ background: ORIGIN_COLORS.none }} />无贡献</span>
        <span className="dot"><i style={{ background: ORIGIN_COLORS.phantom }} />phantom/advance</span>
        <span className="dot"><i style={{
          width: 14, height: 2, background: "var(--base)" }} />基准轮廓</span>
      </div>

      <svg
        className="outline"
        viewBox={`0 0 ${frame.width} ${frame.height}`}
        preserveAspectRatio="xMidYMid meet"
      >
        {/* base outlines */}
        {pathsFor(basePts).map(({ ci, d }) => (
          <path key={`b${ci}`} d={d} fill="none" stroke="var(--base)"
            strokeWidth={1.5} strokeDasharray="5 4" />
        ))}
        {/* current outlines */}
        {pathsFor(curPts).map(({ ci, d }) => (
          <path key={`c${ci}`} d={d} fill="rgba(93,176,255,0.06)"
            stroke="var(--inferred)" strokeWidth={2} />
        ))}

        {/* current points */}
        {outlinePoints.map((p) => {
          const [x, y] = p.final;
          const cx = frame.tx(x);
          const cy = frame.ty(y);
          const color = ORIGIN_COLORS[p.origin] || "var(--none)";
          const isSel = selected === p.index;
          return (
            <g key={p.index} className="point" onClick={() => onSelect(p.index)}>
              <circle cx={cx} cy={cy} r={isSel ? 7 : 5}
                fill={p.onCurve ? color : "none"}
                stroke={color}
                className={`point ${p.onCurve ? "on" : "off"} ${isSel ? "selected" : ""}`}
              />
              <text x={cx + 8} y={cy - 6} className="point-label">
                {p.index}
              </text>
            </g>
          );
        })}

        {/* advance: horizontal bracket through the horizontal phantom points */}
        {left && right && (
          <g>
            <line
              x1={frame.tx(left.final[0])} y1={frame.ty(0)}
              x2={frame.tx(right.final[0])} y2={frame.ty(0)}
              stroke="var(--phantom)" strokeWidth={1.5}
              markerStart=""
            />
            <circle cx={frame.tx(left.final[0])} cy={frame.ty(0)} r={3}
              fill="var(--phantom)" />
            <circle cx={frame.tx(right.final[0])} cy={frame.ty(0)} r={3}
              fill="var(--phantom)" />
            <text
              x={(frame.tx(left.final[0]) + frame.tx(right.final[0])) / 2}
              y={frame.ty(0) + 18}
              textAnchor="middle" fill="var(--phantom)" fontSize={12}
            >
              advance {data.advance.final}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
