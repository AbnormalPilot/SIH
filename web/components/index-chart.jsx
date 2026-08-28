"use client";

import { useEffect, useMemo, useRef } from "react";
import { gsap, withMotion } from "@/lib/motion.js";
import { num } from "@/lib/format.js";

/**
 * The hero visual is the real index series pulled from the running API, drawn
 * with the same blue as the operations dashboard. It is a live component
 * preview, not a mocked-up screenshot of one.
 */
export default function IndexChart({ series, live }) {
  const wrap = useRef(null);
  const path = useRef(null);

  const geo = useMemo(() => {
    if (!series?.length) return null;
    const W = 640;
    const H = 260;
    const P = { t: 18, r: 8, b: 22, l: 8 };
    const vals = series.map((d) => d.index_value);
    let lo = Math.min(...vals, 100);
    let hi = Math.max(...vals, 100);
    const pad = (hi - lo) * 0.2 || 2;
    lo -= pad;
    hi += pad;
    const X = (i) => P.l + (i / (series.length - 1)) * (W - P.l - P.r);
    const Y = (v) => P.t + (H - P.t - P.b) * (1 - (v - lo) / (hi - lo));
    const line = series
      .map((d, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(d.index_value).toFixed(1)}`)
      .join(" ");
    const area = `${line} L${X(series.length - 1).toFixed(1)},${H - P.b} L${X(0).toFixed(1)},${H - P.b} Z`;
    return { W, H, P, line, area, baseY: Y(100), lo, hi, X, Y };
  }, [series]);

  useEffect(() => {
    if (!wrap.current || !geo) return;
    const mm = withMotion(wrap.current, (g) => {
      const len = path.current?.getTotalLength?.() ?? 1000;
      // The line draws itself once, in reading order: the index is a series
      // that accumulates, and the draw makes that legible at a glance.
      g.fromTo(
        path.current,
        { strokeDasharray: len, strokeDashoffset: len },
        { strokeDashoffset: 0, duration: 1.8, ease: "power2.inOut", delay: 0.35 },
      );
      g.fromTo(
        wrap.current.querySelector("[data-area]"),
        { opacity: 0 },
        { opacity: 1, duration: 1.2, delay: 1.1 },
      );
    });
    return () => mm.revert();
  }, [geo]);

  if (!geo) return null;
  const last = series[series.length - 1];

  return (
    <figure ref={wrap} className="panel relative overflow-hidden p-5 sm:p-6">
      <figcaption className="mb-4 flex items-start justify-between gap-4">
        <div>
          <p className="text-[13px] font-semibold text-ink">Pushpak Index, daily</p>
          <p className="kbd mt-0.5">
            base 100 · {series[0].period} to {last.period}
          </p>
        </div>
        <span
          className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
            live
              ? "border-accent/40 text-accent"
              : "border-hair text-muted"
          }`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${live ? "bg-accent" : "bg-muted"}`}
          />
          {live ? "live from API" : "offline sample"}
        </span>
      </figcaption>

      <svg
        viewBox={`0 0 ${geo.W} ${geo.H}`}
        className="w-full"
        role="img"
        aria-label={`Pushpak Index from ${series[0].period} to ${last.period}, latest value ${num(last.index_value)}`}
      >
        <defs>
          <linearGradient id="pushpakFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.32" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>

        <line
          x1={geo.P.l}
          x2={geo.W - geo.P.r}
          y1={geo.baseY}
          y2={geo.baseY}
          stroke="var(--hair)"
          strokeWidth="1"
          strokeDasharray="4 5"
        />
        <text x={geo.P.l} y={geo.baseY - 7} fill="var(--muted)" fontSize="10.5">
          base 100
        </text>

        <path data-area d={geo.area} fill="url(#pushpakFill)" />
        <path
          ref={path}
          d={geo.line}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        <circle
          cx={geo.X(series.length - 1)}
          cy={geo.Y(last.index_value)}
          r="4.5"
          fill="var(--accent)"
          stroke="var(--surface)"
          strokeWidth="2"
        />
      </svg>

      <div className="mt-4 flex flex-wrap items-baseline gap-x-6 gap-y-2 border-t border-hair pt-4">
        <div>
          <span className="font-mono text-2xl font-semibold text-ink">
            {num(last.index_value)}
          </span>
          <span className="kbd ml-2">latest</span>
        </div>
        <p className="kbd">
          {series.length} days · quality {last.quality_flag}
        </p>
      </div>
    </figure>
  );
}
