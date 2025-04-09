FROM python:3.13-slim

# Ensure Python doesn't buffer output
ENV PYTHONUNBUFFERED=1
ENV APP_HOME=/app
WORKDIR $APP_HOME

# 1) Install CA certificates and do a cleanup
RUN apt-get update && \
  apt-get install -y --no-install-recommends ca-certificates && \
  rm -rf /var/lib/apt/lists/*

# 2) Copy in your requirements and upgrade pip + install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip wheel && \
  pip install --no-cache-dir --upgrade -r requirements.txt

# 3) Copy in the rest of your source code
COPY . ./

# 4) Start your Gunicorn web server
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 api:app
