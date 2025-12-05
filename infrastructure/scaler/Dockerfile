FROM gcr.io/google.com/cloudsdktool/cloud-sdk:alpine

WORKDIR /app

COPY scale-services.sh .
RUN chmod +x scale-services.sh

ENTRYPOINT ["./scale-services.sh"]
