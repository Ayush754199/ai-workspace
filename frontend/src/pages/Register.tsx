import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

const Register: React.FC = () => {
  const [email, setEmail] = useState("");
  const [mobile_number, setMobile_number] = useState("");
  const [password, setPassword] = useState("");
  const [full_name, setFull_name] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { register } = useAuth();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (loading) return;
    setLoading(true);
    setError("");
    try {
      await register(email, mobile_number, password, full_name);
    } catch (err: any) {
      setError(err?.message || "Registration failed");
      setLoading(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-glow auth-glow-one" /><div className="auth-glow auth-glow-two" />
      <div className="auth-card">
        <div className="auth-brand"><span>✦</span><div><strong>AI Workspace</strong><small>Secure creative workspace</small></div></div>
        <div className="auth-heading"><p className="eyebrow">Get started</p><h2>Create your workspace account</h2><p>Bring conversations, images and video projects together.</p></div>
        <form onSubmit={handleSubmit} className="auth-form">
          <div>
            <label className="auth-label">
              Mobile number
            </label>
            <input
              value={mobile_number}
              onChange={(e) => setMobile_number(e.target.value)}
              type="tel"
              name="mobile_number"
              autoComplete="tel"
              required
              placeholder="+91 98765 43210"
              className="auth-input"
            />
          </div>
          <div>
            <label className="auth-label">
              Email
            </label>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              type="email"
              name="email"
              required
              className="auth-input"
            />
          </div>
          <div>
            <label className="auth-label">
              Password
            </label>
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              name="password"
              required
              className="auth-input"
            />
          </div>
          <div>
            <label className="auth-label">
              Full Name
            </label>
            <input
              value={full_name}
              onChange={(e) => setFull_name(e.target.value)}
              type="text"
              name="full_name"
              required
              className="auth-input"
            />
          </div>
          {error && <p className="auth-error" role="alert">{error}</p>}
          <button
            type="submit"
            disabled={loading || !mobile_number.trim()}
            className="auth-submit">
            {loading ? "Creating account..." : "Sign Up"}
          </button>
        </form>
        <p className="auth-switch">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
};

export default Register;
