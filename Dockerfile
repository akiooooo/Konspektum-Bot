FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOST=0.0.0.0 DB_PATH=/app/data/konspektum.sqlite3
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && useradd --create-home --uid 10001 shop
COPY konspektum ./konspektum
RUN mkdir -p /app/data && chown -R shop:shop /app
USER shop
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open('http://127.0.0.1:8080/health',timeout=3)" || exit 1
CMD ["python", "-m", "konspektum"]
