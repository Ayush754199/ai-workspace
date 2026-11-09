import React, { useState } from "react";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { downloadBlob, officeApi } from "../api/workspaceApi";

const TOOLS = [
  { id: "xlsx", label: "Excel spreadsheet", hint: "Tables with an AI-authored dataset", icon: "▦" },
  { id: "docx", label: "Word document", hint: "Reports, letters, proposals", icon: "▤" },
  { id: "pptx", label: "PowerPoint deck", hint: "Slide-by-slide outline", icon: "▥" },
  { id: "csv", label: "CSV dataset", hint: "Plain comma-separated data", icon: "≣" },
  { id: "pdf", label: "PDF report", hint: "Printable, styled report", icon: "◫" },
];

const EXAMPLES: Record<string, string> = {
  xlsx: "A 12-row sales plan for Q3 with region, owner, target and pipeline value.",
  docx: "A two-page project proposal for a mobile banking app with scope, timeline and budget.",
  pptx: "An 8-slide investor update for a SaaS startup with traction, metrics and asks.",
  csv: "A dataset of 15 cities with population, country and average temperature.",
  pdf: "A one-page executive summary of our remote-work policy changes.",
};

const Office: React.FC = () => {
  const { token } = useAuth();
  const [format, setFormat] = useState("xlsx");
  const [prompt, setPrompt] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [last, setLast] = useState<{ name: string; provider: string | null } | null>(null);
  const [previewUrl, setPreviewUrl] = useState("");
  const [previewKind, setPreviewKind] = useState("");

  const generate = async () => {
    if (!token || !prompt.trim()) return;
    setBusy(true);
    setError("");
    setLast(null);
    try {
      const { blob, provider } = await officeApi.generate(token, {
        prompt: prompt.trim(),
        format,
        title: title.trim(),
        filename: title.trim() || format,
      });
      const filename = `${title.trim() || "document"}.${format}`;
      await downloadBlob(blob, filename);
      setLast({ name: filename, provider });
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewKind(format);
      setPreviewUrl(format === "pdf" || format === "csv" ? URL.createObjectURL(blob) : "");
    } catch (err: any) {
      setError(err?.message || "Generation failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="tool-page">
        <header className="tool-head">
          <div>
            <p className="eyebrow">Office tools</p>
            <h1>Excel, Word, PowerPoint, CSV &amp; PDF</h1>
            <p className="page-subtitle">Describe what you need and download a real, editable file. Files are generated on the server.</p>
          </div>
          <a className="tool-link" href="/assistant">← Back to chat</a>
        </header>

        {error && <div className="tool-error" role="alert">{error}</div>}

        <div className="tool-grid">
          <section className="tool-card">
            <h2>1 · Choose a format</h2>
            <div className="office-picker">
              {TOOLS.map((tool) => (
                <button
                  key={tool.id}
                  className={`office-tile ${format === tool.id ? "active" : ""}`}
                  onClick={() => {
                    setFormat(tool.id);
                    if (!prompt.trim()) setPrompt(EXAMPLES[tool.id]);
                  }}
                >
                  <span>{tool.icon}</span>
                  <strong>{tool.label}</strong>
                  <small>{tool.hint}</small>
                </button>
              ))}
            </div>
          </section>

          <section className="tool-card">
            <h2>2 · Describe the content</h2>
            <label className="tool-field">
              <span>Title (used as the filename)</span>
              <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Q3 sales plan" />
            </label>
            <label className="tool-field">
              <span>What should the file contain?</span>
              <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6} placeholder={EXAMPLES[format]} />
            </label>
            <button className="tool-btn primary" onClick={generate} disabled={busy || !prompt.trim()}>
              {busy ? "Authoring & rendering…" : `✦ Generate ${format.toUpperCase()}`}
            </button>
            {last && (
              <p className="tool-note">
                Downloaded <strong>{last.name}</strong>
                {last.provider ? ` · authored by ${last.provider}` : ""}
              </p>
            )}
          </section>

          {previewUrl && (
            <section className="tool-card">
              <h2>Preview</h2>
              {previewKind === "pdf" ? (
                <iframe title="PDF preview" src={previewUrl} className="office-preview" />
              ) : (
                <pre className="doc-preview">{previewKind === "csv" ? "CSV file ready — download to open it in Excel or Sheets." : ""}</pre>
              )}
            </section>
          )}
        </div>
      </div>
    </AppShell>
  );
};

export default Office;
