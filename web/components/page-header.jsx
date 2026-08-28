/**
 * The opening block of every page except the overview, which leads with the
 * hero instead. One eyebrow, one title, one paragraph: the same three elements
 * in the same place on each page, so the reader lands the same way every time.
 */
export default function PageHeader({ eyebrow, title, lede, aside }) {
  return (
    <header className="border-b border-hair">
      <div className="shell flex flex-wrap items-end justify-between gap-x-10 gap-y-6 py-12 lg:py-16">
        <div className="max-w-[60ch]">
          {eyebrow && (
            <p className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-accent">
              {eyebrow}
            </p>
          )}
          <h1 className="mt-4 text-[2.1rem] font-semibold leading-[1.1] tracking-tightest text-ink sm:text-[2.75rem]">
            {title}
          </h1>
          {lede && (
            <p className="mt-5 max-w-[58ch] text-[16px] leading-relaxed text-ink2">
              {lede}
            </p>
          )}
        </div>
        {aside}
      </div>
    </header>
  );
}
