import { Contrast, Monitor, Moon, Sun } from "lucide-react";
import { useAppearanceSetting } from "@/components/appearance-context";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { Theme } from "@/lib/appearance";

const THEMES = [
  { value: "system", label: "System", Icon: Monitor },
  { value: "light", label: "Light", Icon: Sun },
  { value: "dark", label: "Dark", Icon: Moon },
] as const;

/** A visitor's light / dark / high-contrast pick, for the hosts where nobody is signed in (every
 *  portfolio, the about page). Renders nothing on jac, where the profile holds the setting. */
export function AppearanceSwitch() {
  const { appearance, set } = useAppearanceSetting();
  if (!set) return null;
  const current = THEMES.find((t) => t.value === appearance.theme) ?? THEMES[0];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="xs" aria-label="Appearance">
          <current.Icon data-icon="inline-start" />
          Appearance
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="center" side="top">
        <DropdownMenuLabel>Appearance</DropdownMenuLabel>
        <DropdownMenuRadioGroup
          value={appearance.theme}
          onValueChange={(theme) =>
            set({ ...appearance, theme: theme as Theme })
          }
        >
          {THEMES.map(({ value, label, Icon }) => (
            <DropdownMenuRadioItem key={value} value={value}>
              <Icon />
              {label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        <DropdownMenuCheckboxItem
          checked={appearance.contrast === "high"}
          onCheckedChange={(on) =>
            set({ ...appearance, contrast: on ? "high" : "normal" })
          }
        >
          <Contrast />
          High contrast
        </DropdownMenuCheckboxItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
