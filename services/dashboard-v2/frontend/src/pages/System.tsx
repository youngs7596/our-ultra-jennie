import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { motion, AnimatePresence } from 'framer-motion'
import {
  Server,
  Database,
  MessageSquare,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
  RefreshCw,
  Container,
  Activity,
  X,
  Terminal,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { systemApi } from '@/lib/api'
import { formatRelativeTime, cn } from '@/lib/utils'

const containerVariants = {
  hidden: { opacity: 0 },
  visible: {
    opacity: 1,
    transition: { staggerChildren: 0.1 },
  },
}

const itemVariants = {
  hidden: { opacity: 0, y: 20 },
  visible: { opacity: 1, y: 0 },
}

const getStatusColor = (status: string) => {
  switch (status.toLowerCase()) {
    case 'active':
    case 'running':
    case 'up':
    case 'healthy':
      return 'text-profit-positive'
    case 'inactive':
    case 'stopped':
    case 'down':
      return 'text-muted-foreground'
    case 'error':
    case 'unhealthy':
      return 'text-profit-negative'
    default:
      return 'text-jennie-gold'
  }
}

const getStatusIcon = (status: string) => {
  switch (status.toLowerCase()) {
    case 'active':
    case 'running':
    case 'up':
    case 'healthy':
      return <CheckCircle className="w-4 h-4 text-profit-positive" />
    case 'inactive':
    case 'stopped':
    case 'down':
      return <XCircle className="w-4 h-4 text-muted-foreground" />
    case 'error':
    case 'unhealthy':
      return <AlertCircle className="w-4 h-4 text-profit-negative" />
    default:
      return <Clock className="w-4 h-4 text-jennie-gold" />
  }
}

export function SystemPage() {
  const [selectedContainer, setSelectedContainer] = useState<string | null>(null)
  
  const { data: schedulerJobs, isLoading: jobsLoading, refetch: refetchJobs } = useQuery({
    queryKey: ['system-status'],
    queryFn: systemApi.getStatus,
    refetchInterval: 30000,
  })

  const { data: dockerStatus, isLoading: dockerLoading, refetch: refetchDocker } = useQuery({
    queryKey: ['docker-status'],
    queryFn: systemApi.getDocker,
    refetchInterval: 30000,
  })

  const { data: rabbitmqStatus, refetch: refetchRabbitMQ } = useQuery({
    queryKey: ['rabbitmq-status'],
    queryFn: systemApi.getRabbitMQ,
    refetchInterval: 30000,
  })

  const { data: containerLogs, isLoading: logsLoading, refetch: refetchLogs } = useQuery({
    queryKey: ['container-logs', selectedContainer],
    queryFn: () => selectedContainer ? systemApi.getContainerLogs(selectedContainer) : null,
    enabled: !!selectedContainer,
    refetchInterval: 5000, // 5초마다 자동 새로고침
  })

  const handleRefreshAll = () => {
    refetchJobs()
    refetchDocker()
    refetchRabbitMQ()
  }

  const handleContainerClick = (containerName: string) => {
    setSelectedContainer(containerName)
  }

  const closeLogModal = () => {
    setSelectedContainer(null)
  }

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
          <h1 className="text-3xl font-display font-bold flex items-center gap-3">
            <Activity className="w-8 h-8 text-jennie-blue" />
            System Status
          </h1>
          <p className="text-muted-foreground mt-1">
            WSL2 + Docker 환경의 서비스 상태를 모니터링합니다
          </p>
        </div>
        <Button variant="outline" onClick={handleRefreshAll} className="gap-2">
          <RefreshCw className="w-4 h-4" />
          새로고침
        </Button>
      </div>

      {/* Quick Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-profit-positive/20">
                <Container className="w-5 h-5 text-profit-positive" />
              </div>
              <div>
                <p className="text-2xl font-bold">{dockerStatus?.count || 0}</p>
                <p className="text-xs text-muted-foreground">실행 중 컨테이너</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-jennie-purple/20">
                <Clock className="w-5 h-5 text-jennie-purple" />
              </div>
              <div>
                <p className="text-2xl font-bold">
                  {schedulerJobs?.filter((j: any) => j.status === 'active').length || 0}
                </p>
                <p className="text-xs text-muted-foreground">활성 스케줄러</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-jennie-pink/20">
                <MessageSquare className="w-5 h-5 text-jennie-pink" />
              </div>
              <div>
                <p className="text-2xl font-bold">
                  {rabbitmqStatus?.queues?.reduce((sum: number, q: any) => sum + (q.messages || 0), 0) || 0}
                </p>
                <p className="text-xs text-muted-foreground">대기 메시지</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-jennie-blue/20">
                <Database className="w-5 h-5 text-jennie-blue" />
              </div>
              <div>
                <p className="text-2xl font-bold">MariaDB</p>
                <p className="text-xs text-muted-foreground">Primary DB (WSL2)</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Docker Containers */}
      <motion.div variants={itemVariants}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Container className="w-5 h-5 text-jennie-blue" />
              Docker Containers (WSL2)
            </CardTitle>
          </CardHeader>
          <CardContent>
            {dockerLoading ? (
              <div className="space-y-3">
                {[...Array(5)].map((_, i) => (
                  <div key={i} className="h-12 rounded-lg bg-white/5 animate-pulse" />
                ))}
              </div>
            ) : dockerStatus?.error ? (
              <div className="text-center py-8 text-muted-foreground">
                <AlertCircle className="w-8 h-8 mx-auto mb-2 text-profit-negative" />
                <p>Docker 상태를 가져올 수 없습니다</p>
                <p className="text-xs mt-1">{dockerStatus.error}</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {dockerStatus?.containers?.map((container: any) => (
                  <div
                    key={container.ID}
                    onClick={() => handleContainerClick(container.Names)}
                    className="p-3 rounded-lg bg-white/5 hover:bg-white/10 transition-colors cursor-pointer group"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-medium text-sm truncate flex items-center gap-2">
                        {container.Names}
                        <Terminal className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity text-jennie-blue" />
                      </span>
                      {getStatusIcon(container.Status?.includes('Up') ? 'running' : 'stopped')}
                    </div>
                    <p className="text-xs text-muted-foreground truncate">
                      {container.Image}
                    </p>
                    <p className="text-xs text-muted-foreground mt-1">
                      {container.Status}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* Scheduler Jobs */}
      <motion.div variants={itemVariants}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Clock className="w-5 h-5 text-jennie-purple" />
              Scheduler Jobs
            </CardTitle>
          </CardHeader>
          <CardContent>
            {jobsLoading ? (
              <div className="space-y-3">
                {[...Array(4)].map((_, i) => (
                  <div key={i} className="h-16 rounded-lg bg-white/5 animate-pulse" />
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                {schedulerJobs?.map((job: any) => (
                  <div
                    key={job.service_name}
                    className="p-4 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        {getStatusIcon(job.status)}
                        <div>
                          <h4 className="font-semibold">{job.service_name}</h4>
                          <p className="text-xs text-muted-foreground">
                            {job.message}
                          </p>
                        </div>
                      </div>
                      <div className="text-right">
                        <p className={cn('text-sm font-medium', getStatusColor(job.status))}>
                          {job.status === 'active' ? '활성' : '비활성'}
                        </p>
                        {job.next_run && (
                          <p className="text-xs text-muted-foreground">
                            다음 실행: {formatRelativeTime(job.next_run)}
                          </p>
                        )}
                      </div>
                    </div>
                    {job.last_run && (
                      <p className="text-xs text-muted-foreground mt-2">
                        마지막 실행: {formatRelativeTime(job.last_run)}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* RabbitMQ Queues */}
      <motion.div variants={itemVariants}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-jennie-pink" />
              RabbitMQ Queues
            </CardTitle>
          </CardHeader>
          <CardContent>
            {rabbitmqStatus?.error ? (
              <div className="text-center py-8 text-muted-foreground">
                <AlertCircle className="w-8 h-8 mx-auto mb-2 text-profit-negative" />
                <p>RabbitMQ 상태를 가져올 수 없습니다</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                {rabbitmqStatus?.queues?.map((queue: any) => (
                  <div
                    key={queue.name}
                    className="p-3 rounded-lg bg-white/5 hover:bg-white/10 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-sm truncate">{queue.name}</span>
                      <span
                        className={cn(
                          'px-2 py-0.5 rounded-full text-xs font-medium',
                          queue.messages > 0
                            ? 'bg-jennie-gold/20 text-jennie-gold'
                            : 'bg-white/10 text-muted-foreground'
                        )}
                      >
                        {queue.messages || 0}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </motion.div>

      {/* Environment Info */}
      <motion.div variants={itemVariants}>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="w-5 h-5 text-muted-foreground" />
              Environment
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <div className="p-3 rounded-lg bg-white/5">
                <p className="text-xs text-muted-foreground">Platform</p>
                <p className="font-medium">WSL2 (Ubuntu)</p>
              </div>
              <div className="p-3 rounded-lg bg-white/5">
                <p className="text-xs text-muted-foreground">Orchestration</p>
                <p className="font-medium">Docker Compose</p>
              </div>
              <div className="p-3 rounded-lg bg-white/5">
                <p className="text-xs text-muted-foreground">Primary DB</p>
                <p className="font-medium">MariaDB (WSL2)</p>
              </div>
              <div className="p-3 rounded-lg bg-white/5">
                <p className="text-xs text-muted-foreground">Backup DB</p>
                <p className="font-medium">Oracle Cloud (ATP)</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </motion.div>

      {/* Log Viewer Modal */}
      <AnimatePresence>
        {selectedContainer && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4"
            onClick={closeLogModal}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              className="bg-card border border-white/10 rounded-xl w-full max-w-4xl max-h-[80vh] overflow-hidden shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Modal Header */}
              <div className="flex items-center justify-between p-4 border-b border-white/10 bg-white/5">
                <div className="flex items-center gap-3">
                  <Terminal className="w-5 h-5 text-jennie-blue" />
                  <h3 className="font-semibold text-lg">{selectedContainer}</h3>
                  <span className="text-xs text-muted-foreground bg-white/10 px-2 py-1 rounded">
                    실시간 로그 (5초 갱신)
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => refetchLogs()}
                    className="gap-1"
                  >
                    <RefreshCw className={cn("w-4 h-4", logsLoading && "animate-spin")} />
                    새로고침
                  </Button>
                  <Button variant="ghost" size="sm" onClick={closeLogModal}>
                    <X className="w-4 h-4" />
                  </Button>
                </div>
              </div>

              {/* Log Content */}
              <div className="p-4 overflow-auto max-h-[60vh] font-mono text-sm bg-black/50">
                {logsLoading && !containerLogs ? (
                  <div className="flex items-center justify-center py-8 text-muted-foreground">
                    <RefreshCw className="w-5 h-5 animate-spin mr-2" />
                    로그를 불러오는 중...
                  </div>
                ) : containerLogs?.error ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <AlertCircle className="w-8 h-8 mx-auto mb-2 text-profit-negative" />
                    <p>로그를 가져올 수 없습니다</p>
                    <p className="text-xs mt-1">{containerLogs.error}</p>
                  </div>
                ) : containerLogs?.logs?.length === 0 ? (
                  <div className="text-center py-8 text-muted-foreground">
                    <Terminal className="w-8 h-8 mx-auto mb-2" />
                    <p>최근 1시간 내 로그가 없습니다</p>
                  </div>
                ) : (
                  <div className="space-y-1">
                    {containerLogs?.logs?.map((log: any, index: number) => (
                      <div
                        key={index}
                        className="flex gap-3 hover:bg-white/5 px-2 py-0.5 rounded"
                      >
                        <span className="text-muted-foreground shrink-0 text-xs">
                          {log.timestamp}
                        </span>
                        <span className={cn(
                          "break-all",
                          log.message.toLowerCase().includes('error') && "text-profit-negative",
                          log.message.toLowerCase().includes('warn') && "text-jennie-gold",
                          log.message.toLowerCase().includes('success') && "text-profit-positive",
                        )}>
                          {log.message}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div className="p-3 border-t border-white/10 bg-white/5 flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  {containerLogs?.count || 0}개 로그 (최근 1시간)
                </span>
                <span>
                  Powered by Loki + Grafana
                </span>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}

