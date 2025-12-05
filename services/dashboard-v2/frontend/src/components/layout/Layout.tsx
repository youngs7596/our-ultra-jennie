import { Outlet } from 'react-router-dom'
import { Sidebar } from './Sidebar'

export function Layout() {
  return (
    <div className="min-h-screen bg-jennie-darker bg-grid-pattern">
      {/* Noise Overlay */}
      <div className="noise-overlay" />
      
      {/* Gradient Background */}
      <div className="fixed inset-0 pointer-events-none">
        <div className="absolute top-0 left-1/4 w-96 h-96 bg-jennie-purple/20 rounded-full blur-[128px]" />
        <div className="absolute bottom-0 right-1/4 w-96 h-96 bg-jennie-pink/20 rounded-full blur-[128px]" />
        <div className="absolute top-1/2 right-0 w-64 h-64 bg-jennie-blue/10 rounded-full blur-[100px]" />
      </div>

      {/* Sidebar */}
      <Sidebar />

      {/* Main Content */}
      <main className="ml-[280px] min-h-screen relative">
        <div className="p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

