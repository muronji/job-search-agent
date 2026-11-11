import { AuthProvider, useAuth } from './auth';
import { Login } from './components/Login';
import { Shell } from './components/Shell';
import './styles.css';

function Router() {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <Shell /> : <Login />;
}

export default function App() {
  return (
    <AuthProvider>
      <Router />
    </AuthProvider>
  );
}
