import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import { api } from "./api";
import OutlineView from "./OutlineView";
import PointDetail from "./PointDetail";
import TupleTable from "./TupleTable";

export default function App() {
  const [info, setInfo] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [gid, setGid] = useState(0);
  const [location, setLocation] = useState({});
  const [instance, setInstance] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(0);
  const fileRef = useRef(null);

  const axes = info?.axes || [];

  const loadInfo = useCallback(async () => {
    try {
      const data = await api.info();
      setInfo(data);
      setLocation((prev) => {
        const next = { ...prev };
        for (const a of data.axes) {
          if (!(a.tag in next)) next[a.tag] = a.default;
        }
        return next;
      });
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  useEffect(() => { loadInfo(); }, [loadInfo]);

  // Fetch an instance whenever glyph or an axis value changes.
  useEffect(() => {
    if (!info) return;
    let cancelled = false;
    setError(null);
    api.instance(gid, location)
      .then((d) => { if (!cancelled) { setInstance(d); setSelected(0); } })
      .catch((e) => { if (!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [info, gid, JSON.stringify(location)]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedPoint = useMemo(
    () => instance?.points.find((p) => p.index === selected) || null,
    [instance, selected]
  );

  const currentGlyph = info?.glyphs?.[gid];

  const onFile = async (file) => {
    if (!file) return;
    setLoadError(null);
    try {
      const { info: newInfo } = await api.load(file);
      setInfo(newInfo);
      setGid(0);
      setSelected(0);
      const next = {};
      for (const a of newInfo.axes) next[a.tag] = a.default;
      setLocation(next);
    } catch (e) {
      setLoadError(e.message);
      await loadInfo();
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <h1>gvar 轮廓位移溯源器</h1>
        <span className="sub">
          glyf/gvar · fvar 归一化 · avar 分段 · tuple 权重 · 逐轮廓 IUP
        </span>
        <div className="file-picker" style={{ marginLeft: "auto" }}>
          <input
            ref={fileRef}
            type="file"
            accept=".ttf"
            style={{ display: "none" }}
            onChange={(e) => onFile(e.target.files?.[0])}
          />
          <button onClick={() => fileRef.current?.click()}>加载 TTF…</button>
        </div>
      </header>

      <div className="banner">
        所有轮廓与坐标均由后端直接从 glyf/gvar 计算，<b>不是</b>系统字体的
        渲染截图；虚线基准轮廓与实线当前实例在同一坐标系叠画。
      </div>

      {loadError && <div className="error">{loadError}</div>}

      <div className="controls">
        {axes.map((a) => {
          const value = location[a.tag] ?? a.default;
          const norm = instance?.normalizedLocation?.[a.tag];
          const hasAvar = !!info?.avarSegments?.[a.tag]?.length;
          return (
            <div className={`axis ${hasAvar ? "avar" : ""}`} key={a.tag}>
              <div className="row1">
                <span className="tag">{a.tag}</span>
                <span>
                  <input
                    type="number"
                    value={value}
                    min={a.min} max={a.max}
                    step={(a.max - a.min) / 200}
                    style={{ width: 90, background: "var(--panel-2)",
                      color: "var(--text)", border: "1px solid var(--border)",
                      borderRadius: 4, padding: "2px 4px" }}
                    onChange={(e) => setLocation((l) => ({
                      ...l, [a.tag]: parseFloat(e.target.value) || 0,
                    }))}
                  />
                </span>
              </div>
              <input
                type="range"
                min={a.min} max={a.max}
                step={(a.max - a.min) / 1000}
                value={value}
                onChange={(e) => setLocation((l) => ({
                  ...l, [a.tag]: parseFloat(e.target.value),
                }))}
              />
              <div className="row1">
                <span className="muted">{a.min} / {a.default} / {a.max}</span>
                <span className="norm">
                  归一化 {norm !== undefined ? num(norm) : "-"}
                  {hasAvar ? "（经 avar 映射 + F2Dot14 量化）" : "（F2Dot14）"}
                </span>
              </div>
            </div>
          );
        })}
        <button
          className="reset"
          onClick={() => {
            const next = {};
            for (const a of axes) next[a.tag] = a.default;
            setLocation(next);
          }}
        >
          复位到默认
        </button>
      </div>

      {info && (
        <div className="glyph-picker" style={{ marginTop: -4 }}>
          {info.glyphs.map((g, i) => (
            <button key={g.gid} className={i === gid ? "active" : ""}
              onClick={() => { setGid(i); setSelected(0); }}>
              {g.name}
              <span className="muted"> · {g.pointCount}pt</span>
            </button>
          ))}
        </div>
      )}

      {error && <div className="error">计算失败：{error}</div>}

      {instance && (
        <div className="main">
          <div className="vpane">
            <OutlineView
              data={instance}
              selected={selected}
              onSelect={setSelected}
            />
            <div className="card advance">
              <h2>advance（来自水平 phantom 点，不混入轮廓 IUP）</h2>
              默认 <b>{instance.advance.default}</b>
              浮点 <b>{instance.advance.float.toFixed(2)}</b>
              最终 <b>{instance.advance.final}</b>
              <span className="muted">Δ = {instance.advance.delta}</span>
            </div>
          </div>
          <div className="vpane">
            <TupleTable tuples={instance.tuples}
              normalized={instance.normalizedLocation} />
            <PointDetail point={selectedPoint} glyphName={instance.glyph} />
            {currentGlyph && (
              <div className="card">
                <h2>字形信息 · {currentGlyph.name}</h2>
                <div className="muted" style={{ fontSize: 12 }}>
                  轮廓数 {currentGlyph.numberOfContours}，
                  轮廓点 {currentGlyph.pointCount}（+4 phantom），
                  gvar tuple {instance.tuples.length} 个，
                  每轮廓独立 IUP。
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function num(v) {
  return Number(v).toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}
