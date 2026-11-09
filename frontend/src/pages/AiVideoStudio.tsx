import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { apiBaseUrl, readErrorDetail } from "../api/client";

type Mode = "text" | "image";

type Job = {
  job_id: string;
  prompt: string;
  mode: string;
  result?: { status?: string; message?: string; output?: unknown };
};

type Capabilities = {
  feature_enabled: boolean;
  user_permitted: boolean;
  text_to_video: { ready: boolean; space: string | null };
  image_to_video: { ready: boolean; space: string | null };
};

const ASPECTS = [
  { id: "16:9", label: "Landscape", hint: "YouTube · 1920×1080" },
  { id: "9:16", label: "Portrait", hint: "Reels · 1080×1920" },
  { id: "1:1", label: "Square", hint: "Feed · 1080×1080" },
];

function outputUrl(output: unknown): string | null {
  if (typeof output === "string" && /^https?:\/\//.test(output)) return output;
  if (output && typeof output === "object") {
    const record = output as Record<string, unknown>;
    for (const key of ["url", "path", "video"]) {
      const value = record[key];
      if (typeof value === "string" && value) return value.startsWith("http") ? value : null;
    }
  }
  return null;
}

const AiVideoStudio: React.FC = () => {
  const { token } = useAuth();
  const [mode, setMode] = useState<Mode>("text");
  const [prompt, setPrompt] = useState("");
  const [image, setImage] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState("");
  const [aspect, setAspect] = useState("16:9");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const headers = { Authorization: `Bearer ${token}` };

  useEffect(() => {
    if (!token) return;
    fetch(`${apiBaseUrl}/api/v1/video/capabilities`, { headers })
      .then((response) => response.json())
      .then(setCapabilities)
      .catch(() => setCapabilities(null));
    fetch(`${apiBaseUrl}/api/v1/video/jobs`, { headers })
      .then((response) => (response.ok ? response.json() : []))
      .then((rows: Job[]) => setJobs(rows.slice(-20).reverse()))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const ready = mode === "text" ? capabilities?.text_to_video.ready : capabilities?.image_to_video.ready;
  const blocked = capabilities ? !capabilities.feature_enabled || !capabilities.user_permitted : false;
  const canGenerate = Boolean(token) && !busy && !blocked && ready && prompt.trim().length > 0 && (mode === "text" || image);

  const chooseImage = (file: File) => {
    setImage(file);
    if (imagePreview) URL.revokeObjectURL(imagePreview);
    setImagePreview(URL.createObjectURL(file));
  };

  const generate = async () => {
    if (!token || !canGenerate) return;
    setBusy(true);
    setError("");
    setNotice(mode === "image" ? "Animating your image — this can take a few minutes." : "Rendering your clip — this can take a few minutes.");
    try {
      let response: Response;
      if (mode === "image" && image) {
        const form = new FormData();
        form.append("prompt", prompt.trim());
        form.append("image", image);
        form.append("aspect_ratio", aspect);
        response = await fetch(`${apiBaseUrl}/api/v1/video/image-to-video`, { method: "POST", headers, body: form });
      } else {
        response = await fetch(`${apiBaseUrl}/api/v1/video/generate`, {
          method: "POST",
          headers: { ...headers, "Content-Type": "application/json" },
          body: JSON.stringify({ prompt: prompt.trim() }),
        });
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Video generation failed"));
      const job = data as Job;
      const status = job.result?.status;
      setJobs((current) => [job, ...current].slice(0, 20));
      if (status === "completed") {
        setNotice("Video ready — added to your project media in the editor.");
      } else if (status === "disabled") {
        setNotice(job.result?.message || "This generation mode is not configured on the server.");
      } else {
        setNotice(job.result?.message || `Provider returned: ${status ?? "unknown"}`);
      }
    } catch (err: any) {
      setError(err?.message || "Video generation failed");
      setNotice("");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="tool-page">
        <header className="tool-head">
          <div>
            <p className="eyebrow">Video Studio · AI</p>
            <h1>Create a video</h1>
            <p className="page-subtitle">
              Both generation modes live here. Editing an existing timeline happens in the{" "}
              <Link className="tool-link inline" to="/video-studio/editor">professional editor</Link>.
            </p>
          </div>
          <Link className="tool-link" to="/video-studio/editor">▣ Open editor</Link>
        </header>

        {error && <div className="tool-error" role="alert">{error}</div>}
        {notice && <div className="tool-notice">{notice}</div>}

        {blocked && capabilities && (
          <div className="tool-error" role="alert">
            {!capabilities.feature_enabled
              ? "Video generation is disabled by an administrator."
              : "Your account does not have the video_generation permission. Ask an administrator to enable it."}
          </div>
        )}

        <div className="av-layout">
          <section className="tool-card">
            <div className="av-mode" role="tablist" aria-label="Generation mode">
              <button role="tab" aria-selected={mode === "text"} className={mode === "text" ? "active" : ""} onClick={() => setMode("text")}>
                <span>✍</span> Prompt to Video
              </button>
              <button role="tab" aria-selected={mode === "image"} className={mode === "image" ? "active" : ""} onClick={() => setMode("image")}>
                <span>🖼</span> Image to Video
              </button>
            </div>

            {mode === "image" && (
              <div
                className="doc-drop compact av-drop"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  if (e.dataTransfer.files[0]) chooseImage(e.dataTransfer.files[0]);
                }}
                onClick={() => fileRef.current?.click()}
              >
                {imagePreview ? (
                  <img src={imagePreview} alt="Starting frame" className="av-frame" />
                ) : (
                  <>
                    <strong>Drop a starting image</strong>
                    <small>PNG, JPG or WEBP — this becomes the first frame</small>
                  </>
                )}
                <input
                  ref={fileRef}
                  type="file"
                  hidden
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(e) => {
                    if (e.target.files?.[0]) chooseImage(e.target.files[0]);
                    e.target.value = "";
                  }}
                />
              </div>
            )}

            <label className="tool-field">
              <span>{mode === "text" ? "Describe the scene" : "Describe the motion"}</span>
              <textarea
                rows={5}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder={
                  mode === "text"
                    ? "A slow cinematic drone shot over a misty mountain valley at sunrise, golden light…"
                    : "Gentle camera push-in while the clouds drift and leaves move in the wind…"
                }
              />
            </label>

            <div className="av-aspects">
              {ASPECTS.map((item) => (
                <button
                  key={item.id}
                  className={aspect === item.id ? "pill active" : "pill"}
                  onClick={() => setAspect(item.id)}
                  title={`${item.label} · ${item.hint}`}
                >
                  {item.label} <small>{item.id}</small>
                </button>
              ))}
            </div>

            <button className="tool-btn primary" onClick={generate} disabled={!canGenerate}>
              {busy ? "Generating…" : mode === "text" ? "✦ Generate video" : "✦ Animate image"}
            </button>

            {capabilities && !ready && (
              <p className="tool-note">
                {mode === "text" ? "Text → Video" : "Image → Video"} is not configured on this server
                {mode === "text" ? capabilities.text_to_video.space : capabilities.image_to_video.space
                  ? ` (${mode === "text" ? capabilities.text_to_video.space : capabilities.image_to_video.space})`
                  : ""}
                . An administrator must point it at an eligible Hugging Face Space.
              </p>
            )}
            <p className="tool-note">
              Generation runs on the provider your admin enabled. Results are stored in the job list and can be opened in the editor.
            </p>
          </section>

          <section className="tool-card">
            <h2>Generation history</h2>
            {jobs.length === 0 && <p className="tool-note">No generations yet. Your clips will appear here.</p>}
            <div className="av-jobs">
              {jobs.map((job) => {
                const url = outputUrl(job.result?.output);
                const failed = job.result?.status && job.result.status !== "completed";
                return (
                  <article key={job.job_id} className="av-job">
                    <div className="av-job-thumb">
                      {url ? (
                        <video src={url} muted preload="metadata" />
                      ) : (
                        <span>{failed ? "⚠" : "▣"}</span>
                      )}
                    </div>
                    <div className="av-job-copy">
                      <strong>{job.prompt?.slice(0, 60) || "Untitled"}</strong>
                      <small>{job.mode === "image" ? "Image → Video" : "Text → Video"} · {job.result?.status ?? "queued"}</small>
                      {job.result?.message && <em>{job.result.message}</em>}
                    </div>
                    <div className="av-job-actions">
                      {url && (
                        <>
                          <a href={url} target="_blank" rel="noreferrer noopener">Open</a>
                          <Link to={`/video-studio/editor?asset=${encodeURIComponent(url)}&name=${encodeURIComponent(job.prompt?.slice(0, 40) || "AI clip")}`}>Edit</Link>
                        </>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </section>
        </div>
      </div>
    </AppShell>
  );
};

export default AiVideoStudio;
