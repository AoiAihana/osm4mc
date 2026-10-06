# openstreetmap-website + osm-carto4mc 组合部署

把上游两个项目组合成**一套可一键启动的服务**：网站内置的 **iD 编辑器**写入的地理数据，
经自动增量同步后由 **osm-carto4mc** 渲染成瓦片。

**① 请求链路** —— 浏览器只需要认识一个端口：

```
                         http://localhost:3000   ← 整个栈唯一对外的端口
                                      │
                                      ▼
      ┌───────────────────────────────────────────────────────────────┐
      │ edge（nginx 反向代理）                                        │
      │   /       → web        :3000   Rails · 站点 / API / iD 编辑器 │
      │   /tile/  → tileserver :8080   renderd + mod_tile             │
      │   /tiles/ → dyn2xyz    :8080   Minecraft（Dynmap）→ XYZ 瓦片  │
      └───────┬─────────────────────┬───────────────────────┬─────────┘
              │                     │                       │
              ▼                     ▼                       ▼
       ┌────────────┐    ┌────────────────────┐    ┌─────────────────┐
       │ web        │    │ tileserver         │    │ dyn2xyz         │
       │ :3000      │    │ :8080              │    │ :8080           │
       │ Rails + iD │    │ renderd + mod_tile │    │ Minecraft → XYZ │
       └────────────┘    └────────────────────┘    └─────────────────┘
```

**② 数据链路** —— iD 的一次编辑怎样变成瓦片：

```
            iD / API 写入
                │
                ▼
      ┌──────────────────┐     ┌────────────────┐     ┌──────────────┐     ┌───────────────┐
      │ openstreetmap    │     │ sync           │     │ gis          │     │ PNG 瓦片      │
      │ （apidb schema） │ ──▶ │ osm_sync.py    │ ──▶ │ planet_osm_* │ ──▶ │ mod_tile 缓存 │
      │ 编辑数据         │     │ osm2pgsql 增量 │     │ 渲染数据     │     │ renderd 渲染  │
      └──────────────────┘     └────────────────┘     └──────────────┘     └───────────────┘
```

`openstreetmap` 与 `gis` 是**同一个 PostgreSQL + PostGIS 容器里的两个逻辑库**，共用一个数据卷；
`sync` / `web` / `tileserver` 是另外三个容器，`dyn2xyz` 完全不碰数据库（它去抓上游 Minecraft 地图）。

- 网站 / API / iD 编辑器：<http://localhost:3000>（`edge` 反向代理，**唯一入口**）
- osm-carto4mc 瓦片：<http://localhost:3000/tile/{z}/{x}/{y}.png>（`edge` → `tileserver`）
- Minecraft（dyn2xyz）瓦片：<http://localhost:3000/tiles/{z}/{x}/{y}.png>（`edge` → `dyn2xyz`）
- 容器：`db`（两个库共用）、`web`、`sync`（同步引擎）、`tileserver`（renderd + mod_tile）、`dyn2xyz`、`edge`

---

## 1. 为什么是「一个实例 + 两个逻辑库」

上游两个项目的数据库 schema **根本不同**，无法指向同一个 schema：

| | openstreetmap-website | osm-carto4mc |
|---|---|---|
| 库名 | `openstreetmap` | `gis` |
| schema | 规范化 apidb：`nodes`/`ways`/`relations` 全版本历史表 + `current_*` 当前表 + `*_tags` 行式标签表 | osm2pgsql flex 反规范化：`planet_osm_point/line/polygon/roads` |
| 用途 | API 读写、支持编辑与版本历史 | 只读渲染，为出图性能优化 |

所以这里的「同一个数据库」落在**同一个 PostgreSQL 实例、同一个数据卷、同一套账号与网络**上，
两个库由 `sync` 服务保持内容一致：

```
iD 编辑 → openstreetmap 库（唯一数据源）
        → osm_sync.py 按 changeset 增量导出 osmChange
        → osm2pgsql --append 写入 gis 库
        → 清 mod_tile 缓存 → 瓦片重新渲染
```

好处是 osm-carto4mc 侧**零改动**：`project.mml` 里写死的 `dbname: gis`、`SCHEMA='public'`、
`indexes.sql` / `functions.sql` / `common-values.sql`、外部 shapefile 全部继续按上游方式工作。

> 如果你确实想要**字面意义上的一个 database**（API 表在 `public`、`planet_osm_*` 在 `render` schema），
> 改动量也不大：把 `openstreetmap-carto-flex.lua` 的 `SCHEMA = 'public'` 改成 `'render'`，
> 给 renderd 连接设 `search_path`，三个 `.sql` 加 schema 限定。本仓库没有选择这条路，
> 因为它会牵动外部 shapefile 的 `schema: public` 配置，收益仅是「库少一个」。

---

## 2. 目录结构与对上游的改动

```
osm/
├── docker-compose.yml          # 统一编排（开发与生产共用这一份）
├── docker-compose.prod.yml     #   生产覆盖：RAILS_ENV、SECRET_KEY_BASE、启动命令
├── .env                        #   开发环境变量（默认加载）
├── .env.prod.example           #   生产环境变量模板（.env.prod 由脚本生成，含密钥）
├── README-COMBINED.md          # 本文档
├── COPYRIGHT.md                # 版权与开源许可声明（自撰部分 CC0，其余逐项列明）
├── tools/
│   ├── switch-env.sh           #   测试/生产环境切换（含 status/logs/restart/passwd）
│   ├── prod-init.rb            #   建管理员 + 注册该环境的 OAuth 标识
│   ├── reset-password.rb       #   重置某个账号的密码（passwd 子命令用）
│   ├── backup-db.sh            #   gis 库定时备份（配置在 backup/backup.conf）
│   ├── check-id-basemaps.sh    #   校验 iD 编辑器实际拿到哪些底图（含实取瓦片）
│   └── e2e-test.sh             #   端到端验证脚本
├── data/                       # 开发环境的运行时数据，可随时删除
│   ├── carto/                  #   编译出的 mapnik.xml，以及指向 osm-carto4mc 样式资源的
│   │                           #   symbols/ patterns/ 符号链接（下载的 shapefile 由
│   │                           #   get-external-data.py 导入后自行清理，不长期占盘）
│   ├── fonts/                  #   Noto 字体（两个环境共享）
│   ├── sync/                   #   导出产物：full.osm、changes.osc
│   ├── replication/            #   state.txt + NNNNNNNNN.osc.gz（标准复制格式）
│   ├── tiles/                  #   mod_tile 瓦片缓存
│   └── prod/                   #   生产环境同结构的独立数据目录
├── backup/                     # gis 库定时备份的产物（*.dump / *.sha256 / 日志，都被 gitignore）
│   └── backup.conf             #   备份周期、保留份数、目标环境等配置
├── sync/                       # 同步引擎（Python + osm2pgsql）
│   ├── osm_sync.py             #   导出 / 导入 / 增量 / 循环
│   ├── reliable_run.py         #   给上游下载脚本加超时与重试
│   ├── entrypoint.sh
│   └── Dockerfile
├── tileserver/                 # renderd + mod_tile + carto 编译
│   ├── renderd.conf
│   ├── apache-tiles.conf
│   ├── entrypoint.sh
│   └── Dockerfile
├── edge/                       # 唯一入口：反向代理（站点 + /tile/ + /tiles/）
│   ├── default.conf            #   按路径分流；内网穿透只需要映射它这一个端口
│   └── proxy-headers.conf
├── web/start.sh                #   web 容器启动包装：前端输入变了就重建 bundles
├── dyn2xyz/                    # Dynmap -> XYZ 瓦片转译代理（见第 10 节）
│   ├── app/                    #   geo/dynmap/render/config/server/main
│   ├── config/dyn2xyz.toml     #   瓦片来源与缩放范围（只读挂载进容器）
│   ├── tools/                  #   scan_extent.py / sidebyside.py
│   ├── tests/                  #   34 项单元测试 + 3 项联网验证
│   └── README.md               #   数值约定、缩放对齐推导、验证方法
├── openstreetmap-website/      # 上游仓库（6 处改动，见下）
└── osm-carto4mc/               # 上游 openstreetmap-carto 的只读克隆（未改动）
```

**对上游 `openstreetmap-website` 的改动**（都是「变成可配置、默认行为不变」的性质）：

| 文件 | 改动 | 原因 |
|---|---|---|
| `config/layers.yml` | `tileUrl` 改为**根相对路径** `/tile/{z}/{x}/{y}.png`；**并删除 CyclOSM / CycleMap / TransportMap / Tracestrack / HOT / Shortbread / OpenMapTiles 等图层**，只留标准图层 | 私有实例只用自己渲染的瓦片；其余图层指向第三方或商业服务，与本实例数据无关。绝对地址（旧值 `http://localhost:8080/tile/...`）在别的设备上会指向那台设备自己的 localhost，见第 13 节 |
| `config/environments/production.rb` | `force_ssl` / `assume_ssl` 改为读 `Settings`，默认仍为 `true` | 生产环境默认强制 HTTPS，纯 HTTP 的私有实例会整站重定向到 https 而不可用 |
| `config/initializers/doorkeeper.rb` | `force_ssl_in_redirect_uri` 在「显式关闭 TLS（`force_ssl: false`）且目标是本实例自己的 host」时放行 | 否则纯 HTTP 的生产实例连自己的 OAuth 应用都注册不了 |
| `config/initializers/content_security_policy.rb` | `img-src` / `connect-src` 仍自动从 `config/layers.yml` **与 `config/id_imagery.yml`** 推导瓦片 origin（相对路径同源，由 `'self'` 覆盖） | 一旦打开 `csp_enforce`，写绝对地址的瓦片源仍会被自动放行，不必手改 CSP |
| `config/id_imagery.yml`（新增）+ `app/controllers/id_imagery_controller.rb`（新增）+ `config/routes.rb` | iD 编辑器的底图清单改为由本实例提供，只列本站真正有的底图 | iD 自带 1300+ 个第三方影像源（多数要 API key）；见第 10 节 |
| `app/views/site/id.html.erb` | 把 iD 的 asset map 里 `…/data/imagery.min.json` 一项改指到 `/id/imagery.json` | 这是让上面那份清单真正生效的接线处；iD 通过 asset map 解析自己的数据文件 |

`osm-carto4mc`（本地目录原名 `openstreetmap-carto`，2026-09-30 改名）完全未改动（`git status` 为空）；2026-09-30 向官方仓库核对过：`origin/master` 仍为 `1cc4b89c`（`v6.1.0-3`），本地即最新，无需拉取更新。
`config/database.yml` 与 `config/settings/production.local.yml` 也改过，但这两个文件在上游就是
gitignore 的本地文件，不算改动。

各组件（上游项目、镜像内的第三方软件、OpenStreetMap 数据、字体）的**版权与开源许可**逐项列在
[COPYRIGHT.md](COPYRIGHT.md) 里。三条要点先记在这里：

- 本仓库**自己写的**代码与文档按 **CC0-1.0** 完全开放；
- 但对 `openstreetmap-website` 的那 4 处改动是 GPL-2.0 的**衍生作品**，只能按 GPL-2.0 分发，
  不能用 CC0 覆盖；
- **数据不是 CC0**：`gis` 库、`backup/*.dump`、`data/` 里的 shapefile 都源自 OpenStreetMap，
  受 **ODbL-1.0** 约束；对外提供时必须保留地图上的 `© OpenStreetMap contributors`。

---

## 3. 快速开始

### 3.1 依赖项

**宿主上只需要下面这些。** 其余依赖（Ruby/Rails、Node、PostgreSQL + PostGIS、osm2pgsql、
Mapnik、renderd/mod_tile、carto、GDAL、字体……）全部由 Docker 在**构建镜像时**装好，
不需要你手工安装，也不需要污染宿主系统。

| 依赖 | 用来做什么 | 必需性 |
|---|---|---|
| **Docker Engine** ≥ 20.10 | 跑 `db` / `web` / `sync` / `tileserver` / `dyn2xyz` / `edge` 六个容器 | **必需** |
| **Docker Compose 插件版**（`docker compose`，v2 及以后） | 编排。本文件用了 `name:`、`depends_on: condition: service_healthy`、`up -d --wait`，**v1 的 `docker-compose`（1.29，Python 版）跑不起来** | **必需** |
| `docker-buildx` | `docker compose build` 需要它（新版 Compose 会检查 buildx 版本，缺失会直接报错）；只 `up` 已构建好的镜像时不需要 | 构建时需要 |
| `bash` + coreutils（`stat` `du` `timeout` `sha256sum`）+ util-linux（`flock`） | `tools/switch-env.sh`、`tools/backup-db.sh` | **必需**（基础系统自带） |
| `grep` / `sed` / `awk` | 脚本里解析配置与日志 | **必需**（基础系统自带） |
| `curl` | `switch-env.sh` 等待站点就绪、`tools/e2e-test.sh`、`tools/check-id-basemaps.sh` | **必需** |
| `git` | 取得两个上游源码目录（`openstreetmap-website`、`osm-carto4mc`） | 克隆/更新仓库时必需 |
| `python3` | `tools/e2e-test.sh`（算瓦片坐标）、`tools/check-id-basemaps.sh`、`mcmap/tools/mctile.py` | 建议 |
| `postgresql-client`（`psql` / `pg_restore`） | `tools/backup-db.sh` 优先用宿主 `pg_restore` 校准备份；**没有会自动退回容器里那个** | 可选 |
| `make` | **不需要**（只在注释里出现过） | — |

> **版本口径**：Compose 的「v2」现在版本号已经到 **5.x**（本机实测 `Docker Compose version 5.5.1`）。
> 别被版本号迷惑，判据是命令形式：**`docker compose`（空格，插件版）= 要的**；
> `docker-compose`（连字符，1.29 的 Python 版）= 不行。

> **磁盘与内存**：镜像约 **10 GB**，数据库卷约 **3 GB**（`gis` 库 1.4 GB 起步，随编辑增长），
> `data/` 约 100 MB（其中字体 98 MB，两个环境共享），构建缓存峰值另计。
> **建议预留 ≥ 30 GB 可用空间**。内存建议 ≥ 4 GB；紧张的话把 `.env` 里的
> `OSM2PGSQL_CACHE`（默认 1024 MB）调小。

#### Arch Linux

```bash
# Arch 的 docker-compose 就是 v2（当前 5.x）；python 包提供 python3
sudo pacman -S --needed docker docker-compose docker-buildx git curl python
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # 重新登录（或 newgrp docker）后生效
```

#### Debian / Ubuntu

**Debian 13 (trixie) 及以上、Ubuntu 24.04 及以上**，直接用发行版自带的包：

```bash
# Debian 13+ ：注意 Debian 12 及更早只有 compose v1（1.29.2），不能用这条
#              docker-buildx 视仓库是否有该包决定装不装
sudo apt update && sudo apt install -y docker.io docker-compose git curl python3
sudo apt install -y docker-buildx 2>/dev/null || true

# Ubuntu 24.04+ ：包名不同 —— docker-compose-v2 / docker-buildx
#                 （别装成 docker-compose，那是 1.29 的 v1）
sudo apt update && sudo apt install -y docker.io docker-compose-v2 docker-buildx git curl python3

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # 重新登录后生效
```

> 发行版自带包的好处是不用加第三方源；如果 `docker compose version` 报找不到
> compose 插件（发行版把插件装到了别处，或你要更新版本），直接改用下面的官方源。

**Debian 12 (bookworm) 以及任何想要更新版本的场合**，走 Docker 官方源
（这条路 Debian / Ubuntu 通用，并且一次性把 compose 插件和 buildx 都装上）：

```bash
sudo apt update && sudo apt install -y ca-certificates curl gnupg git python3
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
                    docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"      # 重新登录后生效
```

> Ubuntu 用官方源时，把上面**两条 URL 里的 `debian` 换成 `ubuntu`**
> （`download.docker.com/linux/ubuntu/gpg` 与 `.../linux/ubuntu` 各一处）。

#### WSL2（Ubuntu / Debian）

WSL2 上有两条路，选一条。

**路线 A（Windows 桌面用户推荐）：用 Windows 里的 Docker Desktop**

在 Windows 装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)，
然后在 Settings → Resources → **WSL Integration** 里打开你的发行版。
WSL 里**不用装 docker**，只要补齐其它依赖：

```bash
sudo apt update && sudo apt install -y git curl python3
# 直接用 Windows 那侧的守护进程：
docker version && docker compose version
```

**路线 B：在 WSL 里原生装 Docker Engine**

先按上面 Debian/Ubuntu 的步骤装好，再处理 WSL 特有的两点：

```bash
# WSL2 默认没有 init 系统，systemctl 会不可用 —— 先打开 systemd
# 用 tee -a 追加：/etc/wsl.conf 里可能已经有 [automount] / [interop] 之类的配置
printf '\n[boot]\nsystemd=true\n' | sudo tee -a /etc/wsl.conf
# 然后在 Windows 里执行 wsl --shutdown，重新打开终端，再：
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

不支持 systemd 的旧版本，就每次开机手动拉起来（也可以写进 `/etc/wsl.conf` 的 `[boot] command=`）：

```bash
sudo service docker start        # WSL 每次重启后都要执行一次
```

**WSL 上必须注意的三件事**（踩了很难查）：

1. **仓库要放在 Linux 文件系统里**，例如 `~/osm`。放在 `/mnt/c/...` 下会走 9p 文件系统：
   绑定挂载、文件监听、权限位都又慢又容易出错，Rails 的资产编译会慢到不可用。
2. **调大 inotify 上限**，否则 Sprockets 会报 `ENOSPC`：
   ```bash
   echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-osm.conf
   sudo sysctl --system
   ```
3. **端口与磁盘**：WSL2 默认把 `localhost` 转发到 Windows，浏览器直接用
   `http://localhost:3000/` 即可；要给局域网其它设备访问见第 13 节。镜像和数据都占 WSL 的
   `ext4.vhdx`（只涨不缩），空间不够时用 `docker system prune -a` 清构建缓存。

> ArchWSL 同理：`pacman` 那段照用，上面 systemd / 文件系统 / inotify 三条注意事项一样适用。

#### 装完之后（所有发行版通用）

```bash
# 让 docker 组权限生效：注销重登；或临时执行 newgrp docker（会开一个新的子 shell）
newgrp docker

# 自检：下面每条都必须有输出
docker version --format '{{.Server.Version}}'   # ≥ 20.10
docker compose version                          # 应是 docker compose（不是 docker-compose）
git --version && curl --version | head -1

# 仓库自带的检查：会报告当前跑的是哪套环境、站点/瓦片是否就绪
cd /home/aoiaihana/osm
./tools/switch-env.sh status
```

### 3.2 启动

```bash
cd /home/aoiaihana/osm

# 1) 构建镜像（web/db 复用已有的 openstreetmap-website 镜像，只构建 sync、tileserver 与 dyn2xyz）
docker compose build sync tileserver dyn2xyz

# 2) 启动全部服务（edge 用的是公共镜像 nginx:alpine，首次会 pull 下来）
docker compose up -d

# 3) 观察首次 bootstrap：全量导出 → osm2pgsql 导入 → 索引/函数 → 字体 → 外部 shapefile
docker compose logs -f sync
```

启动后**只有一个入口**需要记住：

| 地址 | 内容 |
|---|---|
| `http://localhost:3000/` | 站点 / API / iD 编辑器（= `edge` 反代） |
| `http://localhost:3000/tile/{z}/{x}/{y}.png` | osm-carto4mc 底图瓦片（`edge` → tileserver） |
| `http://localhost:3000/tiles/{z}/{x}/{y}.png` | Minecraft dyn2xyz 瓦片（`edge` → dyn2xyz） |
| `http://localhost:3000/tilejson.json` | dyn2xyz 的 TileJSON（QGIS / MapLibre 可直接导入） |

站点页面里的瓦片地址是**根相对路径**，浏览器按当前 origin 解析，所以
`http://localhost:3000/`、`http://<局域网IP>:3000/`、内网穿透出来的域名三者**共用同一份配置**，
而且外部访问只需要穿透这一个端口——详见第 13 节。

`http://localhost:3001/`（`WEB_PORT`）是 Rails 的直连端口，仅供调试：瓦片是相对路径，
从这里打开页面地图是空的，属预期。

首次启动 `sync` 会做一次 **bootstrap**：先下载 Noto 字体（约 100 MB），再下载
Natural Earth / 水体 shapefile（约 1 GB）并导入。视网络情况需要十几分钟。
在此之前网站可正常访问，瓦片是空白的，属于预期。
（字体目录已在有字体时会自动跳过下载，见第 8 节的 `FONTS_MIN_FILES`。）

### 3.3 注意事项

- 如果 `docker` 命令报 `read-only file system`（例如在受限沙箱里运行），把 CLI 的配置目录
  指到工作区内即可：`export DOCKER_CONFIG=/home/aoiaihana/osm/.docker`
  （本仓库的 `tools/e2e-test.sh` 与 `tools/switch-env.sh` 会自动检测并使用它。）
- **不要**同时运行 `openstreetmap-website/docker-compose.yml`：它和本编排使用同一个数据卷和
  同一个宿主端口 `54321`。本编排已把该 compose 的两个镜像打上 `osm-{db,web}:latest` 标签复用，
  并把它的三个卷按名字固定下来，所以**原有的 API 数据库、用户与 OAuth 应用都会保留**
  （启动时可能打印一条 `volume ... already exists but was created for project openstreetmap-website`
  警告，属预期，可忽略）。

---

## 4. 测试环境 与 生产环境 切换

切换统一用 `tools/switch-env.sh`。两个环境共用同一份 `docker-compose.yml`，靠
**compose 项目名 + 环境变量文件** 区分，脚本负责停掉另一边、启动目标环境、等待站点就绪并报告结果。

```bash
./tools/switch-env.sh                    # 不带参数 = status
./tools/switch-env.sh status             # 两个环境各自在跑什么、地址是什么
./tools/switch-env.sh test               # 切到测试环境（别名 dev，两者等价）
./tools/switch-env.sh prod               # 切到生产环境
./tools/switch-env.sh prod --init        # 首次初始化生产：建库、建管理员、注册 OAuth 标识
./tools/switch-env.sh restart <env> [服务...]
./tools/switch-env.sh logs <env> [服务...]
./tools/switch-env.sh passwd <env> [用户名] [密码]   # 重置密码，省略则随机生成并打印
./tools/switch-env.sh down               # 停掉两个环境（数据与卷不动）
./tools/switch-env.sh help
```

说明：

- **`test` 与 `dev` 是同一个环境**（测试环境的 Rails 跑在 `development` 模式），两个写法到处都接受。
- 每次切换都会**等待站点真正应答**（轮询 `/api/0.6/capabilities`）再返回，并在结尾打印
  站点地址、瓦片地址和瓦片状态，所以脚本退出码是有意义的，可以直接用在自动化里。
  等待上限由 `WAIT_TIMEOUT` 控制（默认 600 秒——生产首次启动要预编译资源并迁移数据库）。
- 缺少镜像时会自动构建 `sync` / `tileserver`；`.env.prod` 不存在时会从
  `.env.prod.example` 生成并写入随机 `SECRET_KEY_BASE`。
- `status` 会明确区分「未运行」「未初始化」，不会再让人猜。

底层就是这两条命令（脚本只是把它们包起来并加了顺序处理）：

```bash
# 测试
docker compose -p osm up -d
# 生产
docker compose -p osm-prod --env-file .env.prod \
    -f docker-compose.yml -f docker-compose.prod.yml up -d
```

两个环境**故意使用相同的宿主端口**（3000/8080/54321），因此是「切换」而不是「并行运行」——
脚本每次都会先把另一边停掉。如果确实要同时跑，改 `.env.prod` 里的
`WEB_PORT` / `TILE_PORT` / `DB_PORT`，并同步改 `config/layers.yml` 的 `tileUrl`。

### 哪些东西是按环境隔离的

| | 开发/测试 | 生产 |
|---|---|---|
| compose 项目 | `osm` | `osm-prod` |
| 环境变量文件 | `.env` | `.env.prod`（gitignore，含密钥） |
| PostgreSQL | 容器 `osm-db-1` + 卷 `openstreetmap-website_db-data` | 独立容器 + 卷 `osm-prod-db-data` |
| API/渲染库 | `openstreetmap` / `gis` | 同样库名，但在**独立实例**里 |
| 运行时数据 | `./data/{carto,sync,replication,tiles}` | `./data/prod/...` |
| 字体 | `./data/fonts` | **共享**同一个目录（内容相同，省一次 100 MB 下载） |
| `RAILS_ENV` | `development` | `production` |
| tmp / storage | 卷 `openstreetmap-website_web-{tmp,storage}` | 卷 `osm-prod-web-{tmp,storage}` |
| 设置文件 | `openstreetmap-website/config/settings.local.yml` | `openstreetmap-website/config/settings/production.local.yml` |
| 密钥 | 不需要 | `SECRET_KEY_BASE`（写进 `.env.prod`） |

两个数据库/卷都保留，**来回切换不会丢数据**。

### 在环境之间复制数据（`tools/copy-env-data.sh`）

把一个环境的**内容数据**搬到另一个环境（默认 test → prod），源环境的数据原样保留：

```bash
./tools/copy-env-data.sh                 # 只读：两个环境各有多少数据
./tools/copy-env-data.sh run             # 执行复制，复制完自己选什么时候起服务
./tools/copy-env-data.sh run --start     # 复制完顺手把目标环境拉起来
./tools/copy-env-data.sh run --from prod --to test     # 反方向也行
```

复制四样东西：

| 复制什么 | 说明 |
|---|---|
| API 库 `openstreetmap` | 用户、changeset、elements 全版本历史、current_* 表、notes、令牌…… |
| 渲染库 `gis` | `planet_osm_*`、`external_data`，以及 **`osm_sync_state`**（同步水位线就在这个库里，所以水位线自动跟着走，复制完不会重新 bootstrap） |
| `./data/<源>/replication` → `./data/<目标>/replication` | `state.txt` + `NNN.osc.gz`；不搬的话目标那边的序号会断档 |
| 清空 `./data/<目标>/tiles` | mod_tile 缓存是按旧数据渲染的，不清就会继续端出旧图 |

**两样东西故意不覆盖**：

- **目标环境自己的 OAuth 应用**：复制前先按名字存下目标的那几条（`Local iD`、`OpenStreetMap Web Site`），
  恢复完再替换同名行。所以 `config/settings/<env>.local.yml` 里那三个 id/secret 依旧有效，
  JOSM 之类的外部客户端不用重配。源环境里目标没有的应用（例如 `railway-import`）作为数据照常复制过来。
  想改成「和目标完全一致」就复制完跑 `./tools/switch-env.sh prod --init`。
- **目标的设置文件**（`config/settings/<env>.local.yml`）：`server_url` / TLS 开关这些是环境自身的属性。

其他要点：

- **源环境只被 `pg_dump` 读**，从头到尾不写一个字，所以源的数据、卷、OAuth 标识都保持原样；
  脚本会在复制前后各打印一遍源的计数，方便对照。
- 两个环境共用宿主端口，所以中途会**先停源栈**（数据在卷里，不受影响）、起目标库、恢复完再按需启动。
- `mapnik.xml` 不搬：tileserver 每次启动都用 `carto` 重新编译，而且生成物里有指向 checkout 的符号链接。
  字体目录本来就是共享的。
- ⚠️ **目标的两个库是被整个 drop + 重建的**，它原来的内容不会留底。装不下两边数据时别拿它当备份工具，
  渲染库的定期备份还是用 `tools/backup-db.sh`（第 9 节）。
- 中转 dump 默认留在 `backup/copy-<时间戳>/`（本次约 1.2 GB），确认目标没问题后自己删；
  加 `--no-dumps` 可以让脚本复制成功后直接删掉。

### 新增或更换标识（OAuth client id / secret）

这是唯一需要动「文件」的地方，因为**含下划线的设置项无法用环境变量覆盖**：
本项目给 `config` gem 设了 `env_separator = "_"`，所以
`OPENSTREETMAP_SERVER_URL` 会被解析成 `{"server" => {"url" => ...}}` 而不是 `server_url`，
`OPENSTREETMAP_ID_APPLICATION` 同理。我实测确认过：设置环境变量后 `Settings.id_application`
读到的仍是文件里的旧值。**`server_url`、`id_application`、`oauth_application`、`oauth_key`
这类名字里带下划线的项，必须写进设置文件。**

`config` gem 的加载顺序（后者覆盖前者）是：

```
config/settings.yml
config/settings/<environment>.yml
config/environments/<environment>.yml
config/settings.local.yml              ← 上游惯例，开发环境用这个
config/settings/<environment>.local.yml ← 生产环境用这个，优先级更高
```

所以：

- **开发**：`openstreetmap-website/config/settings.local.yml`（上游 `dev:populate` 生成的那个）
- **生产**：`openstreetmap-website/config/settings/production.local.yml`

两个文件都被上游 `.gitignore` 忽略（`config/settings.local.yml` 与 `config/settings/*.local.yml`），
密钥不会被提交。

生产环境的标识由 `tools/prod-init.rb` 生成，它对**当前环境**生效且可重复执行：

```bash
./tools/switch-env.sh prod --init
# 或者只重跑「建管理员 + 注册标识」这一步：
docker compose -p osm-prod --env-file .env.prod \
    -f docker-compose.yml -f docker-compose.prod.yml \
    run --rm -T web bundle exec rails runner - < tools/prod-init.rb
```

它做的事：建/更新管理员（密码可用 `ADMIN_PASSWORD` 指定，否则随机生成并打印一次）→
按名字复用或新建 `Local iD` 与 `OpenStreetMap Web Site` 两个 OAuth 应用 →
把 `id_application` / `oauth_application` / `oauth_key` 写进该环境的设置文件
（**就地改写对应行，不会丢掉你写的注释和其它设置**）。用它可以随时更换标识，且**可重复执行**。

账户密码的行为要特别注意（这里踩过一次坑）：**`ADMIN_PASSWORD` 只在账号还不存在时生效**，
账号已存在时脚本原样保留密码并给出提示。凭据块**只在密码真的被写入时才打印**——
早期版本无论是否写入都会打印一组随机密码，而那组密码根本没被应用，
照着它登录会直接失败（生产环境的初始管理员密码就这样丢过一次）。
要改密码请用：

```bash
./tools/switch-env.sh passwd prod              # 随机生成并打印
./tools/switch-env.sh passwd prod admin 'xxx'  # 指定密码
```

有一个细节值得知道：Doorkeeper 的应用密钥是**哈希存储**的
（`Doorkeeper.config.application_secret_strategy` = `Sha256Hash`，不可还原），
所以 `oauth_key` 只在应用**创建的那一刻**能取到。脚本对已存在的应用会**保留**设置文件里的
旧密钥而不是覆盖成空值；确实要换新密钥时用：

```bash
docker compose -p osm-prod --env-file .env.prod \
    -f docker-compose.yml -f docker-compose.prod.yml \
    run --rm -T -e ROTATE_SECRETS=true web bundle exec rails runner - < tools/prod-init.rb
```

它会删除并重建这两个应用（client id 会变，旧令牌全部失效），并把新的 client id 与密钥写进设置文件。

管理员密码：`ADMIN_PASSWORD` 只在**创建**账号时生效；账号已存在时会原样保留并在日志里给出提示。
要显式改密码，加上 `RESET_PASSWORD=1`：

```bash
docker compose -p osm-prod --env-file .env.prod \
    -f docker-compose.yml -f docker-compose.prod.yml \
    run --rm -T -e RESET_PASSWORD=1 -e ADMIN_NAME=admin -e ADMIN_PASSWORD='<新密码>' \
    web bundle exec rails runner - < tools/prod-init.rb
```

（`RESET_PASSWORD` 在没有显式给出 `ADMIN_PASSWORD` 时会被忽略，避免把账号改成一个人人不知的随机密码。）

`tools/prod-init.rb` 被刻意限制为**不在 development 环境运行**：开发环境没有自己的
`server_url`（会落到 `openstreetmap.example.com` 占位值），跑它会把开发实例正在使用的
OAuth 应用 redirect URI 改坏；开发环境请用上游的 `rails dev:populate` 与
`rails oauth:register_apps[...]`。确实需要时可用 `ALLOW_DEVELOPMENT=1` 绕过该限制。

### 上生产需要注意的三件事

1. **`SECRET_KEY_BASE`**：这个 checkout 里没有 `config/credentials.yml.enc`，所以生产环境必须
   由环境变量提供，否则 Rails 拒绝启动。`switch-env.sh` 首次运行时会自动生成写进 `.env.prod`。
2. **TLS**：上游 `config/environments/production.rb` 默认 `force_ssl = true` + `assume_ssl = true`，
   纯 HTTP 访问会被 301 重定向到 https 并且 cookie 带 secure 标记，**站点直接不可用**。
   现在可以按环境覆盖（已改成读 `Settings`，默认值不变）——在内网纯 HTTP 部署时写：

   ```yaml
   force_ssl: false
   assume_ssl: false
   ```

   真的对外提供服务时应当保持 `true` 并在前面挂 TLS 终结（nginx/caddy）。
3. **`server_url` / `server_protocol`**：必须写成用户实际访问的地址，
   它决定站点生成的绝对链接和 **OAuth 应用的 redirect URI**。
   改完地址后要重跑 `./tools/switch-env.sh prod --init` 让 redirect URI 跟着更新。

   另外一个上游约束值得知道：`force_ssl_in_redirect_uri` 规定「非 development 且 host 不是
   回环地址时必须 HTTPS」。所以纯 HTTP 的生产实例，若 `server_url` 用的是局域网 IP，
   OAuth 应用会因为 redirect URI 不是 HTTPS 而校验失败。本仓库已把这条规则放宽为
   「显式关闭 TLS 且目标就是本实例自己的 host 时放行」（默认 `force_ssl: true` 时行为与上游一致）。

---

## 5. 验证清单

```bash
# 服务状态
docker compose ps

# 同步状态（水位线、已应用批次数、渲染库是否就绪）
docker compose run --rm sync status

# 两个库确实在同一个 PostgreSQL 实例里
docker compose exec db psql -U postgres -c '\l'

# gis 库里是否已经有渲染要素与外部数据
docker compose exec db psql -U postgres -d gis \
  -c 'select count(*) from planet_osm_line;' \
  -c 'select count(*) from planet_osm_polygon;' \
  -c 'select name from external_data;'

# 入口是否活着（站点 + 两条瓦片链路都走 3000）
curl -sS -o /dev/null -w 'site %{http_code}\n' http://localhost:3000/
curl -sS -o /tmp/tile.png -w 'tile %{http_code} %{size_download}\n' \
  http://localhost:3000/tile/0/0/0.png
# Minecraft 底图（dyn2xyz）是否出图 —— 首次请求会去上游现抓，可能要几秒到几十秒
curl -sS -o /tmp/mc.png -w 'mc   %{http_code} %{size_download}\n' \
  http://localhost:3000/tiles/15/16384/16384.png
# 绕过 edge 直连瓦片服务（排查 edge 还是瓦片服务的问题时用）
curl -sS -o /dev/null -w 'direct tile %{http_code}\n' http://localhost:8080/tile/0/0/0.png
curl -sS -o /dev/null -w 'direct mc   %{http_code}\n' http://localhost:8090/tiles/15/16384/16384.png

# iD 编辑器的底图清单（应当只有 OSM Standard 与 Minecraft 两项会显示）
curl -sS http://localhost:3000/id/imagery.json

# 完整校验 iD 底图链路（含 asset map 改写、CSP、实取瓦片）
OSM_COOKIE_JAR=/tmp/cj.txt ./tools/check-id-basemaps.sh
```

### 端到端验证脚本

`tools/e2e-test.sh` 走**完整链路**：用真实 OAuth 令牌调用 OSM API（即 iD 保存时走的同一套
HTTP 接口）创建 changeset + 一个 `amenity=pub` 节点，然后等待同步把它推进 `gis`，最后请求
覆盖该节点的瓦片。

```bash
# 生成本地开发用令牌
TOKEN=$(docker compose exec -T web bundle exec rails runner - <<'RUBY' | tail -1
app = Oauth2Application.find_by(uid: Settings.id_application)
user = User.find_by(display_name: "mapper")
puts Doorkeeper::AccessToken.create!(application: app, resource_owner_id: user.id,
      scopes: "write_api read_prefs", expires_in: 3600).token
RUBY
)

TOKEN="$TOKEN" ./tools/e2e-test.sh            # 创建并验证
TOKEN="$TOKEN" ./tools/e2e-test.sh --cleanup  # 删除测试节点
```

本仓库已在 null-island 测试数据上跑通该脚本，删除路径（`<delete>` 增量）也验证过。
默认账号：`aoiaihana` / `mapper`，密码均为 `password`。

对生产环境跑同一个脚本，加上 `OSM_ENV=prod`（它只是把底层 `docker compose` 换成生产那个项目）：

```bash
OSM_ENV=prod TOKEN="$PROD_TOKEN" ./tools/e2e-test.sh
```

两个环境都实测通过：开发环境验证了「建节点 → 同步 → 出图」与「删节点 → 同步 → 消失」；
生产环境验证了「全新初始化 → 建节点 → 同步 → 出图」，并且确认两边的数据、标识、卷互不影响。

---

## 6. 同步原理（`sync/osm_sync.py`）

**水位线**：`changesets.closed_at`。rails port 里 changeset 打开期间 `closed_at` 被设成**未来时间**
（`Changeset#update_closed_at`），关闭时才落成真实时间。因此 `closed_at <= now()` 恰好只选中已关闭的
changeset —— iD 保存时会立即关闭 changeset，编辑随即可见。水位线用 `(closed_at, id)` 复合值，
避免同一毫秒并列被漏掉。

**增量导出**：对每批 changeset，从 `nodes`/`ways`/`relations` 全版本历史表按 `changeset_id`
取每个要素的**最新版本**（`DISTINCT ON ... ORDER BY id, version DESC`），再按版本号取对应的
`*_tags`、`way_nodes`、`relation_members`，写成标准 `<osmChange>`：

| 动作 | 判定条件 | 输出顺序 |
|---|---|---|
| `create` | `visible AND version = 1` | node → way → relation |
| `modify` | `visible AND version > 1` | node → way → relation |
| `delete` | `NOT visible` | relation → way → node |

先取最新版本再按动作过滤，保证「同一批里先新建后删除」的要素只判定为删除。
`changeset_id` 在历史表上有索引（`nodes_changeset_id_idx` 等），查询不退化。

**应用**：`osm2pgsql --append --slim`。bootstrap 时刻意**不加 `--drop`**，因为 `--append`
依赖 `--slim` 留下的中间表（carto 上游的 `docker-startup.sh` 用 `--slim --drop`，只适合一次性导入）。
导入与追加的选项必须一致，osm2pgsql 会校验 `osm2pgsql_properties`。

**失败安全**：osm2pgsql 失败时**不推进水位线**，下一轮用同一批重试；异常被 `loop` 捕获，服务不退出。

**复制目录**：每批同时在 `data/replication/` 写入标准 `state.txt` 与 `NNNNNNNNN.osc.gz`。
本编排直接消费本地文件，但这是标准复制流；若想让别的机器用 `osm2pgsql-replication` 跟随，
把该目录用 HTTP 暴露即可。不需要就设 `KEEP_REPLICATION_FILES=false`。

**瓦片失效**：应用新数据后清空 `data/tiles/`（`PURGE_TILES_ON_SYNC=false` 可关闭），
mod_tile 在下次请求时按需重渲染。

**下载健壮性**：上游 `get-fonts.py` / `get-external-data.py` 的 HTTP 请求**没有超时**，
一条卡住的 TCP 连接会让它们永久挂起（本机实测到对 `raw.githubusercontent.com` 的
CLOSE_WAIT 死连接），而且 `get-fonts.py` 一旦某个字体失败就整轮退出。为此
`sync/reliable_run.py` 在进程内给 `requests` 注入默认超时与逐请求重试/退避，
`osm_sync.py` 再对整个脚本加超时与整轮重试。相关环境变量见 `.env`。

---

## 7. 瓦片链路要点（website + `tileserver/`）

从 `layers.yml` 到浏览器里的一张瓦片，中间有几个不直观的地方。下面每一条都是实测踩出来的，
改配置时别踩回去：

1. **`AddTileConfig` 而不是 `LoadTileConfigFile`**。`LoadTileConfigFile` 只按名字注册瓦片集，
   **不会**读取 `[default]` 段的 `uri`/`minzoom`/`maxzoom`，日志会打印
   `Loading tile config default at  for zooms 0 - 20`（URI 为空），所有瓦片返回 Apache 404。
   改用 `AddTileConfig /tile/ default` 后日志为
   `Loading tile config default at /tile/ for zooms 0 - 20`。
2. **资源相对路径**。carto 生成的 XML 里符号路径是相对 `project.mml` 的（`symbols/...`、`patterns/...`），
   而 Mapnik 按**样式表所在目录**解析。因为编译输出在 `/data/mapnik.xml` 而不是 osm-carto4mc 仓库里，
   entrypoint 会把 XML 中引用到的每个顶层目录软链到 `/data/` 旁边（当前只有 `symbols` 和 `patterns`）。
   少了这步会报 `file could not be found: '/data/symbols/salt-dots-2.png'`，瓦片全部失败。
3. **字体**。Mapnik 只要**一个都解析不到**样式里请求的字体就会整体报
   `no valid fonts could be loaded in FontSet`，从而拒绝加载样式。因此镜像内置了发行版的
   `fonts-noto-{core,extra,cjk,color-emoji,unhinted}` 作为保底；`sync` 另外下载项目期望的完整字体集
   到 `./data/fonts`（挂载为 `/usr/share/fonts/truetype/osm-carto`）用于提升文字覆盖度。
   entrypoint 只在两者都不可用时才等待。
4. **DB 连接**：`project.mml` 里只写了 `dbname: gis`，没有 host/user。Mapnik 走 libpq 默认值，
   所以靠容器环境变量 `PGHOST=db`、`PGUSER`、`PGDATABASE` 生效。
5. **同源与 CORS**：页面里的瓦片地址是根相对路径，经 `edge` 反代后与站点**同源**，
   所以浏览器既不触发跨域、也不触发 CSP；`apache-tiles.conf` 里的
   `Access-Control-Allow-Origin: *` 现在只对「绕过 edge 直连 8080」的用法有意义
   （那属于跨域），留着不碍事。
6. renderd 启动时创建 socket 后，entrypoint 会 `chmod 0777`，否则以 `www-data` 运行的 Apache 连不上。
7. **renderd 只在启动时加载一次地图，而且加载时就会校验每个图层的 datasource。**
   如果它在 `sync` 把外部 shapefile 导入完之前启动，Mapnik 会报
   `relation "icesheet_polygons" does not exist` 之类的错误并放弃加载地图，而 renderd 进程
   依然存活、日志里也看不到后续报错 —— 结果是**这张地图的所有瓦片永久返回 Apache 404**。
   开发环境当初是因为「先启动、后导入、再手动重启」而侥幸绕过了，生产环境第一次就踩到了。
   现在 entrypoint 会先用 `psql` 等到 `osm_sync_state.bootstrap_ts` 被写入才启动 renderd
   （若目标库里没有 `osm_sync_state` 表，说明不是由 sync 管理的库，只要求 `planet_osm_*` 存在）。
   等待上限是 `RENDER_WAIT_TIMEOUT`（默认 1800 秒）。
8. **前端图层定义是编译期内联的，改 `layers.yml` 不一定立刻生效。**
   `app/assets/javascripts/osm.js.erb` 里有这么一行：

   ```erb
   LAYER_DEFINITIONS: <%= MapLayers::full_definitions("config/layers.yml", ...).to_json %>,
   ```

   也就是说**瓦片地址是在 Sprockets 编译前端 bundle 时被写死进去的**，跟 `/panes/layers`
   那个请求期读取无关。`osm.js.erb` 顶部确实声明了 `//= depend_on layers.yml`、
   且 `config/` 也在资源加载路径里（`config/initializers/assets.rb`），**依赖追踪是有效的**；
   但这个 checkout 走的是 `public/assets` 里预编译好的那一套、靠 `tmp/manifest.json`
   解析摘要，而 manifest 只有显式跑 `assets:precompile` 才会更新。
   一旦漏掉这一步，浏览器会继续加载旧 bundle —— **地图就一直用旧的瓦片地址**
   （对私有实例来说就是"看着像本地地图，其实还在拉官方瓦片"），而日志里毫无提示。
   `web/start.sh` 现在会在启动时比较 `layers.yml`/`legend.yml`/settings 与 manifest 的时间，
   并确认 manifest 指向的产物真的存在（两个环境共用一个 `public/assets`，被清过而 manifest
   残留时站点会缺 JS/CSS），需要时自动 `i18n export` + `assets:precompile`，
   所以正常重启即生效；`FORCE_ASSETS=1` 可强制重建。
9. **CSP**：瓦片经 `edge` 反代后与站点同源，根相对路径由 `'self'` 覆盖；
   但如果哪天又改成绝对地址（第三方瓦片源、或绕过 edge），`img-src`/`connect-src`
   必须包含那个 origin —— `content_security_policy.rb` 仍会从 `layers.yml` 与
   `id_imagery.yml` 自动推导，不需要手改。
   注意默认是 `Content-Security-Policy-Report-Only`（`csp_enforce: false`），
   也就是默认**不会真的拦截**——所以它不会是你看到官方瓦片的原因，但一旦打开强制就会全挂。
10. **瓦片地址必须是相对路径（或者必须让客户端能解析）**。这是私有实例最容易踩的一条，
    详细原因和排查放在第 13 节；一句话版本：**瓦片是浏览器去下载的**，
    页面上写 `http://localhost:8080/...`，在任何别的设备上都指向那台设备自己。

---

## 8. 常用运维命令

下面的命令默认作用于**测试**环境（compose 项目 `osm`）。要在生产上做同样的事，用
`./tools/switch-env.sh restart prod` / `logs prod`，或把 `docker compose` 换成：

```bash
docker compose -p osm-prod --env-file .env.prod \
    -f docker-compose.yml -f docker-compose.prod.yml ...
```

```bash
# 环境切换与状态
./tools/switch-env.sh status
./tools/switch-env.sh test | prod | prod --init
./tools/switch-env.sh restart prod web     # 只重启某个服务
./tools/switch-env.sh logs test sync       # 跟踪日志
./tools/switch-env.sh passwd prod          # 重置管理员密码并打印新密码
./tools/switch-env.sh down                 # 停掉两个环境

# 环境之间复制数据（源不变；目标是整个 drop + 重建那两个库）
./tools/copy-env-data.sh status            # 只读：两边各有多少数据
./tools/copy-env-data.sh run --start       # test -> prod，复制完起生产

# 状态 / 手动同步一次 / 强制全量重建渲染库
docker compose run --rm sync status
docker compose run --rm sync update
docker compose run --rm sync bootstrap

# 只跑 bootstrap 的某一步（便于失败后补齐）
docker compose run --rm sync fonts
docker compose run --rm sync external-data

# 只导出 XML 不导入（调试），产物在 data/sync/full.osm
docker compose run --rm sync export

# 跳过体积很大的外部数据 / 字体，或强制重新下载字体
docker compose run --rm sync bootstrap --no-load-external-data --no-load-fonts
docker compose run --rm sync fonts --force-fonts

# 进数据库
docker compose exec db psql -U postgres -d openstreetmap   # API 库
docker compose exec db psql -U postgres -d gis             # 渲染库

# 改了 project.mml / *.mss 之后重编译样式
docker compose restart tileserver

# 彻底重建渲染库（sync 会重新 bootstrap）
docker compose exec db psql -U postgres -c 'DROP DATABASE gis WITH (FORCE);'
docker compose restart sync

# 渲染库的定时备份（详见第 9 节）
./tools/backup-db.sh status | now | run | list | prune
```

### `.env` / `.env.prod` 可调项

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEB_PORT` | 3000 | 网站端口 |
| `TILE_PORT` | 8080 | 瓦片服务宿主端口（**改了要同步改 `layers.yml` 的 `tileUrl`**；容器内固定 8080） |
| `DB_PORT` | 54321 | PostgreSQL 宿主端口 |
| `SYNC_INTERVAL` | 60（生产 30） | 增量同步间隔（秒） |
| `OSM2PGSQL_CACHE` / `OSM2PGSQL_NUMPROC` | 1024 / 4 | 导入内存与并行度 |
| `BOOTSTRAP` | auto | `auto` 首次自动 bootstrap，`never` 只同步，`force` 每次启动都重建 |
| `LOAD_EXTERNAL_DATA` / `LOAD_FONTS` | true | bootstrap 时是否下载外部数据/字体 |
| `PURGE_TILES_ON_SYNC` | true | 应用新数据后是否清瓦片缓存 |
| `KEEP_REPLICATION_FILES` | true | 是否输出标准 `state.txt` / `*.osc.gz` |
| `FONT_DOWNLOAD_TIMEOUT` / `EXTERNAL_DATA_TIMEOUT` | 1800 / 7200 | 单个下载脚本整轮超时（秒） |
| `DOWNLOAD_ATTEMPTS` | 3 | 单个下载脚本整轮重试次数 |
| `DOWNLOAD_REQUEST_TIMEOUT` / `DOWNLOAD_REQUEST_RETRIES` | 60 / 5 | 单次 HTTP 请求超时（秒）与重试次数 |
| `FONTS_MIN_FILES` | 40 | 字体目录里已有这么多文件就跳过字体下载（多环境共享字体目录时很有用） |
| `FORCE_FONTS` | false | 置 true 则忽略上面的判断，强制重新下载字体 |
| `RENDER_WAIT_TIMEOUT` | 1800 | tileserver 启动前等待 sync 完成 bootstrap 的上限（秒） |
| `DB_VOLUME` / `WEB_TMP_VOLUME` / `WEB_STORAGE_VOLUME` | 开发卷名 | 按环境隔离存储的关键，生产在 `.env.prod` 里覆盖 |
| `DATA_DIR` / `FONTS_DIR` | `./data` / `./data/fonts` | 运行时数据目录；生产用 `./data/prod` 但共享字体 |
| `SECRET_KEY_BASE` | 仅生产 | 生产必需，由 `switch-env.sh` 生成到 `.env.prod` |
| `ADMIN_NAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` | admin / … / 随机 | 交给 `tools/prod-init.rb` 建首个管理员 |

---

## 9. 渲染库备份

`tools/backup-db.sh` 把 **`gis` 渲染库**（osm2pgsql + osm-carto4mc 那一侧）
按周期 dump 到工作区的 `backup/` 目录。

只备份 `gis`，不碰 rails 的 `openstreetmap` API 库：前者是渲染数据与外部
shapefile，后者是账号 / 编辑数据，两者的恢复方式完全不同（API 库的备份还没做，
见第 11 节）。

```bash
./tools/backup-db.sh status          # 配置、当前在跑的环境、已有备份清单
./tools/backup-db.sh run             # 前台调度循环 —— 这就是「定时」
./tools/backup-db.sh now             # 立刻备份一次
./tools/backup-db.sh list            # 列出备份（新 -> 旧）
./tools/backup-db.sh prune           # 按 KEEP 立刻清理旧备份
./tools/backup-db.sh verify <文件>   # 校验归档目录 + sha256
./tools/backup-db.sh restore <文件>  # 只打印恢复步骤，不动数据
./tools/backup-db.sh help
```

备份**不需要停服务**：`pg_dump` 拿的是一致性快照。

`run` 是前台进程，**没有**装成 systemd 服务或 cron 任务（按需求「暂不接入守护
程序」）。要让它长期跑，两种方式：

```bash
# 方式一：nohup 常驻（日志本来就会写进 backup/backup.log）
nohup ./tools/backup-db.sh run >/dev/null 2>&1 &

# 方式二：交给 cron 定时调 `now`，就不用常驻进程了
#   crontab -e
#   0 */6 * * * cd /home/aoiaihana/osm && ./tools/backup-db.sh now >/dev/null 2>&1
```

配置在 `backup/backup.conf`（一个 shell 片段）。脚本**每一轮都会重新读它**，
所以改 `INTERVAL` / `KEEP` / `ENV_NAME` 都不用重启调度器（新周期从下一轮生效）。

| 键 | 默认 | 说明 |
|---|---|---|
| `INTERVAL` | `6h` | 备份周期，支持 `30s` / `10m` / `6h` / `1d`，纯数字按秒 |
| `RUN_ON_START` | `1` | 调度器启动时先立刻备份一次 |
| `ENV_NAME` | `auto` | `auto` 自动认当前在跑的环境；也可写死 `test` / `prod` |
| `BACKUP_DIR` | `backup` | 输出目录（相对仓库根，也可写绝对路径） |
| `GIS_DB` / `GIS_USER` | `gis` / `postgres` | 容器内的库名与角色 |
| `KEEP` | `0` | 只保留最新 N 个；`0` = 全部保留 |
| `EXCLUDE_EXTERNAL_DATA` | `0` | `1` = 不备份可重新下载的外部 shapefile |
| `COMPRESS` | `6` | `-Fc` 的 zlib 级别 |
| `DUMP_TIMEOUT` | `5400` | 单个 dump 的超时（秒），`0` = 不限 |
| `CHECKSUM` | `1` | 生成 `.sha256` |
| `LOG_FILE` | `backup/backup.log` | 额外写一份日志；留空则只输出终端 |
| `LOG_MAX_BYTES` | `5242880` | 日志轮转成 `.1` 的阈值，`0` = 不轮转 |
| `PROJECT_TEST` / `PROJECT_PROD` | `osm` / `osm-prod` | compose 项目名 -> 环境名 |

两个环境用的是同一个宿主端口 `54321`，光看端口分不出测试还是生产，所以
`ENV_NAME=auto` 是按 **compose 项目名**（label）找在跑的 db 容器来判定的。
文件名的环境名也由此而来：`gis-test-20260928-233500.dump`。

### 体积与耗时

本机 `gis` 库 1.4 GB，dump 出来约 1.2 GB、耗时约 2 分 40 秒。体积几乎全在**外部
shapefile** 上：

| 表 | 大小 | 来源 |
|---|---|---|
| `water_polygons` | 1266 MB | `osmdata.openstreetmap.de` |
| `icesheet_outlines` / `icesheet_polygons` | 83 MB / 71 MB | 同上 |
| `simplified_water_polygons` | 34 MB | 同上 |
| `planet_osm_*` / `osm_sync_state` 等 | 合计几百 kB | 真正随编辑变化的数据 |

那几张表 bootstrap 时由 `sync external-data` 生成，所以有两条省空间的路：

- **`KEEP=6`** —— 只留最近 6 份，最旧的自动删。只删本脚本自己生成的
  `gis-*.dump`（连同 `.sha256`），不会碰别的文件。
- **`EXCLUDE_EXTERNAL_DATA=1`** —— dump 从 1.2 GB 掉到 **83 KiB**、从 3 分钟
  掉到 1 秒以内。代价是恢复后要补跑一次 `sync external-data` 才能出图。

默认 `KEEP=0`（一份都不删）+ 不排除外部数据，即「完整但费空间」。想长期按小时
备份，建议至少加上其中一条。

### 为什么必须用容器里的 pg_dump

宿主（Debian 13 自带）的 `pg_dump` 是 18.6，而 `gis` 库跑在 `postgres:14`
容器里。用宿主的 `pg_dump` 会写出 archive version **1.16**，容器里的
`pg_restore` 14 直接拒绝：

```
pg_restore: error: unsupported version (1.16) in file header
```

也就是说备份文件「看起来是好的」，但**用本栈里的工具恢复不了**。所以脚本走
`docker exec <db容器> pg_dump`，用容器内 14.23 那个版本；这样产出的归档
`pg_restore` 14 和 18 都能读。

### 可靠性与锁

- 每个 dump 都先 `pg_restore -l` 校验归档目录，**校验通过才**改名为正式
  `*.dump`（先写 `backup/.tmp/*.partial`）。所以 `*.dump` 里不会混进坏文件；
  失败的半成品留在 `backup/.tmp/*.failed` 供排查，`list` 会提醒。
- dump 期间持有 `backup/.lock` 独占锁，调度器和手动 `now` 不会同时跑。
  调度器**睡觉时不持锁**，所以随时可以插一个 `now` 进来。
- 调度器把 pid 写到 `backup/.scheduler.pid`（退出时删除），`status` 靠它判断
  「是否正在运行」。重复启动会被拒绝。
- 收到 `Ctrl-C` / `SIGTERM` 会等当前 dump 结束再退出，不会留下半个文件。

### 恢复

`./tools/backup-db.sh restore <文件>` 会把完整步骤打印出来（只打印、不动数据），
要点：

```bash
./tools/switch-env.sh down                       # 1) 停掉写库的服务
docker exec osm-db-1 psql -U postgres -d postgres \
    -c 'DROP DATABASE IF EXISTS gis WITH (FORCE);' \
    -c 'CREATE DATABASE gis;'                    # 2) 重建空库
docker exec osm-db-1 psql -U postgres -d gis \
    -c 'CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS hstore;'
docker exec -i osm-db-1 pg_restore -U postgres -d gis \
    --no-owner --no-acl --exit-on-error \
    < backup/gis-test-20260928-233500.dump       # 3) 恢复（用容器里的 pg_restore）
docker compose run --rm sync external-data      # 4) 仅当用了 EXCLUDE_EXTERNAL_DATA=1
./tools/switch-env.sh test                       # 5) 起服务
```

`pg_restore` 会重建索引，1.2 GB 的归档大约需要几分钟。

---

## 10. 排错

**瓦片 404**
1. `docker compose logs tileserver` 里有 `Loading tile config default at /tile/` 吗？
   如果是 `at ` 后面为空，说明用了 `LoadTileConfigFile` —— 见第 7 节第 1 条。
2. 有 `file could not be found: '/data/symbols/...'` 吗？资源软链没建起来，见第 7 节第 2 条。
3. 有 `no valid fonts could be loaded in FontSet` 吗？见第 7 节第 3 条。
4. 有 `Postgis Plugin: connection to server on socket "/var/run/postgresql/..."` 吗？
   说明 `PGHOST` 没生效，Mapnik 退回本地 socket。

**改了 `layers.yml`，但地图还是旧地址（甚至变成空白）**

先确认浏览器拿到的到底是不是新 bundle：

```bash
A=$(curl -sS http://localhost:3000/ | grep -oE '/assets/application-[a-z0-9]+\.js' | head -1)
curl -sS "http://localhost:3000$A" | grep -oE '"url":"[^"]*tile[^"]*"' | sort -u
# 里面应当是 "/tile/{z}/{x}/{y}.png"（根相对路径），且只有一个图层
```

如果还是旧地址，就是 bundle 没重建（见第 7 节第 8 条）：`docker compose restart web`

**别的设备（局域网 / 内网穿透）上打开站点，地图一片空白，本机却正常**

浏览器控制台里是 `GET http://localhost:8080/tile/... net::ERR_CONNECTION_REFUSED` 之类的报错。
这是私有实例的经典问题：**瓦片是浏览器下载的**，而页面里的瓦片地址曾经写死成
`http://localhost:8080`，在别的设备上 `localhost` 指的是那台设备自己。
只穿透网站端口是不够的；多穿透 8080 / 8090 也不够——请求根本没发到这台机器。
完整原因、修法与排查步骤见第 13 节。

**瓦片空白（不是 404）**
- `docker compose run --rm sync status` 看 `render db ready` 是否为 `True`。
- `docker compose exec db psql -U postgres -d gis -c 'select count(*) from planet_osm_line;'`
  为 0 说明数据还没进来，看 `docker compose logs sync`。
- 外部 shapefile 没加载会让水体/冰盖图层报错进而整张图失败：
  `docker compose exec db psql -U postgres -d gis -c 'select name from external_data;'` 应有 4 行。

**字体下载失败**（常见：`raw.githubusercontent.com` 被网络屏蔽）
- 不影响出图：镜像内置的发行版 Noto 字体已能加载样式，日志里只会有一批
  `unable to find face-name 'HanaMinA Regular'` 之类的 warning。
- 想要完整字体集，可在能访问 GitHub 的网络里下载后放入 `./data/fonts/`，然后
  `docker compose restart tileserver`。也可以让 sync 容器借道宿主代理：
  `docker run --rm --network host -e HTTPS_PROXY=http://127.0.0.1:7890 ... osm-sync:latest fonts`。

**`docker compose up` 报卷已在别的 project 中使用** — 见第 3 节，属预期。

**端口冲突** — 确认没有同时跑 `openstreetmap-website/docker-compose.yml`（占用 `54321`），
以及开发/生产两个项目没有同时运行（`./tools/switch-env.sh status` 可以看到）。

### 生产环境相关

**访问 `http://localhost:3000/` 被 301 重定向到 https** — 生产的 `force_ssl` 还是 `true`。
在 `openstreetmap-website/config/settings/production.local.yml` 里写 `force_ssl: false`
和 `assume_ssl: false`，然后 `./tools/switch-env.sh prod`（会重建容器）。

**Rails 启动报 `secret_key_base` 相关错误** — `.env.prod` 里 `SECRET_KEY_BASE` 为空。
`./tools/switch-env.sh prod` 会自动生成；也可手工填一个随机串（`chmod 600 .env.prod`）。

**注册 OAuth 时 `Validation failed: Redirect URIs must be an HTTPS/SSL URI`** —
`force_ssl_in_redirect_uri` 要求非 development 且 host 不是回环地址时用 HTTPS。
用 HTTPS 部署，或确认用的是本仓库打过补丁的规则 + `force_ssl: false` + `server_url`
就是本实例自己的 host（见第 4 节第 3 条）。

**改完 `server_url` 后 iD 无法保存/授权失败** — OAuth 应用的 redirect URI 还是旧地址。
重跑 `./tools/switch-env.sh prod --init` 让它跟着更新。

**切换环境后「数据不对」** — 两个环境的数据本来就是分开的：开发在
`openstreetmap-website_db-data` 卷，生产在 `osm-prod-db-data` 卷。
先 `./tools/switch-env.sh status` 确认当前跑的是哪一个。

---

## 11. 已知限制与后续可做项

1. **API 库目前没有真实地图数据。** 这是唯一实质性的功能缺口。本机 API 库里是上游的
   null-island 测试数据（39 节点 / 11 路 / 1 changeset）。iD 可以自由绘制，也可以逐个上传
   小范围数据，但把一个城市/国家的 PBF 灌进去需要额外工作：上游 `doc/CONFIGURE.md` 推荐的
   `osmosis --write-apidb` 只写历史表，而 rails port 的 `current_*` 表**没有触发器**
   （`db/structure.sql` 里 0 个 `CREATE TRIGGER`，由应用代码维护），所以用 osmosis 灌完数据后
   iD 可能读不到/无法编辑 —— 这正是上游文档里那个「yet-to-be-written script」。
   可行方向是写一个 PBF → apidb 导入器，同时填充历史表与 `current_*` 表，并在同一事务里
   造出 `changesets` / `users` 记录，让水位线自洽。
2. **iD 的底图仍是外部影像服务**（Bing/Esri 等，来自 iD 自带的 imagery index），需要联网。
   要做完全离线的私有实例，需要把自建瓦片注册进 iD 的 imagery 列表
   （网站只透传 `imagery_blacklist`，目前没有配置入口）。
3. **单库双 schema 方案**未实现，理由见第 1 节。
4. **同步依赖 `changesets.closed_at` 语义**。这是在 rails port `Changeset#update_closed_at`
   中确认过的既有行为；若上游改变该语义，同步逻辑需要重审。
5. **本仓库未跑过全量测试套件**。上游两个项目的测试没有针对本编排运行过；
   改动面很小（3 处配置改动 + 新增目录），但请注意这一点。
6. **生产环境的邮件没有配置**。`config/settings.yml` 里的 SMTP 相关项仍是默认值，
   所以生产实例的注册确认、密码重置等邮件发不出去。上游要求新用户激活后才能登录，
   本仓库的 `tools/prod-init.rb` 通过直接激活管理员绕过了这一点；若要开放注册，
   需要先按上游 `doc/CONFIGURE.md` 配好 SMTP。
7. **API 库（`openstreetmap`）还没有备份脚本。** `tools/backup-db.sh` 只覆盖
   `gis` 渲染库（它随时可以从 API 库重建，真正丢不起的是账号、changeset 与
   编辑数据）。给 API 库补一个 `pg_dump -Fc openstreetmap` 是同一套思路，但那
   要求机器上有与 `postgres:14` 兼容的 `pg_restore`，见第 9 节那个版本坑。
8. **备份调度器不是守护进程。** `./tools/backup-db.sh run` 是前台进程，没有
   systemd unit / 容器化 / 开机自启。要长期无人值守，要么 `nohup` 挂着，要么让
   cron 定时调 `now`（两条命令都写在第 9 节），重启机器后需要自己确认它还在跑
   （`./tools/backup-db.sh status` 会显示调度器 pid）。

---

## 12. iD 编辑器的底图（`dyn2xyz` 与自定义清单）

### 12.1 结果

编辑器（`http://localhost:3000/id`，或主站右上角「编辑」）的「背景设置」里只有两项：

| 名称 | 瓦片地址 | 缩放 |
|---|---|---|
| OpenStreetMap (Standard) | `/tile/{zoom}/{x}/{y}.png`（根相对路径，经 edge → 本地 osm-carto4mc） | 0–19 |
| Minecraft（dyn2xyz） | `/tiles/{zoom}/{x}/{y}.png`（根相对路径，经 edge → dyn2xyz） | 13–21 |

两条都是**根相对路径**：浏览器按「它加载页面的那个 origin」解析，所以本机、局域网 IP、
内网穿透域名共用同一份配置。等价的绝对地址是 `http://localhost:3000/tile/...` 与
`http://localhost:3000/tiles/...`（`edge` 反代的端口，见第 13 节）。

iD 上游自带的 1300 多个第三方影像源（Bing、Esri、Mapbox、各国测绘局……绝大多数需要 API key）
全部不再出现。

### 12.2 为什么要绕这么一圈

iD 不是一个可配置的组件，它的底图清单是**打包在自己 dist 里**的
`data/imagery.min.json`。它读这个文件的路径是：

```
fileFetcher.get("imagery") -> fileMap["imagery"] = "data/imagery.min.json"
                          -> context.asset("data/imagery.min.json")
                          -> _assetMap["@openstreetmap/id/dist/" + "data/imagery.min.json"]
```

最后那步查的是 **asset map**——一张「逻辑文件名 → 真实 URL」的表，由 Rails 在渲染
`app/views/site/id.html.erb` 时用 `assets("@openstreetmap/id")` 生成。
**所以只要把那一项改成我们自己的 URL，就能整份替换掉清单，完全不用改 iD 本身。**
这就是 `app/views/site/id.html.erb` 里那句 `.merge(...)` 做的事。

清单内容放在 `config/id_imagery.yml`，由 `IdImageryController` 在
**请求时**渲染成 JSON——所以改完刷新页面即可，不像 `config/layers.yml` 那样需要
`rails assets:precompile`。

### 12.3 三个容易踩的坑

1. **`config/id_imagery.yml` 里的 `Bing` 条目不是给用户用的。**
   iD 的「导览」硬编码 `INTRO_IMAGERY = "Bing"`，找不到就把 `null` 传给
   `baseLayerSource()`，在 `d2.template()` 上抛 `TypeError`——而且抛在
   `context.inIntro(true)` **之后**，会把编辑器卡在导览模式、无法编辑。
   所以清单里保留了一条 id 为 `Bing` 的条目；它带一个极小的 polygon，
   而 iD 只把「polygon 覆盖当前视野」的源列进列表，因此它平时不可见。
   详见该文件里的注释。

2. **占位符是 `{zoom}` 不是 `{z}`。**
   iD 的 `tms` 类型用 editor-layer-index 的模板方言（`{zoom}/{x}/{y}`），
   跟 Leaflet 的 `{z}/{x}/{y}` 不同。写成 `{z}` 会被 iD 原样拼进 URL。

3. **CSP 要跟着改。**
   `content_security_policy.rb` 从 `layers.yml` **和** `id_imagery.yml` 一起推导
   `img-src` / `connect-src`。新增底图来源后不用手改 CSP，但两处配置的 `template`
   字段名不同（`tileUrl` vs `template`），推导逻辑里是分开取的。

### 12.4 验证

```bash
./tools/check-id-basemaps.sh          # 需要已登录的 cookie jar
OSM_COOKIE_JAR=/tmp/cj.txt ./tools/check-id-basemaps.sh
```

脚本复刻浏览器的加载链路：取 `/id` 的 asset map → 解析出清单 URL → 抓清单 →
按 iD 的 `tms` 规则真的去取一张瓦片 → 再单独校验 CSP 是否放行这些 origin。
`/id` 需要登录，所以没有 cookie 时它会跳过第 3 步并明确说明。

### 12.5 dyn2xyz 本身

`dyn2xyz` 是把上游 Dynmap 的瓦片**实时**转译成标准 XYZ 的代理：不落盘、不预渲染，
每个请求现算现抓现合成，峰值内存约 0.6 MB/请求。它与 db / web / sync / tileserver
同属 `name: osm` 这一个 compose 项目，`docker compose up/down` 一起启停。

默认源是 `map.bilicraft.com` 的 `Paralon/flat` 图，缩放范围 13–21
（`http://localhost:3000/tilejson.json` 里的 `minzoom`/`maxzoom` 就是它，
`config/id_imagery.yml` 的 `zoomExtent` 必须与之保持一致）。
换地图来源、改缩放范围、调并发与缓存都在 `dyn2xyz/config/dyn2xyz.toml` 里，
改完 `docker compose restart dyn2xyz` 即可（只读挂载，不用重建镜像）。

### 12.6 地图为什么摆在经纬度 (0,0)

默认约定是「方块 (0,0) → 赤道 × 本初子午线」，但**服务器真正渲染过的区域不在方块原点**。
实测本默认源的数据分布在方块 X ∈ [-1024, 23552]、Z ∈ [-17440, 6112]
（约 24.6k × 23.6k 方块），原点落在边角上——如果按 (0,0) 对齐，打开 iD 就是一片空白。

所以 `dyn2xyz/config/dyn2xyz.toml` 里把 `origin_block_x/z` 设成了数据中心
`(11168, -5568)`，让数据正落在经纬度 (0,0)，iD / QGIS 一打开就能看到。
这个值是 `dyn2xyz/tools/scan_extent.py` 扫出来的（507 个有数据的瓦片）：

```bash
docker compose exec dyn2xyz python tools/scan_extent.py /config/dyn2xyz.toml --seed 3500,-500
```

同时填了 `bounds_blocks`，完全落在数据范围外的瓦片会直接返回空图、不再打扰上游。
换地图来源后要重新扫一次。

### 12.7 两个必须知道的运维点

**(1) dyn2xyz 挂在一个 MTU=1400 的专用网络上，这不是可有可无的。**

本机到上游的 TLS 握手在 Docker 的 **bridge（NAT）网络里会大面积失败**，
报 `SSL: UNEXPECTED_EOF_WHILE_READING`。同时做的 A/B 对照：

| | 结果 |
|---|---|
| 宿主机直连 | **10/10 成功**（0 次 TLS 失败） |
| 容器内（默认 bridge，MTU 1500） | 10 张里 **7~10 张 TLS 失败** |
| 容器内（bridge，MTU 1400） | **10/10 成功** |
| 容器内（`--network host`） | 10/10 成功 |

原因是 PMTU 发现依赖的 ICMP `fragmentation needed` 过不了 Docker 的 NAT，
1500 字节的 TLS 握手包被黑洞掉；把 MTU 降到 1400 就不需要 PMTU 了。
所以 `docker-compose.yml` 末尾给 dyn2xyz 单独建了 `dyn2xyz-net`：
既修好问题，又不动 db / web / sync / tileserver 已在用的默认网络
（换网络会连带重建那些容器）。**如果以后别的服务也出现同类 TLS 抖动，
把同样的 `driver_opts` 加到 `default` 网络上即可。**

**(2) 上游是第三方服务，会抖；程序对此有三层防护。**

再稳的网络也挡不住上游自己出问题——实测抓到过 Cloudflare 的
`525 SSL Handshake Failed with Origin Server`（即 Cloudflare 连不上 bilicraft 的源站）。
对应措施：

| 防护 | 位置 | 作用 |
|---|---|---|
| 启动不再依赖网络 | `config/dyn2xyz.toml` 的 `[source.geometry]` | 预置了一份已验证的几何参数；抓不到上游配置就用它启动，否则一次抖动容器就起不来（**这个真的发生过**）。抓到权威值时仍以权威值为准 |
| 单张瓦片有响应上限 | `render.render_deadline`（默认 12s） | 到点就用**已到手**的源瓦片拼图返回，没等到的不取消、后台跑完进缓存。慢到超时的瓦片对用户等于没有 |
| 自动重启 | compose 里的 `restart: unless-stopped` | 运行期异常退出自愈 |

**冷启动性能**：完全冷缓存时，一个视口（16 张 XYZ 瓦片）在 4 路并行下约 23 秒，
单张中位 2~6 秒、最慢受 `render_deadline` 限制在 12 秒左右；命中缓存后是毫秒级。
一张 XYZ 瓦片要拼约 9 张源瓦片，这个放大倍数由两边网格的几何关系决定、绕不开。

细节（数值约定、缩放层级怎么对齐、验证方法、已知限制）见
[dyn2xyz/README.md](dyn2xyz/README.md)。

---

## 13. 局域网访问 与 内网穿透（frp 等）

### 13.1 只需要穿透一个端口

`edge` 是整个栈唯一的入口（配置见 `edge/default.conf`）：

| 路径 | 转到 |
|---|---|
| `/` | `web:3000` —— 站点、API、iD 编辑器 |
| `/tile/{z}/{x}/{y}.png` | `tileserver:8080` —— osm-carto4mc 底图（renderd + mod_tile） |
| `/tiles/{z}/{x}/{y}.png` | `dyn2xyz:8080` —— Minecraft（Dynmap）底图 |
| `/tilejson.json`、`/tiles.json`、`/status.json`、`/healthz` | `dyn2xyz:8080` —— 元数据与健康检查 |

所以 frp 里把 `EDGE_PORT`（默认 **3000**）映射出去就够了，
**不需要**再给 8080 / 8090 各开一条隧道。而且因为页面里的瓦片地址是根相对路径，
穿透出来的地址不管是 `https://frp-oil.com:24568` 还是局域网 IP，都不用改任何配置。

```ini
# frpc.ini 里大概是这样（type 用 tcp 或 https 都可以，见 13.4）
[osm]
type        = tcp
local_ip    = 127.0.0.1
local_port  = 3000
remote_port = 24568
```

### 13.2 为什么"再多穿透 8080 / 8090"解决不了问题

关键在于：**瓦片是浏览器去下载的，不是服务器下载了再塞进页面的。**

网站和两个瓦片服务是三个不同端口的 HTTP 服务。改造之前，页面里的地址是写死的绝对地址：

```
http://localhost:8080/tile/{z}/{x}/{y}.png      (config/layers.yml)
http://localhost:8090/tiles/{z}/{x}/{y}.png     (config/id_imagery.yml)
```

在跑这套栈的机器上看没问题；在**任何别的设备**上（局域网也好、穿透也好），
`localhost` 都指向那台设备自己：

```
别的设备打开  https://frp-oil.com:24568/            ← 页面加载正常（走穿透）
页面里的瓦片  http://localhost:8080/tile/...        ← 请求发给了「别的设备自己」
             → ERR_CONNECTION_REFUSED，一张图都没有
```

也就是说请求**根本没发到这台机器**，所以穿透多少个端口都没用——
必须让页面给浏览器一个「那台设备解析得了」的地址。两种做法：

| 做法 | 结果 |
|---|---|
| 把地址改成穿透出来的公网地址（`https://域名:端口/tile/...`） | 能用，但穿透域名一变就要重改配置（`layers.yml` 还得重新预编译前端 bundle），本机和局域网访问会跟着一起坏掉，而且穿透是 https 而瓦片地址写 http 时会被浏览器按混合内容拦掉 |
| **把地址改成根相对路径 `/tile/...`，再由 `edge` 在同一个 origin 上分流**（现在的做法） | 本机 / 局域网 IP / 穿透域名三种访问方式**共用同一份配置**，只需穿透一个端口，没有混合内容问题 |

### 13.3 三种访问方式对照

| 从哪访问 | 站点地址 | 页面里的瓦片实际请求 |
|---|---|---|
| 本机 | `http://localhost:3000/` | `http://localhost:3000/tile/...` |
| 局域网 | `http://<本机局域网IP>:3000/` | `http://<本机局域网IP>:3000/tile/...` |
| 内网穿透 | `https://frp-oil.com:24568/` | `https://frp-oil.com:24568/tile/...` |

三条都由 `edge` 收下再转给对应的容器。`http://localhost:3001/` 是 Rails 直连端口，
走它时瓦片是 404（页面里是相对路径），只用于调试，不是故障。

### 13.4 frp 的 TLS 终结与 Host 头

- `edge` 会把收到的 `Host` 原样转给上游（保留端口），所以 Rails 生成的绝对链接、
  dyn2xyz 的 `/tilejson.json` 里的瓦片地址都跟着对外地址走，不需要额外配置。
- frp 用 `type = https`（本地这段是 http、对外是 https）或 `type = tcp` 都可以：
  浏览器拿到的页面是 https，页面里的瓦片是相对路径，因此仍然是 https，
  **不会**触发混合内容拦截。
- 只有一个地方会露出 http：`/tilejson.json` 里 advertise 的 tile URL 用的是它看到的
  scheme（http）。QGIS / MapLibre 导入时直接用 `https://<域名>/tilejson.json` 即可；
  想让接口自己就返回 https，把 `dyn2xyz/config/dyn2xyz.toml` 里的 `public_url`
  设成对外地址，然后 `docker compose restart dyn2xyz`。

### 13.5 排查顺序

```bash
# 1) 入口本身（本机）：站点 + 两条瓦片链路
curl -sS -o /dev/null -w 'site %{http_code}\n' http://localhost:3000/
curl -sS -o /dev/null -w 'tile %{http_code}\n' http://localhost:3000/tile/0/0/0.png
curl -sS -o /dev/null -w 'mc   %{http_code}\n' --max-time 90 http://localhost:3000/tiles/15/16384/16384.png

# 2) 穿透链路本身：在别的设备上/或把域名解析到本机后
curl -sS -o /dev/null -w 'tile %{http_code}\n' https://<域名>:<端口>/tile/0/0/0.png

# 3) 浏览器 F12 → Network：看瓦片请求的 URL
#    应当是 https://<域名>:<端口>/tile/... ；若出现 localhost:8080 说明前端 bundle 是旧的
docker compose restart web        # layers.yml 改过就必须重建 bundle（见第 7 节第 8 条）

# 4) 完整校验 iD 底图链路（也可以直接指向穿透地址）
OSM_BASE=https://<域名>:<端口> ./tools/check-id-basemaps.sh
```

浏览器控制台的报错能直接区分故障位置：

| 报错 | 含义 |
|---|---|
| `ERR_CONNECTION_REFUSED` 且 URL 里是 `localhost:8080` | 前端 bundle 还是旧的绝对地址 → `docker compose restart web` |
| `ERR_CONNECTION_REFUSED` 且 URL 里就是穿透域名 | 穿透只映射了 3000 之外的端口，或 `edge` 没起来 |
| `502 Bad Gateway` | `edge` 起来了但上游容器没起来（`docker compose ps` / `docker compose logs web`） |
| `404` 且 URL 是 `<域名>:<端口>/tile/...` | 请求没被 `edge` 的 `/tile/` 规则接住（改了 `EDGE_PORT` 却没重建 `edge`？） |
| 混合内容（Mixed Content）拦截 | 只可能出现在又改回绝对 http 地址的时候，现在的相对路径不会 |

### 13.6 安全提醒

`edge` 上**没有任何鉴权**：穿透出去等于把整站（含 iD 编辑、OSM API 的写入接口、
GPX 上传、用户注册）放到公网。建议至少做一层：

- frp 用 `type = stcp`（secret tcp），访问端也要跑 frpc 并持有同一个 `sk`；
- 或者在 frp/前置 nginx 上加 HTTP Basic 认证、限制来源 IP；
- 不要用 `remote_port` 直接把 3000 裸奔在公网上长期开着。

另外注意：iD 编辑器在站点内是「已经登录」的（`app/views/site/id.html.erb` 直接下发
`current_user.oauth_token`），所以**登录会话等于编辑权限**；OAuth 应用里注册的
redirect URI 仍是 `http://localhost:3000`，那只影响独立 iD / JOSM 这类外部客户端，
不影响从站点里打开 iD。

---

## 14. iD 的 minecraft 元素预设

`../carto之后的计划.txt` 里的 `minecraft:*` 标签，已经做成 iD 编辑器里**可以点选的
元素预设**：106 个预设 + 21 个自定义字段 + 5 个分类磁贴（杂项 / 自然类 / 基建类 /
生产类 / 红石类），画完元素打开「要素类型」面板，第一屏就能看到这五个分类。

完整的原理、改动清单、重新生成与升级步骤在
[`openstreetmap-website/README-ID-PRESETS.md`](openstreetmap-website/README-ID-PRESETS.md)，
这里只留结论和三个必须知道的点。

### 14.1 结果

| 项 | 值 |
|---|---|
| 预设 / 字段 / 分类 | 106 / 21 / 5（iD 原有 1731 预设、714 字段一个没丢） |
| 手写源 | `openstreetmap-website/config/id_presets_minecraft.yml` |
| 构建产物 | `openstreetmap-website/config/id_minecraft/*.min.json` |
| 伺服地址 | `http://localhost:3000/id/minecraft/data/*.min.json` |
| 验证 | `openstreetmap-website/scripts/verify_id_minecraft_presets.sh`（数据侧） |
| 接线验证 | `tools/check-id-preset-wiring.sh`（asset map + fileMap + 真的执行一遍产物 JS） |
| 与 carto 的一致性 | `tools/check-id-presets-vs-carto.py`（见 14.4） |

### 14.2 为什么不能只往 asset map 里加一条

底图 `imagery.min.json` 能靠 asset map 覆盖，是因为它在 iD 的 `fileMap` 里是**相对
路径**；而 presets / fields / 分类 / 默认列表 / tagging 语言包在 iD.js 里是**绝对 CDN
URL**，`context.asset()` 对 `http(s)://` 开头直接原样返回、根本不查 asset map。
所以 `app/assets/javascripts/id.js` 里必须先把那几个 `fileMap` 键改成相对路径，
asset map 才开始起作用 —— **两步缺一不可**。

### 14.3 分类名 / 字段名没有 JSON 兜底

iD 里只有 **preset 的 name** 有兜底（`t(name, {default: 原文})`），分类名和字段名是
`t('_tagging.presets.categories.<id>.name', {default: <id>})` —— 没有翻译就直接显示
`category-minecraft-nature` / `minecraft_amenity`。所以构建脚本会把我们的文案**合并
进**基础语言包（zh-CN / zh-TW / zh / en），不是替换。

⚠️ **改了 `app/assets/javascripts/id.js` 必须重编译资源**，这个容器服务的是
`public/assets` 里的预编译产物：

```bash
docker compose exec -T web bash -lc 'cd /app && bundle exec rails assets:precompile'
```

只改视图 / 路由 / 控制器 / `config/id_minecraft/` 的话不用重编译，刷新页面即可。

### 14.4 预设与样式表的一致性由脚本守着

`config/id_presets_minecraft.yml`（用户能点什么）和 `osm-carto4mc/style/minecraft*.mss`
（地图会画什么）是两份各自手写的东西，对不上的后果都是**静默**的：预设写出的标签
样式表不画 → 标了元素地图上什么都没有；样式表能画的值预设里没有 → 只能手敲。

```bash
python3 tools/check-id-presets-vs-carto.py
```

它把每个预设**应用后实际写入的标签**（iD 的 `addTags`，缺省等于 `tags`，`"*"` 会落成
`"yes"`）代进从 `.mss` 抽出来的「每个列认哪些值 / 有没有兜底规则」里比对，双向检查。
计划文档里写明「不参与渲染」的值（`minecraft:redstone=yes/timer/memory/monitor/
tnt_spawner`、`minecraft:landuse=village`、`minecraft:cannon=arrow`）和样式表为兼容
笔误而额外接受的值（`igllo`、`guavity`、`gatewey`、`moster_spawner`…）在白名单里，
会作为说明打印出来而不是报错。

这条检查抓到过的真实问题：总类刷怪塔预设 `minecraft/industrial/mob_farm` 如果顺手带上
`minecraft:mob_farm: "*"`，iD 会落成 `mob_farm=yes`，而样式表的通用刷怪塔图案规则是
`[minecraft_industrial = 'mob_farm'][minecraft_mob_farm = null]` —— 两边都不匹配，
**地图上一片空白**。现在该预设只写 `industrial=mob_farm`。

### 14.5 接线检查：一个会让编辑器白屏、而所有 HTTP 检查都发现不了的坑

数据和接线是两回事。预设 JSON 全部正确伺服、页面 asset map 也写对了，**编辑器仍然可能
整个起不来** —— 因为真正把两边接起来的是 `app/assets/javascripts/id.js` 里那几行 JS，
而它只有浏览器会执行。

实际踩到的：`iD.fileFetcher` 导出的是 `coreFileFetcher()` 的**单例对象**，不是函数
（同一个 `iD` 命名空间里 `iD.coreContext()` / `iD.utilDetect()` 确实要加括号，容易顺手写错）。
写成 `iD.fileFetcher()` 会在 `DOMContentLoaded` 回调里、`iD.coreContext()` **之前**抛
`TypeError: iD.fileFetcher is not a function`，后面全部初始化都不执行 —— 打开 `/id`
**一片空白**。而与此同时：

- `/id/minecraft/data/presets.min.json` 照样 200；
- 页面里的 asset map 照样正确；
- `verify_id_minecraft_presets.sh` 照样全绿。

```bash
tools/check-id-preset-wiring.sh
```

它静态检查**预编译产物**里的写法（顺带证明「改了 JS 有重新 precompile」——源文件改了但没
编译时，产物里还是旧写法），然后用 node 把产物**真正执行一遍**：桩一个最小 DOM、跑
`DOMContentLoaded` 回调，断言 iD 的 `fileMap` 确实变成了 `data/presets.min.json`、
且 `asset()` 解析到 `/id/minecraft/data/presets.min.json`。

