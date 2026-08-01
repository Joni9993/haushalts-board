FROM python:3.12-slim

# html2image (used by some optional plugins) needs a headless Chromium.
# Not required for the Haushalts-Board plugin itself, but kept so the
# stock plugin set still works if you enable them later.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Where device state, generated images and the Haushalts-Board JSON live.
# Mount this as a volume so data survives container rebuilds.
VOLUME ["/app/var"]

EXPOSE 4567

CMD ["python", "-m", "trmnl_server.main"]
