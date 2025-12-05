import axios from 'axios'
import { useAuthStore } from '@/store/authStore'

// 프로덕션에서는 /api로, 개발에서는 환경변수 사용
const API_BASE_URL = import.meta.env.VITE_API_URL || '/api'

export const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Request interceptor - JWT 토큰 자동 추가
api.interceptors.request.use(
  (config) => {
    const token = useAuthStore.getState().token
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error)
)

// Response interceptor - 401 에러 시 로그아웃
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      useAuthStore.getState().logout()
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

// API 함수들
export const authApi = {
  login: async (username: string, password: string) => {
    const response = await api.post('/auth/login', { username, password })
    return response.data
  },
  me: async () => {
    const response = await api.get('/auth/me')
    return response.data
  },
  refresh: async () => {
    const response = await api.post('/auth/refresh')
    return response.data
  },
}

export const portfolioApi = {
  getSummary: async () => {
    const response = await api.get('/portfolio/summary')
    return response.data
  },
  getPositions: async () => {
    const response = await api.get('/portfolio/positions')
    return response.data
  },
}

export const watchlistApi = {
  getAll: async (limit = 50) => {
    const response = await api.get(`/watchlist?limit=${limit}`)
    return response.data
  },
}

export const tradesApi = {
  getRecent: async (limit = 50, offset = 0) => {
    const response = await api.get(`/trades?limit=${limit}&offset=${offset}`)
    return response.data
  },
}

export const systemApi = {
  getStatus: async () => {
    const response = await api.get('/system/status')
    return response.data
  },
  getDocker: async () => {
    const response = await api.get('/system/docker')
    return response.data
  },
  getRabbitMQ: async () => {
    const response = await api.get('/system/rabbitmq')
    return response.data
  },
  getScheduler: async () => {
    const response = await api.get('/system/scheduler')
    return response.data
  },
  getContainerLogs: async (containerName: string, limit = 100, since = '1h') => {
    const response = await api.get(`/system/logs/${containerName}?limit=${limit}&since=${since}`)
    return response.data
  },
}

export const scoutApi = {
  getStatus: async () => {
    const response = await api.get('/scout/status')
    return response.data
  },
  getResults: async () => {
    const response = await api.get('/scout/results')
    return response.data
  },
}

export const newsApi = {
  getSentiment: async (stockCode?: string, limit = 20) => {
    const params = new URLSearchParams()
    if (stockCode) params.append('stock_code', stockCode)
    params.append('limit', limit.toString())
    const response = await api.get(`/news/sentiment?${params}`)
    return response.data
  },
}
