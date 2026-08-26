import { useEffect, useRef } from "react";
import { riseIn, withMotion } from "../lib/motion.js";

/**
 * Editorial section. The claim is that the index is hand-reconstructable, so
 * the section shows the actual arithmetic for four of the twenty cells rather
 * than describing it.
 */
const TERMS = [
  { cell: "BOM-DEL @ T+15", base: 5945, price: 5623, rel: 0.9457, w: 0.087, c: 8.2323 },
  { cell: "BOM-DEL @ T+7", base: 7811, price: 6741, rel: 0.863, w: 0.0933, c: 8.0487 },
  { cell: "BLR-BOM @ T+15", base: 4946, price: 6402, rel: 1.2943, w: 0.053, c: 6.8605 },
  { cell: "BLR-DEL @ T+7", base: 9720, price: 9880, rel: 1.0165, w: 0.0675, c: 6.8598 },
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
    <section ref={root} className="border-b border-hair py-20 lg:py-28">
      <div className="shell">
        <h2
          data-r
          className="will-rise max-w-[20ch] text-[2rem] font-semibold leading-[1.1] tracking-tight text-ink sm:text-[2.6rem]"
        >
          Any published number can be rebuilt by hand.
        </h2>
        <p data-r className="will-rise mt-5 max-w-[58ch] leading-relaxed">
          The index is a weighted sum of price relatives, so every value
          decomposes into twenty terms that add back to it exactly. The API
          returns that decomposition on request.
        </p>

        <div
          data-r
          className="will-rise mt-10 overflow-x-auto rounded-xl border border-hair bg-surface"
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
                  plus 16 further cells
                </td>
                <td className="px-6 py-3 text-right text-muted">63.36</td>
              </tr>
              <tr className="border-t border-hair bg-raised/50">
                <td className="px-6 py-3.5 font-semibold text-ink" colSpan={5}>
                  Published index, 25 August 2026
                </td>
                <td className="px-6 py-3.5 text-right font-mono text-base font-semibold text-accent">
                  97.1822
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <p data-r className="will-rise kbd mt-4">
          python3 -m pushpak.cli explain --date 2026-08-25
        </p>
      </div>
    </section>
  );
}
