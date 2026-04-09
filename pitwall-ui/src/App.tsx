import { AnimatePresence } from 'framer-motion';
import { Route, Routes, useLocation } from 'react-router-dom';
import PitWallPage from './pages/PitWallPage';
import TeamPage from './pages/TeamPage';
import AgentPage from './pages/AgentPage';

export default function App() {
  const location = useLocation();
  return (
    <AnimatePresence mode="wait">
      <Routes location={location} key={location.pathname}>
        <Route path="/" element={<PitWallPage />} />
        <Route path="/dashboard" element={<PitWallPage />} />
        <Route path="/pit-wall" element={<PitWallPage />} />
        <Route path="/team/:teamId" element={<TeamPage />} />
        <Route path="/team/:teamId/agent/:agentId" element={<AgentPage />} />
      </Routes>
    </AnimatePresence>
  );
}
