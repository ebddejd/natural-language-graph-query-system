FROM python:3.12-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends g++ make && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ADD source.tar.gz /app/
RUN make all
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
COPY --from=build /app /app
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home appuser
USER appuser
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=10000
EXPOSE 10000
CMD ["python", "hosted.py"]
