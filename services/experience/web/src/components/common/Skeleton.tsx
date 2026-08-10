/** Layout-shaped placeholders — Suspense fallbacks that match final UI geometry. */

export function SkeletonBlock({
  width = "100%",
  height = 14,
  className,
}: {
  width?: number | string;
  height?: number;
  className?: string;
}) {
  return (
    <div
      className={["hl-skeleton", className].filter(Boolean).join(" ")}
      style={{ width, height }}
      aria-hidden
    />
  );
}

export function RegistryPageSkeleton({ cards = 6, minWidth = 240 }: { cards?: number; minWidth?: number }) {
  return (
    <div className="hl-page hl-page--registry" aria-busy aria-label="Loading">
      <header className="hl-page-header">
        <div className="hl-page-header-main">
          <SkeletonBlock width={180} height={24} />
          <SkeletonBlock width="min(560px, 100%)" height={36} className="hl-mt-sm" />
        </div>
        <SkeletonBlock width={120} height={30} />
      </header>
      <CardGridSkeleton count={cards} minWidth={minWidth} />
    </div>
  );
}

export function RegistryTabSkeleton({ cards = 4 }: { cards?: number }) {
  return (
    <div aria-busy aria-label="Loading">
      <div className="hl-flex-between hl-mb-sm">
        <SkeletonBlock width="min(480px, 100%)" height={32} />
        <SkeletonBlock width={110} height={30} />
      </div>
      <CardGridSkeleton count={cards} />
    </div>
  );
}

export function CardGridSkeleton({ count = 6, minWidth = 240 }: { count?: number; minWidth?: number }) {
  return (
    <div
      className="hl-card-grid-skeleton"
      style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` }}
      aria-hidden
    >
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="hl-skeleton-card">
          <SkeletonBlock width="70%" height={16} />
          <SkeletonBlock width="100%" height={12} className="hl-mt-sm" />
          <SkeletonBlock width="55%" height={12} className="hl-mt-xs" />
        </div>
      ))}
    </div>
  );
}

export function TablePageSkeleton({ rows = 10 }: { rows?: number }) {
  return (
    <div className="hl-page hl-page--detail" aria-busy aria-label="Loading">
      <SkeletonBlock width={220} height={14} className="hl-mb-sm" />
      <div className="hl-flex-between hl-mb-md">
        <SkeletonBlock width={160} height={24} />
        <SkeletonBlock width={110} height={30} />
      </div>
      <SkeletonBlock width="min(320px, 100%)" height={30} className="hl-mb-md" />
      <TableSkeleton rows={rows} />
    </div>
  );
}

export function DetailPageSkeleton() {
  return (
    <div className="hl-page hl-page--detail" aria-busy aria-label="Loading">
      <SkeletonBlock width={280} height={14} className="hl-mb-sm" />
      <div className="hl-flex-between hl-mb-md">
        <SkeletonBlock width={240} height={24} />
        <SkeletonBlock width={180} height={30} />
      </div>
      <div className="hl-panel">
        <SkeletonBlock width="100%" height={28} className="hl-mb-sm" />
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="hl-flex-between hl-mb-xs">
            <SkeletonBlock width={120} height={14} />
            <SkeletonBlock width="55%" height={14} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function SearchResultsSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="hl-search-layout hl-mt-md" aria-busy aria-label="Loading search results">
      <div className="hl-search-facets">
        <SkeletonBlock width={80} height={12} className="hl-mb-sm" />
        {Array.from({ length: 4 }, (_, i) => (
          <SkeletonBlock key={i} width="100%" height={28} className="hl-mb-xs" />
        ))}
      </div>
      <div className="hl-flex-1 hl-min-w-0">
        <SkeletonBlock width={100} height={20} className="hl-mb-md" />
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="hl-panel hl-mb-sm">
            <SkeletonBlock width="60%" height={12} />
            <SkeletonBlock width="100%" height={14} className="hl-mt-sm" />
          </div>
        ))}
      </div>
    </div>
  );
}

export function BranchesDialogSkeleton() {
  return (
    <div className="hl-branches-layout" aria-busy aria-label="Loading branches">
      <div className="hl-branches-sidebar">
        <SkeletonBlock width="100%" height={28} className="hl-mb-sm" />
        {Array.from({ length: 4 }, (_, i) => (
          <SkeletonBlock key={i} width="100%" height={36} className="hl-mb-xs" />
        ))}
      </div>
      <div className="hl-branches-main">
        <SkeletonBlock width="40%" height={20} className="hl-mb-sm" />
        <SkeletonBlock width="100%" height={220} />
      </div>
    </div>
  );
}

export function ObjectAppSkeleton() {
  return (
    <div className="hl-object-app-layout" aria-busy aria-label="Loading app">
      <div className="hl-panel hl-object-app-table">
        <TableSkeleton rows={6} />
      </div>
      <div className="hl-panel hl-object-app-detail">
        <SkeletonBlock width="100%" height={180} />
      </div>
    </div>
  );
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="hl-panel hl-table-skeleton" aria-busy aria-label="Loading">
      <SkeletonBlock width="100%" height={28} className="hl-mb-sm" />
      {Array.from({ length: rows }, (_, i) => (
        <SkeletonBlock key={i} width="100%" height={32} className="hl-mb-xs" />
      ))}
    </div>
  );
}
