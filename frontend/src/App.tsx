import { Route, Routes } from 'react-router-dom';
import TopBar from './components/layout/TopBar';
import StatusBar from './components/layout/StatusBar';
import PullToRefresh from './components/layout/PullToRefresh';
import ToastHost from './components/shared/ToastHost';
import HomePage from './pages/HomePage';
import NewsPage from './pages/NewsPage';
import MarketsPage from './pages/MarketsPage';
import MacroPage from './pages/MacroPage';
import ForecastsPage from './pages/ForecastsPage';
import PrivateCreditPage from './pages/PrivateCreditPage';
import RegulatoryPage from './pages/RegulatoryPage';
import AnalystPage from './pages/AnalystPage';
import WatchlistsPage from './pages/WatchlistsPage';
import EntityLookupPage from './pages/EntityLookupPage';

export default function App() {
  return (
    <div className="app-shell">
      <TopBar />
      <PullToRefresh>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/news" element={<NewsPage />} />
          <Route path="/markets" element={<MarketsPage />} />
          <Route path="/macro" element={<MacroPage />} />
          <Route path="/forecasts" element={<ForecastsPage />} />
          <Route path="/private-credit" element={<PrivateCreditPage />} />
          <Route path="/regulatory" element={<RegulatoryPage />} />
          <Route path="/analyst" element={<AnalystPage />} />
          <Route path="/watchlists" element={<WatchlistsPage />} />
          <Route path="/lookup" element={<EntityLookupPage />} />
        </Routes>
      </PullToRefresh>
      <StatusBar />
      <ToastHost />
    </div>
  );
}
