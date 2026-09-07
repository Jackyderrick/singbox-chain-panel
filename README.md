# Sing-box Panel V1

## 给 AI 的 30 秒摘要

这是一个单文件 Python 3 面板，用于管理既有 `sing-box` VLESS Reality 入站、多个家宽 SOCKS5 出站、客户、设备链接、有效期、当前连接流量和基础设备数限制。项目没有第三方 Python 包，入口是 `panel.py`，运行依赖 Linux 上已安装的 `sing-box`、`systemd`、`curl`、`ss`、`journalctl`。最短启动路径是在服务器上准备 `/etc/sing-box/config.json`，设置 `PANEL_PASSWORD` 和 `PANEL_SECRET`，然后执行 `python3 panel.py`。

## 1. 项目概述

一句话定义：`Sing-box Panel V1` 是一个基于 Python 标准库的 Web 管理面板，用来修改和监控本机 `sing-box` 代理配置。

检索关键词：

| 类型 | 关键词 |
| --- | --- |
| 语言 | Python 3 |
| Web | `http.server` |
| 代理核心 | `sing-box` |
| 协议 | VLESS Reality, SOCKS5 |
| 系统集成 | systemd, Nginx |
| 管理模型 | customer, device, outbound |
| 统计 | Clash API `/connections` |

它解决的问题：

- 管理一个已有的 `sing-box` VLESS Reality 入站。
- 为客户添加、删除、停用设备级 VLESS UUID。
- 为客户绑定不同家宽 SOCKS5 出口。
- 添加、删除、测试多个家宽 SOCKS5 出站。
- 生成 Shadowrocket 等客户端可导入的 VLESS Reality 链接。
- 按设备 UUID 记录累计上传、下载、总流量。
- 显示当前活跃连接的来源、目标、上传、下载、链路。
- 给设备链接设置有效期，到期后自动停用。
- 按客户统计最近 10 分钟来源 IP 数，用于近似设备数限制。

它不解决的问题：

- 不安装 `sing-box` 本体。
- 不生成 VLESS Reality 私钥、公钥或初始化完整 `sing-box` 配置。
- 不提供真实设备指纹识别；设备数限制基于公网来源 IP 近似判断。
- 不提供多管理员、权限角色、审计日志。
- 不提供数据库迁移系统。
- 不提供 HTTPS 证书申请；可由 Nginx、Cloudflare 或其他反代层完成。
- 不保证兼容所有 `sing-box` 版本；当前实现依据已读源码和一次部署环境验证。

### 分支与演进方向

| 分支 | 定位 | 说明 |
| --- | --- | --- |
| `main` | 单机稳定版 | 当前可部署版本，面板、状态文件、前端模板、后台线程仍在 `panel.py` 内 |
| `codex/cf-distributed-panel-design` | Cloudflare 分布式面板设计分支 | 讨论 `Cloudflare Worker + D1 + VPS Agent` 的 Master/Node 架构，不影响当前线上单机版 |

Cloudflare 分布式面板的设计文档位于设计分支：

```text
docs/cf-distributed-panel-architecture.md
```

推荐迁移方向：

- 第一阶段保留 `main` 的单机部署，继续用于当前服务器。
- 第二阶段从设计分支启动新目录：`worker/` 承载 Cloudflare Worker API，`agent/` 承载 VPS Python Agent。
- 第三阶段让 Agent 主动向 Worker 心跳、上报流量、拉取配置，逐步替代单机 Web 面板中的远程管理逻辑。

## 2. 技术栈与运行环境

### 已读到的项目技术栈

| 项 | 值 |
| --- | --- |
| 语言 | Python 3 |
| Python 标准库 | `http.server`, `socketserver`, `json`, `subprocess`, `threading`, `urllib.request`, `uuid` |
| Web 框架 | 无 |
| 包管理器 | 无 |
| 第三方 Python 依赖 | 无 |
| 前端 | 内嵌 HTML/CSS/JavaScript 字符串，由 `panel.py` 返回 |
| 测试框架 | 无 |
| 构建系统 | 无 |

### 已部署验证过的运行环境

| 项 | 已验证值 |
| --- | --- |
| 操作系统 | Ubuntu 18.04.6 LTS |
| Python | Python 3.6.9 |
| sing-box | sing-box 1.14.0 |
| systemd | Ubuntu 18.04 默认 systemd，具体版本【需人工验证】 |
| Nginx | Ubuntu apt 安装的 nginx 1.14.0-0ubuntu1.11 |
| GPU | 不需要 |
| 内存门槛 | 【需人工验证】；项目未做压力测试 |

### 外部服务与系统命令

| 依赖 | 用途 | 必需 |
| --- | --- | --- |
| `sing-box` | 代理核心、配置校验、重启、Clash API 数据源 | 是 |
| `/etc/sing-box/config.json` | 被面板读取和修改的主配置 | 是 |
| `systemctl` | 重启 `sing-box`，运行面板服务时由 systemd 管理 | 是 |
| `journalctl` | 读取 `sing-box` 日志、跟随日志识别来源 IP | 是 |
| `curl` | 测试服务器出口 IP、测试家宽 SOCKS5 出口 IP | 是 |
| `ss` | 获取监听端口信息 | 是 |
| Nginx | 反代公网面板域名到 `127.0.0.1:8080` | 可选 |
| Cloudflare DNS | 域名解析，项目代码不调用 Cloudflare API | 可选 |

### 网络要求

| 方向 | 说明 |
| --- | --- |
| 入站 | Web 面板默认监听 `PANEL_HOST:PANEL_PORT`；VLESS 入站由 `sing-box` 监听 |
| 出站 | 面板调用 `https://api.ipify.org` 获取 IP；测试家宽 SOCKS5 时通过该 SOCKS5 访问 `api.ipify.org` |
| 本机 | 面板调用 `http://127.0.0.1:9090/connections` 读取 `sing-box` Clash API |

## 3. 架构速览

### 完整目录树

以下是实际检查后的仓库根目录。已忽略 `__pycache__`、临时调试 HTML/JS 文件等生成物。

```text
.
├── .env.example
├── .gitignore
├── Dockerfile
├── README.md
├── docker-compose.yml
└── panel.py
```

### 关键文件

| 路径 | 作用 |
| --- | --- |
| `panel.py` | Web 服务、API、HTML 页面、状态文件管理、`sing-box` 配置修改、后台线程全部在此文件内 |
| `.gitignore` | 忽略 Python 缓存、环境变量文件、调试导出文件 |
| `.env.example` | Docker Compose 和进程模式所需环境变量模板 |
| `Dockerfile` | 构建容器镜像，安装 Python 3.11 slim 和 sing-box 1.14.0 |
| `docker-compose.yml` | 以 `SINGBOX_MANAGE_MODE=process` 启动面板和 sing-box |
| `README.md` | 给 AI 和维护者的安装、运行、接口、数据结构说明 |

### 调用链

```text
浏览器
  -> panel.py: ThreadingHTTPServer
  -> Handler.do_GET / Handler.do_POST
  -> API 函数
  -> state.json 读写
  -> /etc/sing-box/config.json 读写
  -> sing-box check -c 临时配置
  -> systemctl restart sing-box
```

当前连接流量调用链：

```text
panel.py 后台线程 poll_connection_traffic
  -> Clash API http://127.0.0.1:9090/connections
  -> 按 connection id 计算 upload/download 增量
  -> 按 auth_user(UUID) 累加到 /opt/singbox-panel/state.json
```

客户设备数限制调用链：

```text
panel.py 后台线程 monitor_singbox_logs
  -> journalctl -u sing-box -f
  -> 读取 VLESS 来源 IP
  -> 读取客户内部出口 customer-route-*
  -> 更新客户最近 10 分钟 online_ips
  -> 超限时告警或停用客户下设备
```

链接有效期调用链：

```text
panel.py 后台线程 expire_devices_loop
  -> 每 60 秒扫描 state.json
  -> 到期设备 enabled=false
  -> 从 VLESS users 移除 UUID
  -> sing-box check
  -> systemctl restart sing-box
```

### 入口点清单

| 入口 | 类型 | 默认值/路径 | 说明 |
| --- | --- | --- | --- |
| `python3 panel.py` | Web 服务 | `0.0.0.0:8080` | 直接运行面板 |
| `GET /` | HTTP | `/` | 返回内嵌管理页面 |
| 后台线程 `monitor_singbox_logs` | Worker | 进程内线程 | 统计客户来源 IP |
| 后台线程 `poll_connection_traffic` | Worker | 进程内线程 | 累计设备流量 |
| 后台线程 `expire_devices_loop` | Worker | 进程内线程 | 停用到期设备 |
| `systemd` 服务 | 生产运行 | `/etc/systemd/system/singbox-panel.service` | 仓库未包含该文件；README 给出模板 |
| Nginx 反代 | 可选入口 | `panel.example.com:80` | 仓库未包含该文件；README 给出模板 |

## 4. 安装步骤

以下命令以仓库根目录为基准；服务器路径示例使用 `/opt/singbox-panel`。

### 4.1 前置条件

在目标服务器执行：

```bash
python3 --version
sing-box version
systemctl --version
curl --version
ss --version
journalctl --version
```

已验证组合：

```text
Ubuntu 18.04.6 LTS
Python 3.6.9
sing-box 1.14.0
```

`sing-box` 配置必须已经存在：

```bash
test -f /etc/sing-box/config.json
sing-box check -c /etc/sing-box/config.json
```

`/etc/sing-box/config.json` 中必须已有 tag 为 `vless-reality-in` 的 VLESS Reality 入站。示例片段：

```json
{
  "type": "vless",
  "tag": "vless-reality-in",
  "listen": "0.0.0.0",
  "listen_port": 443,
  "users": [],
  "tls": {
    "enabled": true,
    "server_name": "www.microsoft.com",
    "reality": {
      "enabled": true,
      "handshake": {
        "server": "www.microsoft.com",
        "server_port": 443
      },
      "private_key": "REPLACE_WITH_REALITY_PRIVATE_KEY",
      "short_id": ["REPLACE_WITH_SHORT_ID"]
    }
  }
}
```

### 4.2 克隆或复制项目

在服务器执行：

```bash
sudo mkdir -p /opt/singbox-panel
sudo cp panel.py /opt/singbox-panel/panel.py
sudo chmod 755 /opt/singbox-panel/panel.py
```

如果从 GitHub 克隆，在服务器执行：

```bash
cd /opt
sudo git clone https://github.com/OWNER/REPOSITORY.git singbox-panel
sudo chmod 755 /opt/singbox-panel/panel.py
```

`OWNER/REPOSITORY` 需要替换为实际仓库名。

### 4.3 配置环境变量

在服务器生成 secret：

```bash
python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
```

创建 systemd 服务：

```bash
sudo tee /etc/systemd/system/singbox-panel.service >/dev/null <<'EOF'
[Unit]
Description=Sing-box lightweight panel
After=network-online.target sing-box.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/singbox-panel
Environment=PANEL_HOST=127.0.0.1
Environment=PANEL_PORT=8080
Environment=PANEL_PASSWORD=REPLACE_WITH_PANEL_PASSWORD
Environment=PANEL_SECRET=REPLACE_WITH_RANDOM_SECRET
Environment=PUBLIC_NODE_HOST=node.example.com
ExecStart=/usr/bin/python3 /opt/singbox-panel/panel.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF
```

### 4.4 启动面板

在服务器执行：

```bash
sudo systemctl daemon-reload
sudo systemctl enable singbox-panel
sudo systemctl restart singbox-panel
sudo systemctl status singbox-panel --no-pager -l
```

验证本机可访问：

```bash
curl -i http://127.0.0.1:8080/
```

验证登录 API：

```bash
curl -i \
  -c /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"password":"REPLACE_WITH_PANEL_PASSWORD"}' \
  http://127.0.0.1:8080/api/login
```

验证状态 API：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  http://127.0.0.1:8080/api/status
```

### 4.5 可选：Nginx 反代

在服务器执行：

```bash
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nginx
```

创建反代配置：

```bash
sudo tee /etc/nginx/sites-available/singbox-panel.conf >/dev/null <<'EOF'
server {
    listen 80;
    server_name panel.example.com;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
```

启用 Nginx 配置：

```bash
sudo ln -sf /etc/nginx/sites-available/singbox-panel.conf /etc/nginx/sites-enabled/singbox-panel.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl enable nginx
sudo systemctl restart nginx
```

验证：

```bash
curl -i -H 'Host: panel.example.com' http://127.0.0.1/
```

### 4.6 可选：Cloudflare DNS

在 Cloudflare DNS 中添加：

```text
node.example.com   A   SERVER_IPV4   DNS only
panel.example.com  A   SERVER_IPV4   Proxied 或 DNS only
```

VLESS Reality 节点域名建议使用 `DNS only`。Cloudflare 免费代理不转发任意 VLESS Reality TCP 流量。

### 4.7 安装坑

| 触发场景 | 原因 | 处理 |
| --- | --- | --- |
| `PANEL_PASSWORD is required` | 未设置环境变量 | 在 systemd service 中设置 `Environment=PANEL_PASSWORD=...` |
| `PANEL_SECRET is required` | 未设置签名密钥 | 在 systemd service 中设置 `Environment=PANEL_SECRET=...` |
| `missing inbound: vless-reality-in` | `sing-box` 配置没有目标 VLESS 入站 tag | 修改 `VLESS_TAG` 常量或调整 `/etc/sing-box/config.json` |
| `v2ray api is not included` | 当前项目不使用 v2ray API；不要按 v2ray stats 方式配置 | 使用内置 Clash API `/connections` |
| `sing-box check failed` | 面板生成的新配置没有通过 `sing-box check` | 查看错误文本和 `/opt/singbox-panel/backups/` |
| 页面不能自动复制链接 | 非 HTTPS 页面通常不能使用 Clipboard API | 页面会 fallback 到 `prompt` 手动复制 |

## 5. 运行与使用

### 开发模式

在仓库根目录执行：

```bash
PANEL_HOST=127.0.0.1 \
PANEL_PORT=8080 \
PANEL_PASSWORD=dev-password \
PANEL_SECRET=dev-secret-change-me \
PUBLIC_NODE_HOST=127.0.0.1 \
python3 panel.py
```

开发模式仍会读取和修改 `/etc/sing-box/config.json`。在非目标服务器运行需要准备测试用 `sing-box` 配置，或修改源码中的 `CONFIG_PATH` 后再运行。

### 生产模式

在服务器执行：

```bash
sudo systemctl restart singbox-panel
sudo systemctl status singbox-panel --no-pager -l
```

### 进程托管模式

进程托管模式用于容器化或没有 systemd 的环境。面板会通过 `subprocess.Popen` 启动：

```bash
sing-box run -c /etc/sing-box/config.json
```

在仓库根目录执行：

```bash
PANEL_HOST=0.0.0.0 \
PANEL_PORT=8080 \
PANEL_PASSWORD=dev-password \
PANEL_SECRET=dev-secret-change-me \
PUBLIC_NODE_HOST=node.example.com \
APP_DIR=/opt/singbox-panel \
SINGBOX_CONFIG_PATH=/etc/sing-box/config.json \
SINGBOX_MANAGE_MODE=process \
SINGBOX_BIN=sing-box \
SINGBOX_LOG_PATH=/opt/singbox-panel/sing-box.log \
python3 panel.py
```

进程托管模式的行为：

- 面板启动时会启动 `sing-box run -c SINGBOX_CONFIG_PATH`。
- 面板重启 `sing-box` 时会先终止旧进程，再启动新进程。
- `sing-box` 标准输出和错误输出写入 `SINGBOX_LOG_PATH`。
- 面板日志查看和在线 IP 检测从 `SINGBOX_LOG_PATH` 读取。
- 不调用 `systemctl` 或 `journalctl` 管理 `sing-box`。

### Docker Compose

在仓库根目录执行：

```bash
cp .env.example .env
python3 - <<'PY'
import secrets
print("PANEL_SECRET=" + secrets.token_urlsafe(32))
PY
```

编辑 `.env`，设置：

```text
PANEL_PASSWORD=change-this-password
PANEL_SECRET=replace-with-generated-secret
PUBLIC_NODE_HOST=node.example.com
APP_DIR=/opt/singbox-panel
SINGBOX_CONFIG_PATH=/etc/sing-box/config.json
SINGBOX_MANAGE_MODE=process
SINGBOX_BIN=/usr/local/bin/sing-box
SINGBOX_LOG_PATH=/opt/singbox-panel/sing-box.log
```

把可用的 `sing-box` 配置放到仓库根目录：

```bash
cp /etc/sing-box/config.json ./config.json
```

启动：

```bash
docker compose up -d --build
docker compose logs -f singbox-panel
```

验证：

```bash
curl -i http://127.0.0.1:8080/
```

Docker Compose 使用 `network_mode: host`。在 Linux 主机上，容器内 `sing-box` 可以直接监听宿主机端口。Docker Desktop、macOS、Windows 的 host network 行为【需人工验证】。

### 配置校验

在仓库根目录执行：

```bash
python3 -m py_compile panel.py
```

在服务器执行：

```bash
sing-box check -c /etc/sing-box/config.json
```

### 日志

在服务器执行：

```bash
journalctl -u singbox-panel -n 100 --no-pager
journalctl -u sing-box -n 100 --no-pager
```

### 构建、Lint、测试

| 命令 | 作用 | 示例 |
| --- | --- | --- |
| `python3 -m py_compile panel.py` | Python 语法检查 | `python3 -m py_compile panel.py` |
| 无 npm scripts | 项目没有 `package.json` | 【需人工验证】 |
| 无 Makefile targets | 项目没有 `Makefile` | 【需人工验证】 |
| 无自动化测试 | 项目没有测试目录 | 【需人工验证】 |

## 6. 配置项清单

### 环境变量

| 名称 | 必填 | 默认值 | 含义 | 示例 |
| --- | --- | --- | --- | --- |
| `PANEL_HOST` | 否 | `0.0.0.0` | 面板监听地址 | `127.0.0.1` |
| `PANEL_PORT` | 否 | `8080` | 面板监听端口 | `8080` |
| `PANEL_PASSWORD` | 是 | 空 | 登录面板密码 | `change-me` |
| `PANEL_SECRET` | 是 | 空 | Cookie 签名密钥和 Clash API secret 派生源 | `random-url-safe-secret` |
| `PUBLIC_NODE_HOST` | 否 | `45.8.173.58` | 生成 VLESS 链接时使用的主机名 | `node.example.com` |
| `REALITY_PUBLIC_KEY` | 条件必填 | 空 | 生成 VLESS Reality 链接使用的公钥；未设置时读取 `/opt/singbox-panel/reality-public-key.json` | `PUBLIC_KEY_VALUE` |
| `DEFAULT_HOME_TAG` | 否 | `home-socks5-out` | 初始默认家宽 SOCKS5 出站 tag | `home-socks5-out` |
| `APP_DIR` | 否 | `/opt/singbox-panel` | 状态、备份、日志目录 | `/opt/singbox-panel` |
| `SINGBOX_CONFIG_PATH` | 否 | `/etc/sing-box/config.json` | 被面板管理的 `sing-box` 配置文件 | `/etc/sing-box/config.json` |
| `SINGBOX_BIN` | 否 | `sing-box` | `sing-box` 可执行文件路径 | `/usr/local/bin/sing-box` |
| `SINGBOX_MANAGE_MODE` | 否 | `systemd` | `systemd` 使用系统服务管理；`process` 使用 `subprocess.Popen` 管理 | `process` |
| `SINGBOX_SERVICE` | 否 | `sing-box` | systemd 模式下的服务名 | `sing-box` |
| `SINGBOX_LOG_PATH` | 否 | `/opt/singbox-panel/sing-box.log` | process 模式下的 `sing-box` 日志文件 | `/opt/singbox-panel/sing-box.log` |
| `VLESS_TAG` | 否 | `vless-reality-in` | 被管理的 VLESS 入站 tag | `vless-reality-in` |
| `DEFAULT_VLESS_FLOW` | 否 | `xtls-rprx-vision` | 新建设备和生成链接时使用的 VLESS flow | `xtls-rprx-vision` |
| `CLASH_API_ADDR` | 否 | `127.0.0.1:9090` | 本地 Clash API 地址 | `127.0.0.1:9090` |
| `SOCKS_OUT_PREFIX` | 否 | `home-socks5-` | 家宽 SOCKS5 出站 tag 前缀 | `home-socks5-` |
| `CUSTOMER_OUT_PREFIX` | 否 | `customer-route-` | 客户内部出站 tag 前缀 | `customer-route-` |
| `ONLINE_WINDOW_SECONDS` | 否 | `600` | 来源 IP 观察窗口秒数 | `600` |
| `TRAFFIC_POLL_SECONDS` | 否 | `5` | 连接流量轮询间隔秒数 | `5` |
| `EXPIRE_CHECK_SECONDS` | 否 | `60` | 链接过期检查间隔秒数 | `60` |

### 源码常量

| 名称 | 默认值 | 含义 |
| --- | --- | --- |
| `APP_DIR` | `/opt/singbox-panel` | 状态、备份和附加文件目录 |
| `STATE_PATH` | `/opt/singbox-panel/state.json` | 面板状态文件 |
| `CONFIG_PATH` | `/etc/sing-box/config.json` | `sing-box` 主配置 |
| `BACKUP_DIR` | `/opt/singbox-panel/backups` | 写配置前的备份目录 |
| `VLESS_TAG` | `vless-reality-in` | 被管理的 VLESS 入站 tag |
| `SOCKS_OUT_PREFIX` | `home-socks5-` | 家宽 SOCKS5 出站 tag 前缀 |
| `CUSTOMER_OUT_PREFIX` | `customer-route-` | 客户内部出站 tag 前缀 |
| `ONLINE_WINDOW_SECONDS` | `600` | 设备数限制的来源 IP 观察窗口 |
| `CLASH_API_ADDR` | `127.0.0.1:9090` | 本地 Clash API 地址 |
| `TRAFFIC_POLL_SECONDS` | `5` | 连接流量轮询间隔 |
| `EXPIRE_CHECK_SECONDS` | `60` | 链接过期检查间隔 |

### 配置文件

| 路径 | 格式 | 写入者 | 说明 |
| --- | --- | --- | --- |
| `/etc/sing-box/config.json` | JSON | 面板和管理员 | `sing-box` 主配置；面板会修改 `inbounds`、`outbounds`、`route.rules`、`experimental.clash_api` |
| `/opt/singbox-panel/state.json` | JSON | 面板 | 客户、设备、家宽出口元数据、流量累计、在线 IP |
| `/opt/singbox-panel/backups/config-*.json` | JSON | 面板 | 每次写 `sing-box` 配置前的备份 |
| `/opt/singbox-panel/reality-public-key.json` | JSON | 管理员 | 可选；若存在，读取 `public_key` 用于生成 VLESS 链接 |

## 7. 对外接口

所有 `/api/*` 接口除 `/api/login` 外都需要 Cookie `sbp`。前端通过登录接口取得该 Cookie。

### `GET /`

返回内嵌 HTML 管理页面。

请求：

```bash
curl -i http://127.0.0.1:8080/
```

响应：`text/html; charset=utf-8`

### `POST /api/login`

请求：

```bash
curl -i \
  -c /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"password":"REPLACE_WITH_PANEL_PASSWORD"}' \
  http://127.0.0.1:8080/api/login
```

成功响应：

```json
{"ok": true}
```

失败响应：

```json
{"ok": false, "error": "密码错误"}
```

### `GET /api/status`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  http://127.0.0.1:8080/api/status
```

响应示例：

```json
{
  "service": "active",
  "customers": [],
  "devices": [],
  "homes": [],
  "active_home": "home-socks5-out",
  "listen": "State ...",
  "vless_link": "",
  "server_ip": "203.0.113.10",
  "ok": true
}
```

### `GET /api/logs`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  http://127.0.0.1:8080/api/logs
```

响应示例：

```json
{
  "ok": true,
  "logs": "journalctl output"
}
```

### `GET /api/connections`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  http://127.0.0.1:8080/api/connections
```

响应示例：

```json
{
  "ok": true,
  "download_total": 1024,
  "upload_total": 512,
  "device_totals": [
    {
      "uuid": "82eeb079-af84-46a3-a031-74a0e7fd4be8",
      "name": "iPhone",
      "customer_id": "customer-a",
      "upload_bytes": 512,
      "download_bytes": 1024,
      "used_bytes": 1536,
      "live_upload": 10,
      "live_download": 20,
      "live_total": 30
    }
  ],
  "connections": []
}
```

### `POST /api/customer/add`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"name":"CustomerA","home_tag":"home-socks5-out","device_limit":1,"default_days":30,"limit_action":"alert","quota_gb":0}' \
  http://127.0.0.1:8080/api/customer/add
```

响应：

```json
{"ok": true, "id": "customer-customera"}
```

### `POST /api/customer/set`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"id":"customer-customera","home_tag":"home-socks5-out","device_limit":1,"default_days":30,"limit_action":"alert","quota_gb":0}' \
  http://127.0.0.1:8080/api/customer/set
```

响应：

```json
{"ok": true}
```

### `POST /api/customer/delete`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"id":"customer-customera"}' \
  http://127.0.0.1:8080/api/customer/delete
```

响应：

```json
{"ok": true}
```

限制：客户下仍有设备时返回错误。

### `POST /api/device/add`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"name":"iPhone","customer_id":"customer-customera","days":30}' \
  http://127.0.0.1:8080/api/device/add
```

响应：

```json
{
  "ok": true,
  "uuid": "82eeb079-af84-46a3-a031-74a0e7fd4be8",
  "link": "vless://82eeb079-af84-46a3-a031-74a0e7fd4be8@node.example.com:443?encryption=none&flow=xtls-rprx-vision&security=reality&sni=www.microsoft.com&fp=chrome&pbk=PUBLIC_KEY&sid=SHORT_ID&type=tcp&headerType=none#CustomerA-iPhone"
}
```

### `POST /api/device/set`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"uuid":"82eeb079-af84-46a3-a031-74a0e7fd4be8","name":"iPhone 15","enabled":true,"days":30}' \
  http://127.0.0.1:8080/api/device/set
```

响应：

```json
{"ok": true}
```

说明：

- `name` 只改面板状态和链接 fragment，不重启 `sing-box`。
- `enabled`、`days`、`expires_at`、`customer_id` 会触发 `sing-box` 配置重建。

### `POST /api/device/delete`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"uuid":"82eeb079-af84-46a3-a031-74a0e7fd4be8"}' \
  http://127.0.0.1:8080/api/device/delete
```

响应：

```json
{"ok": true}
```

### `POST /api/home/add`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"name":"HomeUS","server":"198.51.100.10","server_port":8022,"username":"user","password":"pass"}' \
  http://127.0.0.1:8080/api/home/add
```

响应：

```json
{"ok": true, "tag": "home-socks5-homeus"}
```

### `POST /api/home/use`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"tag":"home-socks5-homeus"}' \
  http://127.0.0.1:8080/api/home/use
```

响应：

```json
{"ok": true}
```

### `POST /api/home/test`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"tag":"home-socks5-homeus"}' \
  http://127.0.0.1:8080/api/home/test
```

响应：

```json
{"ok": true, "ip": "198.51.100.10"}
```

### `POST /api/home/delete`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"tag":"home-socks5-homeus"}' \
  http://127.0.0.1:8080/api/home/delete
```

响应：

```json
{"ok": true}
```

限制：当前默认出口或已分配给客户的出口不能删除。

### `POST /api/restart`

请求：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:8080/api/restart
```

响应：

```json
{"ok": true}
```

## 8. 数据与存储

### 数据库

项目没有数据库。

### 状态文件

路径：

```text
/opt/singbox-panel/state.json
```

顶层结构：

```json
{
  "active_home": "home-socks5-out",
  "customers": {},
  "devices": {},
  "homes": {}
}
```

客户对象示例：

```json
{
  "customer-customera": {
    "name": "CustomerA",
    "home_tag": "home-socks5-homeus",
    "device_limit": 1,
    "limit_action": "alert",
    "default_days": 30,
    "quota_gb": 0,
    "online_ips": {
      "203.0.113.20": 1790000000
    },
    "created_at": 1790000000
  }
}
```

设备对象示例：

```json
{
  "82eeb079-af84-46a3-a031-74a0e7fd4be8": {
    "name": "iPhone",
    "customer_id": "customer-customera",
    "enabled": true,
    "created_at": 1790000000,
    "expires_at": 1792592000,
    "used_bytes": 1536,
    "upload_bytes": 512,
    "download_bytes": 1024
  }
}
```

家宽出口对象示例：

```json
{
  "home-socks5-homeus": {
    "name": "HomeUS",
    "server": "198.51.100.10",
    "server_port": 8022,
    "username": "user",
    "password": "pass",
    "created_at": 1790000000
  }
}
```

### `sing-box` 配置写入范围

面板会修改：

- `inbounds` 中 tag 为 `vless-reality-in` 的 `users`。
- `outbounds`，追加或删除 tag 以 `home-socks5-` 和 `customer-route-` 开头的出站。
- `route.rules`，按 `auth_user` 生成客户分流规则。
- `route.final`，设置默认出站。
- `experimental.clash_api`，设置本地 Clash API：

```json
{
  "experimental": {
    "clash_api": {
      "external_controller": "127.0.0.1:9090",
      "secret": "derived-from-panel-secret"
    }
  }
}
```

### 备份

每次写入 `/etc/sing-box/config.json` 前，会复制旧配置到：

```text
/opt/singbox-panel/backups/config-YYYYmmdd-HHMMSS.json
```

## 9. 常见错误与排错速查

| 报错/现象 | 触发场景 | 原因 | 解决办法 |
| --- | --- | --- | --- |
| `PANEL_PASSWORD is required` | 启动面板 | 未设置登录密码 | 设置 `PANEL_PASSWORD` 后重启 |
| `PANEL_SECRET is required` | 启动面板 | 未设置 Cookie 签名密钥 | 设置 `PANEL_SECRET` 后重启 |
| `missing inbound: vless-reality-in` | 打开状态页或添加设备 | `sing-box` 配置没有该 tag | 修改 `VLESS_TAG` 或补齐入站 |
| `VLESS REALITY inbound is missing reality settings` | 生成链接 | 入站不是 REALITY 配置 | 补齐 `tls.reality` |
| `sing-box check failed` | 添加客户、设备、出口或切换出口 | 新配置未通过校验 | 查看错误文本，必要时从 `/opt/singbox-panel/backups/` 恢复 |
| 页面弹出 `Unexpected end of JSON input` | 旧版本前端解析空响应 | 前端直接调用 `response.json()` | 使用当前版本；当前版本先读取文本再解析 |
| `Cannot read properties of undefined (reading 'writeText')` | HTTP 页面点复制 | 浏览器禁止 Clipboard API 或不支持 | 当前版本 fallback 到 `prompt` 手动复制 |
| 节点域名不可用 | Cloudflare `node` 记录开了代理 | VLESS Reality 不是普通 HTTPS | 将 `node.example.com` 改为 DNS only |
| 面板域名打不开 | Nginx 或 DNS 未生效 | 80 端口未监听或 DNS 未解析 | 执行 `nginx -t`、`systemctl status nginx`、检查 DNS |
| 设备流量不增长 | 没有活跃连接或 Clash API 未启动 | `/connections` 取不到数据 | 检查 `ss -lntp | grep 9090` 和 `/api/connections` |

## 10. 给后续 AI 的操作守则

### 可反复执行的命令

在仓库根目录执行：

```bash
python3 -m py_compile panel.py
```

在服务器执行：

```bash
sing-box check -c /etc/sing-box/config.json
systemctl status singbox-panel --no-pager -l
systemctl status sing-box --no-pager -l
journalctl -u singbox-panel -n 100 --no-pager
journalctl -u sing-box -n 100 --no-pager
curl -i http://127.0.0.1:8080/
```

### 有副作用，执行前应确认的命令或操作

| 操作 | 副作用 |
| --- | --- |
| `systemctl restart sing-box` | 会断开已有代理连接 |
| `systemctl restart singbox-panel` | 会中断面板请求，后台流量轮询状态会重置 |
| process 模式下调用 `/api/restart` | 会终止并重启面板托管的 `sing-box` 子进程 |
| 添加/删除设备 | 会修改 `/etc/sing-box/config.json` 并重启 `sing-box` |
| 添加/删除家宽出口 | 会修改 `/etc/sing-box/config.json` 并重启 `sing-box` |
| 切换客户出口 | 会修改路由规则并重启 `sing-box` |
| 删除 `/opt/singbox-panel/state.json` | 会丢失客户、设备元数据和累计流量 |
| 删除 `/opt/singbox-panel/backups/` | 会丢失配置回滚点 |

### 自验证成功标准

在服务器执行：

```bash
python3 -m py_compile /opt/singbox-panel/panel.py
systemctl restart singbox-panel
systemctl is-active singbox-panel
systemctl is-active sing-box
sing-box check -c /etc/sing-box/config.json
```

登录并调用状态接口：

```bash
curl -s \
  -c /tmp/singbox-panel.cookie \
  -H 'Content-Type: application/json' \
  -d '{"password":"REPLACE_WITH_PANEL_PASSWORD"}' \
  http://127.0.0.1:8080/api/login

curl -s \
  -b /tmp/singbox-panel.cookie \
  http://127.0.0.1:8080/api/status
```

读取连接接口：

```bash
curl -s \
  -b /tmp/singbox-panel.cookie \
  http://127.0.0.1:8080/api/connections
```

检查 Clash API：

```bash
ss -lntp | grep ':9090'
```

检查面板 Web：

```bash
curl -i http://127.0.0.1:8080/
```

process 模式下还应检查：

```bash
test -f /opt/singbox-panel/sing-box.log
tail -n 50 /opt/singbox-panel/sing-box.log
```

检查 Nginx 反代：

```bash
curl -i -H 'Host: panel.example.com' http://127.0.0.1/
```

### 二次开发注意点

- `panel.py` 是单文件架构；新增 API 时同步修改 `Handler.do_GET` 或 `Handler.do_POST`。
- 所有写 `sing-box` 配置的路径应走 `atomic_write_config(cfg)`。
- `atomic_write_config` 会备份、临时写入、`sing-box check`、替换正式配置、重启 `sing-box`。
- 只修改设备显示名时不需要重启 `sing-box`。
- 设备流量累计依赖 Clash API 当前连接数据；进程重启后仍保留已写入 `state.json` 的累计值。
- 设备数限制基于来源 IP，手机网络切换可能造成误判。
- 不要把真实 `PANEL_PASSWORD`、`PANEL_SECRET`、家宽 SOCKS5 密码、REALITY 私钥提交到仓库。

## 自检清单

| 问题 | 状态 | 说明 |
| --- | --- | --- |
| 一个全新的 AI 能否不读源码理解项目用途？ | 是 | 见第 1 节和 3 节 |
| 能否从零安装？ | 部分是 | 见第 4 节；前提是已有可用 `sing-box` VLESS Reality 配置 |
| 能否启动服务？ | 是 | 见第 4.4 和第 5 节 |
| 能否调用一个最小功能？ | 是 | 见 `/api/login` 和 `/api/status` 示例 |
| 能否跑通测试？ | 否 | 项目没有测试；只能执行 `python3 -m py_compile panel.py` 和接口级验证 |
| 是否列出配置项？ | 是 | 见第 6 节 |
| 是否列出 HTTP API？ | 是 | 见第 7 节 |
| 是否说明数据存储？ | 是 | 见第 8 节 |
| 是否说明高风险操作？ | 是 | 见第 10 节 |
| 是否标出不确定项？ | 是 | 使用了【需人工验证】标注 |
