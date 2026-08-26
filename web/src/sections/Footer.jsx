import { ArrowRight } from "lucide-react";

export default function Footer() {
  return (
    <footer className="py-16 lg:py-20">
      <div className="shell">
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

        <div className="mt-10 flex flex-col gap-4 border-t border-hair pt-8 text-[12.5px] text-muted sm:flex-row sm:items-center sm:justify-between">
          <p className="max-w-[62ch] leading-relaxed">
            Pushpak is a hackathon prototype, not an official statistic. Route
            weights derive from the DGCA domestic city-pair release. Fare quotes
            in this deployment are synthetic and labelled as such at row level.
          </p>
          <div className="flex shrink-0 gap-5">
            <a className="hover:text-ink2" href="/api/v1/methodology">
              Methodology
            </a>
            <a className="hover:text-ink2" href="/api/v1/provenance">
              Provenance
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
