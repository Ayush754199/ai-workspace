import React, { createContext, useContext, useState, useEffect, ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { apiBaseUrl, requestJson } from "../api/client";

interface User {
  id: number | string;
  email: string;
  mobile_number: string | null;
  full_name: string | null;
  role: string;
  is_active: boolean;
  is_admin: boolean;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  token: string | null;
  login: (identifier: string, password: string) => Promise<void>;
  register: (email: string, mobile_number: string, password: string, full_name: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();

  const loadUser = async (accessToken: string) =>
    requestJson<User>(
      `${apiBaseUrl}/auth/me`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
      "Session expired",
    );

  useEffect(() => {
    const storedToken = localStorage.getItem("auth_token");
    setToken(storedToken);

    let cancelled = false;
    const restoreSession = async () => {
      if (storedToken) {
        try {
          const currentUser = await loadUser(storedToken);
          if (!cancelled) setUser(currentUser);
        } catch {
          if (!cancelled) {
            localStorage.removeItem("auth_token");
            setToken(null);
            setUser(null);
          }
        }
      }
      if (!cancelled) setIsLoading(false);
    };

    void restoreSession();
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  const login = async (identifier: string, password: string) => {
    const data = await requestJson<{ access_token: string }>(
      `${apiBaseUrl}/auth/login`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: identifier.trim(), password }),
      },
      "Login failed",
    );
    try {
      const currentUser = await loadUser(data.access_token);
      setToken(data.access_token);
      localStorage.setItem("auth_token", data.access_token);
      setUser(currentUser);
      navigate(`/assistant${location.search}`);
    } catch (error: any) {
      localStorage.removeItem("auth_token");
      throw new Error(error?.message || "Could not load your profile after signing in");
    }
  };

  const register = async (email: string, mobile_number: string, password: string, full_name: string) => {
    await requestJson(
      `${apiBaseUrl}/auth/register`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), mobile_number: mobile_number.trim(), password, full_name: full_name.trim() }),
      },
      "Registration failed",
    );
    navigate("/login");
  };

  const logout = () => {
    setToken(null);
    setUser(null);
    localStorage.removeItem("auth_token");
    navigate("/login", { replace: true });
  };

  if (isLoading) {
    return <h1>Loading...</h1>;
  }

  return (
    <AuthContext.Provider value={{ user, token, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
};

export { AuthContext };
