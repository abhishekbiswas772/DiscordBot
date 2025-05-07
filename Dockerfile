FROM python:3.10-slim

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Create data directory
RUN mkdir -p ./data

# The PORT environment variable will be provided by Render
# We use a default of 10000 for local development only
ENV PORT=10000

# This EXPOSE is just documentation - Render will use the PORT env var
# No port is needed for Discord functionality, only for Render health checks
EXPOSE ${PORT}

# Run the bot
CMD ["python", "discordbot.py"]