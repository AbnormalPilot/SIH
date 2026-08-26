import { useEffect, useRef } from "react";
import { riseIn, withMotion } from "../lib/motion.js";
import { compactPax, pct } from "../lib/format.js";

/**
 * Bento with exactly six cells for six pieces of content: the lead route as a
 * wide tile, four routes as compact tiles, and one image tile so the grid is
 * not six identical text cards.
 */
export default function Basket({ routes, coverage, window: trafficWindow }) {
  const root = useRef(null);
  useEffect(() => {
    if (!root.current) return;
    const el = root.current;
    const mm = withMotion(el, () => {
      riseIn(el.querySelectorAll("[data-b]"), { stagger: 0.07 });
    });
    return () => mm.revert();
  }, [routes]);

  if (!routes?.length) return null;
  const [lead, ...rest] = routes;

  return (
    <section id="basket" ref={root} className="border-b border-hair py-20 lg:py-28">
      <div className="shell">
        <div className="flex flex-wrap items-end justify-between gap-6">
          <h2 className="max-w-[22ch] text-[2rem] font-semibold leading-[1.1] tracking-tight text-ink sm:text-[2.6rem]">
            The basket is not a guess.
          </h2>
          <p className="max-w-[42ch] text-[14.5px] leading-relaxed">
            Weights come from the DGCA city-pair passenger release for{" "}
            <span className="font-mono text-ink">{trafficWindow}</span>, recomputed
            by a script rather than typed in by hand.
          </p>
        </div>

        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {/* Lead route: wide tile with the weight rendered as a real bar. */}
          <article
            data-b
            className="will-rise panel flex flex-col justify-between p-7 sm:col-span-2"
          >
            <div>
              <p className="font-mono text-[12px] text-muted">Largest route by weight</p>
              <p className="mt-2 font-mono text-[2.25rem] font-semibold leading-none text-ink">
                {lead.route_id}
              </p>
              <p className="mt-3 text-[14.5px] leading-relaxed">
                Mumbai to Delhi carries {compactPax(lead.annual_pax)} passengers a
                year, more than any other domestic pair, and takes{" "}
                {pct(lead.weight)} of the basket.
              </p>
            </div>
            <div className="mt-7">
              <div className="h-2 w-full overflow-hidden rounded-full bg-raised">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${lead.weight * 100}%` }}
                />
              </div>
              <p className="kbd mt-2">{pct(lead.weight)} of basket weight</p>
            </div>
          </article>

          {/* Image tile so the grid carries real visual variation. */}
          <article
            data-b
            className="will-rise relative overflow-hidden rounded-xl border border-hair"
          >
            <img
              src="https://picsum.photos/seed/pushpak-indian-domestic-aviation/800/900"
              alt="Aircraft on an apron at dusk"
              loading="lazy"
              className="h-full min-h-[15rem] w-full object-cover opacity-70"
            />
            <div className="absolute inset-0 bg-gradient-to-t from-plane via-plane/40 to-transparent" />
            <div className="absolute inset-x-0 bottom-0 p-6">
              <p className="font-mono text-[2rem] font-semibold leading-none text-ink">
                {pct(coverage)}
              </p>
              <p className="mt-2 text-[13.5px] leading-snug text-ink2">
                of every domestic passenger in India flies one of these five
                routes.
              </p>
            </div>
          </article>

          {rest.map((r) => (
            <article key={r.route_id} data-b className="will-rise panel p-6">
              <p className="font-mono text-xl font-semibold text-ink">
                {r.route_id}
              </p>
              <p className="mt-3 text-[13.5px] text-ink2">
                {compactPax(r.annual_pax)} passengers a year
              </p>
              <div className="mt-4 h-1.5 w-full overflow-hidden rounded-full bg-raised">
                <div
                  className="h-full rounded-full bg-accentSoft"
                  style={{ width: `${r.weight * 100}%` }}
                />
              </div>
              <p className="kbd mt-2">{pct(r.weight)} of basket</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
