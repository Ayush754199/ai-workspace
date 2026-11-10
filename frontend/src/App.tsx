import React from "react";
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from "react-router-dom";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import ChatWorkspace from "./pages/ChatWorkspace";
import Documents from "./pages/Documents";
import ImageWorkspace from "./pages/ImageWorkspace";
import Office from "./pages/Office";
import Research from "./pages/Research";
import { AuthProvider } from "./contexts/AuthContext";
import AdminDashboard from "./pages/AdminDashboard";
import Profile from "./pages/Profile";
import { useAuth } from "./contexts/AuthContext";
import { CallProvider } from "./calls/CallProvider";
import Calls from "./pages/Calls";

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, isLoading } = useAuth();
  const location = useLocation();
  if (isLoading) return <h1>Loading...</h1>;
  return user ? <>{children}</> : <Navigate to={`/login${location.search}`} replace />;
};

const AdminRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, isLoading } = useAuth();
  if (isLoading) return <h1>Loading...</h1>;
  if (!user) return <Navigate to="/login" replace />;
  return user.is_admin ? <>{children}</> : <Navigate to="/assistant" replace />;
};

const App: React.FC = () => {
  return (
    <Router>
      <AuthProvider>
        <CallProvider><Routes>
          <Route path="/" element={<Navigate to="/assistant" replace />} />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/rooms" element={<ProtectedRoute><Dashboard /></ProtectedRoute>} />
          <Route path="/calls" element={<ProtectedRoute><Calls /></ProtectedRoute>} />
          {/* Legacy room URLs should land on the default assistant experience. */}
          <Route path="/dashboard" element={<Navigate to="/assistant" replace />} />
          <Route path="/chat" element={<Navigate to="/assistant" replace />} />
          <Route path="/assistant" element={<ProtectedRoute><ChatWorkspace /></ProtectedRoute>} />
          <Route path="/documents" element={<ProtectedRoute><Documents /></ProtectedRoute>} />
          <Route path="/images" element={<ProtectedRoute><ImageWorkspace /></ProtectedRoute>} />
          <Route path="/office" element={<ProtectedRoute><Office /></ProtectedRoute>} />
          <Route path="/research" element={<ProtectedRoute><Research /></ProtectedRoute>} />
          <Route path="/profile" element={<ProtectedRoute><Profile /></ProtectedRoute>} />
          <Route path="/admin" element={<AdminRoute><AdminDashboard /></AdminRoute>} />
        </Routes></CallProvider>
      </AuthProvider>
    </Router>
  );
};

export default App;
