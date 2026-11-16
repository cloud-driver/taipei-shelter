FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SHELTER_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -r -u 10001 anxing && mkdir /data && chown anxing /data
COPY app ./app
COPY static ./static
COPY scripts ./scripts
COPY data/public ./data/public
USER anxing
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=3)"
CMD ["python","-m","uvicorn","app.main:app_factory","--factory","--host","0.0.0.0","--port","8765","--no-access-log"]
