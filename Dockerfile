FROM python:3.12-slim
WORKDIR /srv/app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data BEHIND_PROXY=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
RUN useradd -r -u 10001 appuser && mkdir /data && chown appuser /data
USER appuser
VOLUME /data
EXPOSE 8000
CMD ["gunicorn", "-w", "3", "-b", "0.0.0.0:8000", "--timeout", "180", "app:app"]
