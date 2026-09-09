#!/usr/bin/env python3
import os
import socket
import subprocess
import sys
import time
import urllib.request


SINGBOX_SERVICE = os.environ.get("WATCHDOG_SINGBOX_SERVICE", "sing-box")
PANEL_SERVICE = os.environ.get("WATCHDOG_PANEL_SERVICE", "singbox-panel")
NGINX_SERVICE = os.environ.get("WATCHDOG_NGINX_SERVICE", "nginx")
SINGBOX_CONFIG = os.environ.get("WATCHDOG_SINGBOX_CONFIG", "/etc/sing-box/config.json")
PANEL_URL = os.environ.get("WATCHDOG_PANEL_URL", "http://127.0.0.1:8080/")
NGINX_URL = os.environ.get("WATCHDOG_NGINX_URL", "http://127.0.0.1/")
NGINX_HOST = os.environ.get("WATCHDOG_NGINX_HOST", "panel.5858188.xyz")
CHECK_HOST = os.environ.get("WATCHDOG_CHECK_HOST", "127.0.0.1")
CHECK_PORT = int(os.environ.get("WATCHDOG_CHECK_PORT", "443"))
INTERVAL_SECONDS = int(os.environ.get("WATCHDOG_INTERVAL_SECONDS", "30"))
FAIL_THRESHOLD = int(os.environ.get("WATCHDOG_FAIL_THRESHOLD", "3"))
COOLDOWN_SECONDS = int(os.environ.get("WATCHDOG_COOLDOWN_SECONDS", "120"))
HTTP_TIMEOUT_SECONDS = int(os.environ.get("WATCHDOG_HTTP_TIMEOUT_SECONDS", "5"))


def log(message):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


def run(cmd, timeout=15):
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        universal_newlines=True,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def service_active(service):
    code, out, _ = run(["systemctl", "is-active", service], timeout=10)
    return code == 0 and out == "active"


def tcp_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def http_ok(url, host_header=None):
    try:
        req = urllib.request.Request(url)
        if host_header:
            req.add_header("Host", host_header)
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            return 200 <= resp.status < 500
    except Exception:
        return False


def singbox_config_ok():
    code, out, err = run(["sing-box", "check", "-c", SINGBOX_CONFIG], timeout=20)
    if code != 0:
        log("sing-box config check failed: " + (err or out))
        return False
    return True


def restart_service(service):
    code, out, err = run(["systemctl", "restart", service], timeout=30)
    if code == 0:
        log("restarted " + service)
        return True
    log("restart failed for %s: %s" % (service, err or out))
    return False


def check_once():
    failures = []
    if not service_active(SINGBOX_SERVICE):
        failures.append(SINGBOX_SERVICE + " inactive")
    if not tcp_open(CHECK_HOST, CHECK_PORT):
        failures.append("%s:%s closed" % (CHECK_HOST, CHECK_PORT))
    if not service_active(PANEL_SERVICE):
        failures.append(PANEL_SERVICE + " inactive")
    if not http_ok(PANEL_URL):
        failures.append("panel http failed")
    if not service_active(NGINX_SERVICE):
        failures.append(NGINX_SERVICE + " inactive")
    if not http_ok(NGINX_URL, NGINX_HOST):
        failures.append("nginx reverse proxy failed")
    return failures


def main():
    fail_count = 0
    last_restart = 0
    log("watchdog started")
    while True:
        failures = check_once()
        if not failures:
            if fail_count:
                log("health recovered")
            fail_count = 0
            time.sleep(INTERVAL_SECONDS)
            continue

        fail_count += 1
        log("health check failed %s/%s: %s" % (fail_count, FAIL_THRESHOLD, "; ".join(failures)))
        now = time.time()
        if fail_count >= FAIL_THRESHOLD and now - last_restart >= COOLDOWN_SECONDS:
            if singbox_config_ok():
                restart_service(SINGBOX_SERVICE)
            if not service_active(PANEL_SERVICE):
                restart_service(PANEL_SERVICE)
            if not service_active(NGINX_SERVICE) or not http_ok(NGINX_URL, NGINX_HOST):
                restart_service(NGINX_SERVICE)
            last_restart = now
            fail_count = 0
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
