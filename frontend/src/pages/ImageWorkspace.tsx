import React, { useRef, useState } from "react";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { downloadBlob, imageApi, chatV2 } from "../api/workspaceApi";
import FeatureGate from "../components/FeatureGate";

/**
 * Image AI — ChatGPT-style creation studio.
 * Everything runs through the live image pipeline (Cloudflare Workers AI Flux);
 * edit, enhance and understand an uploaded image end to end.
 */
const PROMPT_IDEAS = [
  "A cinematic portrait of a lighthouse keeper at golden hour, film grain",
  "Studio product shot of a sneaker on matte black, soft studio lights",
  "Watercolor illustration of a fox in a snowy forest",
  "Futuristic city skyline at dusk, neon reflections on wet streets",
  "Minimal logo for a coffee brand, warm earthy tones",
];

const ImageWorkspacePage: React.FC = () => {
  const MAX_IMAGE_BYTES = 20 * 1024 * 1024;
  const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp"]);
  const { token } = useAuth();
  const [prompt, setPrompt] = useState("");
  const [source, setSource] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState<string>("");
  const [resultUrl, setResultUrl] = useState<string>("");
  const [resultBlob, setResultBlob] = useState<Blob | null>(null);
  const [answer, setAnswer] = useState("");
  const [variations, setVariations] = useState<string[]>([]);
  const [question, setQuestion] = useState("Describe this image in detail.");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [tolerance, setTolerance] = useState(28);
  const inputRef = useRef<HTMLInputElement>(null);
  const conversationIdRef = useRef<number | null>(null);

  const ensureConversation = async () => {
    if (conversationIdRef.current != null) return conversationIdRef.current;
    if (!token) throw new Error("Sign in to generate an image");
    const conv = await chatV2.createConversation(token, "Image studio", "chat");
    conversationIdRef.current = conv.id;
    return conv.id;
  };


  const run = async (label: string, task: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await task();
    } catch (err: any) {
      setError(err?.message || "The request failed");
    } finally {
      setBusy("");
    }
  };

  const showBlob = (blob: Blob) => {
    if (resultUrl) URL.revokeObjectURL(resultUrl);
    const url = URL.createObjectURL(blob);
    setResultUrl(url);
    setResultBlob(blob);
  };

  const chooseFile = (file: File) => {
    const extension = file.name.includes(".") ? file.name.split(".").pop()?.toLowerCase() : "";
    if (!file.type.startsWith("image/") || !extension || !IMAGE_EXTENSIONS.has(extension) || file.size > MAX_IMAGE_BYTES) {
      setError(file.size > MAX_IMAGE_BYTES
        ? "Image exceeds the 20 MB limit."
        : "Unsupported image. Select a valid PNG, JPG, JPEG, or WEBP file.");
      return;
    }
    setError("");
    setSource(file);
    if (sourceUrl) URL.revokeObjectURL(sourceUrl);
    setSourceUrl(URL.createObjectURL(file));
    setAnswer("");
    setVariations([]);
  };

  const downloadResult = () => {
    if (resultBlob) void downloadBlob(resultBlob, "ai-image.png");
  };

  return (
    <AppShell>
      <div className="img-page">
        <header className="tool-head img-head">
          <div>
            <p className="eyebrow">Image Studio</p>
            <h1>Create with AI</h1>
            <p className="page-subtitle">Generate, edit and understand images — powered by Cloudflare Workers AI Flux.</p>
          </div>
          <a className="tool-link" href="/assistant">← Back to chat</a>
        </header>

        {error && <div className="tool-error img-error" role="alert">{error}</div>}

        <div className="img-layout">
          {/* Hero composer — ChatGPT-like centered input */}
          <section className="img-hero">
            <div className="img-orbit" aria-hidden="true" />
            <div className="img-composer-shell">
              <label className="img-composer">
                <span className="img-composer-label">{source ? "Describe the edit" : "Imagine anything"}</span>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  rows={2}
                  placeholder="A lighthouse on a cliff at stormy sunset, ultra detailed…"
                  onKeyDown={(e) => {
                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && prompt.trim()) void run("generate", async () => {
                      if (!token) return;
                      const cid = await ensureConversation();
                      showBlob(await imageApi.generate(token, prompt.trim(), source, cid));
                    });
                  }}
                />
              </label>
              <div className="img-suggestions" aria-label="Prompt ideas">
                {PROMPT_IDEAS.slice(0, 3).map((idea) => (
                  <button key={idea} className="img-chip" onClick={() => setPrompt(idea)} title={idea}>
                    {idea.length > 34 ? idea.slice(0, 32) + "…" : idea}
                  </button>
                ))}
              </div>
              <div className="img-composer-actions">
                <div
                  className={`img-attach ${source ? "has-source" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-label={source ? `Attached ${source.name}` : "Attach an image to edit (optional)"}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => { e.preventDefault(); if (e.dataTransfer.files[0]) chooseFile(e.dataTransfer.files[0]); }}
                  onClick={() => inputRef.current?.click()}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
                >
                  {source ? <span>📎 {source.name}</span> : <span>📎 {sourceUrl ? "Source image" : "Attach to edit"}</span>}
                  <input ref={inputRef} type="file" hidden accept="image/png,image/jpeg,image/webp"
                    onChange={(e) => { if (e.target.files?.[0]) chooseFile(e.target.files[0]); e.target.value = ""; }} />
                </div>
                <button
                  className="img-generate"
                  disabled={!prompt.trim() || !token || busy === "generate"}
                  onClick={() => run("generate", async () => {
                    if (!token) return;
                    const cid = await ensureConversation();
                    showBlob(await imageApi.generate(token, prompt.trim(), source, cid));
                  })}
                >
                  {busy === "generate" ? <span className="img-spinner" aria-hidden="true" /> : <span className="img-spark">✦</span>}
                  {busy === "generate" ? "Dreaming…" : source ? "Edit image" : "Generate"}
                </button>
              </div>
            </div>
          </section>

          {/* Canvas */}
          <section className="img-canvas-card" aria-label="Canvas">
            <div className="img-canvas-head">
              <h2>{resultUrl ? (busy ? "Creating…" : "Your image") : sourceUrl ? "Source image" : "Your canvas"}</h2>
              {resultBlob && <button className="img-download" onClick={downloadResult}>⤓ Download</button>}
            </div>
            <div className="img-canvas">
              {resultUrl ? (
                <img className="img-result" src={resultUrl} alt="Generated result" />
              ) : sourceUrl ? (
                <img className="img-result img-source" src={sourceUrl} alt="Source" />
              ) : busy === "generate" ? (
                <div className="img-loader" role="status" aria-label="Generating image">
                  <div className="img-shimmer" />
                  <p>Composing pixels…</p>
                </div>
              ) : (
                <div className="img-empty">
                  <div className="img-empty-glyph">✦</div>
                  <p>Your creation appears here.<br />Describe it above and press Generate.</p>
                </div>
              )}
            </div>
          </section>

          {/* Sidebar — tools */}
          <aside className="img-side">
            <div className="img-side-card">
              <h3>Enhance &amp; edit</h3>
              <button className="img-tool" disabled={!source || !token || busy === "enhance"} onClick={() => run("enhance", async () => {
                if (!token || !source) return;
                showBlob(await imageApi.enhance(token, source));
              })}>
                <span>✨ Enhance</span>
                <small>{busy === "enhance" ? "Enhancing…" : "Sharpen & colour-correct"}</small>
              </button>
              <button className="img-tool" disabled={!source || !token || busy === "bg"} onClick={() => run("bg", async () => {
                if (!token || !source) return;
                showBlob(await imageApi.removeBackground(token, source, tolerance));
              })}>
                <span>🪄 Remove background</span>
                <small>{busy === "bg" ? "Removing…" : "One-click cutout"}</small>
              </button>
              <label className="img-tolerance">
                <span>Cutout tolerance <b>{tolerance}</b></span>
                <input type="range" min={2} max={90} value={tolerance} onChange={(e) => setTolerance(Number(e.target.value))} />
              </label>
            </div>

            <div className="img-side-card">
              <h3>Understand this image</h3>
              <input
                className="img-question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="Ask anything…"
                aria-label="Question about the image"
              />
              <button className="img-tool primary-tool" disabled={!source || !token || busy === "understand"} onClick={() => run("understand", async () => {
                if (!token || !source) return;
                const result = await imageApi.understand(token, source, question);
                setAnswer(result.answer);
              })}>
                <span>🔎 Ask vision AI</span>
                <small>{busy === "understand" ? "Reading…" : "Gemini answers"}</small>
              </button>
              {answer && <div className="img-answer" role="status">{answer}</div>}
              <button className="img-tool" disabled={!source || !token || busy === "variations"} onClick={() => run("variations", async () => {
                if (!token || !source) return;
                const result = await imageApi.variations(token, source, prompt);
                setVariations(result.prompts);
              })}>
                <span>🎲 Suggest variations</span>
                <small>{busy === "variations" ? "Thinking…" : "Fresh prompt directions"}</small>
              </button>
              {variations.length > 0 && (
                <ul className="img-variations">
                  {variations.map((item) => (
                    <li key={item}>
                      <span>{item}</span>
                      <button onClick={() => setPrompt(item)}>Use</button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </aside>
        </div>
      </div>
    </AppShell>
  );
};

const ImageWorkspace: React.FC = () => (
  <FeatureGate feature="images">
    <ImageWorkspacePage />
  </FeatureGate>
);

export default ImageWorkspace;
