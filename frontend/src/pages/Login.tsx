import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

const Login: React.FC = () => {
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { login } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setLoading(true);
    setError("");
    try {
      await login(identifier, password);
    } catch (err: any) {
      setError(err?.message || "Login failed");
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-glow auth-glow-one" /><div className="auth-glow auth-glow-two" />
      <div className="auth-card">
        <div className="auth-brand"><span>✦</span><div><strong>AI Workspace</strong><small>Secure creative workspace</small></div></div>
        <div className="auth-heading"><p className="eyebrow">Welcome back</p><h2>Sign in to your workspace</h2><p>Continue your conversations, images and documents.</p></div>
        <form onSubmit={handleSubmit} className="auth-form" noValidate>
          <div>
            <label className="auth-label" htmlFor="login-identifier">
              Email or mobile number
            </label>
            <input
              id="login-identifier"
              value={identifier}
              onChange={(e) => { setIdentifier(e.target.value); if (error) setError(""); }}
              type="text"
              name="identifier"
              autoComplete="username"
              placeholder="you@example.com or +91 98765 43210"
              required
              disabled={loading}
              className="auth-input"
            />
          </div>
          <div>
            <label className="auth-label" htmlFor="login-password">
              Password
            </label>
            <input
              id="login-password"
              value={password}
              onChange={(e) => { setPassword(e.target.value); if (error) setError(""); }}
              type="password"
              name="password"
              autoComplete="current-password"
              required
              disabled={loading}
              className="auth-input"
            />
          </div>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button
            type="submit"
            disabled={loading || !identifier.trim() || !password}
            className="auth-submit">
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>
        <p className="auth-switch">
          Don't have an account? <Link to="/register">Create one</Link>
        </p>
      </div>
    </div>
  );
};

export default Login;
