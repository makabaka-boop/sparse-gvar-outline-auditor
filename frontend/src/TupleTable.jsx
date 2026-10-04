import React from "react";

// Lists every gvar tuple of the selected glyph, its region tent and the
// scalar ("weight") it carries at the current normalized location.
export default function TupleTable({ tuples, normalized }) {
  return (
    <div className="card">
      <h2>
        gvar tuple 权重
        <span className="muted" style={{ textTransform: "none", marginLeft: 8 }}>
          归一化坐标 {Object.entries(normalized)
            .map(([k, v]) => `${k}=${v.toFixed(4)}`).join(", ")}
        </span>
      </h2>
      {tuples.length === 0 ? (
        <p className="muted">该字形没有 tuple。</p>
      ) : (
        <table className="tuples">
          <thead>
            <tr>
              <th>#</th><th>峰值</th><th>支撑区 (start, peak, end)</th>
              <th>权重</th>
            </tr>
          </thead>
          <tbody>
            {tuples.map((t) => (
              <tr key={t.tuple}>
                <td>{t.tuple}</td>
                <td>
                  {Object.entries(t.peak)
                    .map(([k, v]) => `${k}=${v}`).join(", ") || "（无轴）"}
                </td>
                <td>
                  {Object.entries(t.support).map(([axis, [s, p, e]]) => (
                    <div key={axis}>
                      {axis}: {s}, {p}, {e}
                    </div>
                  ))}
                </td>
                <td className={t.weight === 0 ? "zero" : "active"}>
                  {t.weight.toFixed(4).replace(/0+$/, "").replace(/\.$/, "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
