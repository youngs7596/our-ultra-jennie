import { useQuery } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Brain,
  Target,
  Scale,
  Gavel,
  CheckCircle2,
  Clock,
  TrendingUp,
  TrendingDown,
  Sparkles,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { scoutApi, watchlistApi } from '@/lib/api'
import {
  formatRelativeTime,
  getGradeColor,
  getGradeBgColor,
  cn,
} from '@/lib/utils'

const phaseIcons = [Target, Scale, Gavel]
const phaseNames = ['Hunter Scout', 'Bull vs Bear Debate', 'Final Judge']
const phaseDescriptions = [
  'Claude Haiku로 정밀한 1차 필터링',
  'GPT-5-mini로 Bull vs Bear 심층 토론',
  'GPT-5-mini로 최종 의사결정 및 등급 부여',
]
const phaseLLMs = ['Claude Haiku 4.5', 'GPT-5-mini', 'GPT-5-mini']

export function ScoutPage() {
  const { data: status } = useQuery({
    queryKey: ['scout-status'],
    queryFn: scoutApi.getStatus,
    refetchInterval: 5000, // 5초마다 상태 갱신
  })

  const { data: results } = useQuery({
    queryKey: ['scout-results'],
    queryFn: scoutApi.getResults,
    refetchInterval: 30000,
  })

  const { data: watchlist } = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => watchlistApi.getAll(20),
  })

  const isRunning = status?.status === 'running'

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6"
    >
      {/* Header - Slow Brain */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <motion.div
              animate={{ rotate: [0, 5, -5, 0] }}
              transition={{ duration: 4, repeat: Infinity }}
            >
              <Brain className="w-10 h-10 text-jennie-purple" />
            </motion.div>
            <div>
              <h1 className="text-3xl font-display font-bold">
                Scout-Debate-Judge Pipeline
              </h1>
              <p className="text-sm text-jennie-gold font-medium">
                🧠 The Slow Brain of Supreme Jennie
              </p>
            </div>
          </div>
          <p className="text-muted-foreground">
            3단계 Multi-Agent LLM이 신중하게 종목을 선별합니다 • 
            <span className="text-jennie-purple ml-1">1시간마다 시장 상황 재평가</span>
          </p>
        </div>
        {isRunning ? (
          <motion.div
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            className="flex items-center gap-2 px-4 py-2 rounded-full bg-jennie-purple/20 border border-jennie-purple/50"
          >
            <motion.div 
              className="w-2 h-2 rounded-full bg-jennie-purple"
              animate={{ scale: [1, 1.5, 1], opacity: [1, 0.5, 1] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
            <span className="text-sm font-medium text-jennie-purple">분석 중</span>
          </motion.div>
        ) : (
          <div className="flex items-center gap-2 px-4 py-2 rounded-full bg-profit-positive/20 border border-profit-positive/50">
            <CheckCircle2 className="w-4 h-4 text-profit-positive" />
            <span className="text-sm font-medium text-profit-positive">대기 중</span>
          </div>
        )}
      </div>

      {/* Pipeline Visualization */}
      <Card glow={isRunning}>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-jennie-gold" />
            3-Phase LLM Pipeline
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="relative">
            {/* Connection Line */}
            <div className="absolute top-12 left-0 right-0 h-1 bg-gradient-to-r from-jennie-pink via-jennie-purple to-jennie-blue opacity-30" />
            
            {/* Phases */}
            <div className="grid grid-cols-3 gap-4 relative">
              {[1, 2, 3].map((phase) => {
                const PhaseIcon = phaseIcons[phase - 1]
                const isActive = status?.phase === phase
                const isComplete = status?.phase > phase
                const isPending = status?.phase < phase

                return (
                  <motion.div
                    key={phase}
                    initial={{ opacity: 0, y: 20 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: phase * 0.1 }}
                    className={cn(
                      'relative p-6 rounded-xl border-2 transition-all duration-300',
                      isActive && 'border-jennie-purple bg-jennie-purple/10 shadow-lg shadow-jennie-purple/20',
                      isComplete && 'border-profit-positive/50 bg-profit-positive/5',
                      isPending && 'border-white/10 bg-white/5'
                    )}
                  >
                    {/* Phase Number Badge */}
                    <div
                      className={cn(
                        'absolute -top-3 -left-3 w-8 h-8 rounded-full flex items-center justify-center font-bold text-sm',
                        isActive && 'bg-jennie-purple text-white animate-pulse',
                        isComplete && 'bg-profit-positive text-white',
                        isPending && 'bg-white/10 text-muted-foreground'
                      )}
                    >
                      {isComplete ? <CheckCircle2 className="w-5 h-5" /> : phase}
                    </div>

                    {/* Content */}
                    <div className="flex flex-col items-center text-center">
                      <div
                        className={cn(
                          'w-16 h-16 rounded-2xl flex items-center justify-center mb-4',
                          isActive && 'bg-jennie-purple/20',
                          isComplete && 'bg-profit-positive/20',
                          isPending && 'bg-white/10'
                        )}
                      >
                        <PhaseIcon
                          className={cn(
                            'w-8 h-8',
                            isActive && 'text-jennie-purple',
                            isComplete && 'text-profit-positive',
                            isPending && 'text-muted-foreground'
                          )}
                        />
                      </div>

                      <h3 className="font-semibold mb-1">{phaseNames[phase - 1]}</h3>
                      <p className="text-xs text-muted-foreground mb-3">
                        {phaseDescriptions[phase - 1]}
                      </p>

                      {/* LLM Badge */}
                      <div className="px-3 py-1 rounded-full bg-white/5 border border-white/10 text-xs font-mono">
                        {phaseLLMs[phase - 1]}
                      </div>

                      {/* Stats */}
                      <div className="mt-4 text-sm">
                        {phase === 1 && (
                          <span className={cn(isActive && 'text-jennie-purple', isComplete && 'text-profit-positive')}>
                            {status?.passed_phase1 || 0}개 통과
                          </span>
                        )}
                        {phase === 2 && (
                          <span className={cn(isActive && 'text-jennie-purple', isComplete && 'text-profit-positive')}>
                            {status?.passed_phase2 || 0}개 토론
                          </span>
                        )}
                        {phase === 3 && (
                          <span className={cn(isActive && 'text-jennie-purple', isComplete && 'text-profit-positive')}>
                            {status?.final_selected || 0}개 선정
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Active Animation */}
                    {isActive && (
                      <motion.div
                        className="absolute inset-0 rounded-xl border-2 border-jennie-purple"
                        animate={{ opacity: [0.5, 1, 0.5] }}
                        transition={{ duration: 2, repeat: Infinity }}
                      />
                    )}
                  </motion.div>
                )
              })}
            </div>
          </div>

          {/* Current Stock */}
          {status?.current_stock && isRunning && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="mt-6 p-4 rounded-lg bg-white/5 border border-white/10"
            >
              <div className="flex items-center gap-3">
                <div className="w-2 h-2 rounded-full bg-jennie-purple animate-ping" />
                <span className="text-sm text-muted-foreground">현재 분석 중:</span>
                <span className="font-semibold">{status.current_stock}</span>
              </div>
              {status.progress > 0 && (
                <div className="mt-3">
                  <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                    <motion.div
                      className="h-full bg-gradient-to-r from-jennie-pink to-jennie-purple"
                      initial={{ width: 0 }}
                      animate={{ width: `${status.progress}%` }}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 text-right">
                    {status.progress.toFixed(0)}% 완료
                  </p>
                </div>
              )}
            </motion.div>
          )}

          {/* Last Updated */}
          {status?.last_updated && (
            <p className="mt-4 text-xs text-muted-foreground text-right">
              마지막 업데이트: {formatRelativeTime(status.last_updated)}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Results Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Selected Stocks */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-profit-positive" />
              선정된 종목 (Judge 통과)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <AnimatePresence mode="popLayout">
              {results?.results?.filter((r: any) => r.selected)?.length === 0 && (
                <p className="text-center text-muted-foreground py-8">
                  아직 선정된 종목이 없습니다
                </p>
              )}
              <div className="space-y-3">
                {results?.results
                  ?.filter((r: any) => r.selected)
                  ?.map((result: any, i: number) => (
                    <motion.div
                      key={result.stock_code}
                      initial={{ opacity: 0, x: -20 }}
                      animate={{ opacity: 1, x: 0 }}
                      exit={{ opacity: 0, x: 20 }}
                      transition={{ delay: i * 0.05 }}
                      className="p-4 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <div
                            className={cn(
                              'w-10 h-10 rounded-lg flex items-center justify-center font-bold border',
                              getGradeBgColor(result.grade)
                            )}
                          >
                            <span className={getGradeColor(result.grade)}>
                              {result.grade}
                            </span>
                          </div>
                          <div>
                            <h4 className="font-semibold">{result.stock_name}</h4>
                            <p className="text-xs text-muted-foreground font-mono">
                              {result.stock_code}
                            </p>
                          </div>
                        </div>
                        <div className="text-right">
                          <p className="font-mono font-semibold text-profit-positive">
                            {result.final_score}점
                          </p>
                          <p className="text-xs text-muted-foreground">
                            최종 점수
                          </p>
                        </div>
                      </div>
                      {result.judge_reason && (
                        <p className="mt-2 text-sm text-muted-foreground line-clamp-2">
                          {result.judge_reason}
                        </p>
                      )}
                    </motion.div>
                  ))}
              </div>
            </AnimatePresence>
          </CardContent>
        </Card>

        {/* Watchlist with LLM Scores */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Target className="w-5 h-5 text-jennie-blue" />
              Watchlist LLM 점수
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2 max-h-[400px] overflow-y-auto custom-scrollbar">
              {watchlist?.map((item: any, i: number) => (
                <motion.div
                  key={item.stock_code}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: i * 0.02 }}
                  className="flex items-center justify-between p-3 rounded-lg hover:bg-white/5 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={cn(
                        'w-8 h-8 rounded-lg flex items-center justify-center text-xs font-bold border',
                        getGradeBgColor(item.llm_grade || 'C')
                      )}
                    >
                      <span className={getGradeColor(item.llm_grade || 'C')}>
                        {item.llm_grade || '-'}
                      </span>
                    </div>
                    <div>
                      <p className="font-medium text-sm">{item.stock_name}</p>
                      <p className="text-xs text-muted-foreground font-mono">
                        {item.stock_code}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                    {/* LLM Score */}
                    <div className="text-right">
                      <p className="font-mono text-sm">
                        {item.llm_score ? `${item.llm_score}점` : '-'}
                      </p>
                    </div>
                    {/* Sentiment */}
                    {item.news_sentiment !== null && (
                      <div className="flex items-center gap-1">
                        {item.news_sentiment >= 50 ? (
                          <TrendingUp className="w-3 h-3 text-profit-positive" />
                        ) : (
                          <TrendingDown className="w-3 h-3 text-profit-negative" />
                        )}
                        <span className="text-xs font-mono">
                          {item.news_sentiment?.toFixed(0)}
                        </span>
                      </div>
                    )}
                  </div>
                </motion.div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Pipeline Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="p-4 text-center">
            <Clock className="w-6 h-6 mx-auto text-muted-foreground mb-2" />
            <p className="text-2xl font-bold">{status?.total_candidates || 200}</p>
            <p className="text-xs text-muted-foreground">전체 후보</p>
            <p className="text-xs text-jennie-gold mt-1">KOSPI 200</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <Target className="w-6 h-6 mx-auto text-jennie-pink mb-2" />
            <p className="text-2xl font-bold">{status?.passed_phase1 || 0}</p>
            <p className="text-xs text-muted-foreground">Phase 1 통과</p>
            <p className="text-xs text-jennie-pink mt-1">Claude Haiku</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <Scale className="w-6 h-6 mx-auto text-jennie-purple mb-2" />
            <p className="text-2xl font-bold">{status?.passed_phase2 || 0}</p>
            <p className="text-xs text-muted-foreground">Phase 2 토론</p>
            <p className="text-xs text-jennie-purple mt-1">Bull vs Bear</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4 text-center">
            <Gavel className="w-6 h-6 mx-auto text-jennie-blue mb-2" />
            <p className="text-2xl font-bold">{status?.final_selected || 0}</p>
            <p className="text-xs text-muted-foreground">최종 선정</p>
            <p className="text-xs text-profit-positive mt-1">→ Watchlist</p>
          </CardContent>
        </Card>
      </div>

      {/* Slow Brain Philosophy */}
      <Card className="border-dashed border-jennie-purple/30">
        <CardContent className="p-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-full bg-jennie-purple/20 flex items-center justify-center">
                <Brain className="w-6 h-6 text-jennie-purple" />
              </div>
              <div>
                <h4 className="font-semibold text-jennie-purple">Slow Brain 🧠</h4>
                <p className="text-sm text-muted-foreground">신중하게 종목 선별 • UPSERT로 누적 관리</p>
              </div>
            </div>
            <div className="text-center px-4">
              <p className="text-xs text-muted-foreground">→</p>
            </div>
            <div className="flex items-center gap-4">
              <div>
                <h4 className="font-semibold text-jennie-gold">Fast Hand ⚡</h4>
                <p className="text-sm text-muted-foreground">가격 변동 시 빠른 체결</p>
              </div>
              <div className="w-12 h-12 rounded-full bg-jennie-gold/20 flex items-center justify-center">
                <Target className="w-6 h-6 text-jennie-gold" />
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  )
}

