import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { PAGES } from "@/lib/nav.js";

export const metadata = { title: "Not found" };

export default function NotFound() {
  return (
    <div className="shell py-24 lg:py-32">
      <p className="font-mono text-[11.5px] uppercase tracking-[0.18em] text-accent">
        404
      </p>
      <h1 className="mt-4 text-[2.1rem] font-semibold tracking-tightest text-ink sm:text-[2.75rem]">
        No page at this address.
      </h1>
      <p className="mt-5 max-w-[52ch] leading-relaxed">
        There is a landing page and five documentation pages. Here are the
        five.
      </p>
      <ul className="mt-9 divide-y divide-hair border-y border-hair">
        {PAGES.map((p) => (
          <li key={p.to}>
            <Link
              href={p.to}
              className="group flex items-center justify-between gap-4 py-4 transition-colors hover:text-ink"
            >
              <span>
                <span className="block text-[15px] font-semibold text-ink">
                  {p.label}
                </span>
                <span className="mt-0.5 block text-[12.5px] text-muted">
                  {p.blurb}
                </span>
              </span>
              <ArrowRight
                size={15}
                strokeWidth={2}
                className="shrink-0 text-muted transition-transform group-hover:translate-x-0.5"
              />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
