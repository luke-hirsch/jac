import {
  useRef,
  useState,
  type PointerEvent,
  type SyntheticEvent,
} from "react";
import { MaximizeIcon, PauseIcon, PlayIcon } from "lucide-react";
import { anchorId } from "@/lib/portfolio/content";
import {
  clockText,
  isVideo,
  mediaClock,
  progressOf,
  seekAt,
  seekFor,
} from "@/lib/portfolio/media";
import type { PortfolioItem } from "@/lib/queries/portfolio";

type Fullscreenable = HTMLVideoElement & { webkitEnterFullscreen?: () => void };

/** A media block: the owner's own recording, uploaded to jac and played from this site, never an
 *  embed (an embed loads a third party for every visitor). Custom controls over the native
 *  element. Nothing loads until play (`preload="none"`), the bar is a slider that shows how much
 *  has really played, and every control works from the keyboard. No waveform: there's no data
 *  for one, and a drawn one would be decoration posing as information. */
export function MediaPlayer({ item }: { item: PortfolioItem }) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const [current, setCurrent] = useState(0);
  const [duration, setDuration] = useState(Number.NaN);
  const [failed, setFailed] = useState(false);

  const video = isVideo(item.media_type);
  const el = () => (video ? videoRef.current : audioRef.current);
  const name = item.title || (video ? "the video" : "the recording");
  const descId = item.alt_text ? `${anchorId(item.id)}-desc` : undefined;
  const ready = Number.isFinite(duration) && duration > 0;

  const toggle = () => {
    const m = el();
    if (!m) return;
    // A play() cut short by a pause rejects; a real failure arrives as the element's error.
    if (m.paused) m.play().catch(() => {});
    else m.pause();
  };
  const seek = (t: number) => {
    const m = el();
    if (!m) return;
    m.currentTime = t;
    setCurrent(t);
  };
  const seekPointer = (e: PointerEvent<HTMLDivElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    seek(seekAt(e.clientX - box.left, box.width, duration));
  };
  const fullscreen = () => {
    const v = videoRef.current as Fullscreenable | null;
    if (v?.requestFullscreen) v.requestFullscreen().catch(() => {});
    else v?.webkitEnterFullscreen?.(); // iPhone Safari has only its own
  };

  // The same props on <audio> and <video>: the element's own state is the truth.
  type MediaEvent = SyntheticEvent<HTMLMediaElement>;
  const events = {
    src: item.media_url ?? undefined,
    preload: "none" as const,
    onPlay: () => setPlaying(true),
    onPause: () => setPlaying(false),
    onEnded: () => setPlaying(false),
    onTimeUpdate: (e: MediaEvent) => setCurrent(e.currentTarget.currentTime),
    onLoadedMetadata: (e: MediaEvent) => setDuration(e.currentTarget.duration),
    onDurationChange: (e: MediaEvent) => setDuration(e.currentTarget.duration),
    onError: () => setFailed(true),
  };

  return (
    <div className="flex flex-col gap-5">
      {video ? (
        <video
          ref={videoRef}
          {...events}
          poster={item.image_url ?? undefined}
          playsInline
          onClick={toggle}
          aria-label={item.title || "Video"}
          aria-describedby={descId}
          className="aspect-video w-full cursor-pointer rounded-xl bg-black object-contain"
        />
      ) : (
        <audio ref={audioRef} {...events} />
      )}

      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={toggle}
          aria-label={`${playing ? "Pause" : "Play"} ${name}`}
          aria-describedby={descId}
          className="flex size-[60px] shrink-0 items-center justify-center rounded-full bg-studio-accent text-studio-ink"
        >
          {playing ? (
            <PauseIcon className="size-6" fill="currentColor" aria-hidden />
          ) : (
            <PlayIcon
              className="size-6 translate-x-0.5"
              fill="currentColor"
              aria-hidden
            />
          )}
        </button>
        {!video && item.image_url ? (
          <img
            src={item.image_url}
            alt=""
            loading="lazy"
            className="size-14 shrink-0 rounded-lg object-cover"
          />
        ) : null}
        <div className="flex min-w-0 flex-1 flex-col gap-0.5">
          {item.title ? (
            <h3 className="font-display text-lg leading-snug font-semibold">
              {item.title}
            </h3>
          ) : null}
          {item.body ? (
            <p className="text-sm text-muted-foreground">{item.body}</p>
          ) : null}
        </div>
        {video ? (
          <button
            type="button"
            onClick={fullscreen}
            aria-label="Full screen"
            className="flex size-11 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:text-foreground"
          >
            <MaximizeIcon className="size-5" aria-hidden />
          </button>
        ) : null}
      </div>

      <div
        role="slider"
        tabIndex={0}
        aria-label={`Position in ${name}`}
        aria-valuemin={0}
        aria-valuemax={ready ? Math.floor(duration) : 0}
        aria-valuenow={Math.floor(current)}
        aria-valuetext={clockText(current, duration)}
        aria-disabled={!ready}
        onKeyDown={(e) => {
          const t = seekFor(e.key, current, duration);
          if (t === null) return;
          e.preventDefault();
          seek(t);
        }}
        onPointerDown={(e) => {
          if (!ready) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          seekPointer(e);
        }}
        onPointerMove={(e) => {
          if (e.currentTarget.hasPointerCapture(e.pointerId)) seekPointer(e);
        }}
        className="flex h-6 cursor-pointer touch-none items-center rounded-full aria-disabled:cursor-default"
      >
        <span className="h-1.5 w-full overflow-hidden rounded-full bg-studio-raise">
          <span
            className="block h-full rounded-full bg-studio-accent"
            style={{ width: `${progressOf(current, duration) * 100}%` }}
          />
        </span>
      </div>
      <div className="-mt-3 flex justify-between text-[13px] text-muted-foreground tabular-nums">
        <span>{mediaClock(current)}</span>
        <span>{ready ? mediaClock(duration) : "–:––"}</span>
      </div>

      {failed ? (
        <p role="status" className="text-sm text-muted-foreground">
          This recording can't be played right now.
        </p>
      ) : null}
      {item.alt_text ? (
        <p id={descId} className="sr-only">
          {item.alt_text}
        </p>
      ) : null}
    </div>
  );
}
