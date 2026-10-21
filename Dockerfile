FROM python:3.12-slim

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app/client.py /app/client.py

# Unprivileged, so pods can run under the restricted Pod Security Standard. A
# real account, since libpq looks up the OS user when no database user is set.
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin app
USER 10001:10001

CMD ["python", "/app/client.py"]
