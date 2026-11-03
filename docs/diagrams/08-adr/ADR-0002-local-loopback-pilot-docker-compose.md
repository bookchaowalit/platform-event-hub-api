# ADR-0002 — Local loopback pilot on Docker Compose with Mosquitto (not hosted)

- Status: accepted (retro-documented)
- Date: retro-documented 2026-11-04

## Context
Pilot สำหรับ telemetry ปริมาณน้อย ไม่มีอุปกรณ์จริงยืนยัน และ MQTT ยังไม่มี TLS.

## Decision
รันด้วย `infra/event-hub/compose.yml`: `se-event-hub` (python:3.11-slim, UID 1000, cap_drop ALL, no-new-privileges, 512m / 0.5 CPU)
และ `se-event-hub-mqtt` (eclipse-mosquitto:2.0.18, `allow_anonymous false`, ACL, 64m / 0.25 CPU). Publish เฉพาะ `127.0.0.1:8112` และ
`${EVENT_HUB_MQTT_BIND:-127.0.0.1}:${EVENT_HUB_MQTT_PORT:-1884}`. Hosted deployment อยู่นอก scope.

## Evidence
- `infra/event-hub/compose.yml`, `infra/event-hub/Dockerfile`, `infra/event-hub/mosquitto.conf`
- `PRODUCT.md` Positioning / Operating Context ("Not deployed", "Out of scope: hosted deployment")
- solo-empire `repository-catalog/registries/online-systems.yaml`: `event-hub` `host: {provider: local, mode: local}`, `status: exception`

## Consequences
- ไม่มี uptime monitor, backup, หรือ CD แบบ hosted services อื่น (ดู 14, 16, 20)
- การเปิด LAN ต้องตั้ง bind เอง และควรใช้ TLS/tunnel (README)
