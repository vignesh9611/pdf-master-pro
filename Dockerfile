FROM python:3.11-slim

# System deps (LibreOffice for DOCX→PDF, Ghostscript for compression, fonts)
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    libreoffice-common libreoffice-writer \
    ghostscript fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

ENV PYTHONUNBUFFERED=1
ENV PORT=10000
EXPOSE $PORT
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:10000", "--workers", "2"]
