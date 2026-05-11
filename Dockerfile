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
    CMD python -c "import urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health',timeout=4); sys.exit(0 if 200<=r.status<300 else 1)" || exit 1

EXPOSE 8000

CMD ["python", "-m", "src.main"]
