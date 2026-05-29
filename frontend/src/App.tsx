import { Route, Routes } from 'react-router-dom';
import TopBar from './components/layout/TopBar';
import StatusBar from './components/layout/StatusBar';
import HomePage from './pages/HomePage';
import NewsPage from './pages/NewsPage';
import MarketsPage from './pages/MarketsPage';
import MacroPage from './pages/MacroPage';
import ABSPage from './pages/ABSPage';
import DealsPage from './pages/DealsPage';
import PrivateCreditPage from './pages/PrivateCreditPage';
import RegulatoryPage from './pages/RegulatoryPage';
import AnalystPage from './pages/AnalystPage';

export default function App() {
  return (
    <div className="app-shell">
      <TopBar />
      <main className="app-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/news" element={<NewsPage />} />
          <Route path="/markets" element={<MarketsPage />} />
          <Route path="/macro" element={<MacroPage />} />
          <Route path="/private-credit" element={<PrivateCreditPage />} />
          <Route path="/regulatory" element={<RegulatoryPage />} />
          <Route path="/abs" element={<ABSPage />} />
          <Route path="/deals" element={<DealsPage />} />
          <Route path="/analyst" element={<AnalystPage />} />
        </Routes>
      </main>
      <StatusBar />
    </div>
  );
}
