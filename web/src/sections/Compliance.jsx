import { useEffect, useRef } from "react";
import { ShieldOff, FileWarning } from "lucide-react";
import { riseIn, withMotion } from "../lib/motion.js";

/**
 * The project's central empirical finding, presented as the ledger it is.
 * Every verdict here was produced by running the gate against the live
 * robots.txt of each site; none of it is illustrative.
 */
const SOURCES = [
  { name: "goindigo.in", kind: "Airline", rule: "robots.txt unreachable, client blocked", verdict: "REFUSE" },
  { name: "airindia.com", kind: "Airline", rule: "robots.txt unreachable, client blocked", verdict: "REFUSE" },
  { name: "skyscanner.co.in", kind: "OTA", rule: "Disallow: /transport/*", verdict: "REFUSE" },
  { name: "cleartrip.com", kind: "OTA", rule: "Disallow: /flights/search*", verdict: "REFUSE" },
  { name: "ixigo.com", kind: "OTA", rule: "Disallow: /search/result/", verdict: "REFUSE" },
  { name: "easemytrip.com", kind: "OTA", rule: "Disallow: /flight-search/listing*", verdict: "REFUSE" },
  { name: "spicejet.com", kind: "Airline", rule: "public pages allowed, /api/v1 disallowed", verdict: "ALLOW" },
  { name: "akasaair.com", kind: "Airline", rule: "robots.txt declares no Disallow", verdict: "ALLOW" },
];

export default function Compliance() {
  const root = useRef(null);

  useEffect(() => {
    if (!root.current) return;
    const el = root.current;
    const mm = withMotion(el, (g) => {
      // Rows arrive one by one. The stagger is the point: the wall builds,
      // and by the last row the conclusion is already obvious.
      riseIn(el.querySelectorAll("[data-row]"), {
        stagger: 0.085,
        y: 16,
        trigger: el.querySelector("[data-ledger]"),
      });
      riseIn(el.querySelectorAll("[data-copy]"), { stagger: 0.1 });
    });
    return () => mm.revert();
  }, []);

  return (
    <section
      id="compliance"
      ref={root}
      className="border-b border-hair py-20 lg:py-28"
    >
      <div className="shell grid gap-12 lg:grid-cols-[0.85fr_1.15fr] lg:gap-16">
        <div>
          <p className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-accent">
            What scraping actually returns
          </p>
          <h2
            data-copy
            className="will-rise mt-5 text-[2rem] font-semibold leading-[1.12] tracking-tight text-ink sm:text-[2.5rem]"
          >
            Six of eight fare sources refuse automated collection.
          </h2>
          <p data-copy className="will-rise mt-5 max-w-[52ch] leading-relaxed">
            The gate checks robots.txt before it opens a socket, and it fails
            closed: a site that cannot be reached has not granted permission.
            SpiceJet and Akasa Air do permit crawling, so the gate is
            demonstrably discriminating rather than refusing everything.
          </p>
          <p data-copy className="will-rise mt-4 max-w-[52ch] leading-relaxed">
            The two permitted sites still return no prices: their fare grids
            render client-side, and SpiceJet disallows the very API that fills
            them. Permitted to crawl is not the same as yields fares, which is
            why real data here comes from a licensed API, not a faster crawler.
          </p>

          <div
            data-copy
            className="will-rise mt-8 flex items-start gap-3 rounded-xl border border-warn/25 bg-warn/[0.06] p-4"
          >
            <FileWarning size={18} className="mt-0.5 shrink-0 text-warn" />
            <p className="text-[13.5px] leading-relaxed text-ink2">
              Python's standard-library robots parser reports Skyscanner's fare
              path as <span className="font-mono text-ink">allowed</span>: it
              does not implement wildcards. SpiceJet writes an absolute URL
              where a path belongs, which a literal matcher would also miss.
              The gate handles both, and regression tests pin them.
            </p>
          </div>
        </div>

        <div data-ledger className="panel overflow-hidden">
          <div className="flex items-center justify-between border-b border-hair px-5 py-3.5">
            <p className="font-mono text-[12px] text-muted">
              pushpak.cli robots-audit
            </p>
            <span className="inline-flex items-center gap-1.5 rounded-full border border-bad/40 px-2.5 py-1 text-[11px] font-semibold text-bad">
              <ShieldOff size={12} strokeWidth={2.2} />
              2 of 8 permitted
            </span>
          </div>

          <ul>
            {SOURCES.map((s) => (
              <li
                key={s.name}
                data-row
                className="will-rise flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-4 odd:bg-raised/40"
              >
                <span className="min-w-[9.5rem] font-mono text-[13.5px] text-ink">
                  {s.name}
                </span>
                <span className="rounded-full border border-hair px-2 py-0.5 text-[11px] text-muted">
                  {s.kind}
                </span>
                <span
                  className={`ml-auto font-mono text-[11px] font-semibold ${
                    s.verdict === "ALLOW" ? "text-good" : "text-bad"
                  }`}
                >
                  {s.verdict}
                </span>
                <span className="w-full font-mono text-[12px] leading-relaxed text-muted">
                  {s.rule}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
