import React, { useCallback, useEffect, useState } from "react";
import { useAuth } from "../contexts/AuthContext";
import { apiBaseUrl, readErrorDetail } from "../api/client";

type Provider = { id: number; name: string; enabled: boolean; free_only: boolean; models: number };
type ModelRow = {
  id: number;
  provider: string;
  provider_enabled: boolean;
  model: string;
  enabled: boolean;
  is_free: boolean;
  capabilities: string[];
  availability: string;
  verified_at: string | null;
  restrictions: string[];
};
type Usage = { days: number; total_requests: number; total_units: number; by_provider: Record<string, number>; by_day: Record<string, number>; unique_users: number };
type ProviderBudget = { provider: string; bucket: string; period: string; window_key: string; limit_units: number; used_units: number; reserved_units: number; unit_name: string };
type BudgetReport = { configured_limits: { muse_spark_monthly_usd: number; muse_image_monthly_usd: number; cloudflare_daily_neurons: number; cloudflare_bucket_share: number }; readiness: { chat: Record<string, boolean>; research: { search_credentials: boolean; cloudflare_analysis: boolean; muse_analysis: boolean }; image: { cloudflare_generation: boolean; cloudflare_editing: boolean; metering_units: number; orchestration_enabled: boolean } }; buckets: ProviderBudget[] };
type LogRow = { id: number; user_id: number | null; provider: string; model: string; status: string; units: number; request_id: string; created_at: string | null };
type UserRow = { id: number; email: string; full_name: string | null; role: string; is_active: boolean; is_admin: boolean; image_generation?: boolean; video_generation?: boolean };
type CapabilityQuota = { daily_limit: number; daily_used: number; reserved: number };
type Quota = { daily_limit: number; per_chat_limit: number; daily_used: number; image: CapabilityQuota; video: CapabilityQuota };

const FEATURES = ["research", "images", "documents", "office", "voice", "video_generation"] as const;

/**
 * Admin console for the AI gateway. Everything here is driven by the backend:
 * provider toggles, verified models, per-user limits, usage logs and feature
 * toggles. Model verification is mandatory before a route becomes selectable.
 */
const AdminConsole: React.FC<{ tab: "providers" | "quotas" | "usage" | "logs" | "features" }> = ({ tab }) => {
  const { token } = useAuth();
  const [providers, setProviders] = useState<Provider[]>([]);
  const [models, setModels] = useState<ModelRow[]>([]);
  const [order, setOrder] = useState<string[]>([]);
  const [allowed, setAllowed] = useState<string[]>([]);
  const [mode, setMode] = useState("");
  const [users, setUsers] = useState<UserRow[]>([]);
  const [selectedUser, setSelectedUser] = useState<number | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [usage, setUsage] = useState<Usage | null>(null);
  const [budgetReport, setBudgetReport] = useState<BudgetReport | null>(null);
  const [logs, setLogs] = useState<LogRow[]>([]);
  const [features, setFeatures] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState({ provider: "gemini", model: "", capabilities: "chat", is_free: true, verified: false, enabled: false });
  const [probe, setProbe] = useState<{ state: "idle" | "running" | "ok" | "fail"; message: string }>({ state: "idle", message: "" });

  const request = useCallback(
    async (path: string, init: RequestInit = {}) => {
      const response = await fetch(`${apiBaseUrl}/api/v1/admin${path}`, {
        ...init,
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...(init.headers || {}) },
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(readErrorDetail((data as { detail?: unknown }).detail, "Admin request failed"));
      return data;
    },
    [token],
  );

  const run = async (task: () => Promise<void>) => {
    try {
      setError("");
      await task();
    } catch (err: any) {
      setError(err?.message || "Request failed");
    }
  };

  const loadProviders = useCallback(async () => {
    if (!token) return;
    await run(async () => {
      const [providerRows, modelRows, orderInfo] = await Promise.all([
        request("/providers"),
        request("/providers/models"),
        request("/gateway/order"),
      ]);
      setProviders(providerRows);
      setModels(modelRows);
      setOrder(orderInfo.order ?? []);
      setAllowed(orderInfo.allowed ?? []);
      setMode(orderInfo.mode ?? "");
    });
  }, [token, request]);

  const loadQuotas = useCallback(async () => {
    if (!token) return;
    await run(async () => {
      const [userRows, permissionRows] = await Promise.all([request("/users"), request("/video/users")]);
      const permissions = new Map(permissionRows.map((row: UserRow) => [row.id, row]));
      setUsers(userRows.map((user: UserRow) => ({ ...user, image_generation: permissions.get(user.id)?.image_generation ?? false, video_generation: permissions.get(user.id)?.video_generation ?? false })));
    });
  }, [token, request]);

  useEffect(() => {
    if (tab === "providers") loadProviders();
    if (tab === "quotas") loadQuotas();
    if (tab === "features")
      run(async () => {
        const data = await request("/settings");
        setFeatures(data.features || {});
      });
    if (tab === "usage")
      run(async () => {
        const [usageData, budgetData] = await Promise.all([
          request<Usage>("/analytics/usage?days=14"),
          request<BudgetReport>("/provider-budgets"),
        ]);
        setUsage(usageData);
        setBudgetReport(budgetData);
      });
    if (tab === "logs")
      run(async () => {
        setLogs(await request("/logs?limit=100"));
      });
  }, [tab, loadProviders, loadQuotas, request]);

  useEffect(() => {
    if (!token || selectedUser == null) return;
    run(async () => {
      try {
        const data = await request(`/users/${selectedUser}/quotas`);
        setQuota({ ...data.quotas.chat, image: data.quotas.image, video: data.quotas.video });
      } catch {
        setQuota({ daily_limit: 0, per_chat_limit: 20, daily_used: 0, image: { daily_limit: 0, daily_used: 0, reserved: 0 }, video: { daily_limit: 0, daily_used: 0, reserved: 0 } });
      }
    });
  }, [selectedUser, token, request]);

  const toggleProvider = (provider: Provider) =>
    run(async () => {
      await request(`/providers/${provider.id}?enabled=${!provider.enabled}`, { method: "PATCH" });
      setNotice(`${provider.name} ${provider.enabled ? "disabled" : "enabled"}`);
      await loadProviders();
    });

  const verifyModel = () =>
    run(async () => {
      if (!form.model.trim()) throw new Error("Model id is required");
      setProbe({ state: "running", message: "Sending one test request to the provider…" });
      try {
        const result = await request("/providers/verify", {
          method: "POST",
          body: JSON.stringify({ provider: form.provider, model: form.model.trim(), capability: form.capabilities.split(",")[0]?.trim() || "chat" }),
        });
        setProbe({ state: "ok", message: `${result.message} (${result.latency_ms} ms, sample: “${result.sample}”)` });
        setForm((current) => ({ ...current, verified: true }));
        await loadProviders();
      } catch (err: any) {
        setProbe({ state: "fail", message: err?.message || "Probe failed" });
        throw err;
      }
    });

  const saveModel = () =>
    run(async () => {
      if (!form.model.trim()) throw new Error("Model id is required");
      if (form.enabled && !form.verified) throw new Error("Verify the model (free + available) before enabling it");
      await request("/providers/models", {
        method: "PUT",
        body: JSON.stringify({
          provider: form.provider,
          provider_enabled: form.enabled || providers.find((p) => p.name === form.provider)?.enabled || false,
          model: form.model.trim(),
          enabled: form.enabled,
          is_free: form.is_free,
          capabilities: form.capabilities.split(",").map((item) => item.trim()).filter(Boolean),
          availability: form.verified ? "available" : "unknown",
          verified_at: form.verified ? new Date().toISOString() : null,
          restrictions: form.verified ? [] : ["not verified by admin"],
        }),
      });
      setNotice(`Model ${form.model} saved`);
      setForm({ ...form, model: "" });
      await loadProviders();
    });

  const moveProvider = (name: string, direction: -1 | 1) =>
    run(async () => {
      const next = [...order];
      const index = next.indexOf(name);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= next.length) return;
      [next[index], next[target]] = [next[target], next[index]];
      const data = await request("/gateway/order", { method: "PUT", body: JSON.stringify({ order: next }) });
      setOrder(data.order);
      setNotice("Routing order updated");
    });

  const saveQuota = () =>
    run(async () => {
      if (selectedUser == null || !quota) return;
      if (quota.daily_limit < 1 || quota.per_chat_limit < 1) throw new Error("Limits must be at least 1");
      await request(`/users/${selectedUser}/quota`, {
        method: "PUT",
        body: JSON.stringify({ daily_limit: quota.daily_limit, per_chat_limit: quota.per_chat_limit }),
      });
      for (const capability of ["image", "video"] as const) {
        await request(`/users/${selectedUser}/quotas/${capability}`, {
          method: "PUT",
          body: JSON.stringify({ capability, daily_limit: quota[capability].daily_limit }),
        });
      }
      setNotice("Quota saved");
    });

  const toggleFeature = (key: string, enabled: boolean) =>
    run(async () => {
      const data = await request("/settings/features", { method: "PUT", body: JSON.stringify({ key, enabled }) });
      setFeatures(data.features);
      setNotice(`${key} ${enabled ? "enabled" : "disabled"}`);
    });

  const toggleUserPermission = (userId: number, feature: "image_generation" | "video_generation", enabled: boolean) =>
    run(async () => {
      await request(`/video/users/${userId}/feature`, { method: "PATCH", body: JSON.stringify({ feature, enabled }) });
      setUsers((current) => current.map((user) => user.id === userId ? { ...user, [feature]: enabled } : user));
      setNotice(`${feature === "image_generation" ? "Image" : "Video"} access ${enabled ? "granted" : "revoked"}`);
    });

  return (
    <section className="admin-console">
      {error && <p className="admin-error" role="alert">{error}</p>}
      {notice && <p className="admin-notice">{notice}</p>}

      {tab === "providers" && (
        <>
          <div className="admin-grid two">
            <article className="admin-card">
              <h2>Providers</h2>
              <p className="admin-hint">Gateway mode: <strong>{mode || "unknown"}</strong>. Only free-tier providers can be enabled.</p>
              {providers.length === 0 && <p className="admin-hint">No providers configured yet. Add one on the left, or register a model below.</p>}
              {providers.map((provider) => (
                <div className="admin-row" key={provider.id}>
                  <div>
                    <strong>{provider.name}</strong>
                    <small>{provider.models} model(s) · free only: {String(provider.free_only)}</small>
                  </div>
                  <button className={provider.enabled ? "status-on" : "status-off"} onClick={() => toggleProvider(provider)}>
                    {provider.enabled ? "Enabled" : "Disabled"}
                  </button>
                </div>
              ))}
              <h2 className="admin-mt">Routing order</h2>
              <p className="admin-hint">Fallback order used when a provider fails. Allowed: {allowed.join(", ") || "—"}</p>
              {order.map((name, index) => (
                <div className="admin-row" key={name}>
                  <strong>{index + 1}. {name}</strong>
                  <span>
                    <button onClick={() => moveProvider(name, -1)}>↑</button>
                    <button onClick={() => moveProvider(name, 1)}>↓</button>
                  </span>
                </div>
              ))}
            </article>

            <article className="admin-card">
              <h2>Register / verify a model</h2>
              <div className="admin-form">
                <label>Provider
                  <input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} />
                </label>
                <label>Model id
                  <input value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} placeholder="llama-3.3-70b-versatile" />
                </label>
                <label>Capabilities (comma separated)
                  <input value={form.capabilities} onChange={(e) => setForm({ ...form, capabilities: e.target.value })} placeholder="chat, image, vision" />
                </label>
                <label className="admin-check"><input type="checkbox" checked={form.is_free} onChange={(e) => setForm({ ...form, is_free: e.target.checked })} /> Verified free-tier model</label>
                <label className="admin-check"><input type="checkbox" checked={form.verified} onChange={(e) => setForm({ ...form, verified: e.target.checked })} /> Availability checked (run the probe below)</label>
                <label className="admin-check"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Enable for routing</label>
                <div className="admin-probe">
                  <button type="button" className="admin-probe-btn" onClick={verifyModel} disabled={probe.state === "running" || !form.model.trim()}>
                    {probe.state === "running" ? "Testing…" : "◎ Test connection"}
                  </button>
                  {probe.message && <p className={`admin-probe-msg ${probe.state}`}>{probe.message}</p>}
                </div>
                <button className="primary-button" onClick={saveModel} disabled={form.enabled && !form.verified}>Save model</button>
              </div>
            </article>
          </div>

          <div className="admin-table-wrap">
            <table>
              <thead><tr><th>Provider</th><th>Model</th><th>Capabilities</th><th>Free</th><th>Availability</th><th>Enabled</th></tr></thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.id}>
                    <td>{model.provider}</td>
                    <td><strong>{model.model}</strong>{model.restrictions.length > 0 && <small>{model.restrictions.join(" · ")}</small>}</td>
                    <td>{model.capabilities.join(", ") || "—"}</td>
                    <td>{model.is_free ? "Yes" : "No"}</td>
                    <td>{model.availability}</td>
                    <td><span className={model.enabled ? "status-on" : "status-off"}>{model.enabled ? "On" : "Off"}</span></td>
                  </tr>
                ))}
                {models.length === 0 && <tr><td colSpan={6}>No verified models yet — routes stay disabled until an admin registers one.</td></tr>}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "quotas" && (
        <div className="admin-grid two">
          <article className="admin-card">
            <h2>Users</h2>
            {users.map((user) => (
              <button key={user.id} className={`admin-row admin-row-btn ${selectedUser === user.id ? "active" : ""}`} onClick={() => setSelectedUser(user.id)}>
                <div><strong>{user.full_name || user.email}</strong><small>{user.email} · {user.role}</small></div>
                <span className={user.is_active ? "status-on" : "status-off"}>{user.is_active ? "Active" : "Disabled"}</span>
              </button>
            ))}
          </article>
          <article className="admin-card">
            <h2>Limits</h2>
            {quota ? (
              <div className="admin-form">
                <p className="admin-hint">Used today: {quota.daily_used}</p>
                <label>Chat daily limit
                  <input type="number" min={1} value={quota.daily_limit} onChange={(e) => setQuota({ ...quota, daily_limit: Number(e.target.value) })} />
                </label>
                <label>Image daily limit
                  <input type="number" min={0} value={quota.image.daily_limit} onChange={(e) => setQuota({ ...quota, image: { ...quota.image, daily_limit: Number(e.target.value) } })} />
                </label>
                <label>Video daily limit
                  <input type="number" min={0} value={quota.video.daily_limit} onChange={(e) => setQuota({ ...quota, video: { ...quota.video, daily_limit: Number(e.target.value) } })} />
                </label>
                <label>Per-chat limit
                  <input type="number" min={1} value={quota.per_chat_limit} onChange={(e) => setQuota({ ...quota, per_chat_limit: Number(e.target.value) })} />
                </label>
                <button className="primary-button" onClick={saveQuota}>Save limits</button>
                {selectedUser != null && <>
                  <h3 className="admin-mt">Media access</h3>
                  <p className="admin-hint">Users cannot generate images or videos until access is granted here.</p>
                  {(["image_generation", "video_generation"] as const).map((feature) => {
                    const user = users.find((item) => item.id === selectedUser);
                    const enabled = Boolean(user?.[feature]);
                    return <div className="admin-row" key={feature}><strong>{feature === "image_generation" ? "Image generation" : "Video generation"}</strong><button className={enabled ? "status-on" : "status-off"} onClick={() => toggleUserPermission(selectedUser, feature, !enabled)}>{enabled ? "Granted" : "Revoked"}</button></div>;
                  })}
                </>}
              </div>
            ) : (
              <p className="admin-hint">Select a user to edit their daily and per-chat limits.</p>
            )}
          </article>
        </div>
      )}

      {tab === "usage" && usage && (
        <>
          <div className="admin-cards">
            {[
              ["Requests (14d)", usage.total_requests],
              ["Units consumed", usage.total_units],
              ["Unique users", usage.unique_users],
              ["Providers used", Object.keys(usage.by_provider).length],
              ["Days with traffic", Object.keys(usage.by_day).length],
            ].map(([label, value]) => (
              <article key={String(label)}><span>{label}</span><strong>{value}</strong></article>
            ))}
          </div>
          <article className="admin-card">
            <h2>By provider</h2>
            {Object.entries(usage.by_provider).map(([name, units]) => (
              <div className="admin-row" key={name}><strong>{name}</strong><span>{units} units</span></div>
            ))}
            {Object.keys(usage.by_provider).length === 0 && <p className="admin-hint">No AI usage recorded yet.</p>}
            <h2 className="admin-mt">Daily volume</h2>
            <div className="admin-bars">
              {Object.entries(usage.by_day).map(([day, units]) => {
                const max = Math.max(...Object.values(usage.by_day), 1);
                return (
                  <div className="admin-bar" key={day} title={`${day}: ${units}`}>
                    <span style={{ height: `${Math.max(4, (units / max) * 100)}%` }} />
                    <small>{day.slice(5)}</small>
                  </div>
                );
              })}
            </div>
          </article>
          {budgetReport && (
            <article className="admin-card">
              <h2>Provider budget guardrails</h2>
              <p className="admin-hint">Backend-enforced limits; reserved units are held for in-flight requests.</p>
              <div className="admin-row"><strong>Muse Spark / month</strong><span>${budgetReport.configured_limits.muse_spark_monthly_usd.toFixed(2)}</span></div>
              <div className="admin-row"><strong>Muse Image / month</strong><span>${budgetReport.configured_limits.muse_image_monthly_usd.toFixed(2)}</span></div>
              <div className="admin-row"><strong>Cloudflare / day</strong><span>{budgetReport.configured_limits.cloudflare_daily_neurons.toLocaleString()} neurons · 50% per bucket</span></div>
              <h3 className="admin-subhead">Verified readiness</h3>
              <div className="admin-row"><strong>Chat providers</strong><span>{Object.entries(budgetReport.readiness.chat).filter(([, ready]) => ready).map(([name]) => name).join(", ") || "none"}</span></div>
              <div className="admin-row"><strong>Research</strong><span>{budgetReport.readiness.research.search_credentials ? "search configured" : "search missing"} · {[budgetReport.readiness.research.cloudflare_analysis && "Cloudflare", budgetReport.readiness.research.muse_analysis && "Muse"].filter(Boolean).join(" + ") || "no analysis route"}</span></div>
              <div className="admin-row"><strong>Images</strong><span>{budgetReport.readiness.image.cloudflare_generation ? "Cloudflare generation ready" : "Cloudflare generation unavailable"} · {budgetReport.readiness.image.cloudflare_editing ? "editing verified" : "editing unavailable"}</span></div>
              {budgetReport.buckets.map((bucket) => (
                <div className="admin-row" key={`${bucket.provider}:${bucket.bucket}:${bucket.period}`}>
                  <div><strong>{bucket.provider} · {bucket.bucket}</strong><small>{bucket.window_key} · {bucket.unit_name}</small></div>
                  <span>{bucket.used_units} used · {bucket.reserved_units} reserved / {bucket.limit_units}</span>
                </div>
              ))}
              {budgetReport.buckets.length === 0 && <p className="admin-hint">No provider budget has been consumed yet.</p>}
            </article>
          )}
        </>
      )}

      {tab === "logs" && (
        <div className="admin-table-wrap">
          <table>
            <thead><tr><th>When</th><th>User</th><th>Provider</th><th>Model</th><th>Status</th><th>Units</th><th>Request</th></tr></thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id}>
                  <td>{log.created_at ? new Date(log.created_at).toLocaleString() : "—"}</td>
                  <td>{log.user_id ?? "—"}</td>
                  <td>{log.provider}</td>
                  <td>{log.model}</td>
                  <td><span className={log.status === "COMMITTED" ? "status-on" : "status-off"}>{log.status}</span></td>
                  <td>{log.units}</td>
                  <td><code>{log.request_id.slice(0, 10)}</code></td>
                </tr>
              ))}
              {logs.length === 0 && <tr><td colSpan={7}>No usage logs yet.</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {tab === "features" && (
        <article className="admin-card">
          <h2>Feature toggles</h2>
          <p className="admin-hint">A disabled feature stops answering immediately (503) for every user — no code change needed.</p>
          {FEATURES.map((key) => (
            <div className="admin-row" key={key}>
              <strong>{key}</strong>
              <button
                className={features[key] === false ? "status-off" : "status-on"}
                onClick={() => toggleFeature(key, features[key] === false)}
              >
                {features[key] === false ? "Disabled" : "Enabled"}
              </button>
            </div>
          ))}
        </article>
      )}
    </section>
  );
};

export default AdminConsole;
