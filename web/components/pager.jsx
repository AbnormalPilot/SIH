"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { neighbours } from "@/lib/nav.js";

/**
 * Previous / next across the site's reading order. Slicing one scroll into six
 * pages removes the reader's ability to simply keep scrolling, so the order is
 * handed back to them explicitly at the foot of each page.
 */
export default function Pager() {
  const pathname = usePathname();
  const { prev, next } = neighbours(pathname);
  if (!prev && !next) return null;

  return (
    <nav aria-label="Pagination" className="border-t border-hair">
      <div className="shell grid gap-px py-10 sm:grid-cols-2">
        {prev ? (
          <Link
            href={prev.to}
            className="group rounded-xl px-5 py-4 transition-colors hover:bg-raised/60 sm:-mx-5"
          >
            <span className="flex items-center gap-2 text-[11.5px] font-medium text-muted">
              <ArrowLeft size={13} strokeWidth={2} />
              Previous
            </span>
            <span className="mt-1.5 block text-[15px] font-semibold text-ink">
              {prev.label}
            </span>
            <span className="mt-1 block text-[12.5px] text-muted">{prev.blurb}</span>
          </Link>
        ) : (
          <span />
        )}

        {next && (
          <Link
            href={next.to}
            className="group rounded-xl px-5 py-4 text-right transition-colors hover:bg-raised/60 sm:-mx-5 sm:ml-auto sm:w-full"
          >
            <span className="flex items-center justify-end gap-2 text-[11.5px] font-medium text-muted">
              Next
              <ArrowRight size={13} strokeWidth={2} />
            </span>
            <span className="mt-1.5 block text-[15px] font-semibold text-ink">
              {next.label}
            </span>
            <span className="mt-1 block text-[12.5px] text-muted">{next.blurb}</span>
          </Link>
        )}
      </div>
    </nav>
  );
}
