import { createContext, useContext } from "react";
import { DEFAULT_APPEARANCE, type Appearance } from "@/lib/appearance";

/** The theme actually on screen ("system" already resolved). For widgets that take a
 *  theme prop instead of reading the `.dark` class — e.g. the sonner Toaster, whose
 *  shadcn wrapper asks next-themes, which this app does not mount. */
export const ResolvedThemeContext = createContext<"light" | "dark">("light");

export function useResolvedTheme(): "light" | "dark" {
  return useContext(ResolvedThemeContext);
}

/** The appearance setting and, where a visitor may change it here, its setter. On the jac host
 *  the setting is the profile's (Account → Profile), so `set` is null there; on the portfolio
 *  host (every portfolio, the about page) it's the visitor's own, kept in localStorage. */
export type AppearanceSetting = {
  appearance: Appearance;
  set: ((a: Appearance) => void) | null;
};

export const AppearanceSettingContext = createContext<AppearanceSetting>({
  appearance: DEFAULT_APPEARANCE,
  set: null,
});

export function useAppearanceSetting(): AppearanceSetting {
  return useContext(AppearanceSettingContext);
}
