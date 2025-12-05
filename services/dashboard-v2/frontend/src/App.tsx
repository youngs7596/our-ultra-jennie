import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { Layout } from '@/components/layout/Layout'
import { LoginPage } from '@/pages/Login'
import { OverviewPage } from '@/pages/Overview'
import { PortfolioPage } from '@/pages/Portfolio'
import { ScoutPage } from '@/pages/Scout'
import { SystemPage } from '@/pages/System'
import { NewsPage } from '@/pages/News'
import { SettingsPage } from '@/pages/Settings'
import { useAuthStore } from '@/store/authStore'

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const checkAuth = useAuthStore((state) => state.checkAuth)

  // 토큰 유효성 확인
  if (!isAuthenticated || !checkAuth()) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Public Routes */}
        <Route path="/login" element={<LoginPage />} />

        {/* Protected Routes */}
        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<OverviewPage />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="scout" element={<ScoutPage />} />
          <Route path="system" element={<SystemPage />} />
          <Route path="news" element={<NewsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        {/* Fallback */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App

