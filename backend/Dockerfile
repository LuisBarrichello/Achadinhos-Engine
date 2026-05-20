# ESTÁGIO 1: Builder (Compila pacotes C e Wheels)
FROM python:3.11-slim as builder

RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app
COPY backend/requirements.txt .
# Cria wheels de tudo para instalar rápido no próximo estágio
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /usr/src/app/wheels -r requirements.txt

# ESTÁGIO 2: Runner (Imagem final de produção, enxuta e segura)
FROM python:3.11-slim as runner

# Libpq5 é a runtime do Postgres (não precisa do gcc aqui)
RUN apt-get update && apt-get install -y libpq5 curl && rm -rf /var/lib/apt/lists/*

# Hardening: Criação do usuário não-root
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Copia os wheels e instala
COPY --from=builder /usr/src/app/wheels /wheels
COPY --from=builder /usr/src/app/requirements.txt .
RUN pip install --no-cache /wheels/*

# Copia o código do projeto
COPY shared /app/shared
COPY backend /app/backend

# Ajusta permissões
RUN chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000

# Executa com Uvicorn
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]