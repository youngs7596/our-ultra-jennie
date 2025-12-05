import { motion } from 'framer-motion'
import {
  Settings as SettingsIcon,
  Bell,
  Database,
  Brain,
  Shield,
  Moon,
  Sun,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'

export function SettingsPage() {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6 max-w-4xl"
    >
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold flex items-center gap-3">
          <SettingsIcon className="w-8 h-8 text-muted-foreground" />
          Settings
        </h1>
        <p className="text-muted-foreground mt-1">
          대시보드 및 트레이딩 설정을 관리합니다
        </p>
      </div>

      {/* Theme */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Moon className="w-5 h-5" />
            테마
          </CardTitle>
          <CardDescription>대시보드 테마를 설정합니다</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex gap-3">
            <Button variant="default" className="gap-2">
              <Moon className="w-4 h-4" />
              다크 모드
            </Button>
            <Button variant="outline" className="gap-2" disabled>
              <Sun className="w-4 h-4" />
              라이트 모드 (준비 중)
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Notifications */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Bell className="w-5 h-5" />
            알림 설정
          </CardTitle>
          <CardDescription>텔레그램 알림을 설정합니다</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between p-3 rounded-lg bg-white/5">
            <div>
              <p className="font-medium">매수 알림</p>
              <p className="text-sm text-muted-foreground">매수 체결 시 알림</p>
            </div>
            <Button variant="outline" size="sm">활성화됨</Button>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-white/5">
            <div>
              <p className="font-medium">매도 알림</p>
              <p className="text-sm text-muted-foreground">매도 체결 시 알림</p>
            </div>
            <Button variant="outline" size="sm">활성화됨</Button>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-white/5">
            <div>
              <p className="font-medium">일일 브리핑</p>
              <p className="text-sm text-muted-foreground">매일 17:00 포트폴리오 요약</p>
            </div>
            <Button variant="outline" size="sm">활성화됨</Button>
          </div>
        </CardContent>
      </Card>

      {/* LLM Settings */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Brain className="w-5 h-5" />
            LLM 설정
          </CardTitle>
          <CardDescription>Scout-Debate-Judge 파이프라인 설정</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="p-3 rounded-lg bg-white/5">
              <p className="text-sm text-muted-foreground">Phase 1 (Hunter)</p>
              <p className="font-medium font-mono">Gemini-2.5-Flash</p>
            </div>
            <div className="p-3 rounded-lg bg-white/5">
              <p className="text-sm text-muted-foreground">Phase 2 (Debate)</p>
              <p className="font-medium font-mono">GPT-4o-mini</p>
            </div>
            <div className="p-3 rounded-lg bg-white/5">
              <p className="text-sm text-muted-foreground">Phase 3 (Judge)</p>
              <p className="font-medium font-mono">GPT-4o-mini</p>
            </div>
            <div className="p-3 rounded-lg bg-white/5">
              <p className="text-sm text-muted-foreground">News Sentiment</p>
              <p className="font-medium font-mono">Gemini-2.5-Flash</p>
            </div>
          </div>
          <div className="p-3 rounded-lg bg-white/5">
            <p className="text-sm text-muted-foreground mb-2">Scout Job 실행 주기</p>
            <p className="font-medium">1시간 (SCOUT_MIN_LLM_INTERVAL_MINUTES: 60)</p>
          </div>
        </CardContent>
      </Card>

      {/* Database */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="w-5 h-5" />
            데이터베이스
          </CardTitle>
          <CardDescription>연결된 데이터베이스 정보</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between p-3 rounded-lg bg-white/5">
            <div>
              <p className="font-medium">Primary DB</p>
              <p className="text-sm text-muted-foreground">MariaDB (WSL2 Local)</p>
            </div>
            <span className="px-2 py-1 rounded-full text-xs bg-profit-positive/20 text-profit-positive">
              연결됨
            </span>
          </div>
          <div className="flex items-center justify-between p-3 rounded-lg bg-white/5">
            <div>
              <p className="font-medium">Secondary DB</p>
              <p className="text-sm text-muted-foreground">MariaDB (Local)</p>
            </div>
            <span className="px-2 py-1 rounded-full text-xs bg-muted text-muted-foreground">
              대기
            </span>
          </div>
        </CardContent>
      </Card>

      {/* Security */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="w-5 h-5" />
            보안
          </CardTitle>
          <CardDescription>계정 보안 설정</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm text-muted-foreground">현재 비밀번호</label>
            <Input type="password" placeholder="••••••••" disabled />
          </div>
          <div className="space-y-2">
            <label className="text-sm text-muted-foreground">새 비밀번호</label>
            <Input type="password" placeholder="새 비밀번호 입력" disabled />
          </div>
          <Button disabled>비밀번호 변경 (준비 중)</Button>
        </CardContent>
      </Card>

      {/* Version Info */}
      <Card className="border-white/5">
        <CardContent className="p-4">
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>Dashboard Version</span>
            <span className="font-mono">v2.0.0</span>
          </div>
          <div className="flex items-center justify-between text-sm text-muted-foreground mt-2">
            <span>Contributors</span>
            <span>GPT-5.1-Codex, Gemini-3.0-Pro, Claude Opus 4.5</span>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  )
}

