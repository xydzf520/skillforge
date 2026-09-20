FROM python:3.12-slim

RUN pip install --no-cache-dir "websockets>=12,<16" "cryptography>=44,<45" "requests>=2,<3"

WORKDIR /app
COPY fake_openclaw_gateway.py /app/fake_openclaw_gateway.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENV SKILLS_DIR=/home/sim/.openclaw/skills
ENV LOCAL_AICLAW_URL=ws://127.0.0.1:18789

ENTRYPOINT ["/app/entrypoint.sh"]
