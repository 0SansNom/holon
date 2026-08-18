const TYPE_COLOR_PALETTE = ["#2d63c8", "#b8551f", "#0e8a5f", "#8f6a1f", "#a8386b", "#3a8a4a"];

export function colorForObjectType(objectType: string): string {
  let hash = 0;
  for (let index = 0; index < objectType.length; index += 1) {
    hash = (hash * 31 + objectType.charCodeAt(index)) >>> 0;
  }
  return TYPE_COLOR_PALETTE[hash % TYPE_COLOR_PALETTE.length];
}
