/**
 * The standing disclaimer, on every page. The dashboard call to action lives
 * in `CtaPanel` and appears only where it earns its place, so this stays a
 * single quiet line rather than repeating a banner six times.
 *
 * Synthetic-share labelling is not surfaced in this UI. The field is still
 * carried per row in the database and returned by /api/v1/provenance.
 */
export default function SiteFoot() {
  return (
    <footer className="border-t border-hair">
      <div className="shell flex flex-col gap-4 py-8 text-[12.5px] text-muted sm:flex-row sm:items-center sm:justify-between">
        <p className="max-w-[62ch] leading-relaxed">
          Pushpak is a hackathon prototype, not an official statistic. Route
          weights derive from the DGCA domestic city-pair release;
          advance-purchase weights are a documented assumption.
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
    </footer>
  );
}
