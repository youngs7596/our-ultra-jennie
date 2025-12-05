import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  TrendingUp,
  TrendingDown,
  Search,
  ArrowUpDown,
  ExternalLink,
  BarChart2,
  X,
} from 'lucide-react'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { portfolioApi } from '@/lib/api'
import {
  formatCurrency,
  formatPercent,
  formatNumber,
  getProfitColor,
  cn,
} from '@/lib/utils'
import TradingChart from '@/components/TradingChart'

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.05 },
  },
}

const itemVariants = {
  hidden: { opacity: 0, x: -20 },
  visible: { opacity: 1, x: 0 },
}

export function PortfolioPage() {
  const [searchTerm, setSearchTerm] = useState('')
  const [sortBy, setSortBy] = useState<'profit' | 'weight' | 'name'>('weight')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [selectedStock, setSelectedStock] = useState<{code: string, name: string} | null>(null)

  const { data: positions, isLoading } = useQuery({
    queryKey: ['portfolio-positions'],
    queryFn: portfolioApi.getPositions,
    refetchInterval: 30000,
  })

  const { data: summary } = useQuery({
    queryKey: ['portfolio-summary'],
    queryFn: portfolioApi.getSummary,
  })

  // 필터링 및 정렬
  const filteredPositions = positions
    ?.filter((p: any) =>
      p.stock_name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      p.stock_code.includes(searchTerm)
    )
    .sort((a: any, b: any) => {
      let comparison = 0
      if (sortBy === 'profit') {
        comparison = a.profit_rate - b.profit_rate
      } else if (sortBy === 'weight') {
        comparison = a.weight - b.weight
      } else {
        comparison = a.stock_name.localeCompare(b.stock_name)
      }
      return sortOrder === 'desc' ? -comparison : comparison
    })

  const toggleSort = (field: 'profit' | 'weight' | 'name') => {
    if (sortBy === field) {
      setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(field)
      setSortOrder('desc')
    }
  }

  return (
    <motion.div
      variants={containerVariants}
      initial="hidden"
      animate="visible"
      className="space-y-6"
    >
      {/* Header */}
      <div>
        <h1 className="text-3xl font-display font-bold">Portfolio</h1>
        <p className="text-muted-foreground mt-1">보유 종목을 실시간으로 모니터링하세요</p>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="p-4">
            <p className="text-sm text-muted-foreground">총 평가금액</p>
            <p className="text-2xl font-bold mt-1">
              {formatCurrency(summary?.total_value || 0)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm text-muted-foreground">총 투자금액</p>
            <p className="text-2xl font-bold mt-1">
              {formatCurrency(summary?.total_invested || 0)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm text-muted-foreground">총 수익</p>
            <p className={cn('text-2xl font-bold mt-1', getProfitColor(summary?.total_profit || 0))}>
              {formatCurrency(summary?.total_profit || 0)}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <p className="text-sm text-muted-foreground">수익률</p>
            <p className={cn('text-2xl font-bold mt-1', getProfitColor(summary?.profit_rate || 0))}>
              {formatPercent(summary?.profit_rate || 0)}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* TradingView Chart Modal */}
      {selectedStock && (
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 20 }}
          className="relative"
        >
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSelectedStock(null)}
            className="absolute -top-2 right-0 z-10"
          >
            <X className="w-4 h-4" />
          </Button>
          <TradingChart
            stockCode={selectedStock.code}
            stockName={selectedStock.name}
            height={350}
          />
        </motion.div>
      )}

      {/* Search & Sort */}
      <div className="flex flex-col sm:flex-row gap-4">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="종목명 또는 코드로 검색..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-10"
          />
        </div>
        <div className="flex gap-2">
          <Button
            variant={sortBy === 'weight' ? 'default' : 'outline'}
            size="sm"
            onClick={() => toggleSort('weight')}
            className="gap-1"
          >
            비중
            <ArrowUpDown className="w-3 h-3" />
          </Button>
          <Button
            variant={sortBy === 'profit' ? 'default' : 'outline'}
            size="sm"
            onClick={() => toggleSort('profit')}
            className="gap-1"
          >
            수익률
            <ArrowUpDown className="w-3 h-3" />
          </Button>
          <Button
            variant={sortBy === 'name' ? 'default' : 'outline'}
            size="sm"
            onClick={() => toggleSort('name')}
            className="gap-1"
          >
            이름
            <ArrowUpDown className="w-3 h-3" />
          </Button>
        </div>
      </div>

      {/* Positions List */}
      <Card>
        <CardHeader>
          <CardTitle>보유 종목 ({filteredPositions?.length || 0}개)</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <div key={i} className="h-20 rounded-lg bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : filteredPositions?.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              {searchTerm ? '검색 결과가 없습니다' : '보유 종목이 없습니다'}
            </div>
          ) : (
            <motion.div
              variants={containerVariants}
              initial="hidden"
              animate="visible"
              className="space-y-3"
            >
              {filteredPositions?.map((position: any) => (
                <motion.div
                  key={position.stock_code}
                  variants={itemVariants}
                  className="group p-4 rounded-xl bg-white/5 hover:bg-white/10 border border-transparent hover:border-white/10 transition-all duration-200"
                >
                  <div className="flex items-center justify-between">
                    {/* Left: Stock Info */}
                    <div className="flex items-center gap-4">
                      <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-jennie-pink/20 to-jennie-purple/20 flex items-center justify-center">
                        <span className="font-bold text-jennie-purple">
                          {position.stock_name[0]}
                        </span>
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h3 className="font-semibold">{position.stock_name}</h3>
                          <span className="text-xs text-muted-foreground font-mono">
                            {position.stock_code}
                          </span>
                          <button
                            onClick={() => setSelectedStock({code: position.stock_code, name: position.stock_name})}
                            className="opacity-0 group-hover:opacity-100 transition-opacity p-1 hover:bg-white/10 rounded"
                            title="차트 보기"
                          >
                            <BarChart2 className="w-3 h-3 text-muted-foreground hover:text-jennie-pink" />
                          </button>
                          <a
                            href={`https://finance.naver.com/item/main.nhn?code=${position.stock_code}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="opacity-0 group-hover:opacity-100 transition-opacity"
                          >
                            <ExternalLink className="w-3 h-3 text-muted-foreground hover:text-foreground" />
                          </a>
                        </div>
                        <p className="text-sm text-muted-foreground">
                          {formatNumber(position.quantity)}주 × {formatNumber(position.avg_price)}원
                        </p>
                      </div>
                    </div>

                    {/* Center: Current Price */}
                    <div className="text-center">
                      <p className="text-sm text-muted-foreground">현재가</p>
                      <p className="font-mono font-semibold">
                        {formatNumber(position.current_price)}원
                      </p>
                    </div>

                    {/* Right: Profit */}
                    <div className="text-right">
                      <div className="flex items-center justify-end gap-2">
                        {position.profit >= 0 ? (
                          <TrendingUp className="w-4 h-4 text-profit-positive" />
                        ) : (
                          <TrendingDown className="w-4 h-4 text-profit-negative" />
                        )}
                        <span className={cn('font-mono font-semibold', getProfitColor(position.profit))}>
                          {formatCurrency(position.profit)}
                        </span>
                      </div>
                      <p className={cn('text-sm font-medium', getProfitColor(position.profit_rate))}>
                        {formatPercent(position.profit_rate)}
                      </p>
                    </div>

                    {/* Weight Bar */}
                    <div className="w-24">
                      <div className="flex items-center justify-between text-xs mb-1">
                        <span className="text-muted-foreground">비중</span>
                        <span className="font-medium">{position.weight.toFixed(1)}%</span>
                      </div>
                      <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${position.weight}%` }}
                          transition={{ duration: 0.5, delay: 0.2 }}
                          className="h-full bg-gradient-to-r from-jennie-pink to-jennie-purple rounded-full"
                        />
                      </div>
                    </div>
                  </div>
                </motion.div>
              ))}
            </motion.div>
          )}
        </CardContent>
      </Card>
    </motion.div>
  )
}

