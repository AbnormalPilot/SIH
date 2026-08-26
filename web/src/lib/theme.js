/**
 * Theme state.
 *
 * Light is the default. A reader's explicit choice is remembered in
 * localStorage and stamped onto <html data-theme>, which is what the CSS
 * custom properties key off. The system preference is deliberately not
 * consulted: the brief asks for light by default, and silently following the
 * OS would contradict that on a dark-mode machine.
 */
import { useCallback, useEffect, useState } from "react";

const KEY = "pushpak-theme";

export function readTheme() {
  try {
    return localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

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
  const [theme, setTheme] = useState(readTheme);

  useEffect(() => {
    apply(theme);
  }, [theme]);

  const toggle = useCallback(
    () => setTheme((t) => (t === "dark" ? "light" : "dark")),
    [],
  );

  return { theme, toggle };
}
