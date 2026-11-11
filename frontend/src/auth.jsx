import { createContext, useContext, useEffect, useState } from 'react';
import { getToken, clearToken, apiFetch } from './api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setTokenState] = useState(() => getToken());
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let active = true;
    async function validate() {
      const t = getToken();
      if (t) {
        try {
          await apiFetch('/api/profile/');
        } catch {
          // apiFetch clears the token and reloads on 401
        }
      }
      if (active) setChecked(true);
    }
    validate();
    return () => {
      active = false;
    };
  }, []);

  const logout = () => {
    clearToken();
    window.location.reload();
  };

  const value = {
    isAuthenticated: Boolean(token),
    token,
    setToken: (t) => setTokenState(t),
    logout,
  };

  if (!checked && token) {
    return (
      <div className="loading-screen" aria-live="polite">
        <p>Checking session…</p>
      </div>
    );
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within AuthProvider');
  }
  return context;
}
