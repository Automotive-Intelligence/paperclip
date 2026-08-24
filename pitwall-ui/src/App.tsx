import { AnimatePresence } from 'framer-motion';
import { Route, Routes, useLocation } from 'react-router-dom';
import PitWallPage from './pages/PitWallPage';
import TeamPage from './pages/TeamPage';
import AgentPage from './pages/AgentPage';
import InventoryPage from './pages/InventoryPage';
import OrgPage from './pages/OrgPage';
import DashboardShell from './components/DashboardShell';

export default function App() {
  const location = useLocation();
  return (
    <DashboardShell>
      <AnimatePresence mode="wait">
        <Routes location={location} key={location.pathname}>
          <Route path="/" element={<PitWallPage />} />
          <Route path="/dashboard" element={<PitWallPage />} />
          <Route path="/pit-wall" element={<PitWallPage />} />
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/org" element={<OrgPage />} />
          <Route path="/team/:teamId" element={<TeamPage />} />
          <Route path="/team/:teamId/agent/:agentId" element={<AgentPage />} />
        </Routes>
      </AnimatePresence>
    </DashboardShell>
  );
}
