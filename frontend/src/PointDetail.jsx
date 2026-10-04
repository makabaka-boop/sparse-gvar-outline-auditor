import React from "react";

const ORIGIN_LABEL = {
  explicit: "显式给出",
  inferred: "IUP 推断",
  none: "无位移 (零触及)",
  phantom: "phantom 点",
};

const RULE_LABEL = {
  interpolate: "线性插值",
  clamp_before: "区间外 → 取前一参照点 delta（钳制，不外推）",
  clamp_after: "区间外 → 取后一参照点 delta（钳制，不外推）",
  equal_coordinate_constant: "参照点原坐标相同且 delta 相同 → 取该 delta",
  equal_coordinate_zero: "参照点原坐标相同但 delta 不同 → 强制为 0",
};

function RuleLine({ axis, rule }) {
  if (!rule) return null;
  const [r1, r2] = rule.references;
  return (
    <div className="rule">
      <code>{axis}</code>：{RULE_LABEL[rule.mode] || rule.mode}
      {rule.zeroFilled ? <span className="zero">（零填充）</span> : null}
      {" 参照点 "}
      <code>#{r1}↔#{r2}</code>
    </div>
  );
}

export default function PointDetail({ point, glyphName }) {
  if (!point) {
    return (
      <div className="card point-detail">
        <h2>点位溯源</h2>
        <p className="muted">在左侧图上点击任一点，查看其最终坐标是如何由各
        tuple 的权重与显式 / 推断规则产生的。</p>
      </div>
    );
  }

  const activeContribs = point.tupleContributions.filter((c) => c.weight !== 0);
  const inferred = point.inference || [];

  return (
    <div className="card point-detail">
      <h2>
        点位 #{point.index} · {glyphName}
        {point.phantom ? ` · phantom (${point.phantomRole})` : ""}
      </h2>

      <div>
        <span className={`origin-badge origin-${point.origin}`}>
          {ORIGIN_LABEL[point.origin] || point.origin}
        </span>
      </div>

      <div className="kv" style={{ marginTop: 8 }}>
        <span className="k">默认坐标</span>
        <span><code className="num">{fmt(point.default)}</code></span>
        <span className="k">累加位移</span>
        <span><code className="num">{fmt(point.floatDelta)}</code>（浮点）</span>
        <span className="k">舍入前坐标</span>
        <span><code className="num">{fmt(point.floatCoord)}</code></span>
        <span className="k">最终坐标</span>
        <span><b><code className="num">{fmt(point.final)}</code></b>
          （otRound：floor(v+0.5)）</span>
      </div>

      <h3 style={{ fontSize: 12, color: "var(--muted)" }}>各 tuple 贡献</h3>
      {point.tupleContributions.length === 0 && (
        <div className="muted">该字形没有任何 gvar tuple。</div>
      )}
      {point.tupleContributions.map((c) => (
        <div key={c.tuple} className="contrib">
          <div className="head">
            <span>
              tuple #{c.tuple} ·{" "}
              <span className={`origin-badge origin-${
                c.source === "explicit" ? "explicit"
                  : c.source === "inferred" ? "inferred"
                  : "none"}`}>
                {c.source === "explicit" ? "显式"
                  : c.source === "inferred" ? "推断"
                  : c.source === "none" ? "零触及" : c.source}
              </span>
            </span>
            <span className="muted">
              权重 <code className="num">{num(c.weight)}</code>
            </span>
          </div>
          {c.rawDelta && (
            <div className="rule">
              原始 delta <code className="num">{fmt(c.rawDelta)}</code> →
              加权 <code className="num">{fmt(c.weightedDelta)}</code>
            </div>
          )}
          {c.weight === 0 && (
            <div className="rule">权重为 0（位置在该 tuple 支撑区外），不贡献。</div>
          )}
          {inferred
            .filter((s) => s.tuple === c.tuple)
            .map((s, k) => (
              <div key={k}>
                <RuleLine axis="x" rule={s.xRule} />
                <RuleLine axis="y" rule={s.yRule} />
              </div>
            ))}
        </div>
      ))}

      {point.phantom && (
        <p className="muted" style={{ marginTop: 8 }}>
          phantom 点不参与任何轮廓插值；水平左 / 右 phantom 的间距即
          advance，单独报告。
        </p>
      )}
    </div>
  );
}

function fmt(v) {
  return `(${v[0]}, ${v[1]})`;
}

function num(v) {
  return Number(v).toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}
