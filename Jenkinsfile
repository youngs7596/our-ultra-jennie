pipeline {
    agent any

    environment {
        PYTHON_VERSION = '3.11'
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
                echo "Branch: ${env.BRANCH_NAME ?: env.GIT_BRANCH}"
                echo "Commit: ${env.GIT_COMMIT}"
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

        stage('Lint') {
            steps {
                sh '''
                    . .venv/bin/activate
                    pip install flake8
                    flake8 shared/ services/ scripts/ --count --select=E9,F63,F7,F82 --show-source --statistics || true
                '''
            }
        }

        stage('Unit Test') {
            steps {
                sh '''
                    . .venv/bin/activate
                    pytest tests/ -v --tb=short --junitxml=test-results.xml --cov=shared --cov-report=xml:coverage.xml || true
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'test-results.xml'
                }
            }
        }

        stage('Build') {
            steps {
                echo 'Building application...'
                sh '''
                    . .venv/bin/activate
                    echo "Python version: $(python --version)"
                    echo "Installed packages:"
                    pip list
                '''
            }
        }

        stage('Deploy') {
            when {
                branch 'main'
            }
            steps {
                echo 'Deploying to production...'
                // 여기에 실제 배포 스크립트 추가
                // 예: docker-compose up -d
                // 예: kubectl apply -f k8s/
                sh '''
                    echo "Deployment would happen here for main branch"
                '''
            }
        }

        stage('Deploy to Dev') {
            when {
                branch 'development'
            }
            steps {
                echo 'Deploying to development environment...'
                sh '''
                    echo "Development deployment would happen here"
                '''
            }
        }
    }

    post {
        always {
            echo 'Pipeline finished!'
            cleanWs()
        }
        success {
            echo '✅ Build succeeded!'
        }
        failure {
            echo '❌ Build failed!'
        }
    }
}
