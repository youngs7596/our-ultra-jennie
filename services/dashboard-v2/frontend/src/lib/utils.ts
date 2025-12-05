import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatNumber(num: number, decimals = 0): string {
  return new Intl.NumberFormat('ko-KR', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(num)
}

export function formatCurrency(num: number): string {
  if (Math.abs(num) >= 100000000) {
    return `${(num / 100000000).toFixed(1)}억`
  }
  if (Math.abs(num) >= 10000) {
    return `${(num / 10000).toFixed(0)}만`
  }
  return formatNumber(num)
}

export function formatPercent(num: number, decimals = 2): string {
  const sign = num > 0 ? '+' : ''
  return `${sign}${num.toFixed(decimals)}%`
}

export function formatDate(date: string | Date): string {
  const d = new Date(date)
  return new Intl.DateTimeFormat('ko-KR', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(d)
}

export function formatRelativeTime(date: string | Date): string {
  const d = new Date(date)
  const now = new Date()
  const diff = now.getTime() - d.getTime()
  
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)
  
  if (minutes < 1) return '방금 전'
  if (minutes < 60) return `${minutes}분 전`
  if (hours < 24) return `${hours}시간 전`
  if (days < 7) return `${days}일 전`
  
  return formatDate(date)
}

export function getGradeColor(grade: string): string {
  const colors: Record<string, string> = {
    S: 'text-jennie-gold',
    A: 'text-jennie-purple',
    B: 'text-jennie-blue',
    C: 'text-muted-foreground',
    D: 'text-profit-negative',
  }
  return colors[grade] || 'text-muted-foreground'
}

export function getGradeBgColor(grade: string): string {
  const colors: Record<string, string> = {
    S: 'bg-jennie-gold/20 border-jennie-gold/50',
    A: 'bg-jennie-purple/20 border-jennie-purple/50',
    B: 'bg-jennie-blue/20 border-jennie-blue/50',
    C: 'bg-muted/20 border-muted/50',
    D: 'bg-profit-negative/20 border-profit-negative/50',
  }
  return colors[grade] || 'bg-muted/20 border-muted/50'
}

export function getProfitColor(profit: number): string {
  if (profit > 0) return 'text-profit-positive'
  if (profit < 0) return 'text-profit-negative'
  return 'text-muted-foreground'
}

export function getSentimentColor(score: number): string {
  if (score >= 70) return 'text-profit-positive'
  if (score >= 40) return 'text-jennie-gold'
  return 'text-profit-negative'
}

