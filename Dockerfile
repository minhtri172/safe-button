FROM python:3.11-slim

# Write logs directly to the container runtime
ENV PYTHONUNBUFFERED=True

# Copy local code to the container image
ENV APP_HOME=/app
WORKDIR $APP_HOME
COPY . ./

# Install production dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Run the web service
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "main:app"]
