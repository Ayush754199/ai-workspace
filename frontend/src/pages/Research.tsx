import React, { useState } from "react";
import AppShell from "../components/AppShell";
import { useAuth } from "../contexts/AuthContext";
import { ResearchResult, researchApi } from "../api/workspaceApi";
import renderMarkdown from "../components/Markdown";

const SUGGESTIONS = [
  "Latest stable Python release and what changed",
  "How does the EU AI Act classify high-risk systems",
  "Current best practices for PostgreSQL connection pooling",
];

const Research: React.FC = () => {
  const { token } = useAuth();
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<ResearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const run = async (query: string) => {
    if (!token || !query.trim()) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      setResult(await researchApi.query(token, query.trim()));
    } catch (err: any) {
      setError(err?.message || "Research failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppShell>
      <div className="tool-page">
        <header className="tool-head">
          <div>
            <p className="eyebrow">Research</p>
            <h1>Answers grounded in live web sources</h1>
            <p className="page-subtitle">Searches the web, reads the top pages and answers only from those sources — every claim links back.</p>
          </div>
          <a className="tool-link" href="/assistant">← Back to chat</a>
        </header>

        <form
          className="research-form"
          onSubmit={(e) => {
            e.preventDefault();
            void run(question);
          }}
        >
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What do you want to research?" />
          <button className="tool-btn primary" disabled={busy || !question.trim()}>
            {busy ? "Researching…" : "◎ Research"}
          </button>
        </form>

        {!result && !busy && (
          <div className="pill-row">
            {SUGGESTIONS.map((item) => (
              <button
                key={item}
                className="pill"
                onClick={() => {
                  setQuestion(item);
                  void run(item);
                }}
              >
                {item}
              </button>
            ))}
          </div>
        )}

        {error && <div className="tool-error" role="alert">{error}</div>}

        {result && (
          <div className="research-layout">
            <article className="tool-card">
              <h2>Answer</h2>
              {result.answer ? (
                <div className="md-body">{renderMarkdown(result.answer)}</div>
              ) : (
                <p className="tool-note">
                  Sources were found but no model produced an answer{result.error ? `: ${result.error}` : "."} See the sources on the right.
                </p>
              )}
              {result.provider && <p className="tool-note">Answered by {result.provider}</p>}
            </article>
            <aside className="tool-card">
              <h2>Sources ({result.sources.length})</h2>
              <ol className="source-list">
                {result.sources.map((source, index) => (
                  <li key={source.url}>
                    <a href={source.url} target="_blank" rel="noreferrer noopener">
                      [{index + 1}] {source.title}
                    </a>
                    <small>{source.snippet}</small>
                  </li>
                ))}
              </ol>
            </aside>
          </div>
        )}
      </div>
    </AppShell>
  );
};

export default Research;
