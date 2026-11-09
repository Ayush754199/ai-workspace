import React, { useRef, useState } from "react";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { DocumentResult, documentsApi, downloadBlob } from "../api/workspaceApi";
import renderMarkdown from "../components/Markdown";

const STYLES = [
  { id: "summary", label: "Summary" },
  { id: "bullets", label: "Bullet points" },
  { id: "outline", label: "Outline" },
  { id: "key_facts", label: "Key facts" },
];

const Documents: React.FC = () => {
  const { token } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [extracted, setExtracted] = useState<DocumentResult | null>(null);
  const [style, setStyle] = useState("summary");
  const [summary, setSummary] = useState<{ text: string; provider: string } | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<string>("");
  const [busy, setBusy] = useState<string>("");
  const [error, setError] = useState("");
  const [genFormat, setGenFormat] = useState("pdf");
  const [genTitle, setGenTitle] = useState("");
  const [genContent, setGenContent] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const guard = async (label: string, run: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await run();
    } catch (err: any) {
      setError(err?.message || "Something went wrong");
    } finally {
      setBusy("");
    }
  };

  const pickFile = async (next: File) => {
    setFile(next);
    setExtracted(null);
    setSummary(null);
    setAnswer("");
    await guard("extract", async () => {
      if (!token) return;
      setExtracted(await documentsApi.extract(token, next));
    });
  };

  return (
    <AppShell>
      <div className="tool-page">
        <header className="tool-head">
          <div>
            <p className="eyebrow">Documents</p>
            <h1>PDF &amp; document workspace</h1>
            <p className="page-subtitle">Upload a PDF, Word, PowerPoint, Excel, CSV or text file to extract, summarise or ask questions about it.</p>
          </div>
          <a className="tool-link" href="/assistant">← Back to chat</a>
        </header>

        {error && <div className="tool-error" role="alert">{error}</div>}

        <div className="tool-grid">
          <section className="tool-card">
            <h2>1 · Upload</h2>
            <div
              className="doc-drop"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (e.dataTransfer.files[0]) void pickFile(e.dataTransfer.files[0]);
              }}
              onClick={() => inputRef.current?.click()}
            >
              <span>▤</span>
              <strong>{file ? file.name : "Drop a document here"}</strong>
              <small>PDF · DOCX · PPTX · XLSX · CSV · TXT · MD — up to 25 MB</small>
              <input
                ref={inputRef}
                type="file"
                hidden
                accept=".pdf,.docx,.pptx,.xlsx,.csv,.txt,.md"
                onChange={(e) => {
                  if (e.target.files?.[0]) void pickFile(e.target.files[0]);
                  e.target.value = "";
                }}
              />
            </div>
            {extracted && (
              <dl className="doc-meta">
                <div><dt>Type</dt><dd>{extracted.kind}</dd></div>
                <div><dt>Words</dt><dd>{extracted.words.toLocaleString()}</dd></div>
                <div><dt>Characters</dt><dd>{extracted.characters.toLocaleString()}</dd></div>
                {extracted.pages !== undefined && <div><dt>Pages</dt><dd>{extracted.pages}</dd></div>}
              </dl>
            )}
            {extracted && (
              <>
                <div className="tool-row">
                  <button
                    className="tool-btn"
                    onClick={() => void downloadBlob(new Blob([extracted.text], { type: "text/plain" }), `${(file?.name || "document").replace(/\.[^.]+$/, "")}.txt`)}
                    disabled={!extracted.text}
                  >
                    ⤓ Download extracted text
                  </button>
                  <button className="tool-btn" onClick={() => navigator.clipboard.writeText(extracted.text)}>
                    ⧉ Copy text
                  </button>
                </div>
                <pre className="doc-preview">{extracted.text.slice(0, 4000) || "No extractable text (scanned PDF needs OCR)."}</pre>
              </>
            )}
          </section>

          <section className="tool-card">
            <h2>2 · Summarise</h2>
            <div className="pill-row">
              {STYLES.map((item) => (
                <button key={item.id} className={style === item.id ? "pill active" : "pill"} onClick={() => setStyle(item.id)}>
                  {item.label}
                </button>
              ))}
            </div>
            <button
              className="tool-btn primary"
              disabled={!file || !token || busy === "summarize"}
              onClick={() =>
                guard("summarize", async () => {
                  if (!token || !file) return;
                  const result = await documentsApi.summarize(token, file, style);
                  setSummary({ text: result.summary, provider: result.provider });
                })
              }
            >
              {busy === "summarize" ? "Analysing…" : "✦ Summarise document"}
            </button>
            {summary && (
              <div className="doc-answer">
                <div className="doc-answer-head">Summarised by {summary.provider}</div>
                <div className="md-body">{renderMarkdown(summary.text)}</div>
                <div className="tool-row">
                  <button className="tool-btn" onClick={() => navigator.clipboard.writeText(summary.text)}>⧉ Copy</button>
                  <button
                    className="tool-btn"
                    onClick={() => void downloadBlob(new Blob([summary.text], { type: "text/markdown" }), "summary.md")}
                  >
                    ⤓ Save
                  </button>
                  <button
                    className="tool-btn"
                    onClick={() => guard("pdf", async () => {
                      if (!token) return;
                      const blob = await documentsApi.generate(token, { title: file?.name || "Summary", content: summary.text, format: "pdf" });
                      await downloadBlob(blob, "summary.pdf");
                    })}
                  >
                    Export PDF
                  </button>
                </div>
              </div>
            )}

            <h2 className="mt">3 · Ask a question</h2>
            <form
              className="doc-ask"
              onSubmit={(e) => {
                e.preventDefault();
                void guard("ask", async () => {
                  if (!token || !file || !question.trim()) return;
                  const result = await documentsApi.ask(token, file, question.trim());
                  setAnswer(result.answer);
                });
              }}
            >
              <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What is the refund policy?" />
              <button className="tool-btn primary" disabled={!file || !question.trim() || busy === "ask"}>
                {busy === "ask" ? "…" : "Ask"}
              </button>
            </form>
            {answer && <div className="doc-answer md-body">{renderMarkdown(answer)}</div>}
          </section>

          <section className="tool-card">
            <h2>Document generation</h2>
            <p className="tool-note">Turn Markdown into a real PDF, Word, PowerPoint, Excel or CSV file.</p>
            <label className="tool-field">
              <span>Title</span>
              <input value={genTitle} onChange={(e) => setGenTitle(e.target.value)} placeholder="Quarterly report" />
            </label>
            <label className="tool-field">
              <span>Format</span>
              <select value={genFormat} onChange={(e) => setGenFormat(e.target.value)}>
                <option value="pdf">PDF</option>
                <option value="docx">Word (.docx)</option>
                <option value="pptx">PowerPoint (.pptx)</option>
                <option value="xlsx">Excel table (.xlsx)</option>
                <option value="csv">CSV</option>
              </select>
            </label>
            <label className="tool-field">
              <span>Content (Markdown — for XLSX/CSV use a pipe table)</span>
              <textarea value={genContent} onChange={(e) => setGenContent(e.target.value)} rows={9} placeholder="# Report&#10;&#10;- First point&#10;- Second point" />
            </label>
            <button
              className="tool-btn primary"
              disabled={!genContent.trim() || busy === "generate"}
              onClick={() =>
                guard("generate", async () => {
                  if (!token) return;
                  const blob = await documentsApi.generate(token, {
                    title: genTitle,
                    content: genContent,
                    format: genFormat,
                    filename: genTitle || "document",
                  });
                  await downloadBlob(blob, `${genTitle || "document"}.${genFormat}`);
                })
              }
            >
              {busy === "generate" ? "Generating…" : "⤓ Generate file"}
            </button>
          </section>
        </div>
      </div>
    </AppShell>
  );
};

export default Documents;
