# telegram-bot — HTTP webhook receiver that forwards alerts to Telegram Bot API
#
# STATUS: stub. Real implementation arrives with `bootstrap-telegram-bot` OpenSpec change.

FROM mambaorg/micromamba:1.5

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY environment.yml* requirements.txt* /app/
RUN if [ -f environment.yml ]; then \
        micromamba install -y -n base -f environment.yml && micromamba clean -afy; \
    elif [ -f requirements.txt ]; then \
        micromamba install -y -n base python=3.11 pip && pip install -r requirements.txt; \
    fi

COPY src/ /app/src/

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

EXPOSE 8000

CMD ["python", "-m", "src.main"]
