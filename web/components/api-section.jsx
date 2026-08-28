"use client";

import { useEffect, useRef } from "react";
import { ArrowUpRight } from "lucide-react";
import { riseIn, withMotion } from "@/lib/motion.js";

const ENDPOINTS = [
  ["GET", "/api/v1/index", "The series, daily weekly or monthly"],
  ["GET", "/api/v1/index/latest", "Today's value with its change"],
  ["GET", "/api/v1/index/explain", "Term-by-term reconstruction"],
  ["GET", "/api/v1/index/chained", "Chained variants and the drift they cost"],
  ["GET", "/api/v1/routes/{id}/series", "Per-route sub-index"],
  ["GET", "/api/v1/validation", "Back-test verdict against the benchmark"],
  ["GET", "/api/v1/provenance", "What the data is and is not"],
];

const SAMPLE = `{
  "period": "2026-08-26",
  "index_value": 97.444,
  "change_pct": 2.8861,
  "base_index_value": 100.0,
  "base_period": ["2026-07-28", "2026-07-29", "2026-07-30"],
  "n_quotes": 6000,
  "imputed_share": 0.0,
  "soldout_share": 0.0808,
  "quality_flag": "amber"
}`;

export default function ApiSection() {
  const root = useRef(null);
  useEffect(() => {
    if (!root.current) return;
    const el = root.current;
    const mm = withMotion(el, () => {
      riseIn(el.querySelectorAll("[data-a]"), { stagger: 0.08 });
    });
    return () => mm.revert();
  }, []);

  return (
    <section ref={root} className="py-14 lg:py-20">
      <div className="shell grid gap-12 lg:grid-cols-2 lg:gap-16">
        <div>
          <p data-a className="will-rise max-w-[50ch] leading-relaxed">
            An index value never arrives on its own. NSO or RBI can poll the
            endpoint and gate on the imputed and sold-out shares and the quality
            flag without reading a methodology note first.
          </p>

          <ul data-a className="will-rise mt-7 divide-y divide-hair/70 border-y border-hair">
            {ENDPOINTS.map(([m, path, desc]) => (
              <li key={path} className="flex flex-wrap items-baseline gap-x-3 py-3">
                <span className="font-mono text-[11px] font-semibold text-accent">
                  {m}
                </span>
                <span className="font-mono text-[13px] text-ink">{path}</span>
                <span className="w-full text-[12.5px] text-muted sm:w-auto sm:flex-1 sm:text-right">
                  {desc}
                </span>
              </li>
            ))}
          </ul>

          <a data-a className="btn-ghost will-rise mt-8" href="/docs">
            Interactive OpenAPI docs
            <ArrowUpRight size={15} strokeWidth={2} />
          </a>
        </div>

        <div data-a className="will-rise">
          <div className="panel overflow-hidden">
            <div className="flex items-center gap-2 border-b border-hair px-5 py-3">
              <span className="font-mono text-[12px] text-muted">
                GET /api/v1/index/latest
              </span>
            </div>
            <pre className="overflow-x-auto px-5 py-5 font-mono text-[12.5px] leading-relaxed text-ink2">
              <code>{SAMPLE}</code>
            </pre>
          </div>
          <p className="mt-4 text-[13px] leading-relaxed text-muted">
            <span className="font-mono text-warn">quality_flag</span> is computed
            from the imputed and sold-out shares against the thresholds in{" "}
            <span className="font-mono text-ink">config.QUALITY_THRESHOLDS</span>,
            so a consumer can gate on one field without reading a methodology
            note. The full set of provenance fields is on{" "}
            <span className="font-mono text-ink">/api/v1/provenance</span>.
          </p>
        </div>
      </div>
    </section>
  );
}
