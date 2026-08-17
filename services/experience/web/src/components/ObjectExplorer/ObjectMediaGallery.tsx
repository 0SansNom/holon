import { Tag } from "@blueprintjs/core";
import type { ObjectMediaItem } from "./objectMedia";

export function ObjectMediaGallery({ items }: { items: ObjectMediaItem[] }) {
  if (items.length === 0) {
    return (
      <p className="hl-text-muted">
        No media properties on this object. Mark properties with type class{" "}
        <span className="hl-mono">hubble:media_url</span> or <span className="hl-mono">hubble:icon</span>.
      </p>
    );
  }

  return (
    <div className="hl-oe-media-gallery">
      {items.map((item) => (
        <figure key={`${item.property}-${item.url}`} className="hl-oe-media-card">
          <a href={item.url} target="_blank" rel="noreferrer" className="hl-oe-media-card-link">
            <img src={item.url} alt={item.property} className="hl-oe-media-card-img" />
          </a>
          <figcaption className="hl-oe-media-card-caption">
            <span className="hl-mono">{item.property}</span>
            <Tag minimal>{item.kind === "icon" ? "icon" : "media"}</Tag>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}
