/**
 * The site map, in one place.
 *
 * The top nav, the overview's contents grid and the previous/next pager all
 * read from this file, so a page can never appear in one navigation surface
 * and be missing from another.
 *
 * Paths deliberately avoid the /api, /docs, /dashboard and /static prefixes:
 * nginx in production and next.config.mjs rewrites in development both hand
 * those to FastAPI, so a page living under one of them would never be reached.
 */
import {
  GitBranch,
  Sigma,
  Layers,
  ShieldCheck,
  Terminal,
} from "lucide-react";

/**
 * The documentation pages, in reading order. The landing page is deliberately
 * not among them: it is the front door, not a step in the sequence, so it is
 * neither a pager neighbour nor a card in its own contents grid.
 *
 * The group labels survive the sidebar that used to render them because the
 * overview's contents grid still reads this shape.
 */
export const SECTIONS = [
  {
    label: "The index",
    items: [
      {
        to: "/basket",
        label: "Basket and weights",
        blurb: "Ten city pairs, weighted by DGCA passenger traffic",
        icon: Layers,
      },
    ],
  },
  {
    label: "Methodology",
    items: [
      {
        to: "/method",
        label: "Pipeline",
        blurb: "Collect, normalise, impute, aggregate",
        icon: GitBranch,
      },
      {
        to: "/formula",
        label: "Index formula",
        blurb: "The Laspeyres arithmetic, term by term",
        icon: Sigma,
      },
      {
        to: "/compliance",
        label: "Compliance",
        blurb: "The robots.txt gate and what it refuses",
        icon: ShieldCheck,
      },
    ],
  },
  {
    label: "Build on it",
    items: [
      {
        to: "/developers",
        label: "API reference",
        blurb: "Endpoints, provenance fields, sample payload",
        icon: Terminal,
      },
    ],
  },
];

/**
 * The top nav. Every documentation page is on it: with no rail anywhere on the
 * site, this and the overview's contents grid are the only ways in, so leaving
 * one out would strand it.
 */
export const TOP_NAV = [
  { to: "/basket", label: "Basket" },
  { to: "/method", label: "Method" },
  { to: "/formula", label: "Formula" },
  { to: "/compliance", label: "Compliance" },
  { to: "/developers", label: "API" },
];

/** Flat reading order, used by the pager. */
export const PAGES = SECTIONS.flatMap((s) =>
  s.items.map((i) => ({ ...i, section: s.label })),
);

export function neighbours(pathname) {
  const i = PAGES.findIndex((p) => p.to === pathname);
  if (i === -1) return { prev: null, next: null };
  return { prev: PAGES[i - 1] ?? null, next: PAGES[i + 1] ?? null };
}

/** External destinations, served by the API container rather than the router. */
export const EXTERNAL = [
  { href: "/dashboard", label: "Operations dashboard" },
  { href: "/docs", label: "OpenAPI docs" },
];
