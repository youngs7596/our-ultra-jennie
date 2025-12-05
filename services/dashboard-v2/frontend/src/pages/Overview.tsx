import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  TrendingUp,
  TrendingDown,
  Wallet,
  PieChart,
  Activity,
  Brain,
  RefreshCw,
} from 'lucide-react'
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  PieChart as RechartsPie,
  Pie,
  Cell,
} from 'recharts'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { portfolioApi, scoutApi, tradesApi } from '@/lib/api'
import {
  formatCurrency,
  formatPercent,
  formatNumber,
  formatRelativeTime,
  getProfitColor,
} from '@/lib/utils'
import { cn } from '@/lib/utils'

const COLORS = ['#FF6B9D', '#9B5DE5', '#00F5D4', '#FFD93D', '#6366F1']

// Animation variants
const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1,
    },
  },
}

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

interface StatCardProps {
  title: string
  value: string
  subValue?: string
  icon: React.ElementType
  trend?: number
  color?: string
}

function StatCard({ title, value, subValue, icon: Icon, trend, color }: StatCardProps) {
  return (
    <motion.div variants={itemVariants}>
      <Card className="overflow-hidden">
        <CardContent className="p-6">
          <div className="flex items-start justify-between">
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">{title}</p>
              <p className="text-2xl font-bold font-display">{value}</p>
              {subValue && (
                <p className={cn('text-sm font-medium', trend !== undefined && getProfitColor(trend))}>
                  {subValue}
                </p>
              )}
            </div>
            <div
              className={cn(
                'p-3 rounded-xl',
                color || 'bg-gradient-to-br from-jennie-pink/20 to-jennie-purple/20'
              )}
            >
              <Icon className="w-6 h-6 text-jennie-purple" />
            </div>
          </div>
          {trend !== undefined && (
            <div className="mt-4 flex items-center gap-1">
              {trend >= 0 ? (
                <TrendingUp className="w-4 h-4 text-profit-positive" />
              ) : (
                <TrendingDown className="w-4 h-4 text-profit-negative" />
              )}
              <span className={cn('text-sm', getProfitColor(trend))}>
                {formatPercent(trend)}
              </span>
              <span className="text-xs text-muted-foreground ml-1">vs 어제</span>
            </div>
          )}
        </CardContent>
      </Card>
    </motion.div>
  )
}

export function OverviewPage() {
  const { data: summary, isLoading: summaryLoading, refetch: refetchSummary } = useQuery({
    queryKey: ['portfolio-summary'],
    queryFn: portfolioApi.getSummary,
    refetchInterval: 60000, // 1분마다 자동 새로고침
  })

  const { data: positions } = useQuery({
    queryKey: ['portfolio-positions'],
    queryFn: portfolioApi.getPositions,
    refetchInterval: 60000,
  })

  const { data: scoutStatus } = useQuery({
    queryKey: ['scout-status'],
    queryFn: scoutApi.getStatus,
    refetchInterval: 30000,
  })

  const { data: recentTrades } = useQuery({
    queryKey: ['recent-trades'],
    queryFn: () => tradesApi.getRecent(5),
  })

  // 포트폴리오 파이 차트 데이터
  const pieData = positions?.slice(0, 5).map((p: any, i: number) => ({
    name: p.stock_name,
    value: p.weight,
    color: COLORS[i % COLORS.length],
  })) || []

  // 가상의 자산 추이 데이터 (실제로는 API에서 가져와야 함)
  const chartData = [
    { date: '11/25', value: 10000000 },
    { date: '11/26', value: 10200000 },
    { date: '11/27', value: 10150000 },
    { date: '11/28', value: 10400000 },
    { date: '11/29', value: 10350000 },
    { date: '11/30', value: 10600000 },
    { date: '12/01', value: 10800000 },
    { date: '12/02', value: summary?.total_value || 10800000 },
  ]

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold">Overview</h1>
          <p className="text-muted-foreground mt-1">포트폴리오 현황을 한눈에 확인하세요</p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => refetchSummary()}
          className="gap-2"
        >
          <RefreshCw className="w-4 h-4" />
          새로고침
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="총 자산"
          value={summaryLoading ? '...' : formatCurrency(summary?.total_value || 0)}
          subValue={summary ? formatPercent(summary.profit_rate) : undefined}
          icon={Wallet}
          trend={summary?.profit_rate}
        />
        <StatCard
          title="총 수익"
          value={summaryLoading ? '...' : formatCurrency(summary?.total_profit || 0)}
          icon={summary?.total_profit >= 0 ? TrendingUp : TrendingDown}
          color={summary?.total_profit >= 0 ? 'bg-profit-positive/20' : 'bg-profit-negative/20'}
        />
        <StatCard
          title="보유 종목"
          value={summaryLoading ? '...' : `${summary?.positions_count || 0}개`}
          subValue={`현금: ${formatCurrency(summary?.cash_balance || 0)}`}
          icon={PieChart}
        />
        <StatCard
          title="Scout Pipeline"
          value={scoutStatus?.status === 'running' ? '실행 중' : '대기'}
          subValue={scoutStatus?.phase_name || 'Phase 1 대기'}
          icon={Brain}
          color="bg-jennie-blue/20"
        />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Asset Chart */}
        <motion.div variants={itemVariants} className="lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="w-5 h-5 text-jennie-purple" />
                자산 추이
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[300px]">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={chartData}>
                    <defs>
                      <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#9B5DE5" stopOpacity={0.3} />
                        <stop offset="95%" stopColor="#9B5DE5" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" />
                    <XAxis
                      dataKey="date"
                      stroke="rgba(255,255,255,0.5)"
                      fontSize={12}
                    />
                    <YAxis
                      stroke="rgba(255,255,255,0.5)"
                      fontSize={12}
                      tickFormatter={(v) => formatCurrency(v)}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'rgba(13, 17, 23, 0.9)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: '8px',
                      }}
                      formatter={(value: number) => [formatCurrency(value), '자산']}
                    />
                    <Area
                      type="monotone"
                      dataKey="value"
                      stroke="#9B5DE5"
                      strokeWidth={2}
                      fillOpacity={1}
                      fill="url(#colorValue)"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </motion.div>

        {/* Portfolio Pie Chart */}
        <motion.div variants={itemVariants}>
          <Card className="h-full">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <PieChart className="w-5 h-5 text-jennie-pink" />
                포트폴리오 구성
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-[200px]">
                <ResponsiveContainer width="100%" height="100%">
                  <RechartsPie>
                    <Pie
                      data={pieData}
                      cx="50%"
                      cy="50%"
                      innerRadius={50}
                      outerRadius={80}
                      paddingAngle={2}
                      dataKey="value"
                    >
                      {pieData.map((entry: any, index: number) => (
                        <Cell key={`cell-${index}`} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'rgba(13, 17, 23, 0.9)',
                        border: '1px solid rgba(255,255,255,0.1)',
                        borderRadius: '8px',
                      }}
                      formatter={(value: number) => [`${value.toFixed(1)}%`, '비중']}
                    />
                  </RechartsPie>
                </ResponsiveContainer>
              </div>
              <div className="mt-4 space-y-2">
                {pieData.map((item: any, i: number) => (
                  <div key={i} className="flex items-center justify-between text-sm">
                    <div className="flex items-center gap-2">
                      <div
                        className="w-3 h-3 rounded-full"
                        style={{ backgroundColor: item.color }}
                      />
                      <span className="text-muted-foreground">{item.name}</span>
                    </div>
                    <span className="font-medium">{item.value.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>

      {/* Recent Trades */}
      <motion.div variants={itemVariants}>
        <Card>
          <CardHeader>
            <CardTitle>최근 거래</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {recentTrades?.length === 0 && (
                <p className="text-center text-muted-foreground py-8">
                  최근 거래 내역이 없습니다
                </p>
              )}
              {recentTrades?.map((trade: any) => (
                <div
                  key={trade.id}
                  className="flex items-center justify-between p-3 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={cn(
                        'px-2 py-1 rounded text-xs font-medium',
                        trade.trade_type === 'BUY'
                          ? 'bg-profit-positive/20 text-profit-positive'
                          : 'bg-profit-negative/20 text-profit-negative'
                      )}
                    >
                      {trade.trade_type === 'BUY' ? '매수' : '매도'}
                    </div>
                    <div>
                      <p className="font-medium">{trade.stock_name}</p>
                      <p className="text-xs text-muted-foreground">
                        {trade.stock_code}
                      </p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="font-mono">
                      {formatNumber(trade.quantity)}주 × {formatNumber(trade.price)}원
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {formatRelativeTime(trade.traded_at)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Scout Pipeline Status */}
      {scoutStatus && (
        <motion.div variants={itemVariants}>
          <Card glow={scoutStatus.status === 'running'}>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Brain className="w-5 h-5 text-jennie-blue" />
                Scout-Debate-Judge Pipeline
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-3 gap-4">
                {['Hunter Scout', 'Bull vs Bear Debate', 'Final Judge'].map((phase, i) => (
                  <div
                    key={phase}
                    className={cn(
                      'p-4 rounded-lg border',
                      scoutStatus.phase === i + 1
                        ? 'border-jennie-purple bg-jennie-purple/10'
                        : scoutStatus.phase > i + 1
                        ? 'border-profit-positive/50 bg-profit-positive/10'
                        : 'border-white/10 bg-white/5'
                    )}
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <div
                        className={cn(
                          'w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold',
                          scoutStatus.phase === i + 1
                            ? 'bg-jennie-purple text-white'
                            : scoutStatus.phase > i + 1
                            ? 'bg-profit-positive text-white'
                            : 'bg-white/10 text-muted-foreground'
                        )}
                      >
                        {i + 1}
                      </div>
                      <span className="text-sm font-medium">{phase}</span>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {i === 0 && `통과: ${scoutStatus.passed_phase1 || 0}개`}
                      {i === 1 && `토론: ${scoutStatus.passed_phase2 || 0}개`}
                      {i === 2 && `선정: ${scoutStatus.final_selected || 0}개`}
                    </p>
                  </div>
                ))}
              </div>
              {scoutStatus.current_stock && (
                <p className="mt-4 text-sm text-muted-foreground">
                  현재 분석 중: <span className="text-foreground">{scoutStatus.current_stock}</span>
                </p>
              )}
            </CardContent>
          </Card>
        </motion.div>
      )}
    </motion.div>
  )
}

