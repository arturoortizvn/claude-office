/**
 * Text preparation for the desk and boss marquees.
 */

/**
 * Longest string a marquee will rasterise.
 *
 * MarqueeText draws the whole string into one texture and masks it down to a
 * ~116px window, so length translates directly into canvas width: fontSize 18
 * at resolution 2 is roughly 22px per monospace character. Agent task text is
 * a raw prompt and reached 11,748 characters in practice, asking the GPU for a
 * 262144px-wide canvas — past MAX_TEXTURE_SIZE (16384 on most GPUs), so every
 * upload failed with "INVALID_VALUE: texImage2D: no canvas" and the panel
 * rendered empty. 240 chars keeps the texture near 5k px, and anything longer
 * would take minutes to scroll past anyway.
 */
export const MARQUEE_MAX_CHARS = 240;

/** Collapse a task description to a single capped line. */
export function normalizeMarqueeText(text: string): string {
  const normalized = text
    .replace(/[\r\n]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (normalized.length <= MARQUEE_MAX_CHARS) {
    return normalized;
  }
  return `${normalized.slice(0, MARQUEE_MAX_CHARS - 1).trimEnd()}…`;
}
