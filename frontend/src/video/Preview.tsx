import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Clip, ProjectState, clipEnd, formatTime, gradeFilter, timelineDuration } from "./editorState";

export interface PreviewHandle {
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (time: number) => void;
}

interface PreviewProps {
  project: ProjectState;
  onPlayhead: (time: number) => void;
  onFrameClick?: () => void;
}

/** Returns the top-most visible video/image clip at a given time. */
function visualClipAt(project: ProjectState, time: number): Clip | undefined {
  const order = ["V3", "V2", "V1"];
  const frames = project.clips
    .filter((c) => order.includes(c.track) && time >= c.start && time < clipEnd(c))
    .sort((a, b) => order.indexOf(a.track) - order.indexOf(b.track));
  return frames[0];
}

function titleAt(project: ProjectState, time: number) {
  return project.clips.find((c) => c.track === "T1" && time >= c.start && time < clipEnd(c));
}

const Preview: React.FC<PreviewProps> = forwardRef<PreviewHandle, PreviewProps>(({ project, onPlayhead }, ref) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const rafRef = useRef<number | null>(null);
  const startedAtRef = useRef(0);
  const baseRef = useRef(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(70);
  const duration = timelineDuration(project);
  const playhead = project.playhead;

  const active = useMemo(() => visualClipAt(project, playhead), [project.clips, playhead]);
  const asset = active ? project.media.find((m) => m.id === active.assetId) : undefined;
  const title = titleAt(project, playhead);
  const filter = gradeFilter(project.color);
  const transform = project.transform;
  const aspect = project.exportSettings.aspect;
  const isPlayingAsset = Boolean(asset?.url && active);

  // transport clock (keeps ticking even when the source has no media URL)
  useEffect(() => {
    if (!playing) {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
      return;
    }
    baseRef.current = playhead;
    startedAtRef.current = performance.now();
    const tick = () => {
      const next = baseRef.current + (performance.now() - startedAtRef.current) / 1000;
      if (next >= duration) {
        onPlayhead(duration);
        setPlaying(false);
        return;
      }
      onPlayhead(next);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing]);

  useEffect(() => {
    if (!playing && videoRef.current) videoRef.current.pause();
  }, [playing]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video || !active || !isPlayingAsset) return;
    const offset = playhead - active.start + active.inPoint;
    if (Math.abs(video.currentTime - offset) > 0.35) video.currentTime = Math.min(offset, video.duration || offset);
    if (playing && video.paused) video.play().catch(() => undefined);
    if (!playing && !video.paused) video.pause();
  }, [playhead, active, playing, isPlayingAsset]);

  useImperativeHandle(ref, () => ({
    play: () => setPlaying(true),
    pause: () => setPlaying(false),
    toggle: () => setPlaying((p) => !p),
    seek: (time: number) => onPlayhead(time),
  }));

  const step = (delta: number) => onPlayhead(Math.min(duration, Math.max(0, playhead + delta)));
  const clipStart = active ? active.start : 0;
  const remaining = Math.max(0, duration - playhead);

  return (
    <section className="vd-monitor" aria-label="Program monitor">
      <div className={`vd-stage aspect-${aspect.replace(":", "x")}`}>
        {asset?.url && active ? (
          asset.kind === "image" ? (
            <img src={asset.url} alt={active.name} style={{ filter, transform: `scale(${transform.scaleX / 100}, ${transform.scaleY / 100}) rotate(${transform.rotate}deg) translate(${transform.positionX}px, ${transform.positionY}px)` }} />
          ) : (
            <video
              ref={videoRef}
              src={asset.url}
              style={{ filter, transform: `scale(${transform.scaleX / 100}, ${transform.scaleY / 100}) rotate(${transform.rotate}deg) translate(${transform.positionX}px, ${transform.positionY}px)` }}
              muted={muted}
              playsInline
              preload="metadata"
            />
          )
        ) : (
          <div className="vd-stage-empty">
            <span className="vd-stage-globe">◐</span>
            <p>Import media to start your edit</p>
            <small>Use “Import” in Project Media for videos, audio, images or an AI-generated clip.</small>
          </div>
        )}
        {title?.text && <div className="vd-title-overlay">{title.text}</div>}
        <div className="vd-stage-time">{formatTime(remaining)}</div>
        <div className="vd-timestamp">
          <strong>{formatTime(playhead)}</strong> / {formatTime(duration)}
        </div>
      </div>

      <div className="vd-transport">
        <div className="vd-scrub">
          <span className="vd-scrub-mark" style={{ left: `${(playhead / duration) * 100}%` }} />
          <input
            type="range"
            aria-label="Timeline scrub"
            min={0}
            max={duration}
            step={0.01}
            value={playhead}
            onChange={(e) => onPlayhead(Number(e.target.value))}
          />
        </div>
        <div className="vd-transport-row">
          <button title="Go to start" onClick={() => onPlayhead(0)}>⏮</button>
          <button title="Back 1s" onClick={() => step(-1)}>◀</button>
          <button className="vd-play" title={playing ? "Pause" : "Play"} onClick={() => setPlaying((p) => !p)}>{playing ? "❚❚" : "▶"}</button>
          <button title="Forward 1s" onClick={() => step(1)}>▶</button>
          <button title="Go to end" onClick={() => onPlayhead(duration)}>⏭</button>
          <span className="vd-transport-gap" />
          <span className="vd-inline-select">16:9 ▾</span>
          <button title="Snap to clip start" onClick={() => onPlayhead(clipStart)}>✧</button>
          <span className="vd-transport-gap" />
          <button title="Export frame" className="vd-ghost-badge">▣</button>
          <button title={muted ? "Unmute" : "Mute"} onClick={() => setMuted((m) => !m)}>{muted ? "🔇" : "🔊"}</button>
          <input
            className="vd-volume"
            type="range"
            aria-label="Preview volume"
            min={0}
            max={100}
            value={volume}
            onChange={(e) => setVolume(Number(e.target.value))}
          />
          <button title="Fit to screen" onClick={() => onPlayhead(playhead)} className="vd-add">＋</button>
        </div>
      </div>
    </section>
  );
});

Preview.displayName = "Preview";
export default Preview;
