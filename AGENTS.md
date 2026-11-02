# Sing-box Panel

The owner communicates in Chinese. The panel UI, `README.md`, and `docs/HANDOVER-2026-11-02.md` are written in Chinese. Agent instructions in this file and in `.cursor/rules/` are English. Reply to the owner in Chinese unless they switch language.

This repository is docs-and-config only for Cursor setup. Do not change `panel.py` or runtime behavior unless the user asks for a product change.

## What this project is

Sing-box Panel is a single-machine web panel for an existing `sing-box` VLESS Reality inbound. It manages customers, per-device VLESS links, expiry, cumulative traffic, and several residential SOCKS5 outbounds. There is no database, no third-party Python package, no payment or subscription system, and no real device fingerprinting. Device limits count recent source IPs.

Production (last known, not re-verified by SSH when the 2026-11-02 handover was written):

| Item | Value |
| --- | --- |
| Panel | `https://panel.5858188.xyz` (Nginx reverse proxy; panel domain may be Cloudflare-proxied) |
| Node | `node.5858188.xyz:443` (Cloudflare **DNS only**) |
| Host | Ubuntu 24.04, 1 vCPU / 1 GB RAM, SSH port 22 |
| Process model | systemd, not Docker |
| Panel bind | `singbox-panel` on `127.0.0.1:8080` |
| Clash API | `sing-box` on `127.0.0.1:9090` |
| State | `/opt/singbox-panel/state.json` |
| Core config | `/etc/sing-box/config.json` |

`README.md` still records an older verified stack (Ubuntu 18.04, Python 3.6.9, sing-box 1.14.0). Treat the handover as the latest ops snapshot and the README as the API and install reference. Do not connect to the production host unless the user explicitly approves that action.

## Architecture

One Python process serves both the API and the UI. `sing-box` is a separate process.

```text
browser -> Cloudflare -> nginx -> 127.0.0.1:8080 -> panel.py
client  -> node.5858188.xyz:443 (DNS only) -> sing-box VLESS Reality
sing-box -> per-customer route -> residential SOCKS5 outbound
panel.py -> http://127.0.0.1:9090/connections (Clash API, localhost only)
```

`panel.py` (about 1300 lines, standard library only):

- `ThreadingHTTPServer` + `Handler` (`http.server`) serves `GET /` and `/api/*`.
- `page()` returns one embedded HTML/CSS/JavaScript document (`lang="zh-CN"`). There is no frontend build.
- `state.json` holds customers, devices, home-proxy metadata (including SOCKS5 passwords), traffic counters, and recent source IPs.
- `/etc/sing-box/config.json` is the live sing-box config. The panel rewrites VLESS users, `home-socks5-*` and `customer-route-*` outbounds, `route.rules`, `route.final`, and `experimental.clash_api`.
- Writes go through `atomic_write_config`: copy the old file to `backups/config-YYYYmmdd-HHMMSS.json`, write a temp file, run `sing-box check`, replace the live file, then restart sing-box.
- `main()` starts three daemon threads:
  - `monitor_singbox_logs` — tail sing-box logs and record source IPs (journalctl, or the process-mode log file).
  - `poll_connection_traffic` — every `TRAFFIC_POLL_SECONDS` (default 5) read the Clash API and add byte deltas into `state.json`.
  - `expire_devices_loop` — every `EXPIRE_CHECK_SECONDS` (default 60) disable expired devices, which rewrites config and restarts sing-box.
- Startup calls `migrate_config_for_customers()`, which can rewrite config and restart sing-box immediately. Starting the panel against the production config is a production change.

Two run modes, selected by `SINGBOX_MANAGE_MODE`:

- `systemd` (default, production): `systemctl restart sing-box`, logs via `journalctl`.
- `process` (Docker Compose): the panel spawns `sing-box run -c <config>` itself. Compose uses host networking.

`scripts/singbox_watchdog.py` is a second stdlib process, installed with `deploy/systemd/singbox-watchdog.service`. Every 30 seconds it checks sing-box, local port 443, the panel, and nginx. After 3 failures, and only if `sing-box check` passes, it restarts sing-box, and restarts the panel or nginx when those checks fail. Cooldown is 120 seconds.

## Repository map

```text
panel.py
scripts/singbox_watchdog.py
deploy/systemd/singbox-watchdog.service
Dockerfile
docker-compose.yml
.env.example
README.md
docs/HANDOVER-2026-11-02.md
AGENTS.md
.cursor/rules/
```

The production unit `/etc/systemd/system/singbox-panel.service` and the Nginx site are not in the repo. `README.md` has templates. `.env` is gitignored; only `.env.example` is committed.

## Run and syntax-check locally

Syntax check from the repo root. This does not start a server and does not touch sing-box:

```bash
python3 -m py_compile panel.py
python3 -m py_compile scripts/singbox_watchdog.py
```

There is no test suite, linter, or package manifest. `python3 -m py_compile` is the local check.

Do not start `panel.py` unless the user wants a local run and the paths below are disposable. `main()` reads and may rewrite `SINGBOX_CONFIG_PATH` (default `/etc/sing-box/config.json`) and restart sing-box. Override every path so a laptop or cloud agent cannot hit the production files:

```bash
PANEL_HOST=127.0.0.1 \
PANEL_PORT=8080 \
PANEL_PASSWORD=dev-password \
PANEL_SECRET=dev-secret-change-me \
PUBLIC_NODE_HOST=127.0.0.1 \
APP_DIR=/tmp/singbox-panel-dev \
SINGBOX_CONFIG_PATH=/tmp/singbox-panel-dev/config.json \
SINGBOX_MANAGE_MODE=process \
SINGBOX_LOG_PATH=/tmp/singbox-panel-dev/sing-box.log \
python3 panel.py
```

Use a copy of a sing-box config that already has a `vless-reality-in` Reality inbound. `PANEL_PASSWORD` and `PANEL_SECRET` are required or the process exits. Do not point `APP_DIR` or `SINGBOX_CONFIG_PATH` at `/opt/singbox-panel` or `/etc/sing-box` unless the user has explicitly approved work on that machine.

`sing-box check -c <path>` is the config check. Run it before any config change. On a machine without sing-box, skip the check locally and do not invent a production restart to compensate.

## Coding conventions

Observed in `panel.py` and `scripts/singbox_watchdog.py`. Match them.

- Standard library only. No pip packages, `requirements.txt`, web framework, or frontend toolchain.
- Keep the panel in `panel.py` unless the user explicitly asks to split it. New HTTP routes go in `Handler.do_GET` or the `do_POST` `routes` dict. New UI goes inside the `page()` string.
- Module-level `snake_case` functions. Configuration is a block of `os.environ.get(...)` constants near the top.
- No type annotations, no dataclasses, no f-strings, no walrus operator. Format strings with `%` or concatenation, as the current file does. Do not reformat the file onto a newer style in passing. The container image is Python 3.11; the historical runtime note in the README is 3.6.9. Stay within the syntax already used.
- Expected failures raise `PanelError`. Handlers catch `Exception` and return `{"ok": false, "error": "..."}`. Login failure uses HTTP 403 and the Chinese message `密码错误`. Other `PanelError` messages are English.
- State mutations that touch `state.json` use `@state_transaction` (`STATE_LOCK`, an `RLock`). Persist with `save_json` (temp file, then `os.replace`). State JSON is `ensure_ascii=False`, `indent=2`, `sort_keys=True`.
- Every sing-box config write goes through `atomic_write_config`. Do not open `config.json` and write it yourself. Renaming a device does not restart sing-box; enable, expiry, customer, and home-proxy changes do.
- The embedded UI is Chinese. Keep operator-facing page copy in Chinese. Escape dynamic HTML with the existing `esc()` helper.
- Cookie name is `sbp` (HMAC-SHA256 over `PANEL_SECRET`). Compare secrets with `hmac.compare_digest`. The Clash API secret is `sha256(PANEL_SECRET)` truncated to 32 hex chars.
- Watchdog stays a single stdlib script. Do not shorten `WATCHDOG_INTERVAL_SECONDS` (30), `TRAFFIC_POLL_SECONDS` (5), or `EXPIRE_CHECK_SECONDS` (60) on the production host.

## Safety rules

These are mandatory for every task, including docs, refactors, and "quick checks". `.cursor/rules/ops-safety.mdc` repeats them so they always apply.

- Never touch the production server from code or from an agent task without explicit user approval. That includes SSH, `systemctl`, restarts, deploys, DNS edits, firewall changes, and `curl` against the public panel or node.
- The server is 1 vCPU and has been force-shut down for high CPU. Do not run frequent speed tests, extra polling, or repeated restarts. The in-process traffic poll and the 30-second watchdog are already the steady load.
- Before any sing-box config change, run `sing-box check` and back up `state.json` and `config.json` together. Restoring only one of them can desync panel records from VLESS users. Panel backups under `/opt/singbox-panel/backups/` are config snapshots only; they are not a substitute for a paired state backup.
- Ports 8080 and 9090 stay local-only. Production `PANEL_HOST` is `127.0.0.1`. Do not bind them on a public address, publish them in Docker port mappings, or open them in a firewall. The code default `PANEL_HOST=0.0.0.0` and the Compose `network_mode: host` setting are easy to misuse; do not deploy that combination onto the production host.
- `node.5858188.xyz` stays Cloudflare DNS-only. Proxied (orange-cloud) DNS breaks VLESS Reality. The panel hostname may stay proxied.
- Never commit passwords, `PANEL_PASSWORD`, `PANEL_SECRET`, Reality private keys, SOCKS5 credentials, or session cookies. Do not commit `state.json`, `config.json`, `.env`, or `/etc/singbox-panel.env`. `.env.example` keeps placeholders only.

## Further reading

- `README.md` — install, environment variables, HTTP API, state shape.
- `docs/HANDOVER-2026-11-02.md` — latest ops handover (Chinese). It records topology and read-only checks. It has no live credentials; those are exchanged out of band.
