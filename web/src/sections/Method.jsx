import { useEffect, useRef } from "react";
import { gsap, ScrollTrigger, withMotion } from "../lib/motion.js";

/**
 * Horizontal pan, pinned. The pipeline is a sequence, and panning it sideways
 * as the reader scrolls makes the order of operations the thing they cannot
 * miss. This is the only pinned section on the page.
 */
const STAGES = [
  {
    k: "01",
    title: "Collect",
    body: "Fare quotes for five city pairs at four advance-purchase windows, from airline sites and OTAs. Every request is gated on robots.txt, rate limited, and written to an append-only ledger, including the requests that were refused.",
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
      const distance = () => track.current.scrollWidth - window.innerWidth + 64;
      g.to(track.current, {
        x: () => -distance(),
        ease: "none",
        scrollTrigger: {
          trigger: wrap.current,
          start: "top top",
          end: () => `+=${distance()}`,
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
      <section id="method" className="border-b border-hair py-20 lg:hidden">
        <div className="shell">
          <h2 className="text-[2rem] font-semibold leading-[1.12] tracking-tight text-ink">
            Scrape to index, in four stages.
          </h2>
          <div className="mt-10 flex flex-col gap-8">
            {STAGES.map((s) => (
              <article key={s.k} className="panel p-6">
                <p className="font-mono text-[12px] text-accent">{s.k}</p>
                <h3 className="mt-2 text-xl font-semibold text-ink">{s.title}</h3>
                <p className="mt-3 text-[14.5px] leading-relaxed">{s.body}</p>
                <p className="kbd mt-4">{s.tag}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section
        ref={wrap}
        className="relative hidden overflow-hidden border-b border-hair lg:block"
        aria-labelledby="method-heading"
      >
        <div ref={track} className="flex h-[100dvh] items-center gap-8 pl-8 pr-16">
          <div className="w-[30rem] shrink-0 pr-8">
            <h2
              id="method-heading"
              className="text-[3rem] font-semibold leading-[1.06] tracking-tightest text-ink"
            >
              Scrape to index, in four stages.
            </h2>
            <p className="mt-6 max-w-[42ch] leading-relaxed">
              Each stage is a separate module with its own tests, so any one of
              them can be demonstrated on its own.
            </p>
          </div>

          {STAGES.map((s) => (
            <article
              key={s.k}
              className="panel flex h-[24rem] w-[24rem] shrink-0 flex-col justify-between p-8"
            >
              <div>
                <p className="font-mono text-[12.5px] text-accent">{s.k}</p>
                <h3 className="mt-3 text-[1.75rem] font-semibold tracking-tight text-ink">
                  {s.title}
                </h3>
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
