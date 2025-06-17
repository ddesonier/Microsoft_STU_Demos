FROM python:3.10.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and packages directory
COPY requirements.txt .
COPY packages/ ./packages/

# Install dependencies from the local packages directory
RUN pip install --no-index --find-links=./packages -r requirements.txt

# Copy the rest of the application code
COPY . /app

EXPOSE 8501

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=127.0.0.1"]