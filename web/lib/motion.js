"use client";

/**
 * GSAP helpers.
 *
 * Every animation on this site is registered through `withMotion`, which wraps
 * the work in gsap.matchMedia so that `prefers-reduced-motion: reduce` gets a
 * genuine static page rather than a fast one, and returns the context so the
 * caller's useEffect can revert cleanly on unmount.
 *
 * Client components still render once on the server, so plugin registration is
 * guarded: ScrollTrigger has nothing to attach to without a document.
 */
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";

if (typeof window !== "undefined") {
  gsap.registerPlugin(ScrollTrigger);
}

export const EASE = "power3.out";

export function withMotion(scope, build) {
  const mm = gsap.matchMedia(scope);
  mm.add(
    {
      motion: "(prefers-reduced-motion: no-preference)",
      reduced: "(prefers-reduced-motion: reduce)",
    },
    (ctx) => {
      if (!ctx.conditions.motion) {
        // Reduced motion: put everything in its resting state immediately.
        gsap.set(scope.querySelectorAll(".will-rise"), { opacity: 1, y: 0 });
        return;
      }
      build(gsap, ctx);
    },
  );
  return mm;
}

/** Reveal-on-scroll for a set of elements. Communicates reading order. */
export function riseIn(targets, { stagger = 0.07, y = 22, trigger } = {}) {
  return gsap.to(targets, {
    opacity: 1,
    y: 0,
    duration: 0.75,
    ease: EASE,
    stagger,
    scrollTrigger: {
      trigger: trigger ?? targets,
      start: "top 82%",
      once: true,
    },
  });
}

export { gsap, ScrollTrigger };
