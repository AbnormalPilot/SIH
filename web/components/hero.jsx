"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import IndexChart from "@/components/index-chart.jsx";
import { withMotion } from "@/lib/motion.js";

/**
 * Asymmetric split hero, on the overview page only. Left carries the claim,
 * right carries the evidence. Four text elements exactly: eyebrow, headline,
 * subtext, CTA pair.
 */
export default function Hero({ series, live }) {
  const root = useRef(null);

  useEffect(() => {
    if (!root.current) return;
    const mm = withMotion(root.current, (g) => {
      // Entry timeline establishes hierarchy: label, claim, explanation, action.
      g.timeline({ defaults: { ease: "power3.out", duration: 0.85 } })
        .from("[data-h='eyebrow']", { opacity: 0, y: 14 })
        .from("[data-h='title'] > span", { opacity: 0, y: 30, stagger: 0.09 }, "-=0.55")
        .from("[data-h='sub']", { opacity: 0, y: 18 }, "-=0.6")
        .from("[data-h='cta'] > *", { opacity: 0, y: 14, stagger: 0.08 }, "-=0.55")
        .from("[data-h='chart']", { opacity: 0, y: 36, duration: 1 }, "-=0.9");
    });
    return () => mm.revert();
  }, []);

  return (
    <section ref={root} className="relative overflow-hidden border-b border-hair">
      {/* Single soft light source behind the chart. No mesh gradient. */}
      <div
        aria-hidden
        className="pointer-events-none absolute right-[-10%] top-[-30%] h-[560px] w-[560px] rounded-full bg-accent/10 blur-[130px]"
      />

      <div className="shell grid items-center gap-12 pb-16 pt-12 lg:grid-cols-[1.05fr_1fr] lg:gap-16 lg:pb-24 lg:pt-20">
        <div>
          <p
            data-h="eyebrow"
            className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-accent"
          >
            MoSPI · Data Informatics and Innovation Division
          </p>

          <h1
            data-h="title"
            className="mt-5 text-[2.6rem] font-semibold leading-[1.06] tracking-tightest text-ink sm:text-[3.2rem] lg:text-[3.6rem]"
          >
            <span className="block">A daily price index</span>
            <span className="block">for Indian airfares.</span>
          </h1>

          <p
            data-h="sub"
            className="mt-6 max-w-[54ch] text-[17px] leading-relaxed text-ink2"
          >
            CPI samples airfares monthly. Pushpak builds a route-level index every day,
            weighted by real DGCA passenger traffic.
          </p>

          <div data-h="cta" className="mt-9 flex flex-wrap items-center gap-3">
            {/* The dashboard is served by FastAPI, not by Next, so it is a real
                navigation rather than a client-side route transition. */}
            <a className="btn-primary" href="/dashboard">
              Open dashboard
              <ArrowRight size={16} strokeWidth={2} />
            </a>
            <Link className="btn-ghost" href="/method">
              Read the method
            </Link>
          </div>
        </div>

        <div data-h="chart">
          <IndexChart series={series} live={live} />
        </div>
      </div>
    </section>
  );
}
