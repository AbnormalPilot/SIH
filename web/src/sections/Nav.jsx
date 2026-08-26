import { useEffect, useRef, useState } from "react";
import { List, X, ArrowUpRight, Sun, Moon } from "lucide-react";
import { useTheme } from "../lib/theme.js";

const LINKS = [
  { label: "Method", href: "#method" },
  { label: "Compliance", href: "#compliance" },
  { label: "Basket", href: "#basket" },
  { label: "API", href: "#api" },
];

function ThemeToggle({ theme, toggle }) {
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      className="theme-toggle"
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
    >
      {theme === "dark" ? <Sun size={16} strokeWidth={2} /> : <Moon size={16} strokeWidth={2} />}
    </button>
  );
}

export default function Nav() {
  const [open, setOpen] = useState(false);
  const { theme, toggle } = useTheme();
  const [solid, setSolid] = useState(false);
  const ref = useRef(null);

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
      ref={ref}
      className={`sticky top-0 z-50 transition-colors duration-300 ${
        solid ? "border-b border-hair bg-plane/85 backdrop-blur-md" : "bg-transparent"
      }`}
    >
      <div className="shell flex h-[68px] items-center justify-between gap-6">
        <a href="#top" className="flex items-center gap-2.5" aria-label="Pushpak home">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent font-mono text-[14px] font-bold text-white">
            प
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-ink">
            Pushpak
          </span>
          <span className="hidden text-[11px] font-medium text-muted sm:inline">
            Airfare Price Index
          </span>
        </a>

        <nav className="hidden items-center gap-7 md:flex" aria-label="Primary">
          {LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              className="text-[13.5px] font-medium text-ink2 transition-colors hover:text-ink"
            >
              {l.label}
            </a>
          ))}
        </nav>

        <div className="hidden items-center gap-2.5 md:flex">
          <ThemeToggle theme={theme} toggle={toggle} />
          <a className="btn-ghost" href="/docs">
            API docs
          </a>
          <a className="btn-primary" href="/dashboard">
            Open dashboard
            <ArrowUpRight size={15} strokeWidth={2} />
          </a>
        </div>

        <div className="flex items-center gap-2 md:hidden">
          <ThemeToggle theme={theme} toggle={toggle} />
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
            {LINKS.map((l) => (
              <li key={l.href}>
                <a
                  href={l.href}
                  onClick={() => setOpen(false)}
                  className="block py-2.5 text-[15px] font-medium text-ink2"
                >
                  {l.label}
                </a>
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
