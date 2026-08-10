import { Button, Menu, MenuItem, PopoverNext } from "@blueprintjs/core";
import { useThemeStore, type ThemePreference } from "../../store/theme";

const LABELS: Record<ThemePreference, string> = {
  light: "Light",
  dark: "Dark",
  system: "System",
};

export function ThemeToggle() {
  const preference = useThemeStore((s) => s.preference);
  const setPreference = useThemeStore((s) => s.setPreference);

  return (
    <PopoverNext
      placement="bottom-end"
      content={
        <Menu>
          {(["light", "dark", "system"] as const).map((value) => (
            <MenuItem
              key={value}
              icon={preference === value ? "tick" : "blank"}
              text={LABELS[value]}
              onClick={() => setPreference(value)}
            />
          ))}
        </Menu>
      }
    >
      <Button
        minimal
        small
        icon={preference === "dark" ? "moon" : preference === "light" ? "flash" : "desktop"}
        aria-label="Theme"
        title={`Theme: ${LABELS[preference]}`}
      />
    </PopoverNext>
  );
}
