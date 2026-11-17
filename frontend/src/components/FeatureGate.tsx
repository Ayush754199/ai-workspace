import React from "react";
import AppShell from "./AppShell";
import useFeatureFlags from "../hooks/useFeatureFlags";

const LABELS: Record<string, string> = {
  research: "Research",
  images: "Images",
  documents: "Documents",
  office: "Office",
  voice: "Voice",
  video_generation: "Video generation",
};

/**
 * Renders the page only when an administrator has the feature switched on.
 * Otherwise the user sees an explicit disabled state: the same page used to
 * render fully and then fail on every action with 503 "The X feature is
 * disabled by an administrator".
 */
const FeatureGate: React.FC<{ feature: string; children: React.ReactNode }> = ({ feature, children }) => {
  const { features, isEnabled } = useFeatureFlags();

  // While the flags are loading (or if the probe failed) let the page render;
  // the backend is still the authority and answers 503 when disabled.
  if (isEnabled(feature)) return <>{children}</>;

  const label = LABELS[feature] || feature;
  return (
    <AppShell>
      <div className="tool-page">
        <header className="tool-head">
          <div>
            <p className="eyebrow">{label}</p>
            <h1>{label} is currently disabled</h1>
            <p className="page-subtitle">
              An administrator has turned this feature off for the workspace, so it cannot be used right now.
            </p>
          </div>
          <a className="tool-link" href="/assistant">← Back to chat</a>
        </header>
        <div className="feature-disabled" role="status" aria-live="polite">
          <span aria-hidden="true">⊘</span>
          <div>
            <strong>{label} is disabled by an administrator</strong>
            <p>
              Everything you have already created is safe. Ask an administrator to enable {label.toLowerCase()} in
              Admin → Features, and this page will work again{features ? "" : " after a reload"}.
            </p>
          </div>
        </div>
      </div>
    </AppShell>
  );
};

export default FeatureGate;
