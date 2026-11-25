/** The Studio's media player, the part of it that's arithmetic: the clock, the bar, the keys.
 *  The player itself is components/portfolio/media-player.tsx, custom controls over a native
 *  <audio>/<video>. Times are seconds, as the media element has them. */

/** Arrow keys move this far, as most players do. */
export const SEEK_STEP_S = 5;
/** Page Up / Page Down move a tenth of the recording. */
const PAGE_FRACTION = 0.1;

/** A recording's length before its metadata loads (`preload="none"` loads nothing until
 *  play), or a time that isn't one. */
const known = (s: number) => Number.isFinite(s) && s >= 0;

/** "0:07", "38:12", "1:02:03". Not a time yet: "0:00". */
export function mediaClock(seconds: number): string {
  const s = known(seconds) ? Math.floor(seconds) : 0;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

/** How far the bar is filled, 0 … 1. Nothing while the length is unknown: the bar shows real
 *  progress or none. */
export function progressOf(current: number, duration: number): number {
  if (!known(duration) || duration === 0 || !known(current)) return 0;
  return Math.min(1, current / duration);
}

const clamp = (t: number, duration: number) =>
  Math.min(duration, Math.max(0, t));

/** Where a key on the bar seeks to, or null for a key the bar leaves alone. The bar is a
 *  slider (role="slider"), so it answers the keys a native one does. */
export function seekFor(
  key: string,
  current: number,
  duration: number,
): number | null {
  if (!known(duration) || duration === 0) return null;
  const page = duration * PAGE_FRACTION;
  switch (key) {
    case "ArrowRight":
    case "ArrowUp":
      return clamp(current + SEEK_STEP_S, duration);
    case "ArrowLeft":
    case "ArrowDown":
      return clamp(current - SEEK_STEP_S, duration);
    case "PageUp":
      return clamp(current + page, duration);
    case "PageDown":
      return clamp(current - page, duration);
    case "Home":
      return 0;
    case "End":
      return duration;
    default:
      return null;
  }
}

/** Where a click or a drag on the bar lands: `x` pixels into a bar `width` wide. */
export function seekAt(x: number, width: number, duration: number): number {
  if (!known(duration) || width <= 0) return 0;
  return clamp((x / width) * duration, duration);
}

/** What the screen reader says for the bar: "1:02 of 38:12". */
export function clockText(current: number, duration: number): string {
  return known(duration) && duration > 0
    ? `${mediaClock(current)} of ${mediaClock(duration)}`
    : mediaClock(current);
}

/** A video gets a picture; everything else plays as audio. */
export function isVideo(mediaType: string | null | undefined): boolean {
  return (mediaType ?? "").startsWith("video/");
}
