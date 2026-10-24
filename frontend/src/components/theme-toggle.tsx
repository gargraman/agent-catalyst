"use client";

import * as React from "react";
import { Moon, Sun } from "lucide-react";

// Subscribe to <html> class mutations so the toggle (and any other theme change) re-renders the icon.
function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

export function ThemeToggle() {
  // Read the live theme from the <html> class, set pre-hydration by the no-flash bootstrap script in
  // layout.tsx. useSyncExternalStore keeps SSR + first client render on the dark default (server snapshot),
  // then re-reads the real DOM value after hydration — hydration-safe with no set-state-in-effect.
  const light = React.useSyncExternalStore(
    subscribe,
    () => document.documentElement.classList.contains("light"),
    () => false,
  );

  function toggle() {
    const next = !light;
    document.documentElement.classList.toggle("light", next);
    try {
      localStorage.setItem("ac-theme", next ? "light" : "dark");
    } catch {}
  }

  return (
    <button
      onClick={toggle}
      aria-label="Toggle theme"
      className="inline-flex size-9 items-center justify-center rounded-lg border border-border text-ink-2 transition-colors hover:bg-elevated hover:text-ink"
    >
      {light ? <Moon className="size-4" /> : <Sun className="size-4" />}
    </button>
  );
}
