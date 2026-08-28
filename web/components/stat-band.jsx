"use client";

import { useEffect, useRef } from "react";
import { withMotion } from "@/lib/motion.js";

/** Counts a number up when it scrolls into view. Feedback that this is real data. */
function Counter({ value, decimals = 0, suffix = "", prefix = "" }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!ref.current) return;
    const node = ref.current;
    node.textContent = `${prefix}${value.toFixed(decimals)}${suffix}`;
    const mm = withMotion(node, (g) => {
      const obj = { v: 0 };
      g.to(obj, {
        v: value,
        duration: 1.6,
        ease: "power2.out",
        scrollTrigger: { trigger: node, start: "top 88%", once: true },
        onUpdate: () => {
          node.textContent = `${prefix}${obj.v.toFixed(decimals)}${suffix}`;
        },
      });
    });
    return () => mm.revert();
  }, [value, decimals, suffix, prefix]);
  return <span ref={ref} className="font-mono tabular-nums" />;
}

export default function StatBand({ coverage, routes, latest }) {
  const stats = [
    { v: routes?.length ?? 10, d: 0, label: "city pairs in the fixed basket", sub: "top routes by DGCA passengers" },
    { v: coverage * 100, d: 2, suffix: "%", label: "of all Indian domestic passengers", sub: "carried by those ten routes" },
    { v: 5, d: 0, label: "advance-purchase windows", sub: "T+1, T+7, T+15, T+30 and T+45" },
    { v: latest?.n_quotes ?? 6000, d: 0, label: "fare quotes per collection day", sub: "across carriers and sources" },
  ];

  return (
    <section className="border-b border-hair bg-surface/40">
      <div className="shell grid gap-px sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((s, i) => (
          <div
            key={s.label}
            className={`py-9 lg:py-11 ${i > 0 ? "lg:pl-8" : ""} ${
              i < stats.length - 1 ? "lg:border-r lg:border-hair" : ""
            }`}
          >
            <p className="text-[2rem] font-semibold leading-none text-ink">
              <Counter value={s.v} decimals={s.d} suffix={s.suffix ?? ""} />
            </p>
            <p className="mt-3 text-[13.5px] font-medium leading-snug text-ink2">
              {s.label}
            </p>
            <p className="mt-1 text-[12.5px] text-muted">{s.sub}</p>
          </div>
        ))}
      </div>
    </section>
  );
}
