#!/usr/bin/env python3
"""Prometheus exporter for the MySQL -> Doris CDC validation lab.

Collects:
  * Flink CDC job state / checkpoint age / source event-time lag
  * end-to-end heartbeat lag (MySQL clock vs Doris row)
  * MySQL binlog size / current file/position / retention
  * MySQL Router RW+RO reachability
  * MGR member state and applier queue
  * Doris FE/BE alive and BE stream-load metrics
"""
import json
import os
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

CDC_PASSWORD = os.environ.get("CDC_PASSWORD", "")
MGR_ADMIN_PASSWORD = os.environ.get("MGR_ADMIN_PASSWORD", "")

FLINK_API = "http://10.10.12.130:8081"
FLINK_TIMEOUT = 4

# (host, port, user, password, heartbeat dbs, binlog instance label)
MYSQL_SINGLE = ("10.10.12.121", 3306, "cdc", CDC_PASSWORD, ["app_db"], "mysql-single")
MYSQL_MGR = ("10.10.12.125", 6446, "admin", MGR_ADMIN_PASSWORD, ["app_mgr"], "mysql-mgr-router")
MYSQL_MULTIDB = ("10.10.12.121", 3306, "cdc", CDC_PASSWORD, ["mdb_a", "mdb_b"], "mysql-multi")
MYSQL_INST_B = ("10.10.12.132", 3306, "cdc", CDC_PASSWORD, ["app_db"], "mysql-inst-b")

PIPELINES = [
    {"name": "single", "job": "mysql-single-to-doris", "mysql": MYSQL_SINGLE},
    {"name": "mgr", "job": "mysql-mgr-to-doris", "mysql": MYSQL_MGR},
    {"name": "multi-db", "job": "mysql-multidb-to-doris", "mysql": MYSQL_MULTIDB},
    {"name": "inst-b", "job": "mysql-inst-b-to-doris", "mysql": MYSQL_INST_B},
]

# (pipeline, Doris target database) for the Doris-side cross-check
DORIS_HEARTBEATS = [
    ("single", "app_db"),
    ("mgr", "app_mgr"),
    ("multi-db", "ods_multi_a"),
    ("multi-db", "ods_multi_b"),
    ("inst-b", "ods_b_app_db"),
]

MGR_MEMBERS = ["10.10.12.122", "10.10.12.123", "10.10.12.124"]
MGR_USER = ("admin", MGR_ADMIN_PASSWORD)
ROUTER_ENDPOINTS = [("rw", "10.10.12.125", 6446), ("ro", "10.10.12.125", 6447)]
DORIS_FE = ("10.10.12.126", 9030, "root", "")
DORIS_BES = ["10.10.12.127", "10.10.12.128", "10.10.12.129"]

STATE_LOCK = threading.Lock()
STATE = {"ts": 0, "samples": [], "errors": []}


def mysql_rows(host, port, user, password, sql, timeout=6, header=False):
    cmd = ["mysql", "--protocol=tcp", "-h", host, "-P", str(port), "-u", user, "-B"]
    if not header:
        cmd.append("-N")
    cmd += ["-e", sql]
    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip().splitlines()[-1][:240])
    rows = []
    for line in proc.stdout.rstrip("\n").splitlines():
        if line:
            rows.append(line.split("\t"))
    return rows


def as_float(value, default=-1.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def flink_json(path):
    with urlopen(FLINK_API + path, timeout=FLINK_TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def emit(samples, name, labels, value, help_text=None, metric_type="gauge"):
    if value is None:
        return
    if help_text:
        samples.append(("__HELP__", name, help_text))
    samples.append((metric_type, name, labels, value))


def collect_flink(samples, pipeline, overview):
    job = None
    if overview:
        candidates = [
            j for j in overview.get("jobs", [])
            if j.get("name") == pipeline["job"] and j.get("state") != "CANCELED"
        ]
        candidates.sort(key=lambda j: j.get("start-time", 0), reverse=True)
        job = candidates[0] if candidates else None

    state_value = 0
    if job:
        state_value = {
            "RUNNING": 1, "RESTARTING": 2, "FAILED": 3,
            "INITIALIZING": 2, "CREATED": 2, "SUSPENDED": 2,
            "FINISHED": 0, "CANCELED": 0,
        }.get(job.get("state"), 0)
    emit(samples, "mysql_doris_flink_job_state", {"pipeline": pipeline["name"]}, state_value,
         "Flink CDC job state: 0 down, 1 running, 2 restarting, 3 failed")

    if not job:
        emit(samples, "mysql_doris_flink_checkpoint_age_seconds", {"pipeline": pipeline["name"]}, -1)
        emit(samples, "mysql_doris_flink_checkpoint_failed_total", {"pipeline": pipeline["name"]}, -1)
        emit(samples, "mysql_doris_source_fetch_lag_seconds", {"pipeline": pipeline["name"]}, -1)
        emit(samples, "mysql_doris_source_emit_lag_seconds", {"pipeline": pipeline["name"]}, -1)
        return

    jid = job["jid"]
    try:
        ckpt = flink_json(f"/jobs/{jid}/checkpoints")
        latest = (ckpt.get("latest") or {}).get("completed")
        age = time.time() - latest["trigger_timestamp"] / 1000.0 if latest else -1
        emit(samples, "mysql_doris_flink_checkpoint_age_seconds", {"pipeline": pipeline["name"]}, round(age, 3),
             "Seconds since the latest completed checkpoint was triggered")
        emit(samples, "mysql_doris_flink_checkpoint_failed_total", {"pipeline": pipeline["name"]},
             ckpt.get("counts", {}).get("failed", -1), "Failed checkpoint count")
    except Exception as exc:
        STATE["errors"].append(f"flink checkpoints {pipeline['name']}: {exc}")

    fetch_lag = -1.0
    emit_lag = -1.0
    try:
        detail = flink_json(f"/jobs/{jid}")
        source_vertex = None
        for vertex in detail.get("vertices", []):
            if "Source:" in vertex.get("name", "") and "CDC" in vertex.get("name", ""):
                source_vertex = vertex
                break
        if source_vertex:
            metrics = flink_json(f"/jobs/{jid}/vertices/{source_vertex['id']}/metrics")
            wanted = [
                m["id"] for m in metrics
                if "currentFetchEventTimeLag" in m.get("id", "")
                or "currentEmitEventTimeLag" in m.get("id", "")
            ]
            if wanted:
                values = flink_json(
                    f"/jobs/{jid}/vertices/{source_vertex['id']}/metrics?get=" + ",".join(wanted)
                )
                for metric in values:
                    mid = metric.get("id", "")
                    value = metric.get("value")
                    if value is None:
                        continue
                    if "currentFetchEventTimeLag" in mid:
                        fetch_lag = max(fetch_lag, as_float(value, -1.0))
                    elif "currentEmitEventTimeLag" in mid:
                        emit_lag = max(emit_lag, as_float(value, -1.0))
    except Exception as exc:
        STATE["errors"].append(f"flink source metrics {pipeline['name']}: {exc}")

    # Flink reports milliseconds; convert to seconds. -1 means unknown/idle.
    emit(samples, "mysql_doris_source_fetch_lag_seconds", {"pipeline": pipeline["name"]},
         round(fetch_lag / 1000.0, 3) if fetch_lag >= 0 else -1,
         "Flink CDC source currentFetchEventTimeLag (seconds)")
    emit(samples, "mysql_doris_source_emit_lag_seconds", {"pipeline": pipeline["name"]},
         round(emit_lag / 1000.0, 3) if emit_lag >= 0 else -1,
         "Flink CDC source currentEmitEventTimeLag (seconds)")


def collect_mysql_pipeline(samples, pipeline):
    host, port, user, password, dbs, label = pipeline["mysql"]
    now_local = time.time()
    for db in dbs:
        labels = {"pipeline": pipeline["name"], "db": db}
        try:
            rows = mysql_rows(
                host, port, user, password,
                f"SELECT UNIX_TIMESTAMP(NOW(3)), "
                f"(SELECT UNIX_TIMESTAMP(MAX(ts)) FROM {db}.sync_heartbeat);",
            )
            mysql_now = as_float(rows[0][0])
            hb = as_float(rows[0][1])
            lag = mysql_now - hb if mysql_now > 0 and hb > 0 else -1
            emit(samples, "mysql_doris_heartbeat_lag_seconds", labels, round(lag, 3),
                 "End-to-end heartbeat lag: MySQL NOW(3) minus MAX(ts) synced into Doris")
            emit(samples, "mysql_doris_heartbeat_age_seconds", labels,
                 round(now_local - hb, 3) if hb > 0 else -1)
            emit(samples, "mysql_doris_clock_skew_seconds", labels,
                 round(mysql_now - now_local, 3), "MySQL clock minus exporter clock")
        except Exception as exc:
            emit(samples, "mysql_doris_heartbeat_lag_seconds", labels, -1)
            emit(samples, "mysql_doris_sync_up", labels, 0)
            STATE["errors"].append(f"mysql heartbeat {pipeline['name']}/{db}: {exc}")

    try:
        logs = mysql_rows(host, port, user, password, "SHOW BINARY LOGS;")
        total = sum(int(row[1]) for row in logs if len(row) > 1 and row[1].isdigit())
        emit(samples, "mysql_doris_binlog_total_bytes", {"instance": label}, total,
             "Total size of all binary log files")
        emit(samples, "mysql_doris_binlog_file_count", {"instance": label}, len(logs))
        status = mysql_rows(host, port, user, password, "SHOW BINARY LOG STATUS;")
        if status:
            emit(samples, "mysql_doris_binlog_position", {"instance": label, "file": status[0][0]},
                 int(status[0][1]), "Current binary log position")
        retention = mysql_rows(host, port, user, password, "SELECT @@binlog_expire_logs_seconds;")
        if retention:
            emit(samples, "mysql_doris_binlog_retention_seconds", {"instance": label},
                 int(retention[0][0]), "binlog_expire_logs_seconds")
    except Exception as exc:
        emit(samples, "mysql_doris_binlog_total_bytes", {"instance": label}, -1)
        STATE["errors"].append(f"mysql binlog {pipeline['name']}: {exc}")


def collect_router(samples):
    for name, host, port in ROUTER_ENDPOINTS:
        ok = 0
        try:
            with socket.create_connection((host, port), timeout=1):
                ok = 1
        except OSError:
            ok = 0
        emit(samples, "mysql_doris_router_up", {"endpoint": name, "target": f"{host}:{port}"}, ok,
             "MySQL Router TCP endpoint reachability")


def collect_mgr(samples):
    rows = None
    for member in MGR_MEMBERS:
        try:
            rows = mysql_rows(
                member, 3306, MGR_USER[0], MGR_USER[1],
                "SELECT m.MEMBER_HOST, m.MEMBER_STATE, m.MEMBER_ROLE, "
                "COALESCE(s.COUNT_TRANSACTIONS_IN_QUEUE, 0) "
                "FROM performance_schema.replication_group_members m "
                "LEFT JOIN performance_schema.replication_group_member_stats s "
                "ON m.MEMBER_ID = s.MEMBER_ID;",
            )
            break
        except Exception as exc:
            STATE["errors"].append(f"mgr members via {member}: {exc}")
    if rows is None:
        emit(samples, "mysql_doris_mgr_member_online", {"member": "unknown"}, -1)
        return
    for row in rows:
        if len(row) < 4:
            continue
        host, state, role, queue = row[0], row[1], row[2], as_float(row[3], 0)
        emit(samples, "mysql_doris_mgr_member_online", {"member": host},
             1 if state == "ONLINE" else 0, "MGR member ONLINE flag")
        emit(samples, "mysql_doris_mgr_member_primary", {"member": host},
             1 if role == "PRIMARY" else 0, "MGR member primary flag")
        emit(samples, "mysql_doris_mgr_transactions_in_queue", {"member": host}, queue,
             "MGR member applier transaction queue length")


def collect_doris(samples):
    try:
        rows = mysql_rows(*DORIS_FE, "SHOW FRONTENDS;", header=True)
        if rows:
            idx = {name: i for i, name in enumerate(rows[0])}
            for row in rows[1:]:
                host = row[idx["Host"]] if "Host" in idx else "unknown"
                alive = row[idx["Alive"]].lower() == "true" if "Alive" in idx else False
                emit(samples, "mysql_doris_doris_fe_alive", {"fe": host}, 1 if alive else 0,
                     "Doris FE alive flag")
    except Exception as exc:
        STATE["errors"].append(f"doris frontends: {exc}")

    try:
        rows = mysql_rows(*DORIS_FE, "SHOW BACKENDS;", header=True)
        if rows:
            idx = {name: i for i, name in enumerate(rows[0])}
            for row in rows[1:]:
                host = row[idx["Host"]] if "Host" in idx else "unknown"
                alive = row[idx["Alive"]].lower() == "true" if "Alive" in idx else False
                emit(samples, "mysql_doris_doris_be_alive", {"be": host}, 1 if alive else 0,
                     "Doris BE alive flag")
                if "TabletNum" in idx:
                    emit(samples, "mysql_doris_doris_be_tablets", {"be": host},
                         as_float(row[idx["TabletNum"]], 0))
    except Exception as exc:
        STATE["errors"].append(f"doris backends: {exc}")

    try:
        subqueries = ", ".join(
            f"(SELECT UNIX_TIMESTAMP(MAX(ts)) FROM {db}.sync_heartbeat)"
            for _, db in DORIS_HEARTBEATS
        )
        rows = mysql_rows(*DORIS_FE, f"SELECT UNIX_TIMESTAMP(NOW(3)), {subqueries};")
        doris_now = as_float(rows[0][0])
        for idx, (pipeline, db) in enumerate(DORIS_HEARTBEATS, start=1):
            ts = as_float(rows[0][idx])
            emit(samples, "mysql_doris_heartbeat_lag_doris_seconds",
                 {"pipeline": pipeline, "target_db": db},
                 round(doris_now - ts, 3) if ts > 0 else -1,
                 "Heartbeat lag measured entirely on the Doris clock")
        emit(samples, "mysql_doris_clock_skew_seconds", {"pipeline": "doris"},
             round(doris_now - time.time(), 3), "Doris clock minus exporter clock")
    except Exception as exc:
        STATE["errors"].append(f"doris heartbeat: {exc}")

    for be in DORIS_BES:
        try:
            with urlopen(f"http://{be}:8040/metrics", timeout=3) as resp:
                text = resp.read().decode("utf-8", "replace")
            for metric, labels in (
                ("doris_be_streaming_load_duration_ms", {"be": be}),
                ("doris_be_streaming_load_current_processing", {"be": be}),
                ("doris_be_load_channel_count", {"be": be}),
                ("doris_be_disks_avail_capacity", {"be": be}),
            ):
                match = re.search(rf"^{metric}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)$", text, re.M)
                if match:
                    emit(samples, "mysql_doris_doris_" + metric.replace("doris_be_", ""),
                         labels, as_float(match.group(1), 0))
        except Exception as exc:
            STATE["errors"].append(f"doris be metrics {be}: {exc}")


def refresh():
    samples = []
    STATE["errors"] = []
    overview = None
    flink_up = 0
    try:
        overview = flink_json("/jobs/overview")
        flink_up = 1
    except Exception as exc:
        STATE["errors"].append(f"flink api: {exc}")
    emit(samples, "mysql_doris_flink_api_up", {}, flink_up,
         "Flink REST API reachability from the exporter")
    for pipeline in PIPELINES:
        collect_flink(samples, pipeline, overview)
        collect_mysql_pipeline(samples, pipeline)
    collect_router(samples)
    collect_mgr(samples)
    collect_doris(samples)
    emit(samples, "mysql_doris_exporter_last_refresh_timestamp", {}, int(time.time()))
    with STATE_LOCK:
        STATE["samples"] = samples
        STATE["ts"] = int(time.time())


def refresh_loop():
    while True:
        try:
            refresh()
        except Exception as exc:  # keep serving even if one collector explodes
            with STATE_LOCK:
                STATE["errors"].append(f"refresh: {exc}")
        time.sleep(5)


def render():
    with STATE_LOCK:
        samples = list(STATE["samples"])
        errors = list(STATE["errors"])
    lines = []
    seen_help = set()
    for sample in samples:
        if sample[0] == "__HELP__":
            _, name, text = sample
            if name not in seen_help:
                seen_help.add(name)
                lines.append(f"# HELP {name} {text}")
                lines.append(f"# TYPE {name} gauge")
            continue
        metric_type, name, labels, value = sample
        if isinstance(labels, dict):
            labels = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
            lines.append(f"{name}{{{labels}}} {value}")
        else:
            lines.append(f"{name} {value}")
    lines.append(f"# exporter errors: {len(errors)}")
    return "\n".join(lines) + "\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/metrics"):
            body = render().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok\n")
        elif self.path == "/status":
            with STATE_LOCK:
                body = json.dumps({"last_refresh": STATE["ts"], "errors": STATE["errors"]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=refresh_loop, daemon=True).start()
    time.sleep(1)
    server = ThreadingHTTPServer(("0.0.0.0", 9273), Handler)
    server.serve_forever()
