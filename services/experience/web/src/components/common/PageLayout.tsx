import type { ReactNode } from "react";
import { H3 } from "@blueprintjs/core";
import { PageBreadcrumbs, type Crumb } from "./PageBreadcrumbs";

/** List + optional create action — Collections, Applications list, Ontology shell. */
export function RegistryPage({
  title,
  description,
  actions,
  trailing,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  trailing?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="hl-page hl-page--registry">
      <header className="hl-page-header">
        <div className="hl-page-header-main">
          <H3 className="hl-page-title">{title}</H3>
          {description && <div className="hl-page-description">{description}</div>}
        </div>
        {(trailing || actions) && (
          <div className="hl-page-actions">
            {trailing}
            {actions}
          </div>
        )}
      </header>
      <div className="hl-page-body">{children}</div>
    </div>
  );
}

/** Breadcrumb + title + actions — Object detail, Collection detail, Project detail. */
export function DetailPage({
  breadcrumbs,
  title,
  description,
  actions,
  children,
}: {
  breadcrumbs: Crumb[];
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="hl-page hl-page--detail">
      <PageBreadcrumbs items={breadcrumbs} />
      <header className="hl-page-header hl-mt-xs">
        <div className="hl-page-header-main">
          {typeof title === "string" ? <H3 className="hl-page-title">{title}</H3> : title}
          {description && <div className="hl-page-description">{description}</div>}
        </div>
        {actions && <div className="hl-page-actions">{actions}</div>}
      </header>
      <div className="hl-page-body">{children}</div>
    </div>
  );
}

export function PageSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="hl-page-section">
      <h4 className="hl-page-section-title">{title}</h4>
      {children}
    </section>
  );
}
