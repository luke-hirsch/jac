/**
 * Applies theme + contrast to <html>. Three sources, in priority order:
 *
 *  1. the signed-in user's profile (authoritative once it loads),
 *  2. localStorage (what the pre-paint script in index.html already applied),
 *  3. the system preference.
 *
 * The profile query only runs on the app host: on a `<handle>.` portfolio origin the
 * visitor is anonymous, and firing a 403 on every public page view is noise. Anonymous
 * visitors get the system preference, which is what they want anyway.
 *
 * The appearance is derived, not mirrored into state: the profile page writes its PATCH
 * response into the `["profile"]` cache, so a changed setting re-renders this provider
 * and applies without a reload.
 */
import { useEffect, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { siteHost } from "@/lib/host";
import {
  ALL_APPEARANCE_CLASSES,
  APPEARANCE_KEY,
  coerceAppearance,
  readAppearance,
  resolveTheme,
  themeClasses,
} from "@/lib/appearance";
import type { ProfileRow } from "@/lib/queries/profile";
import { ResolvedThemeContext } from "./appearance-context";

const DARK_QUERY = "(prefers-color-scheme: dark)";

// localStorage can be missing or throw (private windows, blocked site data) — the theme
// then simply falls back to the system preference.
function readStored(): string | null {
  try {
    return localStorage.getItem(APPEARANCE_KEY);
  } catch {
    return null;
  }
}

function writeStored(value: string) {
  try {
    localStorage.setItem(APPEARANCE_KEY, value);
  } catch {
    /* next load just paints from the system preference */
  }
}

export function AppearanceProvider({ children }: { children: ReactNode }) {
  const onAppHost = siteHost().kind === "app";
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia(DARK_QUERY).matches,
  );
  const [stored] = useState(() => readAppearance(readStored()));

  // `theme: system` must keep following the OS while the tab is open, not just at load.
  useEffect(() => {
    const mq = window.matchMedia(DARK_QUERY);
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  // Same query key as the account page and useProfile() — one cache entry per session.
  const profile = useQuery({
    queryKey: ["profile"],
    queryFn: () => api<ProfileRow>("/api/spa/profile/"),
    enabled: onAppHost,
    retry: false,
  });

  const fromProfile = profile.data ? coerceAppearance(profile.data) : null;
  const theme = fromProfile?.theme ?? stored.theme;
  const contrast = fromProfile?.contrast ?? stored.contrast;

  // Mirror the profile into localStorage so the NEXT load paints correctly on the first
  // frame instead of flashing and correcting.
  const hasProfile = fromProfile !== null;
  useEffect(() => {
    if (hasProfile) writeStored(JSON.stringify({ theme, contrast }));
  }, [hasProfile, theme, contrast]);

  useEffect(() => {
    const el = document.documentElement;
    el.classList.remove(...ALL_APPEARANCE_CLASSES);
    el.classList.add(...themeClasses({ theme, contrast }, systemDark));
  }, [theme, contrast, systemDark]);

  return (
    <ResolvedThemeContext.Provider value={resolveTheme(theme, systemDark)}>
      {children}
    </ResolvedThemeContext.Provider>
  );
}
