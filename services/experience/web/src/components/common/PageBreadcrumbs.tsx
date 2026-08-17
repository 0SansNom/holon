import { Breadcrumbs, type BreadcrumbProps } from "@blueprintjs/core";
import { useNavigate } from "@tanstack/react-router";

// A *real* breadcrumb trail — clickable, keyboard-navigable, visually
// unambiguous (Blueprint's own component, not a styled-to-look-like-one
// text link). Every Object Explorer page had its own ad-hoc "back" link
// or none at all (ObjectGraphPage had no way back except the browser
// button) — this replaces all of them with one consistent, genuinely
// discoverable pattern.
export interface Crumb {
  label: string;
  to?: string;
  params?: Record<string, string>;
  search?: Record<string, string | undefined>;
}

export function PageBreadcrumbs({ items }: { items: Crumb[] }) {
  const navigate = useNavigate();

  const crumbs: BreadcrumbProps[] = items.map((item, index) => ({
    text: item.label,
    current: index === items.length - 1,
    onClick: item.to
      ? () =>
          void navigate({
            to: item.to!,
            params: item.params as never,
            search: (item.search ?? {}) as never,
          })
      : undefined,
  }));

  return <Breadcrumbs items={crumbs} />;
}
