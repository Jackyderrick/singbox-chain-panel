#!/usr/bin/env python3
import base64
import hashlib
import hmac
import html
import http.cookies
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid

APP_DIR = os.environ.get("APP_DIR", "/opt/singbox-panel")
STATE_PATH = os.path.join(APP_DIR, "state.json")
CONFIG_PATH = os.environ.get("SINGBOX_CONFIG_PATH", "/etc/sing-box/config.json")
BACKUP_DIR = os.path.join(APP_DIR, "backups")
HOST = os.environ.get("PANEL_HOST", "0.0.0.0")
PORT = int(os.environ.get("PANEL_PORT", "8080"))
ADMIN_PASSWORD = os.environ.get("PANEL_PASSWORD", "")
SECRET = os.environ.get("PANEL_SECRET", "")
SINGBOX_BIN = os.environ.get("SINGBOX_BIN", "sing-box")
SINGBOX_MANAGE_MODE = os.environ.get("SINGBOX_MANAGE_MODE", "systemd")
SINGBOX_SERVICE = os.environ.get("SINGBOX_SERVICE", "sing-box")
SINGBOX_LOG_PATH = os.environ.get("SINGBOX_LOG_PATH", os.path.join(APP_DIR, "sing-box.log"))
VLESS_TAG = os.environ.get("VLESS_TAG", "vless-reality-in")
DEFAULT_VLESS_FLOW = os.environ.get("DEFAULT_VLESS_FLOW", "xtls-rprx-vision")
SOCKS_OUT_PREFIX = os.environ.get("SOCKS_OUT_PREFIX", "home-socks5-")
CUSTOMER_OUT_PREFIX = os.environ.get("CUSTOMER_OUT_PREFIX", "customer-route-")
ONLINE_WINDOW_SECONDS = int(os.environ.get("ONLINE_WINDOW_SECONDS", "600"))
CLASH_API_ADDR = os.environ.get("CLASH_API_ADDR", "127.0.0.1:9090")
TRAFFIC_POLL_SECONDS = int(os.environ.get("TRAFFIC_POLL_SECONDS", "5"))
EXPIRE_CHECK_SECONDS = int(os.environ.get("EXPIRE_CHECK_SECONDS", "60"))
PUBLIC_NODE_HOST = os.environ.get("PUBLIC_NODE_HOST", "45.8.173.58")
REALITY_PUBLIC_KEY = os.environ.get("REALITY_PUBLIC_KEY", "")
DEFAULT_HOME_TAG = os.environ.get("DEFAULT_HOME_TAG", "home-socks5-out")
SINGBOX_PROCESS = None
SINGBOX_PROCESS_LOCK = threading.Lock()
STATE_LOCK = threading.RLock()


class PanelError(Exception):
    pass


def now_ts():
    return int(time.time())


def run(cmd, timeout=20):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    if p.returncode != 0:
        raise PanelError((err or out or "command failed").strip())
    return out


def singbox_cmd(*args):
    return [SINGBOX_BIN] + list(args)


def check_singbox_config(path):
    check = subprocess.run(singbox_cmd("check", "-c", path), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check.returncode != 0:
        msg = check.stderr.decode("utf-8", "replace") or check.stdout.decode("utf-8", "replace")
        raise PanelError("sing-box check failed: " + msg.strip())


def ensure_process_singbox_started():
    global SINGBOX_PROCESS
    if SINGBOX_MANAGE_MODE != "process":
        return
    with SINGBOX_PROCESS_LOCK:
        if SINGBOX_PROCESS is not None and SINGBOX_PROCESS.poll() is None:
            return
        os.makedirs(os.path.dirname(SINGBOX_LOG_PATH), exist_ok=True)
        log = open(SINGBOX_LOG_PATH, "ab", buffering=0)
        SINGBOX_PROCESS = subprocess.Popen(
            singbox_cmd("run", "-c", CONFIG_PATH),
            stdout=log,
            stderr=subprocess.STDOUT,
            close_fds=True
        )


def stop_process_singbox():
    global SINGBOX_PROCESS
    with SINGBOX_PROCESS_LOCK:
        if SINGBOX_PROCESS is None or SINGBOX_PROCESS.poll() is not None:
            SINGBOX_PROCESS = None
            return
        SINGBOX_PROCESS.terminate()
        try:
            SINGBOX_PROCESS.wait(timeout=10)
        except subprocess.TimeoutExpired:
            SINGBOX_PROCESS.kill()
            SINGBOX_PROCESS.wait(timeout=5)
        SINGBOX_PROCESS = None


def restart_singbox():
    check_singbox_config(CONFIG_PATH)
    if SINGBOX_MANAGE_MODE == "process":
        stop_process_singbox()
        ensure_process_singbox_started()
    else:
        run(["systemctl", "restart", SINGBOX_SERVICE], timeout=30)


def singbox_status():
    if SINGBOX_MANAGE_MODE == "process":
        ensure_process_singbox_started()
        return "active" if SINGBOX_PROCESS is not None and SINGBOX_PROCESS.poll() is None else "inactive"
    active = subprocess.run(["systemctl", "is-active", SINGBOX_SERVICE], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return active.stdout.decode("utf-8", "replace").strip()


def read_singbox_logs(lines=120):
    if SINGBOX_MANAGE_MODE == "process":
        if not os.path.exists(SINGBOX_LOG_PATH):
            return ""
        with open(SINGBOX_LOG_PATH, "rb") as f:
            data = f.read()
        text = data.decode("utf-8", "replace").splitlines()
        return "\n".join(text[-lines:])
    return run(["journalctl", "-u", SINGBOX_SERVICE, "-n", str(lines), "--no-pager"], timeout=10)


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def state_transaction(func):
    def wrapped(*args, **kwargs):
        with STATE_LOCK:
            return func(*args, **kwargs)
    return wrapped


@state_transaction
def init_state():
    state = load_json(STATE_PATH, {})
    changed = False
    if not state.get("customers"):
        state["customers"] = {}
        changed = True
    if not state.get("devices"):
        state["devices"] = {}
        changed = True
    if not state.get("homes"):
        state["homes"] = {}
        changed = True
    if "active_home" not in state:
        state["active_home"] = DEFAULT_HOME_TAG
        changed = True
    if changed:
        save_json(STATE_PATH, state)
    return state


def read_config():
    return load_json(CONFIG_PATH, {})


def backup_config():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_DIR, "config-%s.json" % stamp)
    shutil.copy2(CONFIG_PATH, dst)
    return dst


def atomic_write_config(cfg):
    backup_config()
    d = os.path.dirname(CONFIG_PATH)
    fd, tmp = tempfile.mkstemp(prefix=".config-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        check_singbox_config(tmp)
        os.replace(tmp, CONFIG_PATH)
        restart_singbox()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_inbound(cfg, tag):
    for inbound in cfg.get("inbounds", []):
        if inbound.get("tag") == tag:
            return inbound
    raise PanelError("missing inbound: " + tag)


def get_vless_inbound(cfg):
    inbound = get_inbound(cfg, VLESS_TAG)
    tls = inbound.get("tls", {})
    reality = tls.get("reality", {})
    if not reality:
        raise PanelError("VLESS REALITY inbound is missing reality settings")
    return inbound


def get_outbound(cfg, tag):
    for outbound in cfg.get("outbounds", []):
        if outbound.get("tag") == tag:
            return outbound
    return None


def slug(text):
    safe = []
    for ch in text.lower():
        if ch.isalnum():
            safe.append(ch)
        elif ch in "-_ ":
            safe.append("-")
    value = "".join(safe).strip("-")
    return value or "home"


@state_transaction
def ensure_state_from_config():
    state = init_state()
    cfg = read_config()
    changed = False
    if "default" not in state["customers"]:
        state["customers"]["default"] = {
            "name": "默认客户",
            "home_tag": cfg.get("route", {}).get("final") or state.get("active_home") or DEFAULT_HOME_TAG,
            "device_limit": 0,
            "limit_action": "alert",
            "default_days": 30,
            "quota_gb": 0,
            "created_at": now_ts()
        }
        changed = True
    for customer_id, meta in list(state.get("customers", {}).items()):
        if "device_limit" not in meta:
            meta["device_limit"] = 0
            changed = True
        if "limit_action" not in meta:
            meta["limit_action"] = "alert"
            changed = True
        if "online_ips" not in meta:
            meta["online_ips"] = {}
            changed = True
        if "default_days" not in meta:
            meta["default_days"] = 30
            changed = True

    try:
        inbound = get_vless_inbound(cfg)
        for user in inbound.get("users", []):
            uid = user.get("uuid")
            if uid and uid not in state["devices"]:
                customer_id = user.get("name") or "default"
                if customer_id not in state["customers"]:
                    customer_id = "default"
                state["devices"][uid] = {
                    "name": "Device-" + uid[:8],
                    "customer_id": customer_id,
                    "enabled": True,
                    "created_at": now_ts(),
                    "expires_at": 0,
                    "used_bytes": 0
                }
                changed = True
            elif uid:
                meta = state["devices"][uid]
                if "customer_id" not in meta:
                    meta["customer_id"] = user.get("name") or "default"
                    changed = True
                if "quota_gb" in meta:
                    meta.pop("quota_gb", None)
                    changed = True
                if "upload_bytes" not in meta:
                    meta["upload_bytes"] = 0
                    changed = True
                if "download_bytes" not in meta:
                    meta["download_bytes"] = 0
                    changed = True
                if "used_bytes" not in meta:
                    meta["used_bytes"] = 0
                    changed = True
                if "expires_at" not in meta:
                    meta["expires_at"] = 0
                    changed = True
    except Exception:
        pass

    for outbound in cfg.get("outbounds", []):
        if outbound.get("type") == "socks" and outbound.get("tag", "").startswith(SOCKS_OUT_PREFIX):
            tag = outbound["tag"]
            if tag not in state["homes"]:
                state["homes"][tag] = {
                    "name": tag.replace(SOCKS_OUT_PREFIX, "") or tag,
                    "server": outbound.get("server", ""),
                    "server_port": outbound.get("server_port", 0),
                    "username": outbound.get("username", ""),
                    "password": outbound.get("password", ""),
                    "created_at": now_ts()
                }
                changed = True
    final = cfg.get("route", {}).get("final")
    if final and final in state["homes"] and state.get("active_home") != final:
        state["active_home"] = final
        changed = True
    if changed:
        save_json(STATE_PATH, state)
    return state


def rebuild_customer_routes(cfg, state):
    customer_ids = set(state.get("customers", {}).keys())
    cfg["outbounds"] = [
        o for o in cfg.get("outbounds", [])
        if not o.get("tag", "").startswith(CUSTOMER_OUT_PREFIX)
    ]
    generated_outbounds = []
    old_rules = cfg.setdefault("route", {}).get("rules", [])
    kept = []
    for rule in old_rules:
        users = rule.get("auth_user")
        if isinstance(users, str):
            users = [users]
        inbound = rule.get("inbound")
        inbound_matches = inbound == VLESS_TAG or inbound == [VLESS_TAG] or (isinstance(inbound, list) and VLESS_TAG in inbound)
        if inbound_matches:
            if str(rule.get("outbound", "")).startswith(CUSTOMER_OUT_PREFIX):
                continue
            if users and all(u == "default" or str(u).startswith("customer-") for u in users):
                continue
        kept.append(rule)

    generated = []
    for customer_id, meta in sorted(state.get("customers", {}).items()):
        tag = meta.get("home_tag")
        home = get_outbound(cfg, tag) if tag else None
        if home:
            customer_out_tag = CUSTOMER_OUT_PREFIX + customer_id
            cloned = dict(home)
            cloned["tag"] = customer_out_tag
            generated_outbounds.append(cloned)
            device_users = [
                uid for uid, dev in sorted(state.get("devices", {}).items())
                if dev.get("customer_id", "default") == customer_id and dev.get("enabled", True)
            ]
            if device_users:
                generated.append({
                    "inbound": [VLESS_TAG],
                    "auth_user": device_users,
                    "action": "route",
                    "outbound": customer_out_tag
                })
    cfg.setdefault("outbounds", []).extend(generated_outbounds)
    cfg.setdefault("route", {})["rules"] = generated + kept
    if state.get("active_home"):
        cfg.setdefault("route", {})["final"] = state["active_home"]
    return cfg


def public_ip():
    try:
        return run(["curl", "-4fsS", "--max-time", "6", "https://api.ipify.org"], timeout=10).strip()
    except Exception:
        return ""


def clash_secret():
    return hashlib.sha256(SECRET.encode("utf-8")).hexdigest()[:32]


def status_payload():
    state = ensure_state_from_config()
    cfg = read_config()
    listening = run(["ss", "-lntp"], timeout=10)
    return {
        "service": singbox_status(),
        "customers": list_customers(state, cfg),
        "devices": list_devices(state, cfg),
        "homes": list_homes(state, cfg),
        "active_home": cfg.get("route", {}).get("final") or state.get("active_home"),
        "listen": listening,
        "vless_link": make_vless_link_for_first_user(state, cfg),
        "server_ip": public_ip()
    }


def connections_payload():
    data = clash_connections_raw()
    rows = normalize_connections(data)
    state = ensure_state_from_config()
    device_live = {}
    for row in rows:
        uid = row.get("auth_user", "")
        if uid in state.get("devices", {}):
            live = device_live.setdefault(uid, {"upload": 0, "download": 0})
            live["upload"] += row["upload"]
            live["download"] += row["download"]
    device_totals = []
    for uid, meta in state.get("devices", {}).items():
        live = device_live.get(uid, {"upload": 0, "download": 0})
        up = int(meta.get("upload_bytes", 0) or 0)
        down = int(meta.get("download_bytes", 0) or 0)
        device_totals.append({
            "uuid": uid,
            "name": meta.get("name", uid[:8]),
            "customer_id": meta.get("customer_id", "default"),
            "upload_bytes": up,
            "download_bytes": down,
            "used_bytes": up + down,
            "live_upload": live["upload"],
            "live_download": live["download"],
            "live_total": live["upload"] + live["download"]
        })
    return {
        "ok": True,
        "download_total": int(data.get("downloadTotal", 0) or 0),
        "upload_total": int(data.get("uploadTotal", 0) or 0),
        "device_totals": device_totals,
        "connections": rows
    }


def clash_connections_raw():
    req = urllib.request.Request("http://%s/connections" % CLASH_API_ADDR)
    req.add_header("Authorization", "Bearer " + clash_secret())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def extract_auth_user(rule):
    m = re.search(r"auth_user=(\[[^\]]+\]|[^ ]+)", rule or "")
    if not m:
        return ""
    value = m.group(1)
    uuids = UUID_RE.findall(value)
    if uuids:
        return uuids[0].lower()
    return value.strip("[]")


def normalize_connections(data):
    rows = []
    for c in data.get("connections", []):
        meta = c.get("metadata", {}) or {}
        chains = c.get("chains", []) or []
        rule = c.get("rule", "")
        rows.append({
            "id": c.get("id", ""),
            "start": c.get("start", ""),
            "network": meta.get("network", ""),
            "type": meta.get("type", ""),
            "auth_user": extract_auth_user(rule),
            "source_ip": meta.get("sourceIP", ""),
            "source_port": meta.get("sourcePort", ""),
            "host": meta.get("host", "") or meta.get("destinationIP", ""),
            "destination_ip": meta.get("destinationIP", ""),
            "destination_port": meta.get("destinationPort", ""),
            "upload": int(c.get("upload", 0) or 0),
            "download": int(c.get("download", 0) or 0),
            "chains": chains,
            "rule": rule,
            "rule_payload": c.get("rulePayload", "")
        })
    return rows


def list_devices(state, cfg):
    inbound = get_vless_inbound(cfg)
    enabled = set([u.get("uuid") for u in inbound.get("users", []) if u.get("uuid")])
    rows = []
    for uid, meta in sorted(state.get("devices", {}).items(), key=lambda x: x[1].get("created_at", 0)):
        customer_id = meta.get("customer_id", "default")
        customer = state.get("customers", {}).get(customer_id, {})
        rows.append({
            "uuid": uid,
            "name": meta.get("name", uid[:8]),
            "customer_id": customer_id,
            "customer_name": customer.get("name", customer_id),
            "enabled": uid in enabled and meta.get("enabled", True),
            "expired": bool(meta.get("expires_at") and int(meta.get("expires_at")) <= now_ts()),
            "expires_at": int(meta.get("expires_at", 0) or 0),
            "expires_text": format_expiry(meta.get("expires_at", 0)),
            "used_bytes": int(meta.get("used_bytes", 0) or 0),
            "upload_bytes": int(meta.get("upload_bytes", 0) or 0),
            "download_bytes": int(meta.get("download_bytes", 0) or 0),
            "link": make_vless_link(uid, cfg)
        })
    return rows


def list_customers(state, cfg):
    rows = []
    for customer_id, meta in sorted(state.get("customers", {}).items(), key=lambda x: x[1].get("created_at", 0)):
        home = get_outbound(cfg, meta.get("home_tag", ""))
        online_ips = prune_online_ips(meta.get("online_ips", {}))
        limit = int(meta.get("device_limit", 0) or 0)
        rows.append({
            "id": customer_id,
            "name": meta.get("name", customer_id),
            "home_tag": meta.get("home_tag", ""),
            "home_name": state.get("homes", {}).get(meta.get("home_tag", ""), {}).get("name", meta.get("home_tag", "")),
            "home_server": home.get("server", "") if home else "",
            "quota_gb": meta.get("quota_gb", 0),
            "default_days": int(meta.get("default_days", 30) or 0),
            "device_limit": limit,
            "limit_action": meta.get("limit_action", "alert"),
            "online_ip_count": len(online_ips),
            "online_ips": sorted(online_ips.keys()),
            "over_limit": bool(limit and len(online_ips) > limit),
            "device_count": len([1 for d in state.get("devices", {}).values() if d.get("customer_id", "default") == customer_id])
        })
    return rows


def prune_online_ips(ips):
    cutoff = now_ts() - ONLINE_WINDOW_SECONDS
    return {ip: ts for ip, ts in (ips or {}).items() if int(ts or 0) >= cutoff}


def format_expiry(ts):
    ts = int(ts or 0)
    if not ts:
        return "永久"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def list_homes(state, cfg):
    rows = []
    final = cfg.get("route", {}).get("final")
    for tag, meta in sorted(state.get("homes", {}).items(), key=lambda x: x[1].get("created_at", 0)):
        outbound = get_outbound(cfg, tag)
        rows.append({
            "tag": tag,
            "name": meta.get("name", tag),
            "server": (outbound or meta).get("server", ""),
            "server_port": (outbound or meta).get("server_port", 0),
            "username": (outbound or meta).get("username", ""),
            "active": tag == final
        })
    return rows


def reality_params(cfg):
    inbound = get_vless_inbound(cfg)
    tls = inbound.get("tls", {})
    reality = tls.get("reality", {})
    server_name = tls.get("server_name", "")
    short_id = ""
    sid = reality.get("short_id")
    if isinstance(sid, list) and sid:
        short_id = sid[0]
    elif isinstance(sid, str):
        short_id = sid
    pubkey = REALITY_PUBLIC_KEY or load_json(os.path.join(APP_DIR, "reality-public-key.json"), {}).get("public_key", "")
    if not pubkey:
        raise PanelError("REALITY_PUBLIC_KEY is required to generate VLESS links")
    port = inbound.get("listen_port", 443)
    return server_name, short_id, pubkey, port


def make_vless_link(uid, cfg):
    state = init_state()
    dev = state.get("devices", {}).get(uid, {})
    customer = state.get("customers", {}).get(dev.get("customer_id", "default"), {})
    node_name = ("%s-%s" % (customer.get("name", ""), dev.get("name", uid[:8]))).strip("-") or ("VLESS-" + uid[:6])
    sni, sid, pubkey, port = reality_params(cfg)
    q = {
        "encryption": "none",
        "flow": DEFAULT_VLESS_FLOW,
        "security": "reality",
        "sni": sni,
        "fp": "chrome",
        "pbk": pubkey,
        "sid": sid,
        "type": "tcp",
        "headerType": "none"
    }
    return "vless://%s@%s:%s?%s#%s" % (
        uid,
        PUBLIC_NODE_HOST,
        port,
        urllib.parse.urlencode(q),
        urllib.parse.quote(node_name)
    )


def make_vless_link_for_first_user(state, cfg):
    devices = list_devices(state, cfg)
    return devices[0]["link"] if devices else ""


@state_transaction
def add_device(data):
    name = (data.get("name") or "New Device").strip()[:64]
    customer_id = data.get("customer_id") or "default"
    uid = str(uuid.uuid4())
    state = ensure_state_from_config()
    if customer_id not in state.get("customers", {}):
        raise PanelError("customer not found")
    days = data.get("days")
    if days in (None, ""):
        days = state["customers"][customer_id].get("default_days", 30)
    days = int(days or 0)
    expires_at = now_ts() + days * 86400 if days > 0 else 0
    cfg = read_config()
    inbound = get_vless_inbound(cfg)
    inbound.setdefault("users", []).append({"uuid": uid, "name": uid, "flow": DEFAULT_VLESS_FLOW})
    state["devices"][uid] = {
        "name": name,
        "customer_id": customer_id,
        "enabled": True,
        "created_at": now_ts(),
        "expires_at": expires_at,
        "used_bytes": 0,
        "upload_bytes": 0,
        "download_bytes": 0
    }
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True, "uuid": uid, "link": make_vless_link(uid, cfg)}


@state_transaction
def set_device(data):
    uid = data.get("uuid", "")
    if not uid:
        raise PanelError("missing uuid")
    state = ensure_state_from_config()
    if uid not in state["devices"]:
        raise PanelError("device not found")
    config_changed = False
    if "name" in data:
        state["devices"][uid]["name"] = str(data.get("name") or "").strip()[:64] or state["devices"][uid]["name"]
    if "customer_id" in data:
        cid = data.get("customer_id") or "default"
        if cid not in state.get("customers", {}):
            raise PanelError("customer not found")
        state["devices"][uid]["customer_id"] = cid
        config_changed = True
    if "enabled" in data:
        state["devices"][uid]["enabled"] = bool(data.get("enabled"))
        config_changed = True
    if "days" in data:
        days = int(data.get("days") or 0)
        state["devices"][uid]["expires_at"] = now_ts() + days * 86400 if days > 0 else 0
        config_changed = True
    if "expires_at" in data:
        state["devices"][uid]["expires_at"] = int(data.get("expires_at") or 0)
        config_changed = True
    if config_changed:
        cfg = read_config()
        inbound = get_vless_inbound(cfg)
        users = [u for u in inbound.get("users", []) if u.get("uuid") != uid]
        expired = bool(state["devices"][uid].get("expires_at") and int(state["devices"][uid].get("expires_at")) <= now_ts())
        if state["devices"][uid].get("enabled", True) and not expired:
            users.append({
                "uuid": uid,
                "name": uid,
                "flow": DEFAULT_VLESS_FLOW
            })
        inbound["users"] = users
        cfg = rebuild_customer_routes(cfg, state)
        atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True}


@state_transaction
def delete_device(data):
    uid = data.get("uuid", "")
    state = ensure_state_from_config()
    if uid in state.get("devices", {}):
        state["devices"].pop(uid)
    cfg = read_config()
    inbound = get_vless_inbound(cfg)
    inbound["users"] = [u for u in inbound.get("users", []) if u.get("uuid") != uid]
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True}


@state_transaction
def add_customer(data):
    name = (data.get("name") or "客户").strip()[:48]
    home_tag = data.get("home_tag") or DEFAULT_HOME_TAG
    quota = float(data.get("quota_gb") or 0)
    state = ensure_state_from_config()
    cfg = read_config()
    if not get_outbound(cfg, home_tag):
        raise PanelError("home proxy not found")
    customer_id = "customer-" + slug(name)
    if customer_id in state["customers"]:
        customer_id = customer_id + "-" + secrets.token_hex(2)
    state["customers"][customer_id] = {
        "name": name,
        "home_tag": home_tag,
        "device_limit": int(data.get("device_limit") or 0),
        "limit_action": data.get("limit_action") or "alert",
        "default_days": int(data.get("default_days") or 30),
        "quota_gb": quota,
        "online_ips": {},
        "created_at": now_ts()
    }
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True, "id": customer_id}


@state_transaction
def set_customer(data):
    customer_id = data.get("id", "")
    state = ensure_state_from_config()
    if customer_id not in state.get("customers", {}):
        raise PanelError("customer not found")
    cfg = read_config()
    if "name" in data:
        state["customers"][customer_id]["name"] = str(data.get("name") or "").strip()[:48] or state["customers"][customer_id]["name"]
    if "quota_gb" in data:
        state["customers"][customer_id]["quota_gb"] = float(data.get("quota_gb") or 0)
    if "device_limit" in data:
        state["customers"][customer_id]["device_limit"] = int(data.get("device_limit") or 0)
    if "default_days" in data:
        state["customers"][customer_id]["default_days"] = int(data.get("default_days") or 0)
    if "limit_action" in data:
        action = data.get("limit_action") or "alert"
        if action not in ("alert", "disable_customer"):
            raise PanelError("invalid limit action")
        state["customers"][customer_id]["limit_action"] = action
    if "home_tag" in data:
        tag = data.get("home_tag") or ""
        if not get_outbound(cfg, tag):
            raise PanelError("home proxy not found")
        state["customers"][customer_id]["home_tag"] = tag
    inbound = get_vless_inbound(cfg)
    for user in inbound.get("users", []):
        uid = user.get("uuid")
        if uid and state.get("devices", {}).get(uid, {}).get("customer_id", "default") == customer_id:
            user["name"] = uid
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True}


@state_transaction
def delete_customer(data):
    customer_id = data.get("id", "")
    if customer_id == "default":
        raise PanelError("default customer cannot be deleted")
    state = ensure_state_from_config()
    if any(d.get("customer_id", "default") == customer_id for d in state.get("devices", {}).values()):
        raise PanelError("customer still has devices")
    state.get("customers", {}).pop(customer_id, None)
    cfg = rebuild_customer_routes(read_config(), state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True}


@state_transaction
def add_home(data):
    name = (data.get("name") or "Home").strip()[:48]
    server = (data.get("server") or "").strip()
    port = int(data.get("server_port") or 0)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    if not server or not port:
        raise PanelError("server and port are required")
    tag = SOCKS_OUT_PREFIX + slug(name)
    state = ensure_state_from_config()
    if tag in state["homes"] or get_outbound(read_config(), tag):
        tag = tag + "-" + secrets.token_hex(2)
    cfg = read_config()
    cfg.setdefault("outbounds", []).append({
        "type": "socks",
        "tag": tag,
        "server": server,
        "server_port": port,
        "username": username,
        "password": password
    })
    state["homes"][tag] = {
        "name": name,
        "server": server,
        "server_port": port,
        "username": username,
        "password": password,
        "created_at": now_ts()
    }
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True, "tag": tag}


@state_transaction
def set_active_home(data):
    tag = data.get("tag", "")
    state = ensure_state_from_config()
    if tag not in state.get("homes", {}):
        raise PanelError("home proxy not found")
    cfg = read_config()
    if not get_outbound(cfg, tag):
        raise PanelError("outbound missing in sing-box config")
    cfg.setdefault("route", {})["final"] = tag
    state["active_home"] = tag
    if "default" in state.get("customers", {}):
        state["customers"]["default"]["home_tag"] = tag
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True}


@state_transaction
def delete_home(data):
    tag = data.get("tag", "")
    state = ensure_state_from_config()
    cfg = read_config()
    if cfg.get("route", {}).get("final") == tag:
        raise PanelError("cannot delete active home proxy")
    if any(c.get("home_tag") == tag for c in state.get("customers", {}).values()):
        raise PanelError("cannot delete home proxy assigned to a customer")
    cfg["outbounds"] = [o for o in cfg.get("outbounds", []) if o.get("tag") != tag]
    state.get("homes", {}).pop(tag, None)
    cfg = rebuild_customer_routes(cfg, state)
    atomic_write_config(cfg)
    save_json(STATE_PATH, state)
    return {"ok": True}


def test_home(data):
    tag = data.get("tag", "")
    cfg = read_config()
    outbound = get_outbound(cfg, tag)
    if not outbound:
        raise PanelError("home proxy not found")
    proxy = "socks5h://"
    if outbound.get("username"):
        proxy += urllib.parse.quote(outbound.get("username", "")) + ":" + urllib.parse.quote(outbound.get("password", "")) + "@"
    proxy += "%s:%s" % (outbound.get("server"), outbound.get("server_port"))
    out = run(["curl", "-4fsS", "--max-time", "12", "--proxy", proxy, "https://api.ipify.org"], timeout=15).strip()
    return {"ok": True, "ip": out}


def logs():
    return read_singbox_logs(120)


def restart_service():
    restart_singbox()
    return {"ok": True}


FLOW_RE = re.compile(r"\[(\d+)\s+[^\]]+\].*inbound/vless\[" + re.escape(VLESS_TAG) + r"\]: inbound connection from (.+):\d+")
OUT_RE = re.compile(r"\[(\d+)\s+[^\]]+\].*outbound/[^[]+\[(" + re.escape(CUSTOMER_OUT_PREFIX) + r"[^]]+)\]: outbound connection")


@state_transaction
def disable_customer(customer_id):
    state = ensure_state_from_config()
    cfg = read_config()
    inbound = get_vless_inbound(cfg)
    changed = False
    for uid, meta in state.get("devices", {}).items():
        if meta.get("customer_id", "default") == customer_id and meta.get("enabled", True):
            meta["enabled"] = False
            changed = True
    if changed:
        inbound["users"] = [
            u for u in inbound.get("users", [])
            if state.get("devices", {}).get(u.get("uuid", ""), {}).get("customer_id", "default") != customer_id
        ]
        cfg = rebuild_customer_routes(cfg, state)
        atomic_write_config(cfg)
        save_json(STATE_PATH, state)


@state_transaction
def record_customer_ip(customer_id, source_ip):
    state = ensure_state_from_config()
    customer = state.get("customers", {}).get(customer_id)
    if not customer:
        return
    customer["online_ips"] = prune_online_ips(customer.get("online_ips", {}))
    customer["online_ips"][source_ip.strip("[]")] = now_ts()
    limit = int(customer.get("device_limit", 0) or 0)
    over = bool(limit and len(customer["online_ips"]) > limit)
    customer["last_limit_event"] = now_ts() if over else customer.get("last_limit_event", 0)
    save_json(STATE_PATH, state)
    if over and customer.get("limit_action") == "disable_customer":
        disable_customer(customer_id)


def monitor_singbox_logs():
    pending = {}
    while True:
        try:
            if SINGBOX_MANAGE_MODE == "process":
                ensure_process_singbox_started()
                os.makedirs(os.path.dirname(SINGBOX_LOG_PATH), exist_ok=True)
                open(SINGBOX_LOG_PATH, "ab").close()
                with open(SINGBOX_LOG_PATH, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    while True:
                        raw = f.readline()
                        if not raw:
                            time.sleep(1)
                            continue
                        pending = consume_singbox_log_line(raw.decode("utf-8", "replace"), pending)
            else:
                p = subprocess.Popen(
                    ["journalctl", "-u", SINGBOX_SERVICE, "-f", "-n", "0", "--no-pager"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                for raw in iter(p.stdout.readline, b""):
                    pending = consume_singbox_log_line(raw.decode("utf-8", "replace"), pending)
        except Exception:
            time.sleep(3)


def consume_singbox_log_line(line, pending):
    m = FLOW_RE.search(line)
    if m:
        pending[m.group(1)] = (m.group(2), now_ts())
        return pending
    m = OUT_RE.search(line)
    if m:
        flow_id = m.group(1)
        out_tag = m.group(2)
        source = pending.pop(flow_id, (None, 0))[0]
        if source and out_tag.startswith(CUSTOMER_OUT_PREFIX):
            record_customer_ip(out_tag[len(CUSTOMER_OUT_PREFIX):], source)
    cutoff = now_ts() - 60
    return {k: v for k, v in pending.items() if v[1] >= cutoff}


def poll_connection_traffic():
    last = {}
    while True:
        try:
            data = clash_connections_raw()
            rows = normalize_connections(data)
            with STATE_LOCK:
                state = ensure_state_from_config()
                seen = set()
                changed = False
                for row in rows:
                    cid = row.get("id")
                    uid = row.get("auth_user", "")
                    if not cid:
                        continue
                    seen.add(cid)
                    cur_up = int(row.get("upload", 0) or 0)
                    cur_down = int(row.get("download", 0) or 0)
                    prev_up, prev_down = last.get(cid, (cur_up, cur_down))
                    delta_up = max(0, cur_up - prev_up)
                    delta_down = max(0, cur_down - prev_down)
                    last[cid] = (cur_up, cur_down)
                    if uid in state.get("devices", {}) and (delta_up or delta_down):
                        dev = state["devices"][uid]
                        dev["upload_bytes"] = int(dev.get("upload_bytes", 0) or 0) + delta_up
                        dev["download_bytes"] = int(dev.get("download_bytes", 0) or 0) + delta_down
                        dev["used_bytes"] = int(dev.get("upload_bytes", 0) or 0) + int(dev.get("download_bytes", 0) or 0)
                        changed = True
                last = {cid: val for cid, val in last.items() if cid in seen}
                if changed:
                    save_json(STATE_PATH, state)
        except Exception:
            pass
        time.sleep(TRAFFIC_POLL_SECONDS)


def expire_devices_loop():
    while True:
        try:
            with STATE_LOCK:
                state = ensure_state_from_config()
                expired_any = False
                ts = now_ts()
                for dev in state.get("devices", {}).values():
                    if dev.get("enabled", True) and dev.get("expires_at") and int(dev.get("expires_at")) <= ts:
                        dev["enabled"] = False
                        expired_any = True
                if expired_any:
                    cfg = read_config()
                    inbound = get_vless_inbound(cfg)
                    active = []
                    for u in inbound.get("users", []):
                        uid = u.get("uuid")
                        dev = state.get("devices", {}).get(uid)
                        if dev and dev.get("enabled", True) and not (dev.get("expires_at") and int(dev.get("expires_at")) <= ts):
                            u["name"] = uid
                            if "flow" not in u:
                                u["flow"] = DEFAULT_VLESS_FLOW
                            active.append(u)
                    inbound["users"] = active
                    cfg = rebuild_customer_routes(cfg, state)
                    atomic_write_config(cfg)
                    save_json(STATE_PATH, state)
        except Exception:
            pass
        time.sleep(EXPIRE_CHECK_SECONDS)


@state_transaction
def migrate_config_for_customers():
    state = ensure_state_from_config()
    cfg = read_config()
    original = json.dumps(cfg, sort_keys=True)
    cfg["experimental"] = cfg.get("experimental", {})
    cfg["experimental"]["clash_api"] = {
        "external_controller": CLASH_API_ADDR,
        "secret": clash_secret()
    }
    inbound = get_vless_inbound(cfg)
    active_users = []
    for user in inbound.get("users", []):
        uid = user.get("uuid")
        if uid:
            user["name"] = uid
            if "flow" not in user:
                user["flow"] = DEFAULT_VLESS_FLOW
            dev = state.get("devices", {}).get(uid, {})
            expired = bool(dev.get("expires_at") and int(dev.get("expires_at")) <= now_ts())
            if dev.get("enabled", True) and not expired:
                active_users.append(user)
    inbound["users"] = active_users
    cfg = rebuild_customer_routes(cfg, state)
    updated = json.dumps(cfg, sort_keys=True)
    if updated != original:
        atomic_write_config(cfg)


def sign(value):
    mac = hmac.new(SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()
    return value + "." + mac


def verify_signed(value):
    if "." not in value:
        return False
    msg, mac = value.rsplit(".", 1)
    good = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, good)


def page():
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sing-box Panel</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--line:#d9dee7;--text:#161a22;--muted:#6b7280;--accent:#0f766e;--danger:#b42318;--dark:#111827}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{height:58px;background:#111827;color:#fff;display:flex;align-items:center;justify-content:space-between;padding:0 22px;position:sticky;top:0;z-index:2}
header b{font-size:17px}.wrap{max-width:1180px;margin:22px auto;padding:0 18px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}.full{grid-column:1/-1}
section{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px}h2{font-size:16px;margin:0 0 12px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
input,button,select{height:36px;border:1px solid var(--line);border-radius:6px;padding:0 10px;background:#fff;color:var(--text)}input{min-width:160px}button{cursor:pointer}button.primary{background:var(--dark);color:#fff;border-color:var(--dark)}button.good{background:var(--accent);color:#fff;border-color:var(--accent)}button.danger{background:#fff;color:var(--danger);border-color:#f1b6b2}
table{width:100%;border-collapse:collapse}th,td{text-align:left;border-top:1px solid var(--line);padding:10px 8px;vertical-align:middle}th{color:var(--muted);font-weight:600}.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;word-break:break-all}.pill{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:3px 8px;color:var(--muted)}.ok{color:#087443}.bad{color:#b42318}.muted{color:var(--muted)}
pre{white-space:pre-wrap;background:#0b1020;color:#d7e0ff;border-radius:8px;padding:12px;max-height:360px;overflow:auto}.login{max-width:380px;margin:12vh auto;background:#fff;border:1px solid var(--line);border-radius:8px;padding:22px}.login input{width:100%;margin:10px 0}.hidden{display:none}
@media(max-width:850px){.grid{grid-template-columns:1fr}header{padding:0 14px}.wrap{padding:0 12px}input{width:100%}.row button{flex:1}}
</style>
</head>
<body>
<div id="login" class="login hidden"><h2>登录面板</h2><input id="pw" type="password" placeholder="管理密码"><button class="primary" onclick="login()">登录</button><p id="loginErr" class="bad"></p></div>
<div id="app" class="hidden">
<header><b>Sing-box 管理面板</b><span id="svc" class="pill">...</span></header>
<main class="wrap grid">
<section><h2>运行状态</h2><div class="row"><button onclick="refresh()">刷新</button><button onclick="restart()">重启 sing-box</button><button onclick="loadLogs()">查看日志</button><button onclick="loadConnections(true)">刷新连接</button></div><p class="muted">服务器出口 IP：<span id="serverIp">...</span></p><p class="muted">默认家宽出口：<span id="activeHome">...</span></p></section>
<section><h2>添加客户</h2><div class="row"><input id="customerName" placeholder="客户名"><select id="customerHome"></select><input id="customerLimit" type="number" min="0" step="1" placeholder="设备数，0 不限制"><input id="customerDays" type="number" min="0" step="1" value="30" title="默认有效期天数，0 永久"><select id="customerAction"><option value="alert">超限告警</option><option value="disable_customer">超限停用</option></select><input id="customerQuota" type="number" min="0" step="1" placeholder="月流量 GB，0 不限制"><button class="primary" onclick="addCustomer()">添加</button></div></section>
<section class="full"><h2>客户管理</h2><div id="customers"></div></section>
<section><h2>添加家宽 SOCKS5</h2><div class="row"><input id="homeName" placeholder="名称"><input id="homeServer" placeholder="地址"><input id="homePort" type="number" placeholder="端口"><input id="homeUser" placeholder="用户名"><input id="homePass" placeholder="密码"><button class="primary" onclick="addHome()">添加</button></div></section>
<section><h2>家宽出口</h2><table><thead><tr><th>名称</th><th>地址</th><th>用户</th><th>操作</th></tr></thead><tbody id="homes"></tbody></table></section>
<section class="full"><h2>当前连接流量</h2><p class="muted">上传：<span id="connUp">0 B</span> ｜ 下载：<span id="connDown">0 B</span> ｜ <span id="connStatus">等待刷新</span></p><table><thead><tr><th>UUID</th><th>来源</th><th>目标</th><th>上传</th><th>下载</th><th>合计</th><th>链路</th></tr></thead><tbody id="connections"><tr><td colspan="7" class="muted">点击“刷新连接”加载</td></tr></tbody></table></section>
<section class="full"><h2>日志</h2><pre id="logs">点击“查看日志”加载</pre></section>
</main></div>
<script>
async function api(path, data){let opt={headers:{'Content-Type':'application/json'}};if(data){opt.method='POST';opt.body=JSON.stringify(data)}let r=await fetch(path,opt);if(r.status===401){showLogin();throw new Error('未登录')}let text=await r.text();if(!text.trim())throw new Error('接口没有返回内容，请刷新后重试');let j;try{j=JSON.parse(text)}catch(e){throw new Error('接口返回异常：'+text.slice(0,120))}if(!r.ok)throw new Error(j.error||('HTTP '+r.status));if(!j.ok&&j.error)throw new Error(j.error);return j}
function showLogin(){document.getElementById('login').classList.remove('hidden');document.getElementById('app').classList.add('hidden')}
function showApp(){document.getElementById('login').classList.add('hidden');document.getElementById('app').classList.remove('hidden')}
async function login(){try{await api('/api/login',{password:document.getElementById('pw').value});showApp();refresh()}catch(e){document.getElementById('loginErr').textContent=e.message}}
function copy(t){if(navigator.clipboard&&navigator.clipboard.writeText){return navigator.clipboard.writeText(t).catch(()=>prompt('复制链接',t))}prompt('复制链接',t)}
let lastHomes=[], lastCustomers=[];
let lastDevices=[];
async function refresh(){try{let s=await api('/api/status');showApp();lastHomes=s.homes;lastCustomers=s.customers;lastDevices=s.devices;document.getElementById('svc').textContent='sing-box '+s.service;document.getElementById('svc').className='pill '+(s.service==='active'?'ok':'bad');document.getElementById('serverIp').textContent=s.server_ip||'未知';document.getElementById('activeHome').textContent=s.active_home;renderSelects();renderCustomers(s.customers,s.devices);renderHomes(s.homes)}catch(e){alert(e.message)}}
function renderSelects(){let opts=lastHomes.map(h=>`<option value="${esc(h.tag)}">${esc(h.name)} - ${esc(h.server)}:${h.server_port}</option>`).join('');document.getElementById('customerHome').innerHTML=opts}
function renderCustomers(rows,devices){let box=document.getElementById('customers');box.innerHTML='';rows.forEach(c=>{let homeOptions=lastHomes.map(h=>`<option value="${esc(h.tag)}" ${h.tag===c.home_tag?'selected':''}>${esc(h.name)} - ${esc(h.server)}:${h.server_port}</option>`).join('');let actionOptions=`<option value="alert" ${c.limit_action==='alert'?'selected':''}>超限告警</option><option value="disable_customer" ${c.limit_action==='disable_customer'?'selected':''}>超限停用</option>`;let customerDevices=devices.filter(d=>d.customer_id===c.id);let ipText=c.online_ips&&c.online_ips.length?c.online_ips.join(', '):'暂无';let deviceRows=customerDevices.map(d=>`<tr><td><input value="${esc(d.name)}" onchange="renameDevice('${d.uuid}',this.value)" onkeydown="if(event.key==='Enter')this.blur()" style="width:180px"></td><td class="mono">${d.uuid}</td><td>${d.expired?'<span class="bad">已过期</span>':(d.enabled?'<span class="ok">启用</span>':'<span class="bad">停用</span>')}</td><td>${esc(d.expires_text)}</td><td>${fmtBytes(d.upload_bytes)} / ${fmtBytes(d.download_bytes)} / ${fmtBytes(d.used_bytes)}</td><td><button onclick="copy('${d.link}')">复制链接</button></td><td><button onclick="toggleDevice('${d.uuid}',${!d.enabled})">${d.enabled?'停用':'启用'}</button> <button onclick="renewDevice('${d.uuid}',30)">续 1 月</button> <button onclick="renewDevice('${d.uuid}',0)">永久</button> <button class="danger" onclick="delDevice('${d.uuid}')">删除</button></td></tr>`).join('')||'<tr><td colspan="7" class="muted">暂无设备</td></tr>';let div=document.createElement('div');div.style.borderTop='1px solid var(--line)';div.style.padding='14px 0';div.innerHTML=`<div class="row" style="justify-content:space-between"><div><b>${esc(c.name)}</b> ${c.over_limit?'<span class="pill bad">超限</span>':''}<div class="mono">${esc(c.id)}</div><div class="muted">近 10 分钟在线 IP：${c.online_ip_count}/${c.device_limit||'不限'} ｜ ${esc(ipText)}</div></div><div class="row"><select onchange="setCustomerHome('${c.id}',this.value)">${homeOptions}</select><input type="number" min="0" step="1" value="${c.device_limit||0}" onchange="setCustomerLimit('${c.id}',this.value)" title="设备数，0 不限制" style="width:120px"><input type="number" min="0" step="1" value="${c.default_days||30}" onchange="setCustomerDays('${c.id}',this.value)" title="默认有效期天数，0 永久" style="width:120px"><span class="muted">天</span><select onchange="setCustomerAction('${c.id}',this.value)">${actionOptions}</select><input type="number" min="0" step="1" value="${c.quota_gb||0}" onchange="setCustomerQuota('${c.id}',this.value)" title="月流量 GB，0 不限制" style="width:120px"><span class="muted">GB/月</span><button class="danger" onclick="delCustomer('${c.id}')">删除客户</button></div></div><div class="row" style="margin:12px 0"><input id="newdev-${c.id}" placeholder="给 ${esc(c.name)} 添加设备"><input id="newdays-${c.id}" type="number" min="0" step="1" value="${c.default_days||30}" title="有效期天数，0 永久" style="width:130px"><button class="primary" onclick="addDeviceToCustomer('${c.id}')">添加设备</button><span class="pill">${customerDevices.length} 个链接</span></div><table><thead><tr><th>设备</th><th>UUID</th><th>状态</th><th>到期时间</th><th>上传 / 下载 / 总计</th><th>链接</th><th>操作</th></tr></thead><tbody>${deviceRows}</tbody></table>`;box.appendChild(div)})}
function renderHomes(rows){let tb=document.getElementById('homes');tb.innerHTML='';rows.forEach(h=>{let tr=document.createElement('tr');tr.innerHTML=`<td>${esc(h.name)} ${h.active?'<span class="pill ok">默认</span>':''}</td><td class="mono">${esc(h.server)}:${h.server_port}</td><td>${esc(h.username||'')}</td><td><button onclick="useHome('${h.tag}')">设为默认</button> <button onclick="testHome('${h.tag}')">测试</button> <button class="danger" onclick="delHome('${h.tag}')">删除</button></td>`;tb.appendChild(tr)})}
async function addCustomer(){try{await api('/api/customer/add',{name:customerName.value,home_tag:customerHome.value,device_limit:customerLimit.value,default_days:customerDays.value,limit_action:customerAction.value,quota_gb:customerQuota.value});customerName.value='';customerLimit.value='';customerDays.value='30';customerQuota.value='';refresh()}catch(e){alert(e.message)}}
async function setCustomerHome(id,tag){try{await api('/api/customer/set',{id:id,home_tag:tag});refresh()}catch(e){alert(e.message)}}
async function setCustomerQuota(id,v){try{await api('/api/customer/set',{id:id,quota_gb:v});refresh()}catch(e){alert(e.message)}}
async function setCustomerLimit(id,v){try{await api('/api/customer/set',{id:id,device_limit:v});refresh()}catch(e){alert(e.message)}}
async function setCustomerDays(id,v){try{await api('/api/customer/set',{id:id,default_days:v});refresh()}catch(e){alert(e.message)}}
async function setCustomerAction(id,v){try{await api('/api/customer/set',{id:id,limit_action:v});refresh()}catch(e){alert(e.message)}}
async function delCustomer(id){if(!confirm('删除这个客户？客户下还有设备时不能删除。'))return;try{await api('/api/customer/delete',{id:id});refresh()}catch(e){alert(e.message)}}
async function addDeviceToCustomer(cid){try{let el=document.getElementById('newdev-'+cid);let days=document.getElementById('newdays-'+cid);let j=await api('/api/device/add',{name:el.value,customer_id:cid,days:days.value});copy(j.link);el.value='';refresh()}catch(e){alert(e.message)}}
async function renewDevice(uuid,days){try{await api('/api/device/set',{uuid:uuid,days:days});refresh()}catch(e){alert(e.message)}}
async function renameDevice(uuid,name){try{await api('/api/device/set',{uuid:uuid,name:name});refresh()}catch(e){alert(e.message)}}
async function toggleDevice(uuid,en){try{await api('/api/device/set',{uuid:uuid,enabled:en});refresh()}catch(e){alert(e.message)}}
async function delDevice(uuid){if(!confirm('删除这个设备？'))return;try{await api('/api/device/delete',{uuid:uuid});refresh()}catch(e){alert(e.message)}}
async function addHome(){try{await api('/api/home/add',{name:homeName.value,server:homeServer.value,server_port:homePort.value,username:homeUser.value,password:homePass.value});homeName.value=homeServer.value=homePort.value=homeUser.value=homePass.value='';refresh()}catch(e){alert(e.message)}}
async function useHome(tag){try{await api('/api/home/use',{tag:tag});refresh()}catch(e){alert(e.message)}}
async function testHome(tag){try{let j=await api('/api/home/test',{tag:tag});alert('出口 IP：'+j.ip)}catch(e){alert(e.message)}}
async function delHome(tag){if(!confirm('删除这个家宽出口？当前出口不能删除。'))return;try{await api('/api/home/delete',{tag:tag});refresh()}catch(e){alert(e.message)}}
async function loadLogs(){try{let j=await api('/api/logs');document.getElementById('logs').textContent=j.logs}catch(e){alert(e.message)}}
let connFailCount=0, connPolling=false;
function setConnStatus(text,bad){let el=document.getElementById('connStatus');el.textContent=text;el.className=bad?'bad':'muted'}
async function loadConnections(showError){if(connPolling)return;connPolling=true;try{let j=await api('/api/connections');connFailCount=0;setConnStatus('已更新 '+new Date().toLocaleTimeString(),false);document.getElementById('connUp').textContent=fmtBytes(j.upload_total);document.getElementById('connDown').textContent=fmtBytes(j.download_total);if(j.device_totals){let m={};j.device_totals.forEach(d=>m[d.uuid]=d);lastDevices=lastDevices.map(d=>Object.assign({},d,m[d.uuid]||{}));renderCustomers(lastCustomers,lastDevices)}let tb=document.getElementById('connections');tb.innerHTML='';let rows=j.connections||[];if(!rows.length){tb.innerHTML='<tr><td colspan="7" class="muted">当前没有活跃连接</td></tr>';return}rows.sort((a,b)=>(b.upload+b.download)-(a.upload+a.download));rows.forEach(c=>{let target=(c.host||c.destination_ip||'')+':'+(c.destination_port||'');let source=(c.source_ip||'')+':'+(c.source_port||'');let total=(c.upload||0)+(c.download||0);let tr=document.createElement('tr');tr.innerHTML=`<td class="mono">${esc(c.auth_user||'')}</td><td class="mono">${esc(source)}</td><td class="mono">${esc(target)}</td><td>${fmtBytes(c.upload)}</td><td>${fmtBytes(c.download)}</td><td>${fmtBytes(total)}</td><td class="mono">${esc((c.chains||[]).join(' -> '))}</td>`;tb.appendChild(tr)})}catch(e){connFailCount++;setConnStatus('连接刷新失败，稍后自动重试：'+e.message,true);if(showError)alert(e.message)}finally{connPolling=false}}
async function restart(){try{await api('/api/restart',{});refresh()}catch(e){alert(e.message)}}
function fmtBytes(n){n=Number(n||0);let u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<u.length-1){n/=1024;i++}return (i?n.toFixed(2):n.toFixed(0))+' '+u[i]}
function esc(s){return String(s).replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
refresh().then(()=>loadConnections(false)).catch(showLogin)
setInterval(()=>{if(document.getElementById('app').classList.contains('hidden'))return;let skip=connFailCount>=3&&Date.now()%30000>6000;if(!skip)loadConnections(false)},10000)
</script>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_bytes(self, code, body, ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200, headers=None):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", headers)

    def authed(self):
        raw = self.headers.get("Cookie", "")
        cookie = http.cookies.SimpleCookie(raw)
        if "sbp" not in cookie:
            return False
        return verify_signed(cookie["sbp"].value)

    def body_json(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_bytes(200, page().encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/status":
            if not self.authed():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            try:
                data = status_payload()
                data["ok"] = True
                self.send_json(data)
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/logs":
            if not self.authed():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            try:
                self.send_json({"ok": True, "logs": logs()})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if parsed.path == "/api/connections":
            if not self.authed():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            try:
                self.send_json(connections_payload())
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        self.send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        routes = {
            "/api/customer/add": add_customer,
            "/api/customer/set": set_customer,
            "/api/customer/delete": delete_customer,
            "/api/device/add": add_device,
            "/api/device/set": set_device,
            "/api/device/delete": delete_device,
            "/api/home/add": add_home,
            "/api/home/use": set_active_home,
            "/api/home/delete": delete_home,
            "/api/home/test": test_home,
            "/api/restart": lambda data: restart_service(),
        }
        if self.path == "/api/login":
            try:
                data = self.body_json()
                if not hmac.compare_digest(data.get("password", ""), ADMIN_PASSWORD):
                    self.send_json({"ok": False, "error": "密码错误"}, 403)
                    return
                token = sign(str(now_ts()) + ":" + secrets.token_hex(12))
                cookie = "sbp=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800" % token
                self.send_json({"ok": True}, headers={"Set-Cookie": cookie})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)}, 500)
            return
        if self.path not in routes:
            self.send_json({"ok": False, "error": "not found"}, 404)
            return
        if not self.authed():
            self.send_json({"ok": False, "error": "unauthorized"}, 401)
            return
        try:
            data = self.body_json()
            self.send_json(routes[self.path](data))
        except Exception as e:
            traceback.print_exc()
            self.send_json({"ok": False, "error": str(e)}, 500)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    if not ADMIN_PASSWORD:
        raise SystemExit("PANEL_PASSWORD is required")
    if not SECRET:
        raise SystemExit("PANEL_SECRET is required")
    os.makedirs(APP_DIR, exist_ok=True)
    os.makedirs(BACKUP_DIR, exist_ok=True)
    migrate_config_for_customers()
    ensure_process_singbox_started()
    threading.Thread(target=monitor_singbox_logs, daemon=True).start()
    threading.Thread(target=poll_connection_traffic, daemon=True).start()
    threading.Thread(target=expire_devices_loop, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print("sing-box panel listening on %s:%s" % (HOST, PORT), flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
