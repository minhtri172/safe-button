FROM python:3.11-slim

# Allow Write Logs
ENV PYTHONUNBUFFERED=True

# Copy local code to the container image
ENV APP_HOME=/app
WORKDIR $APP_HOME
COPY . ./

# Install required dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run web service on container startup
# Blind any external traffics coming to the container to port 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
