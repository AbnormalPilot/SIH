"use client";

import { useEffect, useRef } from "react";
import { riseIn, withMotion } from "@/lib/motion.js";

/**
 * Editorial section. The claim is that the index is hand-reconstructable, so
 * the section shows the actual arithmetic for four of the fifty cells rather
 * than describing it. These four are the highest-contributing terms of a real
 * run, copied from `pushpak.cli explain`, not illustrative figures.
 */
const TERMS = [
  { cell: "BOM-DEL @ T+7", base: 7650, price: 6790, rel: 0.8875, w: 0.0541, c: 4.7979 },
  { cell: "BOM-DEL @ T+15", base: 6257, price: 5210, rel: 0.8327, w: 0.0501, c: 4.1684 },
  { cell: "BOM-DEL @ T+1", base: 9082, price: 10207, rel: 1.1239, w: 0.036, c: 4.0505 },
  { cell: "BOM-DEL @ T+30", base: 5925, price: 5868, rel: 0.9904, w: 0.04, c: 3.966 },
];

export default function Formula() {
  const root = useRef(null);
  useEffect(() => {
    if (!root.current) return;
    const el = root.current;
    const mm = withMotion(el, () => {
      riseIn(el.querySelectorAll("[data-r]"), { stagger: 0.09 });
    });
    return () => mm.revert();
  }, []);

  return (
    <section ref={root} className="py-14 lg:py-20">
      <div className="shell">
        <div
          data-r
          className="will-rise overflow-x-auto rounded-xl border border-hair bg-surface"
        >
          <div className="border-b border-hair px-6 py-4">
            <p className="font-mono text-[13px] leading-relaxed text-ink2">
              Pushpak(t) = 100 × Σ<sub>c</sub> [ w(c) × P(c,t) ÷ P(c,0) ]
            </p>
          </div>
          <table className="w-full min-w-[38rem] text-[13.5px]">
            <thead>
              <tr className="text-left text-[11px] uppercase tracking-wider text-muted">
                <th className="px-6 py-3 font-medium">Cell</th>
                <th className="px-3 py-3 text-right font-medium">Base</th>
                <th className="px-3 py-3 text-right font-medium">Price</th>
                <th className="px-3 py-3 text-right font-medium">Relative</th>
                <th className="px-3 py-3 text-right font-medium">Weight</th>
                <th className="px-6 py-3 text-right font-medium">Contribution</th>
              </tr>
            </thead>
            <tbody className="font-mono tabular-nums">
              {TERMS.map((t) => (
                <tr key={t.cell} className="border-t border-hair/60">
                  <td className="px-6 py-3 text-ink">{t.cell}</td>
                  <td className="px-3 py-3 text-right">₹{t.base.toLocaleString("en-IN")}</td>
                  <td className="px-3 py-3 text-right">₹{t.price.toLocaleString("en-IN")}</td>
                  <td className="px-3 py-3 text-right">{t.rel.toFixed(4)}</td>
                  <td className="px-3 py-3 text-right">{t.w.toFixed(4)}</td>
                  <td className="px-6 py-3 text-right font-semibold text-ink">
                    {t.c.toFixed(4)}
                  </td>
                </tr>
              ))}
              <tr className="border-t border-hair">
                <td className="px-6 py-3 text-muted" colSpan={5}>
                  plus 46 further cells
                </td>
                <td className="px-6 py-3 text-right text-muted">80.4612</td>
              </tr>
              <tr className="border-t border-hair bg-raised/50">
                <td className="px-6 py-3.5 font-semibold text-ink" colSpan={5}>
                  Published index, 26 August 2026
                </td>
                <td className="px-6 py-3.5 text-right font-mono text-base font-semibold text-accent">
                  97.4440
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p data-r className="will-rise kbd mt-4">
          python3 -m pushpak.cli explain --date 2026-08-26
        </p>
      </div>
    </section>
  );
}
