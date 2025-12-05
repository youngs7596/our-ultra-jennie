# Dashboard V2 Frontend - React + Vite
# Multi-stage build for production

# Stage 1: Build
FROM node:20-alpine AS builder

WORKDIR /app

# 의존성 파일 복사
COPY package.json ./

# 의존성 설치
RUN npm install

# 소스 코드 복사
COPY . .

# 환경 변수 설정 (빌드 시)
ARG VITE_API_URL=/api
ENV VITE_API_URL=$VITE_API_URL

# 빌드
RUN npm run build

# Stage 2: Production
FROM nginx:alpine

# Nginx 설정 복사
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 빌드 결과물 복사
COPY --from=builder /app/dist /usr/share/nginx/html

# 헬스체크
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:80/ || exit 1

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]

