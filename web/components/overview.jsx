"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";
import Hero from "@/components/hero.jsx";
import StatBand from "@/components/stat-band.jsx";
import CtaPanel from "@/components/cta-panel.jsx";
import { SECTIONS } from "@/lib/nav.js";
import { useData } from "@/lib/data.jsx";

/** Hero-shaped skeleton, so the first paint does not shift when data lands. */
function HeroSkeleton() {
  return (
    <div className="border-b border-hair">
      <div className="shell grid gap-12 pb-16 pt-12 lg:grid-cols-[1.05fr_1fr] lg:pb-24 lg:pt-20">
        <div className="space-y-5">
          <div className="h-3 w-64 animate-pulse rounded bg-raised" />
          <div className="h-14 w-full max-w-lg animate-pulse rounded bg-raised" />
          <div className="h-14 w-4/5 max-w-md animate-pulse rounded bg-raised" />
          <div className="h-4 w-full max-w-sm animate-pulse rounded bg-raised" />
        </div>
        <div className="h-[22rem] animate-pulse rounded-xl bg-raised" />
      </div>
    </div>
  );
}

/** The site map as content: what each of the other five pages holds. */
function Contents() {
  return (
    <section className="border-b border-hair py-14 lg:py-20">
      <div className="shell">
        <h2 className="text-[1.65rem] font-semibold tracking-tight text-ink">
          What is documented here
        </h2>
        <p className="mt-3 max-w-[58ch] leading-relaxed">
          The method, the compliance record and the API each get their own page,
          so a reader can go straight to the one they came for.
        </p>

        <div className="mt-9 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {SECTIONS.flatMap((s) => s.items).map((item) => {
            const Icon = item.icon;
            return (
              <Link
                key={item.to}
                href={item.to}
                className="panel group flex flex-col p-6 transition-colors hover:border-accent/40"
              >
                <Icon size={18} strokeWidth={2} className="text-accent" />
                <h3 className="mt-4 text-[16px] font-semibold text-ink">
                  {item.label}
                </h3>
                <p className="mt-2 flex-1 text-[13.5px] leading-relaxed text-muted">
                  {item.blurb}
                </p>
                <span className="mt-5 inline-flex items-center gap-1.5 text-[12.5px] font-medium text-accent">
                  Read
                  <ArrowRight
                    size={13}
                    strokeWidth={2}
                    className="transition-transform group-hover:translate-x-0.5"
                  />
                </span>
              </Link>
            );
          })}
        </div>
      </div>
    </section>
  );
}

export default function Overview() {
  const data = useData();

  if (data.status !== "ready") return <HeroSkeleton />;

  return (
    <>
      <Hero series={data.series} live={data.live} />
      <StatBand
        coverage={data.coverage}
        routes={data.routes}
        latest={data.latest}
      />
      <Contents />
      <CtaPanel />
    </>
  );
}
