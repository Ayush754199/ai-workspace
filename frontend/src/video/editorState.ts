/**
 * Local project model for the Video Editor workspace.
 *
 * Everything the UI shows (media bin, tracks, clips, colour grade, export
 * settings) lives in this one reducer so toolbar actions, the timeline, the
 * inspector and the export panel all read and write the same state.
 * The project is persisted to localStorage per user, so a reload keeps work.
 */

export type MediaKind = "video" | "audio" | "image";

export interface MediaAsset {
  id: string;
  name: string;
  kind: MediaKind;
  duration: number;
  url: string;
  objectUrl: boolean;
  favorite: boolean;
  thumb?: string;
}

export type TrackKind = "V3" | "V2" | "V1" | "T1" | "A1" | "A2" | "A3";

export interface Clip {
  id: string;
  assetId: string;
  name: string;
  kind: MediaKind;
  track: TrackKind;
  start: number;
  duration: number;
  /** offset into the source asset */
  inPoint: number;
  color: string;
  /** volume 0..100 for audio clips */
  volume?: number;
  /** text overlays / titles */
  text?: string;
  /** visual transition out of this clip */
  transition?: string;
  /** playback rate, 1 = normal */
  speed?: number;
}

export interface ColorGrade {
  contrast: number;
  saturation: number;
  vibrance: number;
  temperature: number;
  tint: number;
  exposure: number;
  gamma: number;
  lift: number;
  gain: number;
  offset: number;
}

export interface Transform {
  scaleX: number;
  scaleY: number;
  positionX: number;
  positionY: number;
  rotate: number;
  anchorX: number;
  anchorY: number;
  stabilize: boolean;
  motionTracking: boolean;
  chromaKey: boolean;
}

export interface ExportSettings {
  format: "MP4" | "MOV" | "WebM" | "GIF";
  resolution: "720p" | "1080p" | "2K" | "4K";
  frameRate: "24" | "30" | "60";
  bitrate: string;
  quality: number;
  aspect: "16:9" | "9:16" | "1:1";
}

export interface MixerState {
  Video: number;
  Music: number;
  Voiceover: number;
  SFX: number;
  Master: number;
}

export interface ProjectState {
  name: string;
  media: MediaAsset[];
  clips: Clip[];
  playhead: number;
  zoom: number;
  selectedClipId: string | null;
  activeCollection: string;
  color: ColorGrade;
  transform: Transform;
  exportSettings: ExportSettings;
  mixer: MixerState;
  durationOverride: number | null;
}

export const TRACK_ORDER: TrackKind[] = ["V3", "V2", "V1", "T1", "A1", "A2", "A3"];

export const TRACK_META: Record<TrackKind, { type: "Video" | "Title" | "Audio"; locked: boolean; muted: boolean }> = {
  V3: { type: "Video", locked: false, muted: false },
  V2: { type: "Video", locked: false, muted: false },
  V1: { type: "Video", locked: false, muted: false },
  T1: { type: "Title", locked: false, muted: false },
  A1: { type: "Audio", locked: false, muted: false },
  A2: { type: "Audio", locked: false, muted: false },
  A3: { type: "Audio", locked: false, muted: false },
};

const CLIP_COLORS: Record<string, string> = {
  V1: "#2f5d8c",
  V2: "#2f5d8c",
  V3: "#7a4a8f",
  T1: "#4b3a8c",
  A1: "#2b7a4b",
  A2: "#2b6f6a",
  A3: "#2b4f7a",
};

export const defaultColor: ColorGrade = {
  contrast: 12,
  saturation: 18,
  vibrance: 10,
  temperature: 2,
  tint: -3,
  exposure: 0,
  gamma: 0,
  lift: 0,
  gain: 0,
  offset: 0,
};

export const defaultTransform: Transform = {
  scaleX: 100,
  scaleY: 100,
  positionX: 0,
  positionY: 0,
  rotate: 0,
  anchorX: 50,
  anchorY: 50,
  stabilize: false,
  motionTracking: false,
  chromaKey: false,
};

export const defaultExport: ExportSettings = {
  format: "MP4",
  resolution: "1080p",
  frameRate: "30",
  bitrate: "High (Recommended)",
  quality: 82,
  aspect: "16:9",
};

export const defaultMixer: MixerState = { Video: 72, Music: 60, Voiceover: 88, SFX: 64, Master: 80 };

export function createProject(name = "Untitled project"): ProjectState {
  return {
    name,
    media: [],
    clips: [],
    playhead: 0,
    zoom: 1,
    selectedClipId: null,
    activeCollection: "all",
    color: { ...defaultColor },
    transform: { ...defaultTransform },
    exportSettings: { ...defaultExport },
    mixer: { ...defaultMixer },
    durationOverride: null,
  };
}

export const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export const round = (value: number, digits = 2) => Number(value.toFixed(digits));

export function uid(prefix: string) {
  return `${prefix}_${Math.random().toString(36).slice(2, 10)}`;
}

export function timelineDuration(project: ProjectState) {
  const end = project.clips.reduce((max, clip) => Math.max(max, clip.start + clip.duration), 0);
  return Math.max(project.durationOverride ?? 0, end, 30);
}

export function clipEnd(clip: Clip) {
  return clip.start + clip.duration;
}

export function formatTime(seconds: number) {
  const total = Math.max(0, seconds);
  const hh = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = Math.floor(total % 60);
  const ff = Math.floor((total - Math.floor(total)) * 100);
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}:${String(ff).padStart(2, "0")}`;
}

export function clipColor(track: TrackKind) {
  return CLIP_COLORS[track] ?? "#2f5d8c";
}

/** CSS filter that mirrors the colour-grade sliders so the preview is truthful. */
export function gradeFilter(color: ColorGrade) {
  const contrast = 1 + color.contrast / 100;
  const saturation = 1 + color.saturation / 100;
  const brightness = 1 + color.exposure / 100;
  const hue = color.tint / 4;
  return `contrast(${round(clamp(contrast, 0.2, 3))}) saturate(${round(clamp(saturation, 0, 3))}) brightness(${round(clamp(brightness, 0.4, 2))}) hue-rotate(${round(hue, 1)}deg)`;
}

function pickTrackFor(kind: MediaKind, clips: Clip[]): TrackKind {
  if (kind === "audio") {
    const audio = ["A1", "A2", "A3"] as const;
    return audio[Math.min(audio.length - 1, clips.filter((c) => audio.includes(c.track as typeof audio[number])).length)] ?? "A2";
  }
  const video = ["V1", "V2", "V3"] as const;
  return video.find((t) => !clips.some((c) => c.track === t)) ?? "V2";
}

export type EditorAction =
  | { type: "load"; project: ProjectState }
  | { type: "addMedia"; assets: MediaAsset[] }
  | { type: "removeMedia"; id: string }
  | { type: "toggleFavorite"; id: string }
  | { type: "setCollection"; collection: string }
  | { type: "appendClip"; assetId: string }
  | { type: "addTitle"; text: string }
  | { type: "updateClip"; id: string; patch: Partial<Clip> }
  | { type: "moveClip"; id: string; start: number; track?: TrackKind }
  | { type: "trimClip"; id: string; start: number; duration: number }
  | { type: "splitClip"; id: string; at: number }
  | { type: "rippleDelete"; id: string }
  | { type: "rotateClip"; id: string; direction: 1 | -1 }
  | { type: "addTransition"; id: string; transition: string }
  | { type: "selectClip"; id: string | null }
  | { type: "setPlayhead"; at: number }
  | { type: "setZoom"; zoom: number }
  | { type: "setColor"; patch: Partial<ColorGrade> }
  | { type: "setTransform"; patch: Partial<Transform> }
  | { type: "setExport"; patch: Partial<ExportSettings> }
  | { type: "setMixer"; patch: Partial<MixerState> }
  | { type: "setProjectName"; name: string }
  | { type: "duplicateClip"; id: string };

export function editorReducer(state: ProjectState, action: EditorAction): ProjectState {
  switch (action.type) {
    case "load":
      return { ...createProject(), ...action.project };
    case "addMedia":
      return { ...state, media: [...action.assets, ...state.media] };
    case "removeMedia":
      return {
        ...state,
        media: state.media.filter((m) => m.id !== action.id),
        clips: state.clips.filter((c) => c.assetId !== action.id),
      };
    case "toggleFavorite":
      return { ...state, media: state.media.map((m) => (m.id === action.id ? { ...m, favorite: !m.favorite } : m)) };
    case "setCollection":
      return { ...state, activeCollection: action.collection };
    case "appendClip": {
      const asset = state.media.find((m) => m.id === action.assetId);
      if (!asset) return state;
      const track = pickTrackFor(asset.kind, state.clips);
      const trackClips = state.clips.filter((c) => c.track === track);
      const start = trackClips.reduce((max, clip) => Math.max(max, clipEnd(clip)), 0);
      const clip: Clip = {
        id: uid("clip"),
        assetId: asset.id,
        name: asset.name,
        kind: asset.kind,
        track,
        start,
        duration: asset.kind === "image" ? 5 : Math.min(asset.duration || 5, 600),
        inPoint: 0,
        color: clipColor(track),
        volume: asset.kind === "audio" ? 100 : undefined,
      };
      return { ...state, clips: [...state.clips, clip], selectedClipId: clip.id };
    }
    case "addTitle": {
      const trackClips = state.clips.filter((c) => c.track === "T1");
      const start = trackClips.reduce((max, clip) => Math.max(max, clipEnd(clip)), 0);
      const clip: Clip = {
        id: uid("title"),
        assetId: "",
        name: action.text,
        text: action.text,
        kind: "image",
        track: "T1",
        start,
        duration: 4,
        inPoint: 0,
        color: clipColor("T1"),
      };
      return { ...state, clips: [...state.clips, clip], selectedClipId: clip.id };
    }
    case "updateClip":
      return { ...state, clips: state.clips.map((c) => (c.id === action.id ? { ...c, ...action.patch } : c)) };
    case "moveClip":
      return {
        ...state,
        clips: state.clips.map((c) =>
          c.id === action.id ? { ...c, start: Math.max(0, action.start), track: action.track ?? c.track, color: action.track ? clipColor(action.track) : c.color } : c,
        ),
      };
    case "trimClip":
      return {
        ...state,
        clips: state.clips.map((c) =>
          c.id === action.id ? { ...c, start: Math.max(0, action.start), duration: Math.max(0.2, action.duration) } : c,
        ),
      };
    case "splitClip": {
      const clip = state.clips.find((c) => c.id === action.id);
      if (!clip) return state;
      const offset = action.at - clip.start;
      if (offset <= 0.1 || offset >= clip.duration - 0.1) return state;
      const left: Clip = { ...clip, duration: round(offset) };
      const right: Clip = {
        ...clip,
        id: uid("clip"),
        start: round(action.at),
        duration: round(clip.duration - offset),
        inPoint: round(clip.inPoint + offset),
      };
      return { ...state, clips: [...state.clips.filter((c) => c.id !== clip.id), left, right], selectedClipId: right.id };
    }
    case "rippleDelete": {
      const clip = state.clips.find((c) => c.id === action.id);
      if (!clip) return state;
      const gap = clip.duration;
      return {
        ...state,
        clips: state.clips
          .filter((c) => c.id !== action.id)
          .map((c) => (c.track === clip.track && c.start > clip.start ? { ...c, start: round(c.start - gap) } : c)),
        selectedClipId: null,
      };
    }
    case "rotateClip": {
      const clip = state.clips.find((c) => c.id === action.id);
      if (!clip || clip.track !== "V1") return state;
      const siblings = state.clips.filter((c) => c.track === "V1").sort((a, b) => a.start - b.start);
      const index = siblings.findIndex((c) => c.id === clip.id);
      if (index < 0) return state;
      const target = siblings[index + action.direction];
      if (!target) return state;
      // swap positions while keeping the timeline order
      const clips = state.clips.map((c) => {
        if (c.id === clip.id) return { ...c, start: target.start };
        if (c.id === target.id) return { ...c, start: clip.start };
        return c;
      });
      return { ...state, clips };
    }
    case "addTransition":
      return { ...state, clips: state.clips.map((c) => (c.id === action.id ? { ...c, transition: action.transition } : c)) };
    case "selectClip":
      return { ...state, selectedClipId: action.id };
    case "setPlayhead":
      return { ...state, playhead: Math.max(0, action.at) };
    case "setZoom":
      return { ...state, zoom: clamp(action.zoom, 0.25, 5) };
    case "setColor":
      return { ...state, color: { ...state.color, ...action.patch } };
    case "setTransform":
      return { ...state, transform: { ...state.transform, ...action.patch } };
    case "setExport":
      return { ...state, exportSettings: { ...state.exportSettings, ...action.patch } };
    case "setMixer":
      return { ...state, mixer: { ...state.mixer, ...action.patch } };
    case "setProjectName":
      return { ...state, name: action.name };
    case "duplicateClip": {
      const clip = state.clips.find((c) => c.id === action.id);
      if (!clip) return state;
      const copy: Clip = { ...clip, id: uid("clip"), start: round(clipEnd(clip)) };
      return { ...state, clips: [...state.clips, copy], selectedClipId: copy.id };
    }
    default:
      return state;
  }
}

export function storageKey(userKey: string | number) {
  return `video-project:${userKey}`;
}

export function saveProject(userKey: string | number, project: ProjectState) {
  const serializable = {
    ...project,
    // blob/data URLs die on reload; keep the entry but drop the URL so the UI
    // does not show a broken preview after a refresh.
    media: project.media.map((m) => ({ ...m, url: m.objectUrl ? "" : m.url, objectUrl: m.objectUrl })),
  };
  try {
    localStorage.setItem(storageKey(userKey), JSON.stringify(serializable));
  } catch {
    // storage full or disabled — editing still works for the session
  }
}

export function loadProject(userKey: string | number): ProjectState | null {
  try {
    const raw = localStorage.getItem(storageKey(userKey));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ProjectState;
    if (!parsed || !Array.isArray(parsed.media)) return null;
    return { ...createProject(), ...parsed, media: parsed.media.map((m) => ({ ...m, url: m.objectUrl ? "" : m.url })) };
  } catch {
    return null;
  }
}
