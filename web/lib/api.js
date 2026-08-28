/**
 * Thin client for the Pushpak API.
 *
 * The landing page must render even when the API container is down (a demo
 * laptop, a judge opening the static build). Every call degrades to a clearly
 * marked offline fallback rather than throwing, and `live` tells the UI which
 * it got so the page never presents cached sample numbers as live readings.
 */
// Empty by default: nginx in production and next.config rewrites in
// development both put the API on this origin, so relative paths are right.
const BASE = process.env.NEXT_PUBLIC_PUSHPAK_API ?? "";

async function get(path, timeoutMs = 3500) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return { live: true, data: await res.json() };
  } catch {
    return { live: false, data: null };
  } finally {
    clearTimeout(timer);
  }
}

/** Deterministic stand-in series, used only when the API cannot be reached. */
const FALLBACK_SERIES = [
  100.0, 101.4, 99.2, 98.6, 100.8, 103.1, 104.6, 102.2, 100.4, 99.1, 101.7,
  104.9, 106.3, 103.8, 101.2, 100.6, 102.4, 105.7, 107.1, 104.3, 102.9, 101.5,
  103.6, 106.1, 103.2, 106.7, 108.2, 106.1, 97.7, 97.2,
].map((v, i) => ({
  period: new Date(Date.UTC(2026, 6, 27 + i)).toISOString().slice(0, 10),
  index_value: v,
  synthetic_share: 1,
  quality_flag: "amber",
}));

export async function fetchIndex() {
  const { live, data } = await get("/api/v1/index?frequency=daily");
  if (live && data?.series?.length) return { live, series: data.series };
  return { live: false, series: FALLBACK_SERIES };
}

export async function fetchLatest() {
  const { live, data } = await get("/api/v1/index/latest");
  if (live && data) return { live, latest: data };
  return {
    live: false,
    latest: {
      period: "2026-08-26",
      index_value: 97.44,
      change_pct: 2.89,
      n_quotes: 6000,
      synthetic_share: 1,
      soldout_share: 0.08,
      quality_flag: "amber",
      base_period: ["2026-07-28", "2026-07-29", "2026-07-30"],
    },
  };
}

export async function fetchRoutes() {
  const { live, data } = await get("/api/v1/routes");
  if (live && data?.routes?.length) return { live, ...data };
  return {
    live: false,
    national_coverage_share: 0.1289,
    traffic_window: "202506..202605",
    routes: [
      { route_id: "BOM-DEL", annual_pax: 6743036, weight: 0.310876, national_share: 0.0401 },
      { route_id: "BLR-DEL", annual_pax: 4879073, weight: 0.224941, national_share: 0.029 },
      { route_id: "BLR-BOM", annual_pax: 4106189, weight: 0.189309, national_share: 0.0244 },
      { route_id: "DEL-HYD", annual_pax: 3095780, weight: 0.142726, national_share: 0.0184 },
      { route_id: "CCU-DEL", annual_pax: 2866361, weight: 0.132149, national_share: 0.017 },
    ],
  };
}

export async function fetchProvenance() {
  const { live, data } = await get("/api/v1/provenance");
  if (live && data) return { live, prov: data };
  return { live: false, prov: null };
}
