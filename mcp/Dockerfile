FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py selftest.py ./
EXPOSE 8100
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('VG_MCP_PORT','8100'),timeout=3)"
CMD ["python", "server.py"]
