FROM python:3.11-slim

WORKDIR /app

# 최소 의존성
RUN pip install --no-cache-dir flask requests

COPY docker/kis-mock/mock_server.py .

EXPOSE 9443

CMD ["python", "mock_server.py"]

