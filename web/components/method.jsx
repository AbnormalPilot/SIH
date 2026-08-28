"use client";

import { useEffect, useRef } from "react";
import { ScrollTrigger, withMotion } from "@/lib/motion.js";

/**
 * Horizontal pan, pinned. The pipeline is a sequence, and panning it sideways
 * as the reader scrolls makes the order of operations the thing they cannot
 * miss. It is the only pinned section in the app.
 *
 * The pan distance is measured from the track's own container rather than the
 * viewport, and the pin starts below the sticky nav rather than at the top of
 * the window: measuring against `window.innerWidth` and pinning at 0 would
 * overshoot by the header's height and pan the last card off the end.
 */
const NAV_H = 68;

const STAGES = [
  {
    k: "01",
    title: "Collect",
    body: "Fare quotes for ten city pairs at five advance-purchase windows, from airline sites and OTAs. Every request is gated on robots.txt, rate limited, and written to an append-only ledger, including the requests that were refused.",
    tag: "collect/",
  },
  {
    k: "02",
    title: "Normalise",
    body: "One record schema for every source. Sold-out, cancelled and blocked quotes are kept as rows with null fares rather than dropped, because discarding sold-out inventory biases an airfare index downward.",
    tag: "schema.py",
  },
  {
    k: "03",
    title: "Impute",
    body: "Gaps are filled by a documented ladder: carry the last observed price forward, then impute the movement rather than the level from the route's other windows, then hold at base. Every filled cell is flagged with its rule.",
    tag: "clean.py",
  },
  {
    k: "04",
    title: "Aggregate",
    body: "A fixed-basket Laspeyres index. Cell relatives are weighted by DGCA passenger share times booking-window share, summed, and scaled to a base of 100. No chain-linking, no seasonal adjustment.",
    tag: "index.py",
  },
];

export default function Method() {
  const wrap = useRef(null);
  const track = useRef(null);

  useEffect(() => {
    if (!wrap.current || !track.current) return;
    const mm = withMotion(wrap.current, (g) => {
      const distance = () =>
        Math.max(0, track.current.scrollWidth - wrap.current.offsetWidth);
      g.to(track.current, {
        x: () => -distance(),
        ease: "none",
        scrollTrigger: {
          trigger: wrap.current,
          start: `top ${NAV_H}px`,
          end: () => `+=${distance() + 200}`,
          pin: true,
          scrub: 1,
          invalidateOnRefresh: true,
        },
      });
    });
    return () => {
      mm.revert();
      ScrollTrigger.refresh();
    };
  }, []);

  return (
    <>
      {/* Mobile and reduced-motion readers get an honest vertical stack. */}
      <section className="pan-stack py-14 lg:hidden">
        <div className="shell flex flex-col gap-6">
          {STAGES.map((s) => (
            <article key={s.k} className="panel p-6">
              <p className="font-mono text-[12px] text-accent">{s.k}</p>
              <h2 className="mt-2 text-xl font-semibold text-ink">{s.title}</h2>
              <p className="mt-3 text-[14.5px] leading-relaxed">{s.body}</p>
              <p className="kbd mt-4">{s.tag}</p>
            </article>
          ))}
        </div>
      </section>

      <section
        ref={wrap}
        className="pan-track relative hidden overflow-hidden lg:block"
        aria-label="Pipeline stages"
      >
        <div
          ref={track}
          className="flex h-[calc(100dvh-68px)] items-center gap-8 pl-8 pr-16"
        >
          <div className="w-[24rem] shrink-0 pr-6">
            <p className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-accent">
              Four modules, four test suites
            </p>
            <p className="mt-5 text-[1.6rem] font-semibold leading-[1.15] tracking-tight text-ink">
              Each stage stands alone, so any one of them can be demonstrated on
              its own.
            </p>
            <p className="mt-5 max-w-[38ch] leading-relaxed">
              Scroll to pan the pipeline left to right. The order to the right is
              the order the data actually moves in.
            </p>
          </div>

          {STAGES.map((s) => (
            <article
              key={s.k}
              className="panel flex h-[24rem] w-[23rem] shrink-0 flex-col justify-between p-8"
            >
              <div>
                <p className="font-mono text-[12.5px] text-accent">{s.k}</p>
                <h2 className="mt-3 text-[1.75rem] font-semibold tracking-tight text-ink">
                  {s.title}
                </h2>
                <p className="mt-4 text-[14.5px] leading-relaxed">{s.body}</p>
              </div>
              <p className="kbd">{s.tag}</p>
            </article>
          ))}
        </div>
      </section>
    </>
  );
}
