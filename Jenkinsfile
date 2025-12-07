pipeline {
    agent any

    environment {
        DOCKER_COMPOSE_FILE = 'docker-compose.yml'
        COMPOSE_PROJECT_NAME = 'my-ultra-jennie'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                echo "🔀 Branch: ${env.BRANCH_NAME ?: env.GIT_BRANCH}"
                echo "📝 Commit: ${env.GIT_COMMIT}"
            }
        }

        stage('Unit Test') {
            agent {
                docker {
                    image 'python:3.11-slim'
                    args '-v $PWD:/app -w /app'
                }
            }
            steps {
                echo '🧪 Running Unit Tests...'
                sh '''
                    pip install --quiet -r requirements.txt
                    pip install --quiet pytest pytest-cov
                    pytest tests/ -v --tb=short --junitxml=test-results.xml || true
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'test-results.xml'
                }
            }
        }

        // ====================================================
        // main 브랜치에서만 실행: Docker Build & Deploy
        // ====================================================
        stage('Docker Build') {
            when {
                anyOf {
                    branch 'main'
                    expression { env.GIT_BRANCH?.contains('main') }
                }
            }
            steps {
                echo '🐳 Building Docker images...'
                sh '''
                    docker-compose -p ${COMPOSE_PROJECT_NAME} -f ${DOCKER_COMPOSE_FILE} build --no-cache
                '''
            }
        }

        stage('Deploy') {
            when {
                anyOf {
                    branch 'main'
                    expression { env.GIT_BRANCH?.contains('main') }
                }
            }
            steps {
                echo '🚀 Deploying to production...'
                sh '''
                    git config --global --add safe.directory "*" 
                    
                    # 배포용 프로젝트 경로로 이동 (main 브랜치 전용)
                    cd /home/youngs75/projects/my-ultra-jennie-main
                    
                    # 최신 코드 가져오기
                    git pull origin main
                    
                    # 기존 컨테이너 중지 및 제거
                    docker-compose -p ${COMPOSE_PROJECT_NAME} -f ${DOCKER_COMPOSE_FILE} down --remove-orphans || true
                    
                    # 새 컨테이너 시작
                    docker-compose -p ${COMPOSE_PROJECT_NAME} -f ${DOCKER_COMPOSE_FILE} up -d
                    
                    # 상태 확인
                    docker-compose -p ${COMPOSE_PROJECT_NAME} -f ${DOCKER_COMPOSE_FILE} ps
                '''
            }
        }
    }

    post {
        always {
            echo '📋 Pipeline finished!'
        }
        success {
            script {
                def branchName = env.BRANCH_NAME ?: env.GIT_BRANCH ?: ''
                if (branchName.contains('main')) {
                    echo '✅ Build & Deploy succeeded!'
                } else {
                    echo '✅ Unit Tests passed!'
                }
            }
        }
        failure {
            echo '❌ Pipeline failed!'
        }
    }
}
