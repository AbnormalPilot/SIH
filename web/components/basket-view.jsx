"use client";

import Basket from "@/components/basket.jsx";
import { useData } from "@/lib/data.jsx";

/**
 * Context adapter for the basket grid, which is a pure props component so it
 * stays testable and reusable. Everything the page needs arrives from the one
 * fetch in the root layout.
 */
export default function BasketView() {
  const data = useData();

  if (data.status !== "ready") {
    return (
      <div className="shell grid gap-4 py-14 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className={`h-52 animate-pulse rounded-xl bg-raised ${
              i === 0 ? "sm:col-span-2" : ""
            }`}
          />
        ))}
      </div>
    );
  }

  return (
    <Basket
      routes={data.routes}
      coverage={data.coverage}
      window={data.trafficWindow}
    />
  );
}
