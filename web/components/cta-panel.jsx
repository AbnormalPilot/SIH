import { ArrowRight } from "lucide-react";

/**
 * The one dashboard call to action. It appears on the overview and at the end
 * of the API reference — the two pages a reader arrives at wanting to see the
 * thing run — and nowhere else.
 */
export default function CtaPanel() {
  return (
    <section className="border-t border-hair">
      <div className="shell py-14 lg:py-20">
        <div className="panel flex flex-wrap items-center justify-between gap-8 p-8 lg:p-12">
          <div>
            <h2 className="max-w-[18ch] text-[1.75rem] font-semibold leading-tight tracking-tight text-ink sm:text-[2.25rem]">
              See the pipeline running.
            </h2>
            <p className="mt-3 max-w-[46ch] text-[14.5px] leading-relaxed">
              The dashboard shows the index trend, the route heatmap, the
              lead-time curve and the audit ledger from the live database.
            </p>
          </div>
          <a className="btn-primary" href="/dashboard">
            Open dashboard
            <ArrowRight size={16} strokeWidth={2} />
          </a>
        </div>
      </div>
    </section>
  );
}
