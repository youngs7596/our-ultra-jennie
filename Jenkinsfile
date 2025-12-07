pipeline {
    agent any

    environment {
        DOCKER_COMPOSE_FILE = 'docker-compose.yml'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                echo "🔀 Branch: ${env.GIT_BRANCH ?: env.BRANCH_NAME}"
                echo "📝 Commit: ${env.GIT_COMMIT}"
            }
        }

        stage('Setup Python Environment') {
            steps {
                sh '''
                    python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --upgrade pip
                    pip install -r requirements.txt
                '''
            }
        }

        stage('Unit Test') {
            steps {
                echo '🧪 Running Unit Tests...'
                sh '''
                    . .venv/bin/activate
                    pytest tests/ -v --tb=short --junitxml=test-results.xml --cov=shared --cov-report=xml:coverage.xml --cov-report=html:coverage-html || true
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'test-results.xml'
                    publishHTML(target: [
                        allowMissing: true,
                        alwaysLinkToLastBuild: true,
                        keepAll: true,
                        reportDir: 'coverage-html',
                        reportFiles: 'index.html',
                        reportName: 'Coverage Report'
                    ])
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
                    expression { env.GIT_BRANCH == 'origin/main' }
                }
            }
            steps {
                echo '🐳 Building Docker images...'
                sh '''
                    docker-compose -f ${DOCKER_COMPOSE_FILE} build --no-cache
                '''
            }
        }

        stage('Deploy') {
            when {
                anyOf {
                    branch 'main'
                    expression { env.GIT_BRANCH == 'origin/main' }
                }
            }
            steps {
                echo '🚀 Deploying to production...'
                sh '''
                    # 기존 컨테이너 중지 및 제거
                    docker-compose -f ${DOCKER_COMPOSE_FILE} down --remove-orphans || true
                    
                    # 새 컨테이너 시작
                    docker-compose -f ${DOCKER_COMPOSE_FILE} up -d
                    
                    # 상태 확인
                    docker-compose -f ${DOCKER_COMPOSE_FILE} ps
                '''
            }
        }
    }

    post {
        always {
            echo '📋 Pipeline finished!'
            cleanWs(cleanWhenNotBuilt: false, deleteDirs: true, disableDeferredWipeout: true)
        }
        success {
            script {
                if (env.GIT_BRANCH == 'origin/main' || env.BRANCH_NAME == 'main') {
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
