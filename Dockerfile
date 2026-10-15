# Python workspace for the assignment: publisher, subscriber, loaders, analysis, forecasting, dashboard timing.
FROM python:3.12-slim

ENV TZ=Africa/Kigali \
    PYTHONUNBUFFERED=1

# tzdata = real CAT clock; postgresql-client = psql; chromium + fonts = headless browser for Section 7.4 timing
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata postgresql-client chromium fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir playwright==1.48.0

# The project folder is mounted at /app at runtime, so code edits need no rebuild.
CMD ["sleep", "infinity"]