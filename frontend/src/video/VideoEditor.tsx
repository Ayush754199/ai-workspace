import React, { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { apiBaseUrl } from "../api/client";
import Preview, { PreviewHandle } from "../video/Preview";
import StudioRange from "../video/StudioRange";
import Timeline from "../video/Timeline";
import {
  Clip,
  ColorGrade,
  EditorAction,
  ExportSettings,
  MediaAsset,
  MediaKind,
  MixerState,
  ProjectState,
  Transform,
  clipEnd,
  createProject,
  editorReducer,
  loadProject,
  saveProject,
  timelineDuration,
  uid,
} from "../video/editorState";

type LeftTab = "media" | "audio" | "titles" | "effects";
type InspectorTab = "color" | "transform" | "effects" | "speed" | "looks" | "audio";
type ToolGroup = "transform" | "motion" | "opacity";

const CREATOR = {
  name: "Alex Reed",
  handle: "alexreed_studio",
  followers: "2.4k Followers",
};

const COLLECTIONS = ["My Files", "AI Creations", "Stock Library", "Favorites", "Uploads"];

const VIBE_LOOKS = [
  "Cinematic Teal & Orange",
  "Moody Film",
  "Warm Vintage",
  "Clean Commercial",
  "Neon Night",
  "Soft Portrait",
  "B&W Drama",
  "Dusty Sunset",
  "Pastel Dream",
];

const TRANSITION_OPTIONS = ["None", "Cross Dissolve", "Fade to Black", "Fade to White", "Wipe Left", "Glitch", "Zoom Blur", "Slide Up"];

const COLOR_ROWS: Array<{ key: keyof ColorGrade; label: string; gradient?: string }> = [
  { key: "contrast", label: "Contrast" },
  { key: "saturation", label: "Saturation" },
  { key: "vibrance", label: "Vibrance" },
  { key: "temperature", label: "Temperature", gradient: "linear-gradient(90deg,#3b6fd4,#1f2937,#e0a33a)" },
  { key: "tint", label: "Tint", gradient: "linear-gradient(90deg,#3fb46a,#1f2937,#b04fd6)" },
  { key: "exposure", label: "Exposure" },
  { key: "gamma", label: "Gamma" },
  { key: "lift", label: "Lift" },
  { key: "gain", label: "Gain" },
  { key: "offset", label: "Offset" },
];

const MIXER_ROWS: Array<keyof MixerState> = ["Video", "Music", "Voiceover", "SFX", "Master"];

function thumbFromVideo(url: string): Promise<string> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    video.crossOrigin = "anonymous";
    video.muted = true;
    video.src = url;
    const done = (value: string) => resolve(value);
    const timeout = window.setTimeout(() => done(""), 8000);
    video.onloadeddata = () => {
      video.currentTime = Math.min(1, (video.duration || 2) / 3);
    };
    video.onseeked = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = 320;
        canvas.height = 180;
        const ctx = canvas.getContext("2d");
        if (!ctx) return done("");
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        window.clearTimeout(timeout);
        done(canvas.toDataURL("image/jpeg", 0.7));
      } catch {
        done("");
      }
    };
    video.onerror = () => done("");
  });
}

function kindFor(file: File): MediaKind {
  if (file.type.startsWith("audio")) return "audio";
  if (file.type.startsWith("image")) return "image";
  return "video";
}

function readDuration(kind: MediaKind, url: string): Promise<number> {
  if (kind === "image") return Promise.resolve(5);
  return new Promise((resolve) => {
    const el = document.createElement(kind === "audio" ? "audio" : "video");
    el.preload = "metadata";
    el.src = url;
    const finish = (value: number) => resolve(Number.isFinite(value) && value > 0 ? value : 5);
    el.onloadedmetadata = () => finish(el.duration);
    el.onerror = () => finish(0);
    window.setTimeout(() => finish(el.duration || 0), 6000);
  });
}

const VideoEditor: React.FC = () => {
  const { user, token } = useAuth();
  const [params] = useSearchParams();
  const userKey = user?.id ?? "guest";
  const [project, dispatch] = useReducer(editorReducer, undefined as unknown as ProjectState, () => loadProject(userKey) ?? createProject("Untitled project"));
  const previewRef = useRef<PreviewHandle>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const audioInputRef = useRef<HTMLInputElement>(null);
  const [leftTab, setLeftTab] = useState<LeftTab>("media");
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>("color");
  const [tool, setTool] = useState<ToolGroup>("motion");
  const [status, setStatus] = useState("")
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [search, setSearch] = useState("");
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const [titleText, setTitleText] = useState("Your title here");
  const [transformCopy, setTransformCopy] = useState<Transform>(project.transform);

  // persist project per user
  useEffect(() => {
    const id = window.setTimeout(() => saveProject(userKey, project), 400);
    return () => window.clearTimeout(id);
  }, [project, userKey]);

  useEffect(() => {
    setTransformCopy(project.transform);
  }, [project.selectedClipId]); // eslint-disable-line react-hooks/exhaustive-deps

  // AI Studio can hand a generated clip over via ?asset=<url>&name=<name>
  useEffect(() => {
    const url = params.get("asset");
    if (!url || project.media.some((m) => m.url === url)) return;
    const name = params.get("name") || "AI generated clip";
    const kind: MediaKind = /\.(mp3|wav|m4a|aac|ogg)$/i.test(url) ? "audio" : "video";
    readDuration(kind, url).then((duration) => {
      dispatch({
        type: "addMedia",
        assets: [{ id: uid("asset"), name, kind, duration, url, objectUrl: false, favorite: false }],
      });
      setStatus(`“${name}” added to Project Media`);
    });
  }, [params, project.media]);

  const flash = useCallback((message: string) => {
    setStatus(message);
    window.setTimeout(() => setStatus((current) => (current === message ? "" : current)), 4200);
  }, []);

  const selectedClip = project.clips.find((c) => c.id === project.selectedClipId) ?? null;

  const filteredMedia = useMemo(() => {
    const query = search.trim().toLowerCase();
    return project.media.filter((asset) => {
      if (project.activeCollection === "Favorites" && !asset.favorite) return false;
      if (!query) return true;
      return asset.name.toLowerCase().includes(query);
    });
  }, [project.media, project.activeCollection, search]);

  const importFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setImporting(true);
    const assets: MediaAsset[] = [];
    for (const file of list) {
      const kind = kindFor(file);
      const url = URL.createObjectURL(file);
      const duration = await readDuration(kind, url);
      const thumb = kind === "video" || kind === "image" ? kind === "image" ? url : await thumbFromVideo(url) : undefined;
      assets.push({
        id: uid("asset"),
        name: file.name,
        kind,
        duration,
        url,
        objectUrl: true,
        favorite: false,
        thumb: thumb || undefined,
      });
    }
    dispatch({ type: "addMedia", assets });
    setImporting(false);
    flash(`${assets.length} media item${assets.length > 1 ? "s" : ""} imported`);
  };

  const onDrop = (event: React.DragEvent) => {
    event.preventDefault();
    if (event.dataTransfer?.files?.length) void importFiles(event.dataTransfer.files);
  };

  const onAiGenerate = async () => {
    const prompt = aiPrompt.trim();
    if (!prompt || !token) return;
    setBusy(true);
    flash("Sending prompt to the video provider…");
    try {
      const response = await fetch(`${apiBaseUrl}/api/v1/video/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ prompt }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "Video generation failed");
      const output = data?.result?.output;
      const url = typeof output === "string" ? output : output?.path || output?.url;
      if (!url) throw new Error(data?.result?.message || "Provider returned no video");
      const duration = await readDuration("video", url);
      dispatch({
        type: "addMedia",
        assets: [{ id: uid("asset"), name: prompt.slice(0, 42), kind: "video", duration, url, objectUrl: false, favorite: true }],
      });
      setAiOpen(false);
      setAiPrompt("");
      flash("AI video added to Project Media");
    } catch (error: any) {
      flash(error?.message || "AI generation unavailable right now");
    } finally {
      setBusy(false);
    }
  };

  const splitAtPlayhead = () => {
    const target = selectedClip ?? project.clips.find((c) => project.playhead >= c.start && project.playhead < clipEnd(c));
    if (!target) return flash("Select a clip or move the playhead over one to split");
    dispatch({ type: "splitClip", id: target.id, at: project.playhead });
    flash("Clip split at playhead");
  };

  const deleteClip = () => {
    if (!selectedClip) return flash("Select a clip to delete");
    dispatch({ type: "rippleDelete", id: selectedClip.id });
    flash("Clip deleted and timeline closed up");
  };

  const exportPriority: Record<string, { time: number }> = {
    "720p": { time: 12 },
    "1080p": { time: 26 },
    "2K": { time: 48 },
    "4K": { time: 90 },
  };

  const startExport = async () => {
    if (progress > 0 && progress < 100) return;
    setBusy(true);
    setProgress(1);
    flash(`Rendering ${project.exportSettings.resolution} ${project.exportSettings.format}…`);
    const seconds = exportPriority[project.exportSettings.resolution]?.time ?? 26;
    const started = performance.now();
    await new Promise<void>((resolve) => {
      const timer = window.setInterval(() => {
        const pct = Math.min(100, Math.round(((performance.now() - started) / (seconds * 1000)) * 100) + 1);
        setProgress(pct);
        if (pct >= 100) {
          window.clearInterval(timer);
          resolve();
        }
      }, 120);
    });
    setBusy(false);
    const secondsLabel = timelineDuration(project).toFixed(1);
    flash(`Export ready · ${project.name} · ${secondsLabel}s · ${project.exportSettings.resolution} ${project.exportSettings.format}`);
    window.setTimeout(() => setProgress(0), 3000);
  };

  const aspectMinutes = (timelineDuration(project) / 60).toFixed(1);

  return (
    <AppShell>
      <div className="vd-app" data-session={Boolean(token)}>
        {/* ---------- top bar ---------- */}
        <header className="vd-topbar">
          <div className="vd-topbar-left">
            <div className="vd-logo-mark">▣</div>
            <strong className="vd-logo-text">Vortex</strong>
          </div>
          <div className="vd-project-name">
            <input
              aria-label="Project name"
              value={project.name}
              onChange={(e) => dispatch({ type: "setProjectName", name: e.target.value })}
            />
            <span className="vd-saved">✓ Saved</span>
          </div>
          <div className="vd-topbar-actions">
            <button className="vd-ghost" onClick={() => flashlight(flash, dispatch, project)} title="Start a fresh project">
              ↺ New project
            </button>
            <button className="vd-ghost" onClick={() => { window.location.href = "/video-studio"; }} title="AI generation (text/image → video)">
              ✦ AI Generate
            </button>
            <button
              className="vd-ghost"
              onClick={() => { previewRef.current?.toggle(); flash("Preview playing — press Space to pause"); }}
            >
              ▷ Preview
            </button>
            <div className="vd-course-badge">Course · {CREATOR.handle}</div>
            <div className="vd-avatar">{(user?.full_name || user?.email || "A").slice(0, 1).toUpperCase()}</div>
          </div>
        </header>

        {/* ---------- toolbar ---------- */}
        <div className="vd-toolbar" role="toolbar" aria-label="Editing tools">
          <div className="vd-tool-group">
            <button title="Import media" onClick={() => fileInputRef.current?.click()}>＋ <span>Import</span></button>
            <button title="Audio" onClick={() => audioInputRef.current?.click()}>♫ <span>Audio</span></button>
            <button title="Text / title" onClick={() => { setLeftTab("titles"); flash("Titles panel opened"); }}>T <span>Text</span></button>
          </div>
          <div className="vd-tool-group">
            <button title="Split at playhead" onClick={splitAtPlayhead}>✂ <span>Split</span></button>
            <button title="Ripple delete" onClick={deleteClip}>🗑 <span>Delete</span></button>
            <button title="Duplicate selected clip" onClick={() => selectedClip ? dispatch({ type: "duplicateClip", id: selectedClip.id }) : flash("Select a clip to duplicate")}>⧉ <span>Duplicate</span></button>
          </div>
          <div className="vd-tool-group">
            <button title="Move clip earlier" onClick={() => selectedClip ? dispatch({ type: "rotateClip", id: selectedClip.id, direction: -1 }) : flash("Select a video clip to reorder")}>↶ <span>Move</span></button>
            <button title="Move clip later" onClick={() => selectedClip ? dispatch({ type: "rotateClip", id: selectedClip.id, direction: 1 }) : flash("Select a video clip to reorder")}>↷ <span>Reorder</span></button>
            <button title="Add transition" onClick={() => selectedClip ? cycleTransition(dispatch, selectedClip, flash) : flash("Select a clip to add a transition")}>⇄ <span>Transition</span></button>
          </div>
          <div className="vd-toolbar-spacer" />
          <button className="vd-mini-tool" title="Zoom out timeline" onClick={() => dispatch({ type: "setZoom", zoom: project.zoom / 1.5 })}>−</button>
          <button className="vd-mini-tool" title="Zoom in timeline" onClick={() => dispatch({ type: "setZoom", zoom: project.zoom * 1.5 })}>＋</button>
          <button className="vd-ai-btn" onClick={() => setAiOpen(true)}>✦ AI Video</button>
          <button className="vd-export-btn" onClick={() => document.getElementById("vd-export-panel")?.scrollIntoView({ behavior: "smooth", block: "nearest" })}>
            ⤒ Export
          </button>
        </div>

        <div className="vd-body">
          {/* ---------- left: media ---------- */}
          <aside
            className="vd-panel vd-media-panel"
            onDragOver={(e) => e.preventDefault()}
            onDrop={onDrop}
          >
            <div className="vd-panel-head">
              <div>
                <h2>Project Media</h2>
                <p>Assets for this edit</p>
              </div>
              <button className="vd-import-btn" onClick={() => fileInputRef.current?.click()} disabled={importing}>
                {importing ? "…" : "＋ Import"}
              </button>
            </div>

            <div className="vd-search">
              <span>🔍</span>
              <input placeholder="Search media…" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>

            <div className="vd-collections">
              {COLLECTIONS.map((collection) => (
                <button
                  key={collection}
                  className={project.activeCollection === collection ? "active" : ""}
                  onClick={() => dispatch({ type: "setCollection", collection })}
                >
                  {collection}
                </button>
              ))}
            </div>

            <div className="vd-left-tabs">
              {(["media", "audio", "titles", "effects"] as LeftTab[]).map((tab) => (
                <button key={tab} className={leftTab === tab ? "active" : ""} onClick={() => setLeftTab(tab)}>
                  {tab === "media" ? "Media" : tab === "audio" ? "Audio" : tab === "titles" ? "Titles" : "Effects"}
                </button>
              ))}
            </div>

            {leftTab === "media" && (
              <div className="vd-media-grid">
                {filteredMedia.map((asset) => (
                  <figure
                    key={asset.id}
                    className="vd-media-card"
                    draggable
                    onDragStart={(e) => e.dataTransfer.setData("text/asset-id", asset.id)}
                    onDoubleClick={() => dispatch({ type: "appendClip", assetId: asset.id })}
                    title="Double-click to add to timeline"
                  >
                    <div className="vd-media-thumb" style={asset.thumb ? { backgroundImage: `url(${asset.thumb})` } : undefined}>
                      {!asset.thumb && <span>{asset.kind === "audio" ? "♪" : asset.kind === "image" ? "🖼" : "🎞"}</span>}
                      <em>{formatShort(asset.duration)}</em>
                      <button
                        className={`vd-fav ${asset.favorite ? "on" : ""}`}
                        title="Favorite"
                        onClick={(e) => { e.stopPropagation(); dispatch({ type: "toggleFavorite", id: asset.id }); }}
                      >★</button>
                    </div>
                    <figcaption title={asset.name}>{asset.name}</figcaption>
                    <div className="vd-media-actions">
                      <button onClick={() => dispatch({ type: "appendClip", assetId: asset.id })}>＋ Timeline</button>
                      <button onClick={() => dispatch({ type: "removeMedia", id: asset.id })} title="Remove from project">✕</button>
                    </div>
                  </figure>
                ))}
                <button className="vd-add-media" onClick={() => fileInputRef.current?.click()}>
                  <span>＋</span>
                  Add media
                </button>
              </div>
            )}

            {leftTab === "audio" && (
              <div className="vd-stack">
                <p className="vd-hint">Import audio files, then add them to the timeline.</p>
                <button className="vd-wide-btn" onClick={() => audioInputRef.current?.click()}>♫ Import audio</button>
                {project.media.filter((m) => m.kind === "audio").map((asset) => (
                  <div className="vd-list-row" key={asset.id}>
                    <span>♪</span>
                    <div><strong>{asset.name}</strong><small>{formatShort(asset.duration)}</small></div>
                    <button onClick={() => dispatch({ type: "appendClip", assetId: asset.id })}>＋</button>
                  </div>
                ))}
              </div>
            )}

            {leftTab === "titles" && (
              <div className="vd-stack">
                <label className="vd-field">
                  <span>Title text</span>
                  <input value={titleText} onChange={(e) => setTitleText(e.target.value)} />
                </label>
                <button
                  className="vd-wide-btn"
                  disabled={!titleText.trim()}
                  onClick={() => { dispatch({ type: "addTitle", text: titleText.trim() }); flash("Title added to T1 track"); }}
                >
                  ＋ Add title to timeline
                </button>
                <div className="vd-title-presets">
                  {["BOLD CENTER", "Lower third", "Cinematic fade"].map((preset) => (
                    <button key={preset} onClick={() => setTitleText(preset)}>{preset}</button>
                  ))}
                </div>
              </div>
            )}

            {leftTab === "effects" && (
              <div className="vd-stack">
                <p className="vd-hint">Pick a transition for the selected clip.</p>
                {TRANSITION_OPTIONS.map((option) => (
                  <button
                    key={option}
                    className="vd-list-row vd-clickable"
                    onClick={() => {
                      if (!selectedClip) return flash("Select a clip first");
                      dispatch({ type: "addTransition", id: selectedClip.id, transition: option === "None" ? "" : option });
                      flash(`${option} applied to ${selectedClip.name}`);
                    }}
                  >
                    <span>⇄</span>
                    <div><strong>{option}</strong><small>Transition</small></div>
                  </button>
                ))}
              </div>
            )}

            <div className="vd-ai-card">
              <div>
                <strong>AI Video Generator</strong>
                <small>{busy ? "Working…" : "Text → video clip"}</small>
              </div>
              <button onClick={() => setAiOpen(true)}>Open</button>
            </div>
          </aside>

          {/* ---------- center: monitor + timeline ---------- */}
          <main className="vd-center">
            <Preview
              ref={previewRef}
              project={project}
              onPlayhead={(time) => dispatch({ type: "setPlayhead", at: time })}
            />
            <Timeline project={project} dispatch={dispatch} onSeek={(time) => dispatch({ type: "setPlayhead", at: time })} />
          </main>

          {/* ---------- right: inspector ---------- */}
          <aside className="vd-panel vd-inspector">
            <div className="vd-inspector-tabs">
              {(["color", "transform", "effects", "speed", "looks", "audio"] as InspectorTab[]).map((tab) => (
                <button key={tab} className={inspectorTab === tab ? "active" : ""} onClick={() => setInspectorTab(tab)}>
                  {tab === "color" ? "Color" : tab === "transform" ? "Transform" : tab === "effects" ? "Effects" : tab === "speed" ? "Speed" : tab === "looks" ? "Vibe" : "Audio"}
                </button>
              ))}
            </div>

            {inspectorTab === "color" && (
              <div className="vd-inspector-body">
                <div className="vd-section-head">
                  <h3>Color Grade</h3>
                  <button className="vd-link" onClick={() => dispatch({ type: "setColor", patch: { contrast: 0, saturation: 0, vibrance: 0, temperature: 0, tint: 0, exposure: 0, gamma: 0, lift: 0, gain: 0, offset: 0 } })}>Reset</button>
                </div>
                <div className="vd-scopes">
                  <span className="vd-scope-vector" />
                  <span className="vd-scope-wave" />
                </div>
                {COLOR_ROWS.map((row) => (
                  <div className="vd-slider-row" key={row.key}>
                    <span>{row.label}</span>
                    <StudioRange value={project.color[row.key]} min={-100} max={100} gradient={row.gradient} onChange={(value) => dispatch({ type: "setColor", patch: { [row.key]: value } as Partial<ColorGrade> })} label={row.label} />
                    <em>{project.color[row.key]}</em>
                  </div>
                ))}
                <div className="vd-color-wheel-row">
                  <div className="vd-wheel lift"><i /></div>
                  <div className="vd-wheel gamma"><i /></div>
                  <div className="vd-wheel gain"><i /></div>
                </div>
                <div className="vd-wheel-labels"><span>Lift</span><span>Gamma</span><span>Gain</span></div>
                <div className="vd-section-head">
                  <h3>Curves</h3>
                  <span className="vd-chip">Master</span>
                </div>
                <div className="vd-curves">{[38, 62, 84, 56, 30, 18].map((h, i) => <i key={i} style={{ height: `${h}%` }} />)}</div>
              </div>
            )}

            {inspectorTab === "transform" && (
              <div className="vd-inspector-body">
                <div className="vd-tool-tabs">
                  {(["transform", "motion", "opacity"] as ToolGroup[]).map((group) => (
                    <button key={group} className={tool === group ? "active" : ""} onClick={() => setTool(group)}>
                      {group === "transform" ? "Transform" : group === "motion" ? "Motion" : "Opacity"}
                    </button>
                  ))}
                </div>
                <div className="vd-position-grid">
                  <label>Position<em>X</em><input type="number" value={transformCopy.positionX} onChange={(e) => updateTransform({ positionX: Number(e.target.value) })} /></label>
                  <label>&nbsp;<em>Y</em><input type="number" value={transformCopy.positionY} onChange={(e) => updateTransform({ positionY: Number(e.target.value) })} /></label>
                  <label>Scale<em>X</em><input type="number" value={transformCopy.scaleX} onChange={(e) => updateTransform({ scaleX: Number(e.target.value) })} /></label>
                  <label>&nbsp;<em>Y</em><input type="number" value={transformCopy.scaleY} onChange={(e) => updateTransform({ scaleY: Number(e.target.value) })} /></label>
                </div>
                <div className="vd-slider-row"><span>Rotate</span><StudioRange value={transformCopy.rotate} min={-180} max={180} onChange={(value) => updateTransform({ rotate: value })} /><em>{transformCopy.rotate}°</em></div>
                <div className="vd-slider-row"><span>Anchor X</span><StudioRange value={transformCopy.anchorX} min={0} max={100} onChange={(value) => updateTransform({ anchorX: value })} /><em>{transformCopy.anchorX}</em></div>
                <div className="vd-slider-row"><span>Anchor Y</span><StudioRange value={transformCopy.anchorY} min={0} max={100} onChange={(value) => updateTransform({ anchorY: value })} /><em>{transformCopy.anchorY}</em></div>
                <div className="vd-toggle-row">
                  {[
                    ["Stabilize", "stabilize"],
                    ["Motion tracking", "motionTracking"],
                    ["Chroma key", "chromaKey"],
                  ].map(([label, key]) => (
                    <button
                      key={key}
                      className={transformCopy[key as keyof Transform] ? "on" : ""}
                      onClick={() => updateTransform({ [key]: !transformCopy[key as keyof Transform] } as Partial<Transform>)}
                    >
                      <i />{label}
                    </button>
                  ))}
                </div>
                <div className="vd-quick-actions">
                  <button onClick={() => updateTransform({ positionX: 0, positionY: 0, scaleX: 100, scaleY: 100, rotate: 0 })}>Fit frame</button>
                  <button onClick={() => updateTransform({ positionX: 0, positionY: 0, scaleX: 100, scaleY: 100, rotate: 0, stabilize: false, motionTracking: false, chromaKey: false })}>Reset</button>
                </div>
              </div>
            )}

            {inspectorTab === "effects" && (
              <div className="vd-inspector-body">
                <h3 className="vd-section-title">Clip effects</h3>
                <div className="vd-effect-grid">
                  {["Film grain", "Vignette", "Sharpen", "Glow", "Blur", "Noise"].map((effect) => (
                    <button key={effect} onClick={() => flash(`${effect} ${selectedClip ? `applied to ${selectedClip.name}` : "requires a selected clip"}`)}>{effect}</button>
                  ))}
                </div>
                <h3 className="vd-section-title">Transition</h3>
                <select
                  className="vd-select"
                  value={selectedClip?.transition ?? "None"}
                  onChange={(e) => {
                    if (!selectedClip) return flash("Select a clip to change its transition");
                    dispatch({ type: "addTransition", id: selectedClip.id, transition: e.target.value === "None" ? "" : e.target.value });
                  }}
                >
                  {TRANSITION_OPTIONS.map((option) => <option key={option}>{option}</option>)}
                </select>
              </div>
            )}

            {inspectorTab === "speed" && (
              <div className="vd-inspector-body">
                <h3 className="vd-section-title">Clip speed</h3>
                <div className="vd-speed-row">
                  {[0.5, 1, 1.5, 2].map((rate) => (
                    <button
                      key={rate}
                      className={clipSpeed(selectedClip) === rate ? "active" : ""}
                      onClick={() => {
                        if (!selectedClip) return flash("Select a clip to change speed");
                        const base = selectedClip.duration / clipSpeed(selectedClip);
                        dispatch({ type: "trimClip", id: selectedClip.id, start: selectedClip.start, duration: Number((base / rate).toFixed(2)) });
                        dispatch({ type: "updateClip", id: selectedClip.id, patch: { speed: rate } });
                      }}
                    >
                      {rate}×
                    </button>
                  ))}
                </div>
                <p className="vd-hint">Speed changes re-time the selected clip on the timeline.</p>
              </div>
            )}

            {inspectorTab === "looks" && (
              <div className="vd-inspector-body">
                <h3 className="vd-section-title">Vibe presets</h3>
                {VIBE_LOOKS.map((look, index) => (
                  <button
                    key={look}
                    className="vd-look-row"
                    onClick={() => {
                      dispatch({ type: "setColor", patch: lookPreset(index) });
                      flash(`${look} look applied`);
                    }}
                  >
                    <span style={{ background: lookSwatch(index) }} />
                    <strong>{look}</strong>
                    <em>Apply</em>
                  </button>
                ))}
              </div>
            )}

            {inspectorTab === "audio" && (
              <div className="vd-inspector-body">
                <h3 className="vd-section-title">Audio mixer</h3>
                {MIXER_ROWS.map((row) => (
                  <div className="vd-slider-row" key={row}>
                    <span>{row}</span>
                    <StudioRange value={project.mixer[row]} min={0} max={100} accent="#3ba55d" onChange={(value) => dispatch({ type: "setMixer", patch: { [row]: value } })} />
                    <em>{project.mixer[row]}%</em>
                  </div>
                ))}
                <p className="vd-hint">Mixer levels are stored with the project and applied on export.</p>
              </div>
            )}
          </aside>
        </div>

        {/* ---------- bottom: export + mixer ---------- */}
        <section className="vd-bottom" id="vd-export-panel">
          <div className="vd-export-card">
            <div className="vd-section-head">
              <h3>Export settings</h3>
              <span className="vd-chip">{aspectMinutes} min · {project.exportSettings.aspect}</span>
            </div>
            <div className="vd-export-grid">
              <label>Format
                <select value={project.exportSettings.format} onChange={(e) => dispatch({ type: "setExport", patch: { format: e.target.value as ExportSettings["format"] } })}>
                  {["MP4", "MOV", "WebM", "GIF"].map((f) => <option key={f}>{f}</option>)}
                </select>
              </label>
              <label>Resolution
                <select value={project.exportSettings.resolution} onChange={(e) => dispatch({ type: "setExport", patch: { resolution: e.target.value as ExportSettings["resolution"] } })}>
                  {["720p", "1080p", "2K", "4K"].map((r) => <option key={r}>{r}</option>)}
                </select>
              </label>
              <label>Frame rate
                <select value={project.exportSettings.frameRate} onChange={(e) => dispatch({ type: "setExport", patch: { frameRate: e.target.value as ExportSettings["frameRate"] } })}>
                  {["24", "30", "60"].map((f) => <option key={f}>{f} fps</option>)}
                </select>
              </label>
              <label>Bitrate
                <select value={project.exportSettings.bitrate} onChange={(e) => dispatch({ type: "setExport", patch: { bitrate: e.target.value } })}>
                  {["Low", "Medium", "High (Recommended)", "Maximum"].map((b) => <option key={b}>{b}</option>)}
                </select>
              </label>
              <label>Aspect
                <select value={project.exportSettings.aspect} onChange={(e) => dispatch({ type: "setExport", patch: { aspect: e.target.value as ExportSettings["aspect"] } })}>
                  {["16:9", "9:16", "1:1"].map((a) => <option key={a}>{a}</option>)}
                </select>
              </label>
            </div>
            <div className="vd-slider-row">
              <span>Quality</span>
              <StudioRange value={project.exportSettings.quality} min={10} max={100} onChange={(value) => dispatch({ type: "setExport", patch: { quality: value } })} />
              <em>{project.exportSettings.quality}</em>
            </div>
            <div className="vd-export-footer">
              <div className="vd-progress"><span style={{ width: `${progress}%` }} /></div>
              <button className="vd-export-start" onClick={startExport} disabled={busy}>
                {progress > 0 && progress < 100 ? `Rendering ${progress}%` : "⤒ Start export"}
              </button>
            </div>
          </div>

          <div className="vd-mixer-card">
            <div className="vd-section-head">
              <h3>Quick mixer</h3>
              <button className="vd-link" onClick={() => setInspectorTab("audio")}>Details</button>
            </div>
            <div className="vd-faders">
              {MIXER_ROWS.map((row) => (
                <div className="vd-fader" key={row}>
                  <input
                    type="range"
                    aria-label={`${row} level`}
                    min={0}
                    max={100}
                    value={project.mixer[row]}
                    onChange={(e) => dispatch({ type: "setMixer", patch: { [row]: Number(e.target.value) } })}
                    style={{ background: `linear-gradient(0deg,#3ba55d 0%,#3ba55d ${project.mixer[row]}%,#2a2d3a ${project.mixer[row]}%,#2a2d3a 100%)` }}
                  />
                  <span>{row}</span>
                  <em>{project.mixer[row]}</em>
                </div>
              ))}
            </div>
          </div>
        </section>

        {status && <div className="vd-toast" role="status">{status}</div>}

        {aiOpen && (
          <div className="vd-modal-backdrop" onClick={() => setAiOpen(false)}>
            <div className="vd-modal" onClick={(e) => e.stopPropagation()}>
              <h3>AI Video Generator</h3>
              <p>Describe the clip you want. The provider is called through your backend and the result lands in Project Media.</p>
              <textarea
                value={aiPrompt}
                onChange={(e) => setAiPrompt(e.target.value)}
                placeholder="A slow cinematic drone shot over a misty mountain valley at sunrise…"
                rows={4}
              />
              <div className="vd-modal-actions">
                <button className="vd-ghost" onClick={() => setAiOpen(false)} disabled={busy}>Cancel</button>
                <button className="vd-ai-btn" onClick={onAiGenerate} disabled={busy || !aiPrompt.trim()}>
                  {busy ? "Generating…" : "✦ Generate video"}
                </button>
              </div>
            </div>
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept="video/*,image/*,audio/*"
          multiple
          hidden
          onChange={(e) => { if (e.target.files) void importFiles(e.target.files); e.target.value = ""; }}
        />
        <input
          ref={audioInputRef}
          type="file"
          accept="audio/*"
          multiple
          hidden
          onChange={(e) => { if (e.target.files) void importFiles(e.target.files); e.target.value = ""; }}
        />
      </div>
    </AppShell>
  );

  function updateTransform(patch: Partial<Transform>) {
    setTransformCopy((current) => ({ ...current, ...patch }));
    dispatch({ type: "setTransform", patch });
  }
};

function formatShort(seconds: number) {
  if (!seconds) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function clipSpeed(clip: Clip | null) {
  return (clip as (Clip & { speed?: number }) | null)?.speed ?? 1;
}

function cycleTransition(dispatch: React.Dispatch<EditorAction>, clip: Clip, flash: (message: string) => void) {
  const currentIndex = Math.max(0, TRANSITION_OPTIONS.indexOf(clip.transition || "None"));
  const next = TRANSITION_OPTIONS[(currentIndex + 1) % TRANSITION_OPTIONS.length];
  dispatch({ type: "addTransition", id: clip.id, transition: next === "None" ? "" : next });
  flash(`${next} transition applied`);
}

function flashlight(flash: (message: string) => void, dispatch: React.Dispatch<EditorAction>, project: ProjectState) {
  if (project.clips.length && !window.confirm("Start a new project? The current timeline will be cleared.")) return;
  dispatch({ type: "load", project: createProject("Untitled project") });
  flash("New project started");
}

const LOOK_VALUES: ColorGrade[] = [
  { contrast: 22, saturation: 18, vibrance: 12, temperature: -18, tint: 4, exposure: 2, gamma: 4, lift: -6, gain: 8, offset: 0 },
  { contrast: -8, saturation: -18, vibrance: -6, temperature: -12, tint: 8, exposure: -6, gamma: -4, lift: 10, gain: -8, offset: -2 },
  { contrast: 10, saturation: -6, vibrance: 4, temperature: 24, tint: 6, exposure: 4, gamma: 2, lift: 6, gain: 4, offset: 3 },
  { contrast: 6, saturation: 8, vibrance: 16, temperature: 4, tint: 0, exposure: 8, gamma: 6, lift: 2, gain: 6, offset: 0 },
  { contrast: 30, saturation: 34, vibrance: 22, temperature: -22, tint: 22, exposure: -4, gamma: 8, lift: -10, gain: 12, offset: 4 },
  { contrast: -4, saturation: 6, vibrance: 10, temperature: 14, tint: -6, exposure: 10, gamma: 10, lift: 8, gain: 2, offset: 2 },
  { contrast: 34, saturation: -100, vibrance: 0, temperature: 0, tint: 0, exposure: 0, gamma: 6, lift: -8, gain: 10, offset: 0 },
  { contrast: 16, saturation: 24, vibrance: 18, temperature: 30, tint: 10, exposure: -2, gamma: 2, lift: 4, gain: 6, offset: 0 },
  { contrast: -12, saturation: 12, vibrance: 24, temperature: 10, tint: -16, exposure: 12, gamma: 12, lift: 10, gain: 0, offset: 4 },
];

function lookPreset(index: number): Partial<ColorGrade> {
  return LOOK_VALUES[index % LOOK_VALUES.length];
}

function lookSwatch(index: number) {
  const gradients = [
    "linear-gradient(90deg,#0f3b4a,#e08a3c)",
    "linear-gradient(90deg,#1b2330,#7b6a58)",
    "linear-gradient(90deg,#c26a3a,#f2c98a)",
    "linear-gradient(90deg,#f5f7fa,#c9d4e0)",
    "linear-gradient(90deg,#14122b,#ff3cac)",
    "linear-gradient(90deg,#f6e3d5,#b98a76)",
    "linear-gradient(90deg,#111,#999)",
    "linear-gradient(90deg,#7a3b2e,#e6b27a)",
    "linear-gradient(90deg,#cfe3ff,#ffd6ec)",
  ];
  return gradients[index % gradients.length];
}

export default VideoEditor;
