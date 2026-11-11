import { useState } from 'react';
import { useAuth } from '../auth';
import { Roles } from './Roles';
import { Profile } from './Profile';
import { Pipeline } from './Pipeline';
import { Employers } from './Employers';

const TABS = [
  { id: 'roles', label: 'Roles' },
  { id: 'employers', label: 'Employers' },
  { id: 'pipeline', label: 'Pipeline' },
  { id: 'profile', label: 'Profile' },
];

export function Shell() {
  const [activeTab, setActiveTab] = useState('roles');
  const { logout } = useAuth();

  return (
    <div className="shell">
      <header className="top-bar">
        <h1>Job Search Agent</h1>
        <nav className="tabs" role="tablist">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              className={activeTab === tab.id ? 'active' : ''}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <button className="logout-button" onClick={logout}>
          Log out
        </button>
      </header>
      <main className="tab-content">
        {activeTab === 'roles' && <Roles />}
        {activeTab === 'employers' && <Employers />}
        {activeTab === 'pipeline' && <Pipeline />}
        {activeTab === 'profile' && <Profile />}
      </main>
    </div>
  );
}
