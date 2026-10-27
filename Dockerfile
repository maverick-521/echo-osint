# ECHO - cloud image (Render / Railway / Fly / any Docker host). PIN-gated: set ECHO_PIN.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 ECHO_BIND=cloud PORT=8080
WORKDIR /srv/echo
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.*
COPY app ./app
COPY engine ./engine
RUN mkdir -p reports && useradd -r -u 10001 echo && chown -R echo /srv/echo
USER echo
EXPOSE 8080
# one worker (jobs live in memory), many threads; scans can run for 20+ min so no request timeout
CMD ["sh", "-c", "gunicorn --chdir app -w 1 --threads 16 --timeout 0 -b 0.0.0.0:${PORT} server:app"]
