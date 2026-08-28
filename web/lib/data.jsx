"use client";

/**
 * One fetch for the whole site.
 *
 * The site is several routed pages rather than one scroll, so the API is
 * polled once from the root layout and shared through context. Navigating
 * between pages must not refire three requests, and a page that needs no live
 * data (method, formula, compliance, API reference) must not wait on them
 * either — so the provider never blocks rendering, it just reports `status`.
 */
import { createContext, useContext, useEffect, useState } from "react";
import { fetchIndex, fetchLatest, fetchRoutes } from "./api.js";

const DataContext = createContext({ status: "loading" });

export function DataProvider({ children }) {
  const [state, setState] = useState({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    Promise.all([fetchIndex(), fetchLatest(), fetchRoutes()]).then(
      ([idx, lat, rts]) => {
        if (cancelled) return;
        setState({
          status: "ready",
          series: idx.series,
          live: idx.live && lat.live,
          latest: lat.latest,
          routes: rts.routes,
          coverage: rts.national_coverage_share,
          trafficWindow: rts.traffic_window ?? "202506..202605",
        });
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return <DataContext.Provider value={state}>{children}</DataContext.Provider>;
}

export function useData() {
  return useContext(DataContext);
}
