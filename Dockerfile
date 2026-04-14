FROM python:3.10-slim

# System deps for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmupdf-dev \
    mupdf-tools \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Install CPU-only torch to keep image size manageable in Docker context.
# The GPU cluster uses the full CUDA build directly via conda.
RUN pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Expects data to be mounted at runtime — see docker-compose.yml
# gdc_downloads → /data/gdc_downloads
# ocr_output    → /data/ocr_output

CMD ["python3", "brca_ocr_extract.py"]
