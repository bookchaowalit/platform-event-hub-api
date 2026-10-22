"""Loopback-only pilot transports; per-source HTTP auth and MQTT QoS 1."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.request import Request, urlopen

from _sentry import capture
from event_hub import (
    DEFAULT_CONFIG,
    INFRA,
    MAX_BYTES,
    Hub,
    Rejected,
    fixture,
    load_config,
)
from data_lake.ingest import canonical_json  # on sys.path once event_hub is imported

if TYPE_CHECKING:
    import argparse

    from paho.mqtt.client import Client, ConnectFlags, DisconnectFlags, MQTTMessage
    from paho.mqtt.properties import Properties
    from paho.mqtt.reasoncodes import ReasonCode


def read_secrets(runtime: Path) -> dict[str, Any]:
    path = runtime / "secrets.json"
    if path.stat().st_mode & 0o077:
        raise RuntimeError("secret_file_permissions")
    values: dict[str, Any] = json.loads(path.read_text())
    if set(values) != {"mqtt", "http", "operator"}:
        raise RuntimeError("invalid_secret_file")
    if (
        not isinstance(values["mqtt"], dict)
        or not isinstance(values["http"], dict)
        or not isinstance(values["operator"], str)
    ):
        raise RuntimeError("invalid_secret_file")
    return values


def write_private_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically write a runtime file with no group/other permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_acl(runtime: Path, values: dict[str, Any]) -> Path:
    """Render the scoped MQTT ACL from the source registry."""
    auth = runtime / "auth"
    auth.mkdir(mode=0o700, parents=True, exist_ok=True)
    lines = ["user event-hub", "topic read se/events/+", ""]
    for source in sorted(values["mqtt"]):
        if source == "event-hub":
            continue
        lines.extend([f"user {source}", f"topic write se/events/{source}", ""])
    path = auth / "acl"
    temporary = auth / ".acl.tmp"
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write("\n".join(lines))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _password_stage(runtime: Path, values: dict[str, Any]) -> Path:
    auth = runtime / "auth"
    auth.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = auth / ".passwords.tmp"
    stage.unlink(missing_ok=True)
    fd = os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(
            "".join(
                f"{name}:{password}\n"
                for name, password in sorted(values["mqtt"].items())
            )
        )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0",
            "--mount",
            f"type=bind,source={auth},target=/auth",
            "eclipse-mosquitto:2.0.18",
            "mosquitto_passwd",
            "-U",
            "/auth/.passwords.tmp",
        ],
        capture_output=True,
        timeout=60,
        check=False,  # returncode is checked below
    )
    if result.returncode:
        stage.unlink(missing_ok=True)
        raise RuntimeError("mqtt_password_setup_failed")
    stage.chmod(0o600)
    return stage


def write_password_file(runtime: Path, values: dict[str, Any]) -> Path:
    stage = _password_stage(runtime, values)
    destination = runtime / "auth/passwords"
    stage.replace(destination)
    destination.chmod(0o600)
    return destination


def setup(runtime: Path, config: Path) -> dict[str, Any]:
    runtime = runtime.resolve()
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    load_config(config)
    path = runtime / "secrets.json"
    if not path.exists():
        values = {
            "mqtt": {
                "sensor-demo": secrets.token_urlsafe(32),
                "event-hub": secrets.token_urlsafe(32),
            },
            "http": {"app-demo": secrets.token_urlsafe(32)},
            "operator": secrets.token_urlsafe(32),
        }
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(canonical_json(values) + "\n")
    values = read_secrets(runtime)
    auth = runtime / "auth"
    auth.mkdir(mode=0o700, exist_ok=True)
    passwd = auth / "passwords"
    target = runtime / "config.json"
    if not target.exists():
        target.write_text(config.read_text())
        target.chmod(0o600)
    if not passwd.exists():
        write_password_file(runtime, values)
    else:
        passwd.chmod(0o600)
    write_acl(runtime, values)
    return {"status": "ready", "message": "เตรียมค่าของ pilot แล้ว"}


def onboard(
    runtime: Path,
    config: Path,
    source_id: str,
    event_type: str,
    device_host: str,
    device_port: int,
) -> dict[str, Any]:
    """Create one scoped MQTT identity and a private device connection profile."""
    setup(runtime, config)
    runtime = runtime.resolve()
    runtime_config = runtime / "config.json"
    cfg = load_config(runtime_config)
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", source_id or ""):
        raise Rejected("invalid_source_id")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", event_type or ""):
        raise Rejected("invalid_event_type")
    if source_id == "event-hub" or source_id in cfg["sources"]:
        raise Rejected("source_exists")
    if not device_host or any(char in device_host for char in "/\\@ \t\r\n"):
        raise Rejected("invalid_device_host")
    if not 1 <= int(device_port) <= 65535:
        raise Rejected("invalid_device_port")
    values = read_secrets(runtime)
    if source_id in values["mqtt"]:
        raise Rejected("source_exists")
    password = secrets.token_urlsafe(32)
    next_values = json.loads(canonical_json(values))
    next_values["mqtt"][source_id] = password
    next_config = json.loads(canonical_json(cfg))
    next_config["sources"][source_id] = {
        "enabled": True,
        "transport": "mqtt",
        "event_types": [event_type],
    }
    stage = _password_stage(runtime, next_values)
    try:
        write_private_json(runtime / "secrets.json", next_values)
        write_private_json(runtime_config, next_config)
        stage.replace(runtime / "auth/passwords")
        (runtime / "auth/passwords").chmod(0o600)
        write_acl(runtime, next_values)
    except Exception:
        stage.unlink(missing_ok=True)
        raise
    devices = runtime / "devices"
    devices.mkdir(mode=0o700, exist_ok=True)
    profile = {
        "schema_version": "event-hub.device-profile.v1",
        "source_id": source_id,
        "username": source_id,
        "password": password,
        "host": device_host,
        "port": int(device_port),
        "topic": f"se/events/{source_id}",
        "qos": 1,
        "retain": False,
        "event": {
            "schema_version": "event.v1",
            "source_id": source_id,
            "event_type": event_type,
            "payload": {"value": 0, "unit": "celsius"},
        },
    }
    profile_path = devices / f"{source_id}.json"
    write_private_json(profile_path, profile)
    return {
        "status": "created",
        "source_id": source_id,
        "topic": profile["topic"],
        "profile_path": str(profile_path),
        "restart_required": True,
        "message": "อุปกรณ์ถูกลงทะเบียนแล้ว; restart broker เพื่อโหลด ACL ใหม่",
    }


def mqtt_client(
    values: dict[str, Any], username: str, *, client_id: str, durable: bool = False
) -> Client:
    import paho.mqtt.client as mqtt

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        clean_session=not durable,
        protocol=mqtt.MQTTv311,
        manual_ack=durable,
    )
    client.username_pw_set(username, values["mqtt"][username])
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    return client


class MQTTWorker:
    def __init__(
        self, hub: Hub, values: dict[str, Any], port: int, host: str = "127.0.0.1"
    ) -> None:
        self.hub = hub
        self.port = port
        self.host = host
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.last_error: str | None = None
        self.client = mqtt_client(
            values, "event-hub", client_id="se-event-hub-worker-v1", durable=True
        )
        self.client.on_connect = self.on_connect
        self.client.on_subscribe = self.on_subscribe
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def on_connect(
        self,
        client: Client,
        userdata: Any,
        flags: ConnectFlags,
        reason_code: ReasonCode,
        properties: Properties | None,
    ) -> None:
        if reason_code.is_failure:
            self.last_error = "mqtt_auth_failed"
        else:
            client.subscribe("se/events/+", qos=1)

    def on_subscribe(
        self,
        client: Client,
        userdata: Any,
        mid: int,
        reasons: list[ReasonCode],
        properties: Properties | None,
    ) -> None:
        if reasons and all(not reason.is_failure for reason in reasons):
            self.ready.set()
            self.last_error = None
        else:
            self.last_error = "mqtt_subscription_failed"

    def on_disconnect(
        self,
        client: Client,
        userdata: Any,
        flags: DisconnectFlags,
        reason: ReasonCode,
        properties: Properties | None,
    ) -> None:
        self.ready.clear()

    def on_message(self, client: Client, userdata: Any, message: MQTTMessage) -> None:
        try:
            source = message.topic.removeprefix("se/events/")
            if message.qos != 1 or message.retain:
                result = self.hub.reject(message.payload, "mqtt_qos_or_retained")
            else:
                result = self.hub.receive(message.payload, source, "mqtt")
            if result["status"] in {"complete", "duplicate", "rejected"} or result.get(
                "retry_exhausted"
            ):
                # Exhausted work is durably retained for explicit operator retry.
                client.ack(message.mid, message.qos)
                self.last_error = None
            else:
                self.last_error = "mqtt_processing_failed"
                client.disconnect()  # Preserve unacked QoS 1 for redelivery.
        except Exception as exc:
            capture(exc)  # no-op unless SENTRY_DSN is set
            self.last_error = "mqtt_processing_failed"
            client.disconnect()

    def loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.client.connect(self.host, self.port, keepalive=30)
                self.client.loop_forever(retry_first_connection=False)
            except Exception:
                self.last_error = "mqtt_unavailable"
            self.ready.clear()
            self.stop_event.wait(3)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.stop_event.set()
        self.client.disconnect()
        self.thread.join(timeout=35)


def handler_for(
    hub: Hub, values: dict[str, Any], mqtt_worker: MQTTWorker | None = None
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "EventHub/1"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_args: Any) -> None:
            pass

        def respond(
            self, code: int, payload: dict[str, Any], cookie: str | None = None
        ) -> None:
            raw = canonical_json(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(raw)

        def bearer(self) -> str:
            header = self.headers.get("Authorization", "")
            return header[7:] if header.startswith("Bearer ") else ""

        def operator(self) -> bool:
            candidate = self.bearer()
            if not candidate:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    candidate = (
                        cookie["hub_session"].value if "hub_session" in cookie else ""
                    )
                except Exception:
                    return False
            return hmac.compare_digest(candidate, values["operator"])

        def local_origin(self) -> bool:
            port = cast("HTTPServer", self.server).server_port
            expected = {f"127.0.0.1:{port}", f"localhost:{port}"}
            return self.headers.get("Host") in expected and (
                not self.headers.get("Origin")
                or self.headers["Origin"] in {"http://" + x for x in expected}
            )

        def body(self, maximum: int = MAX_BYTES) -> bytes:
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                raise Rejected("content_length_required")
            try:
                length = int(self.headers["Content-Length"])
            except ValueError as exc:
                raise Rejected("payload_size") from exc
            if not 0 < length <= maximum:
                raise Rejected("payload_size")
            if self.headers.get_content_type() != "application/json":
                raise Rejected("content_type_required")
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise Rejected("incomplete_body")
            return raw

        def do_GET(self) -> None:
            try:
                return self.get_response()
            except Exception as exc:
                capture(exc)  # no-op unless SENTRY_DSN is set
                return self.respond(503, {"code": "status_unavailable"})

        def get_response(self) -> None:
            if not self.local_origin():
                return self.respond(403, {"code": "origin_not_allowed"})
            if self.path == "/":
                raw = (INFRA / "event-hub/index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
                )
                self.end_headers()
                self.wfile.write(raw)
                return None
            if self.path == "/health":
                healthy = mqtt_worker is None or (
                    mqtt_worker.ready.is_set() and not mqtt_worker.last_error
                )
                healthy = healthy and hub.snapshot()["status"] == "healthy"
                return self.respond(
                    200 if healthy else 503,
                    {"status": "healthy" if healthy else "degraded"},
                )
            if not self.operator():
                return self.respond(401, {"code": "unauthorized"})
            if self.path == "/api/status":
                state = hub.snapshot()
                state["mqtt_connected"] = (
                    mqtt_worker.ready.is_set() if mqtt_worker else False
                )
                return self.respond(200, state)
            return self.respond(404, {"code": "not_found"})

        def do_POST(self) -> None:
            try:
                if not self.local_origin():
                    return self.respond(403, {"code": "origin_not_allowed"})
                if self.path == "/session":
                    if not self.operator():
                        return self.respond(401, {"code": "unauthorized"})
                    return self.respond(
                        200,
                        {"status": "ok"},
                        "hub_session="
                        + values["operator"]
                        + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600",
                    )
                if self.path.startswith("/api/events/"):
                    source = self.path[len("/api/events/") :]
                    expected = values["http"].get(source)
                    if not expected or not hmac.compare_digest(self.bearer(), expected):
                        return self.respond(401, {"code": "unauthorized"})
                    result = hub.receive(self.body(), source, "http")
                    code = (
                        422
                        if result["status"] == "rejected"
                        else 503
                        if result["status"] == "failed"
                        else 200
                    )
                    return self.respond(code, result)
                if not self.operator():
                    return self.respond(401, {"code": "unauthorized"})
                if self.path == "/api/retry":
                    return self.respond(200, {"results": hub.retry()})
                if self.path == "/api/config":
                    raw = self.body(65536)
                    # Only the pilot runtime config can be changed from the UI.
                    if hub.config_path.resolve() != hub.runtime / "config.json":
                        return self.respond(409, {"code": "runtime_config_required"})
                    with hub.lock:
                        stage = hub.runtime / "config.staging.json"
                        stage.write_bytes(raw)
                        try:
                            load_config(stage)
                            stage.replace(hub.config_path)
                            hub.sync_sources()
                        finally:
                            stage.unlink(missing_ok=True)
                    return self.respond(200, {"status": "saved"})
                return self.respond(404, {"code": "not_found"})
            except Rejected as exc:
                self.respond(400, {"code": str(exc)})
            except Exception as exc:
                capture(exc)  # no-op unless SENTRY_DSN is set
                self.respond(503, {"code": "processing_unavailable"})

    return Handler


def simulate(
    runtime: Path, transport: str, port: int, mqtt_port: int, value: float
) -> dict[str, Any]:
    values = read_secrets(runtime)
    source = "sensor-demo" if transport == "mqtt" else "app-demo"
    obj = fixture(source, value)
    raw = canonical_json(obj).encode()
    if transport == "http":
        request = Request(
            f"http://127.0.0.1:{port}/api/events/{source}",
            data=raw,
            headers={
                "Authorization": "Bearer " + values["http"][source],
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=15) as response:
            answer: dict[str, Any] = json.load(response)
            return answer
    client = mqtt_client(values, source, client_id="sim-" + secrets.token_hex(8))
    client.connect("127.0.0.1", mqtt_port, keepalive=30)
    client.loop_start()
    try:
        info = client.publish("se/events/" + source, raw, qos=1, retain=False)
        info.wait_for_publish(timeout=10)
        if not info.is_published():
            raise RuntimeError("publish_timeout")
        return {"status": "broker_accepted", "event_id": obj["event_id"]}
    finally:
        client.disconnect()
        client.loop_stop()


def run(args: argparse.Namespace) -> None:
    if args.command == "setup":
        result = setup(args.runtime, args.config)
    elif args.command == "simulate":
        result = simulate(
            args.runtime, args.transport, args.port, args.mqtt_port, args.value
        )
    elif args.command == "onboard":
        result = onboard(
            args.runtime,
            args.config,
            args.source_id,
            args.event_type,
            args.device_host,
            args.device_port,
        )
    else:
        values = read_secrets(args.runtime)
        config = (
            args.runtime / "config.json"
            if args.config == DEFAULT_CONFIG
            else args.config
        )
        hub = Hub(args.runtime, config)
        hub.retry(automatic=True)
        worker = MQTTWorker(hub, values, args.mqtt_port, args.mqtt_host)

        class Server(HTTPServer):
            last_retry = 0.0

            def service_actions(self) -> None:
                if time.monotonic() - self.last_retry >= 15:
                    self.last_retry = time.monotonic()
                    # Receipts retain failure codes for the status API.
                    with contextlib.suppress(Exception):
                        hub.retry(limit=10, automatic=True)

        server = Server((args.bind, args.port), handler_for(hub, values, worker))
        worker.start()
        print(
            canonical_json(
                {"status": "starting", "url": f"http://127.0.0.1:{args.port}"}
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            worker.close()
            hub.close()
        return
    print(canonical_json(result))
