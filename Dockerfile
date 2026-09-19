# Imagem do app web do Prospector. Serve para qualquer hospedagem que rode Docker.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY prospector ./prospector
COPY webapp ./webapp

# Não roda como administrador dentro do contêiner.
RUN useradd --create-home app && chown -R app /app
USER app

EXPOSE 8000

# Um único processo: as tarefas em andamento ficam em memória (veja webapp/jobs.py).
CMD ["python", "-m", "webapp"]
