import { useQuery } from '@tanstack/react-query'
import { motion } from 'framer-motion'
import {
  Newspaper,
  TrendingUp,
  TrendingDown,
  Minus,
  RefreshCw,
} from 'lucide-react'
import { useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { newsApi, watchlistApi } from '@/lib/api'
import { cn, getSentimentColor } from '@/lib/utils'

export function NewsPage() {
  const [selectedStock, setSelectedStock] = useState<string | null>(null)

  const { data: sentimentData, isLoading, refetch } = useQuery({
    queryKey: ['news-sentiment', selectedStock],
    queryFn: () => newsApi.getSentiment(selectedStock || undefined, 50),
    refetchInterval: 60000,
  })

  const { data: watchlist } = useQuery({
    queryKey: ['watchlist'],
    queryFn: () => watchlistApi.getAll(100),
  })

  const getSentimentIcon = (score: number) => {
    if (score >= 60) return <TrendingUp className="w-4 h-4 text-profit-positive" />
    if (score <= 40) return <TrendingDown className="w-4 h-4 text-profit-negative" />
    return <Minus className="w-4 h-4 text-muted-foreground" />
  }

  const getSentimentLabel = (score: number) => {
    if (score >= 70) return '매우 긍정'
    if (score >= 55) return '긍정'
    if (score >= 45) return '중립'
    if (score >= 30) return '부정'
    return '매우 부정'
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="space-y-6"
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-display font-bold flex items-center gap-3">
            <Newspaper className="w-8 h-8 text-jennie-gold" />
            News & Sentiment
          </h1>
          <p className="text-muted-foreground mt-1">
            실시간 뉴스 감성 분석 (Gemini-2.5-Flash)
          </p>
        </div>
        <Button variant="outline" onClick={() => refetch()} className="gap-2">
          <RefreshCw className="w-4 h-4" />
          새로고침
        </Button>
      </div>

      {/* Stock Filter */}
      <Card>
        <CardContent className="p-4">
          <div className="flex flex-wrap gap-2">
            <Button
              variant={selectedStock === null ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSelectedStock(null)}
            >
              전체
            </Button>
            {watchlist?.slice(0, 10).map((item: any) => (
              <Button
                key={item.stock_code}
                variant={selectedStock === item.stock_code ? 'default' : 'outline'}
                size="sm"
                onClick={() => setSelectedStock(item.stock_code)}
              >
                {item.stock_name}
              </Button>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Sentiment Overview */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card>
          <CardContent className="p-6 text-center">
            <TrendingUp className="w-8 h-8 mx-auto text-profit-positive mb-2" />
            <p className="text-3xl font-bold text-profit-positive">
              {sentimentData?.items?.filter((i: any) => i.sentiment_score >= 60).length || 0}
            </p>
            <p className="text-sm text-muted-foreground">긍정 종목</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6 text-center">
            <Minus className="w-8 h-8 mx-auto text-muted-foreground mb-2" />
            <p className="text-3xl font-bold">
              {sentimentData?.items?.filter((i: any) => i.sentiment_score > 40 && i.sentiment_score < 60).length || 0}
            </p>
            <p className="text-sm text-muted-foreground">중립 종목</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-6 text-center">
            <TrendingDown className="w-8 h-8 mx-auto text-profit-negative mb-2" />
            <p className="text-3xl font-bold text-profit-negative">
              {sentimentData?.items?.filter((i: any) => i.sentiment_score <= 40).length || 0}
            </p>
            <p className="text-sm text-muted-foreground">부정 종목</p>
          </CardContent>
        </Card>
      </div>

      {/* Sentiment List */}
      <Card>
        <CardHeader>
          <CardTitle>종목별 감성 점수</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(10)].map((_, i) => (
                <div key={i} className="h-16 rounded-lg bg-white/5 animate-pulse" />
              ))}
            </div>
          ) : sentimentData?.items?.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              감성 분석 데이터가 없습니다
            </div>
          ) : (
            <div className="space-y-3">
              {sentimentData?.items?.map((item: any, i: number) => {
                const stock = watchlist?.find((w: any) => w.stock_code === item.stock_code)
                const score = item.sentiment_score || 50

                return (
                  <motion.div
                    key={item.stock_code}
                    initial={{ opacity: 0, x: -20 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.03 }}
                    className="p-4 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-jennie-gold/20 to-jennie-pink/20 flex items-center justify-center">
                          {getSentimentIcon(score)}
                        </div>
                        <div
                          className="cursor-pointer hover:opacity-80 transition-opacity"
                          onClick={() => item.source_url && window.open(item.source_url, '_blank')}
                          title={item.source_url ? "뉴스 원문 보기" : "링크 없음"}
                        >
                          <h4 className="font-semibold text-lg">
                            {item.stock_name || stock?.stock_name || item.stock_code}
                          </h4>
                          <div className="flex items-center gap-2">
                            <p className="text-xs text-muted-foreground font-mono">
                              {item.stock_code}
                            </p>
                            {item.source_url && (
                              <Newspaper className="w-3 h-3 text-jennie-gold opacity-50" />
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-6">
                        {/* Sentiment Score Bar */}
                        <div className="w-32">
                          <div className="flex items-center justify-between text-xs mb-1">
                            <span className="text-muted-foreground">감성</span>
                            <span className={getSentimentColor(score)}>
                              {getSentimentLabel(score)}
                            </span>
                          </div>
                          <div className="h-2 rounded-full bg-white/10 overflow-hidden">
                            <motion.div
                              initial={{ width: 0 }}
                              animate={{ width: `${score}%` }}
                              transition={{ duration: 0.5 }}
                              className={cn(
                                'h-full rounded-full',
                                score >= 60 && 'bg-profit-positive',
                                score > 40 && score < 60 && 'bg-jennie-gold',
                                score <= 40 && 'bg-profit-negative'
                              )}
                            />
                          </div>
                        </div>

                        {/* Score */}
                        <div className="text-right min-w-[60px]">
                          <p className={cn('text-2xl font-bold font-mono', getSentimentColor(score))}>
                            {score?.toFixed(0)}
                          </p>
                        </div>
                      </div>
                    </div>
                  </motion.div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Info Card */}
      <Card className="border-jennie-gold/30 bg-jennie-gold/5">
        <CardContent className="p-4">
          <div className="flex items-start gap-3">
            <div className="p-2 rounded-lg bg-jennie-gold/20">
              <Newspaper className="w-5 h-5 text-jennie-gold" />
            </div>
            <div>
              <h4 className="font-semibold text-jennie-gold">뉴스 감성 분석이란?</h4>
              <p className="text-sm text-muted-foreground mt-1">
                News Crawler가 수집한 뉴스를 Gemini-2.5-Flash가 실시간으로 분석하여
                0~100점 사이의 감성 점수를 산출합니다.
                60점 이상은 긍정, 40점 이하는 부정으로 분류됩니다.
                Buy Scanner는 이 점수를 활용하여 매수 결정에 반영합니다.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>
    </motion.div>
  )
}

