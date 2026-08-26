import { useEffect, useRef } from "react";
import { ArrowUpRight } from "lucide-react";
import { riseIn, withMotion } from "../lib/motion.js";

const ENDPOINTS = [
  ["GET", "/api/v1/index", "The series, daily weekly or monthly"],
  ["GET", "/api/v1/index/latest", "Today's value with its change"],
  ["GET", "/api/v1/index/explain", "Term-by-term reconstruction"],
  ["GET", "/api/v1/routes/{id}/series", "Per-route sub-index"],
  ["GET", "/api/v1/validation", "Back-test verdict against the benchmark"],
  ["GET", "/api/v1/provenance", "What the data is and is not"],
];

const SAMPLE = `{
  "period": "2026-08-25",
  "index_value": 97.1822,
  "change_pct": -0.5,
  "base_index_value": 100.0,
  "n_quotes": 2400,
  "synthetic_share": 1.0,
  "imputed_share": 0.0,
  "soldout_share": 0.1008,
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
    <section id="api" ref={root} className="border-b border-hair py-20 lg:py-28">
      <div className="shell grid gap-12 lg:grid-cols-2 lg:gap-16">
        <div>
          <p className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-accent">
            For downstream consumers
          </p>
          <h2
            data-a
            className="will-rise mt-5 text-[2rem] font-semibold leading-[1.1] tracking-tight text-ink sm:text-[2.5rem]"
          >
            Provenance ships with every number.
          </h2>
          <p data-a className="will-rise mt-5 max-w-[50ch] leading-relaxed">
            An index value never arrives on its own. NSO or RBI can poll the
            endpoint and gate on the synthetic, imputed and sold-out shares
            without reading a methodology note first.
          </p>

          <ul data-a className="will-rise mt-8 divide-y divide-hair/70 border-y border-hair">
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
            <span className="font-mono text-warn">synthetic_share: 1.0</span> and{" "}
            <span className="font-mono text-warn">quality_flag: amber</span> are
            the honest reading of this prototype. Wholly synthetic input can
            never be reported green.
          </p>
        </div>
      </div>
    </section>
  );
}
