FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
RUN pip install --no-cache-dir -c constraints.txt . \
    && useradd --create-home bot \
    && mkdir -p /app/data && chown -R bot:bot /app
USER bot
CMD ["python", "-m", "market_bot"]
