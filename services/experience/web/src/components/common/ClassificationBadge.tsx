export function ClassificationBadge({ classification }: { classification: string }) {
  return <span className={`hl-classification-badge hl-classification-${classification}`}>{classification}</span>;
}
