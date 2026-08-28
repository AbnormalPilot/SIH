"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { List, X, ArrowUpRight, Sun, Moon } from "lucide-react";
import { TOP_NAV } from "@/lib/nav.js";
import { useTheme } from "@/lib/theme.js";

/**
 * The landing page's navigation.
 *
 * The front door is a page you read top to bottom, not a place you navigate
 * around in, so it gets a top nav rather than the sidebar. The rail starts at
 * the documentation pages, where there is a map worth keeping on screen.
 */
function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    // `theme` is null until the stored choice resolves after mount, so the
    // button holds its box but names no action it cannot yet perform.
    <button
      type="button"
      onClick={toggle}
      className="theme-toggle"
      disabled={!theme}
      aria-label={theme ? `Switch to ${next} theme` : "Theme"}
      title={theme ? `Switch to ${next} theme` : undefined}
    >
      {theme === "dark" ? (
        <Sun size={16} strokeWidth={2} />
      ) : theme === "light" ? (
        <Moon size={16} strokeWidth={2} />
      ) : null}
    </button>
  );
}

export default function SiteNav() {
  const [open, setOpen] = useState(false);
  const [solid, setSolid] = useState(false);

  useEffect(() => {
    // IntersectionObserver rather than a scroll listener: no per-frame work.
    const sentinel = document.createElement("div");
    sentinel.style.cssText = "position:absolute;top:0;height:80px;width:1px";
    document.body.appendChild(sentinel);
    const io = new IntersectionObserver(([e]) => setSolid(!e.isIntersecting), {
      threshold: 0,
    });
    io.observe(sentinel);
    return () => {
      io.disconnect();
      sentinel.remove();
    };
  }, []);

  return (
    <header
      className={`sticky top-0 z-50 transition-colors duration-300 ${
        solid ? "border-b border-hair bg-plane/85 backdrop-blur-md" : "bg-transparent"
      }`}
    >
      <div className="shell flex h-[68px] items-center justify-between gap-6">
        <Link href="/" className="flex items-center gap-2.5" aria-label="Pushpak home">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent font-mono text-[14px] font-bold text-white">
            प
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-ink">
            Pushpak
          </span>
          <span className="hidden text-[11px] font-medium text-muted sm:inline">
            Airfare Price Index
          </span>
        </Link>

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {TOP_NAV.map((l) => (
            <Link
              key={l.to}
              href={l.to}
              className="text-[13.5px] font-medium text-ink2 transition-colors hover:text-ink"
            >
              {l.label}
            </Link>
          ))}
        </nav>

        <div className="hidden items-center gap-2.5 md:flex">
          <ThemeToggle />
          {/* Served by FastAPI, not by Next: real navigations, not routes. */}
          <a className="btn-ghost" href="/docs">
            API docs
          </a>
          <a className="btn-primary" href="/dashboard">
            Open dashboard
            <ArrowUpRight size={15} strokeWidth={2} />
          </a>
        </div>

        <div className="flex items-center gap-2 md:hidden">
          <ThemeToggle />
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-label={open ? "Close menu" : "Open menu"}
            className="rounded-lg p-2 text-ink"
          >
            {open ? <X size={20} /> : <List size={20} />}
          </button>
        </div>
      </div>

      {open && (
        <nav
          className="border-t border-hair bg-plane px-5 pb-5 pt-3 md:hidden"
          aria-label="Mobile"
        >
          <ul className="flex flex-col">
            {TOP_NAV.map((l) => (
              <li key={l.to}>
                <Link
                  href={l.to}
                  onClick={() => setOpen(false)}
                  className="block py-2.5 text-[15px] font-medium text-ink2"
                >
                  {l.label}
                </Link>
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-col gap-2.5">
            <a className="btn-ghost w-full" href="/docs">
              API docs
            </a>
            <a className="btn-primary w-full" href="/dashboard">
              Open dashboard
            </a>
          </div>
        </nav>
      )}
    </header>
  );
}
