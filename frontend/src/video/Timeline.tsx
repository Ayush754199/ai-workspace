import React, { useCallback, useMemo, useRef, useState } from "react";
import { Clip, ProjectState, TRACK_META, TRACK_ORDER, TrackKind, clipEnd, formatTime, timelineDuration } from "./editorState";

interface TimelineProps {
  project: ProjectState;
  dispatch: React.Dispatch<any>;
  onSeek: (time: number) => void;
}

const PX_PER_SECOND = 42;

const Timeline: React.FC<TimelineProps> = ({ project, dispatch, onSeek }) => {
  const laneRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ id: string; mode: "move" | "trim"; originX: number; start: number; duration: number; track: TrackKind } | null>(null);
  const duration = timelineDuration(project);
  const scale = PX_PER_SECOND * project.zoom;
  const width = Math.max(900, duration * scale + 200);

  const secondsFromEvent = useCallback(
    (clientX: number) => {
      const rect = laneRef.current?.getBoundingClientRect();
      if (!rect) return 0;
      return Math.max(0, (clientX - rect.left + laneRef.current!.scrollLeft) / scale);
    },
    [scale],
  );

  const onLaneClick = (event: React.MouseEvent) => {
    if ((event.target as HTMLElement).closest(".vd-clip")) return;
    onSeek(secondsFromEvent(event.clientX));
  };

  const onPointerMove = (event: React.PointerEvent) => {
    if (!drag) return;
    const rect = laneRef.current?.getBoundingClientRect();
    if (!rect) return;
    const delta = (event.clientX - drag.originX) / scale;
    if (drag.mode === "move") {
      dispatch({ type: "moveClip", id: drag.id, start: Number(Math.max(0, drag.start + delta).toFixed(2)) });
    } else {
      const durationNext = Number(Math.max(0.2, drag.duration + delta).toFixed(2));
      dispatch({ type: "trimClip", id: drag.id, start: drag.start, duration: durationNext });
    }
  };

  const endDrag = () => setDrag(null);

  const ticks = useMemo(() => {
    const tickStep = project.zoom >= 2 ? 1 : project.zoom >= 1 ? 2 : project.zoom >= 0.5 ? 4 : 8;
    const list: number[] = [];
    for (let t = 0; t <= duration; t += tickStep) list.push(t);
    return list;
  }, [duration, project.zoom]);

  const waveform = (clip: Clip) => {
    const bars = Math.max(3, Math.min(48, Math.round(clip.duration * 6)));
    return Array.from({ length: bars }, (_, i) => {
      const seed = Math.sin((clip.name.length + 1) * (i + 1) * 1.7);
      return 18 + Math.abs(seed) * 62;
    });
  };

  return (
    <div className="vd-timeline" aria-label="Timeline">
      <div className="vd-ruler-wrap">
        <div className="vd-track-head vd-ruler-head">
          <span className="vd-zoom-in">🔍</span>
          <button onClick={() => dispatch({ type: "setZoom", zoom: project.zoom / 1.5 })} title="Zoom out">−</button>
          <button onClick={() => dispatch({ type: "setZoom", zoom: project.zoom * 1.5 })} title="Zoom in">＋</button>
          <span className="vd-zoom-label">{project.zoom.toFixed(2)}×</span>
        </div>
        <div className="vd-ruler" style={{ width }} onClick={(e) => onSeek(secondsFromEvent(e.clientX))}>
          {ticks.map((t) => (
            <span key={t} className="vd-tick" style={{ left: t * scale }}>
              {String(Math.floor(t / 3600)).padStart(2, "0")}:{String(Math.floor((t % 3600) / 60)).padStart(2, "0")}:{String(Math.floor(t % 60)).padStart(2, "0")}:00
            </span>
          ))}
        </div>
      </div>

      <div
        className="vd-lanes"
        ref={laneRef}
        style={{ width }}
        onClick={onLaneClick}
        onPointerMove={onPointerMove}
        onPointerUp={endDrag}
        onPointerLeave={endDrag}
      >
        <div className="vd-playhead" style={{ left: (project.playhead * scale) + 116 }}>
          <span className="vd-playhead-cap" />
        </div>
        {TRACK_ORDER.map((track) => {
          const meta = TRACK_META[track];
          const clips = project.clips.filter((c) => c.track === track);
          return (
            <div className={`vd-track-row kind-${meta.type.toLowerCase()}`} key={track}>
              <div className="vd-track-head">
                <strong>{track}</strong>
                <span>{meta.type === "Video" ? "🎞" : meta.type === "Audio" ? "♪" : "T"}</span>
                <button title="Lock track" className={meta.locked ? "on" : ""}>🔒</button>
                <button title="Mute track" className={track === "A1" && clips.length ? "on" : ""}>🔇</button>
              </div>
              <div className="vd-lane" style={{ width }}>
                {clips.map((clip) => {
                  const selected = project.selectedClipId === clip.id;
                  const asset = project.media.find((m) => m.id === clip.assetId);
                  return (
                    <div
                      key={clip.id}
                      className={`vd-clip ${selected ? "selected" : ""} ${clip.kind}`}
                      style={{ left: clip.start * scale, width: Math.max(28, clip.duration * scale), background: clip.color }}
                      title={`${clip.name} · ${formatTime(clip.duration)}`}
                      onPointerDown={(e) => {
                        if (e.button !== 0) return;
                        e.stopPropagation();
                        dispatch({ type: "selectClip", id: clip.id });
                        setDrag({ id: clip.id, mode: "move", originX: e.clientX, start: clip.start, duration: clip.duration, track });
                      }}
                    >
                      <span className="vd-clip-label">
                        {clip.text ? <b>𝕿</b> : clip.kind === "audio" ? "♪" : "▣"} {clip.name}
                      </span>
                      {meta.type === "Video" && asset?.thumb && !clip.text && (
                        <span className="vd-clip-thumbs" style={{ backgroundImage: `url(${asset.thumb})` }} />
                      )}
                      {clip.kind === "audio" && (
                        <span className="vd-wave">
                          {waveform(clip).map((h, i) => (
                            <i key={i} style={{ height: `${h}%` }} />
                          ))}
                        </span>
                      )}
                      {clip.transition && <span className="vd-clip-badge">{clip.transition}</span>}
                      <span
                        className="vd-clip-trim"
                        onPointerDown={(e) => {
                          e.stopPropagation();
                          dispatch({ type: "selectClip", id: clip.id });
                          setDrag({ id: clip.id, mode: "trim", originX: e.clientX, start: clip.start, duration: clip.duration, track });
                        }}
                      />
                    </div>
                  );
                })}
                {!clips.length && <span className="vd-lane-empty">Drop {meta.type.toLowerCase()} here</span>}
              </div>
            </div>
          );
        })}
        <div className="vd-timeline-end" style={{ left: duration * scale }}>{formatTime(duration)}</div>
      </div>
    </div>
  );
};

export default Timeline;
