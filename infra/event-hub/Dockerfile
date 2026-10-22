FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# Hash-pinned lock (uv pip compile --generate-hashes): every build installs the versions CI tested.
COPY event-hub/requirements.lock /tmp/event-hub-requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /tmp/event-hub-requirements.lock
COPY scripts/data_lake /app/infra/scripts/data_lake
# event_hub imports cell_store (the Intake cell client), which needs task_store and recurring_step.
COPY scripts/utils/_env.py scripts/utils/cell_store.py scripts/utils/task_store.py scripts/utils/recurring_step.py /app/infra/scripts/utils/
COPY scripts/cli/data-intake-cli.py /app/infra/scripts/cli/
COPY scripts/services/event_hub.py scripts/services/event_hub_transport.py scripts/services/_sentry.py /app/infra/scripts/services/
COPY database/migrations/033_source_registry_intake_queue.sql /app/infra/database/migrations/
COPY event-hub/config.json event-hub/index.html /app/infra/event-hub/
USER 1000:1000
CMD ["python", "infra/scripts/services/event_hub.py", "serve", "--bind", "0.0.0.0", "--mqtt-host", "mqtt", "--mqtt-port", "1883"]
