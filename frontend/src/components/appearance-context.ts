import { createContext, useContext } from "react";

/** The theme actually on screen ("system" already resolved). For widgets that take a
 *  theme prop instead of reading the `.dark` class — e.g. the sonner Toaster, whose
 *  shadcn wrapper asks next-themes, which this app does not mount. */
export const ResolvedThemeContext = createContext<"light" | "dark">("light");

export function useResolvedTheme(): "light" | "dark" {
  return useContext(ResolvedThemeContext);
}
