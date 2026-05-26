import { Route, Routes } from 'react-router-dom';
import TopBar from './components/layout/TopBar';
import StatusBar from './components/layout/StatusBar';
import HomePage from './pages/HomePage';
import NewsPage from './pages/NewsPage';
import MarketsPage from './pages/MarketsPage';
import MacroPage from './pages/MacroPage';
import ABSPage from './pages/ABSPage';
import DealsPage from './pages/DealsPage';

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
          <Route path="/abs" element={<ABSPage />} />
          <Route path="/deals" element={<DealsPage />} />
        </Routes>
      </main>
      <StatusBar />
    </div>
  );
}
