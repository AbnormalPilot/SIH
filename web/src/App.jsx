import { useEffect, useState } from "react";
import Nav from "./sections/Nav.jsx";
import Hero from "./sections/Hero.jsx";
import StatBand from "./sections/StatBand.jsx";
import Compliance from "./sections/Compliance.jsx";
import Method from "./sections/Method.jsx";
import Formula from "./sections/Formula.jsx";
import Basket from "./sections/Basket.jsx";
import ApiSection from "./sections/ApiSection.jsx";
import Footer from "./sections/Footer.jsx";
import { fetchIndex, fetchLatest, fetchRoutes } from "./lib/api.js";
import { ScrollTrigger } from "./lib/motion.js";

/** Skeleton matching the hero's final shape, so there is no layout shift. */
function Skeleton() {
  return (
    <div className="shell grid gap-12 pb-20 pt-16 lg:grid-cols-[1.05fr_1fr] lg:pt-24">
      <div className="space-y-5">
        <div className="h-3 w-64 animate-pulse rounded bg-raised" />
        <div className="h-14 w-full max-w-lg animate-pulse rounded bg-raised" />
        <div className="h-14 w-4/5 max-w-md animate-pulse rounded bg-raised" />
        <div className="h-4 w-full max-w-sm animate-pulse rounded bg-raised" />
      </div>
      <div className="h-[22rem] animate-pulse rounded-xl bg-raised" />
    </div>
  );
}

export default function App() {
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

  // Pinned sections measure the document, so they must remeasure once the
  // fetched content has expanded the page.
  useEffect(() => {
    if (state.status !== "ready") return;
    const id = requestAnimationFrame(() => ScrollTrigger.refresh());
    return () => cancelAnimationFrame(id);
  }, [state.status]);

  return (
    <div className="grain min-h-[100dvh] bg-plane">
      <Nav />
      {state.status === "loading" ? (
        <Skeleton />
      ) : (
        <main>
          <Hero series={state.series} live={state.live} />
          <StatBand coverage={state.coverage} routes={state.routes} latest={state.latest} />
          <Compliance />
          <Method />
          <Formula />
          <Basket
            routes={state.routes}
            coverage={state.coverage}
            window={state.trafficWindow}
          />
          <ApiSection />
          <Footer />
        </main>
      )}
    </div>
  );
}
