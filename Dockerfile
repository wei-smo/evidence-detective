FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY evidence_detective/ evidence_detective/
COPY static/ static/
RUN useradd --create-home detective && mkdir -p /app/data/investigations && chown -R detective:detective /app/data
USER detective
EXPOSE 8765
CMD ["python", "-m", "evidence_detective.server", "--host", "0.0.0.0", "--port", "8765"]
