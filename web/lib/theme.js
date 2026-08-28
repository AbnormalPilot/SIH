"use client";

/**
 * Theme state.
 *
 * Light is the default. A reader's explicit choice is remembered in
 * localStorage and stamped onto <html data-theme>, which is what the CSS
 * custom properties key off. The system preference is deliberately not
 * consulted: the brief asks for light by default, and silently following the
 * OS would contradict that on a dark-mode machine.
 *
 * Under SSR the stored choice is unknowable, so the hook starts at `null` and
 * resolves after mount. Reading localStorage in the initial state instead would
 * render "light" on the server and "dark" on the client and blow up hydration.
 * The pre-paint script in the root layout has already stamped the right theme
 * by then, so `null` costs a correct first paint nothing — it only means the
 * toggle renders its icon one tick late.
 */
import { useCallback, useEffect, useState } from "react";

const KEY = "pushpak-theme";

function apply(theme) {
  const root = document.documentElement;
  if (theme === "dark") root.dataset.theme = "dark";
  else delete root.dataset.theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* storage blocked: the theme still applies for this page view */
  }
}

export function useTheme() {
  const [theme, setTheme] = useState(null);

  useEffect(() => {
    try {
      setTheme(localStorage.getItem(KEY) === "dark" ? "dark" : "light");
    } catch {
      setTheme("light");
    }
  }, []);

  useEffect(() => {
    if (theme) apply(theme);
  }, [theme]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );

  return { theme, toggle };
}
