import React, { useRef, useState } from "react";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { downloadBlob, imageApi } from "../api/workspaceApi";

/**
 * Image AI: generate (text or image-to-image through /chat/image), understand,
 * remove background, enhance and prompt variations. Every action reports the
 * real backend error when a provider is missing.
 */
const ImageWorkspace: React.FC = () => {
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

  return (
    <AppShell>
      <div className="tool-page">
        <header className="tool-head">
          <div>
            <p className="eyebrow">Image AI</p>
            <h1>Create and edit images</h1>
            <p className="page-subtitle">Generate from a prompt, edit an uploaded image, remove backgrounds, enhance, or ask the vision model about it.</p>
          </div>
          <a className="tool-link" href="/assistant">← Back to chat</a>
        </header>

        {error && <div className="tool-error" role="alert">{error}</div>}

        <div className="tool-grid">
          <section className="tool-card">
            <h2>Prompt</h2>
            <label className="tool-field">
              <span>{source ? "Describe the edit" : "Describe the image"}</span>
              <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={4} placeholder="A cinematic photo of a mountain lake at sunrise, ultra detailed" />
            </label>
            <div
              className="doc-drop compact"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (e.dataTransfer.files[0]) chooseFile(e.dataTransfer.files[0]);
              }}
              onClick={() => inputRef.current?.click()}
            >
              <strong>{source ? source.name : "Attach an image to edit (optional)"}</strong>
              <small>PNG, JPG or WEBP</small>
              <input
                ref={inputRef}
                type="file"
                hidden
                accept="image/png,image/jpeg,image/webp"
                onChange={(e) => {
                  if (e.target.files?.[0]) chooseFile(e.target.files[0]);
                  e.target.value = "";
                }}
              />
            </div>
            <button
              className="tool-btn primary"
              disabled={!prompt.trim() || !token || busy === "generate"}
              onClick={() =>
                run("generate", async () => {
                  if (!token) return;
                  showBlob(await imageApi.generate(token, prompt.trim(), source));
                })
              }
            >
              {busy === "generate" ? "Working…" : source ? "✦ Apply edit" : "✦ Generate image"}
            </button>
            {resultBlob && (
              <button className="tool-btn" onClick={() => void downloadBlob(resultBlob, "ai-image.png")}>⤓ Download result</button>
            )}
          </section>

          <section className="tool-card">
            <h2>Preview</h2>
            <div className="img-stage">
              {resultUrl ? (
                <img src={resultUrl} alt="Result" />
              ) : sourceUrl ? (
                <img src={sourceUrl} alt="Source" />
              ) : (
                <div className="img-placeholder">Your generated or edited image appears here</div>
              )}
            </div>
            <div className="tool-row">
              <button disabled={!source || !token || busy === "bg"} onClick={() => run("bg", async () => {
                if (!token || !source) return;
                showBlob(await imageApi.removeBackground(token, source, tolerance));
              })}>
                {busy === "bg" ? "Removing…" : "Remove background"}
              </button>
              <button disabled={!source || !token || busy === "enhance"} onClick={() => run("enhance", async () => {
                if (!token || !source) return;
                showBlob(await imageApi.enhance(token, source));
              })}>
                {busy === "enhance" ? "Enhancing…" : "Enhance"}
              </button>
            </div>
            <label className="tool-field">
              <span>Background tolerance: {tolerance}</span>
              <input type="range" min={2} max={90} value={tolerance} onChange={(e) => setTolerance(Number(e.target.value))} />
            </label>
          </section>

          <section className="tool-card">
            <h2>Understand image</h2>
            <label className="tool-field">
              <span>Question about the attached image</span>
              <input value={question} onChange={(e) => setQuestion(e.target.value)} />
            </label>
            <div className="tool-row">
              <button className="tool-btn primary" disabled={!source || !token || busy === "understand"} onClick={() => run("understand", async () => {
                if (!token || !source) return;
                const result = await imageApi.understand(token, source, question);
                setAnswer(result.answer);
              })}>
                {busy === "understand" ? "Asking…" : "Ask vision model"}
              </button>
              <button className="tool-btn" disabled={!source || !token || busy === "variations"} onClick={() => run("variations", async () => {
                if (!token || !source) return;
                const result = await imageApi.variations(token, source, prompt);
                setVariations(result.prompts);
              })}>
                {busy === "variations" ? "…" : "Suggest variations"}
              </button>
            </div>
            {answer && <div className="doc-answer">{answer}</div>}
            {variations.length > 0 && (
              <ul className="variation-list">
                {variations.map((item) => (
                  <li key={item}>
                    <span>{item}</span>
                    <button onClick={() => setPrompt(item)}>Use</button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </AppShell>
  );
};

export default ImageWorkspace;
