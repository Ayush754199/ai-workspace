import { useCallback, useEffect, useState } from "react";
import { chatV2 } from "../api/workspaceApi";
import { useAuth } from "../contexts/AuthContext";

/**
 * Admin feature toggles, as the backend enforces them.
 *
 * A disabled feature answers 503 on every action, so the UI has to know about
 * it up front and show a disabled state instead of a page that looks usable and
 * fails on the first click. Unknown/missing keys default to enabled, exactly
 * like `require_feature` on the server.
 */
export function useFeatureFlags() {
  const { token } = useAuth();
  const [features, setFeatures] = useState<Record<string, boolean> | null>(null);

  const reload = useCallback(() => {
    if (!token) return;
    chatV2
      .features(token)
      .then((data) => setFeatures(data.features || {}))
      // Never lock the UI out because the probe failed; the backend still
      // enforces the toggle on the actual call.
      .catch(() => setFeatures({}));
  }, [token]);

  useEffect(reload, [reload]);

  const isEnabled = (key: string) => features === null || features[key] !== false;

  return { features, isEnabled, reload };
}

export default useFeatureFlags;
