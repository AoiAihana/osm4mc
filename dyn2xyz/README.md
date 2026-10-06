# dyn2xyz —— 把 Dynmap 瓦片实时转译成标准 XYZ 瓦片

一个**无状态**的 HTTP 代理：对外提供标准 `{z}/{x}/{y}` 瓦片，对内把每个请求翻译成
Dynmap（dynamic map）的瓦片请求，实时抓取、拼接、重采样后返回。

* **不存储任何瓦片** —— 没有磁盘缓存，没有预渲染。内存里只有一个源瓦片的 LRU（可关闭）。
* **输出即插即用** —— 提供 TileJSON，QGIS / MapLibre / Leaflet / OpenLayers 都能直接导入。
* **缩放范围自动收紧** —— 只暴露 Dynmap 实际能提供的那些缩放层级。
* **与 OSM 栈同步启停** —— 作为 `dyn2xyz` 服务写进根目录的 `docker-compose.yml`。

```
  GIS 工具                    dyn2xyz                        Dynmap
 ┌──────────┐  {z}/{x}/{y}  ┌──────────────┐  层级 L + 分片  ┌──────────┐
 │  QGIS    │ ────────────> │ 几何换算      │ ─────────────> │  瓦片     │
 │ MapLibre │ <──────────── │ 拼接 + 重采样 │ <──────────── │  (JPEG)  │
 └──────────┘   PNG/JPEG    └──────────────┘   并发抓取      └──────────┘
                              （全内存，不落盘）
```

---

## 1. 快速开始

### 1.1 作为整个栈的一部分启动

```bash
cd /home/aoiaihana/osm
docker compose up -d            # 会一并启动 dyn2xyz
```

> **构建**：镜像只装一个 Pillow。本机到官方 PyPI 经常超时，所以 `.env` 里预置了
> `PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`；换回官方源只要改这一行。
>
> **权限**：容器以 uid 10001 运行，`dyn2xyz/config/dyn2xyz.toml` 必须对它可读
> （`chmod 644`）。若权限是 600，容器会以 `Permission denied: '/config/dyn2xyz.toml'` 启动失败。

默认监听 **`http://localhost:8090`**（可用 `.env` 里的 `DYN2XYZ_PORT` 改）。

```bash
curl http://localhost:8090/healthz          # ok
curl http://localhost:8090/tilejson.json    # 元数据
open http://localhost:8090/                 # 浏览器预览页
```

### 1.2 单独跑（不用 Docker）

```bash
cd dyn2xyz
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.main --check config/dyn2xyz.toml   # 先自检
.venv/bin/python -m app.main config/dyn2xyz.toml           # 再启动
```

### 1.3 导入 GIS 工具

**QGIS**：图层 → 添加图层 → XYZ 瓦片 → 新建连接，URL 填

```
http://localhost:8090/tiles/{z}/{x}/{y}.png
```

最低/最高缩放分别填 TileJSON 里的 `minzoom` / `maxzoom`（默认源是 14 / 21）。

**MapLibre / MapLibre GL JS**：直接吃 TileJSON——

```js
map.addSource('mc', { type: 'raster', url: 'http://localhost:8090/tilejson.json' });
```

**Leaflet**：

```js
L.tileLayer('http://localhost:8090/tiles/{z}/{x}/{y}.png',
            { minZoom: 14, maxZoom: 21, tileSize: 256 }).addTo(map);
```

---

## 2. 端点

| 端点 | 说明 |
|---|---|
| `GET /tiles/{z}/{x}/{y}.{png,jpg,webp}` | XYZ 瓦片。`/tile/...` 是别名；扩展名决定输出格式，缺省 png |
| `GET /tilejson.json` | TileJSON 3.0（含 `minzoom`/`maxzoom`/`bounds`/`center`） |
| `GET /status.json` | 诊断：推导出的几何、逐层映射、缓存命中率 |
| `GET /healthz` | 健康检查（容器 healthcheck 用） |
| `GET /` | Leaflet 预览页 |

响应头里有三个诊断字段，排查时很有用：

* `X-Dyn2xyz-Level` —— 本次请求实际用了 Dynmap 的哪个层级
* `X-Dyn2xyz-State` —— `ok` / `out-of-bounds` / `unavailable`
* `Access-Control-Allow-Origin` —— 默认 `*`

---

## 3. 数值约定

### 3.1 世界怎么摆上去

| 项 | 默认 | 配置键 |
|---|---|---|
| 方块 (0, 0) 对应的真实坐标 | **赤道 × 本初子午线**（lon 0, lat 0） | `geo.origin_lon` / `geo.origin_lat` |
| 渲染比例 | **1 方块 = 1 米** | `geo.meters_per_block` |
| 被钉住的方块 | (0, 0) | `geo.origin_block_x` / `geo.origin_block_z` |

轴向：Minecraft 的 **+X 向东**、**+Z 向南**；输出是标准的**北朝上、西在左**的 XYZ 网格，
和任何普通底图一致。

投影用 **Web Mercator（EPSG:3857）**，与 XYZ 规范完全一致：

```
mx = lonlat_to_mercator(origin) . x + (bx - origin_bx) * meters_per_block
my = lonlat_to_mercator(origin) . y - (bz - origin_bz) * meters_per_block   ← 取负：MC 的 Z 向南
```

### 3.2 关于墨卡托变形

墨卡托的尺度因子是 `sec(φ) = 1/cos(φ)`，**在赤道恰好等于 1**（无变形），越往两极越大。
把世界原点放在赤道，意味着变形最小的地方正好是你的世界所在的地方：

| 距赤道（方块，1 方块 = 1 米） | 纬度 | 纵向拉伸 |
|---|---|---|
| ±10 000（±10 km） | 0.09° | **1.0000012**（百万分之 1.2） |
| ±100 000（±100 km） | 0.90° | **1.00012**（万分之 1.2） |
| ±1 000 000（±1000 km） | 9.0° | **1.012**（1.2%） |
| ±5 000 000 | 45° | **1.414**（41%） |

绝大多数 Minecraft 世界在 ±3 万方块以内，对应变形约 **百万分之四**，可以完全忽略——
这正是需求里说的"不考虑变形问题"。

一个更准确的说法：墨卡托是**保角**投影，在小范围内它是**相似变换**（不改变形状，只是整体
缩放一点点），所以你的建筑不会被扭斜，只是随纬度整体略微放大。真正非线性的只是"南北方向
被拉长"这一件事，而它在赤道附近趋近于零。

> 如果你确实要把世界放到高纬度（比如 `origin_lat = 60`），建议改用别的方式：
> 那不是本程序的用途，而且 `|lat| > 85.05` 会被投影直接截断。

### 3.3 缩放层级怎么对齐

两边分辨率的桥接点是：

```
XYZ 在层级 z 的米/像素 :  WORLD_WIDTH / (256 · 2^z)
Dynmap 在层级 L 的米/像素:  (2^L / scale_x) · meters_per_block
```

令两者相等，解出 Dynmap 原生层（L=0）在数值上等价于哪个**实数** XYZ 层级：

```
z_native = log2( WORLD_WIDTH · scale_x / (256 · meters_per_block) )
```

对默认源（`scale_x = 4`）：`z_native = log2(40075016.69 · 4 / 256) = 19.2562`

于是对外暴露的层级范围取：

```
minzoom = round(z_native) − mapzoomout − underzoom
maxzoom = round(z_native) + overzoom
```

对默认源就是 **z14 … z21**。请求落在范围内时，用 `L = round(z_native − z)` 选层：

| XYZ z | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 |
|---|---|---|---|---|---|---|---|---|
| Dynmap L | 5 | 4 | 3 | 2 | 1 | 0 | 0 | 0 |

z=20/21 只是把原生层放大（`overzoom`），没有新信息，但 GIS 工具通常希望还能继续放大。

**为什么 minzoom 定在这里**：`L = round(z_native − z)` 这个取整方式让任意层级下，
一张 XYZ 瓦片需要的源瓦片数稳定落在 **2×2 ~ 3×3** 之间。再往外缩一级，所需源瓦片数就会
翻两番（z13 → 约 5×5，z12 → 约 10×10）。所以 `minzoom` 就是"不把上游打挂"的边界，
而 `render.max_source_tiles`（默认 16）是兜底的安全阀。

---

## 4. 瓦片来源配置

有三种写法，**完整说明写在 `config/dyn2xyz.toml` 的注释里**。最省事的是直接粘一条真实 URL：

```toml
[source]
sample_url = "https://map.bilicraft.com/s2/hyperion/tiles/Paralon/flat/3_1/zzzzz_96_32.jpg?timestamp=1790688914513"
```

程序会从这条 URL 反解出 `webroot` / `world` / `prefix` / 图片格式，
然后自动去抓 `<webroot>/standalone/dynmap_config.json`，从 `worldtomap` 矩阵里读出
瓦片像素、缩放层级与每像素方块数。**不需要手工填任何几何参数。**

另外两种写法（`webroot`+`world`+`prefix`，或完全自定义 `template`）
以及抓不到配置时的手工 `[source.geometry]` 覆盖，都见配置文件注释。

### 支持的 Dynmap 地图类型

| 类型 | 是否支持 | 原因 |
|---|---|---|
| `flat`（俯视，如 `iso_S_90_lowres`） | ✅ | 瓦片网格与世界轴对齐，可映射到北朝上的 XYZ |
| `surface`（等轴测，如 `iso_SE_30_lowres`） | ❌ **明确拒绝** | 瓦片是斜置菱形网格，`worldtomap` 有交叉项；映射到 XYZ 只能靠旋转+重采样，结果对 GIS 无意义 |

程序在启动时会检查 `worldtomap` 矩阵：如果地图经度还依赖方块 Z、或纬度还依赖方块 X，
就直接报错并提示改用 `flat` 图，而**不会**悄悄给你一张错的地图。

---

## 5. 它是怎么算的（以及为什么必须重采样）

Dynmap 的瓦片寻址规则（源码依据见 `../mcmap/docs/02-dynmap.md`）：

```
路径:  <webroot>/tiles/<world>/<prefix>/<x>>5>_<y>>5>/<z前缀>_<x>_<y>.<ext>
层级:  n0 = floor(scale · block / tile_px)        # level 0
       nL = 2^L · floor(n0 / 2^L)                # level L（向下对齐到 2^L 的倍数）
文件名: filename_x = nL_x ,  filename_y = -nL_y   # y 被取反（Dynmap 的 HD 约定）
```

**关键点：Dynmap 的网格与 XYZ 的网格在数值上永远对不齐。**
一、Dynmap 层级 L 的瓦片边长是 `tile_px · 2^L / scale_x` 个方块（默认源是 `32 · 2^L`）；
二、XYZ 层级 z 的瓦片边长是 `40075016.69 / 2^z` 米。
要让两者对齐需要 `40075016.69 / 32 = 1252344` 是 2 的幂——它不是。

所以**纯 302 重定向做不到几何正确**（图像会被拉伸且错位最多 2 倍）。
本程序的做法是每次请求都：

1. 把 XYZ 瓦片换算成方块范围；
2. 选一个 Dynmap 层级 L；
3. 算出该范围在层级 L 的像素窗口；
4. 并发抓取覆盖该窗口的源瓦片（2×2 ~ 3×3 张）；
5. 拼成马赛克；
6. **一次仿射变换**同时完成裁剪与缩放（含半像素修正），输出 256×256；
7. 编码返回。

第 6 步用的仿射系数是：

```
scale = (px1 − px0) / out_size
c     = (px0 − kx0·T) + 0.5·scale − 0.5      ← 这个 −0.5 是半像素修正，
                                               少了它整幅图会偏移半个像素
```

全程在内存里，单次请求的峰值内存约为 `9 × 128×128×4 B ≈ 590 KB`。

### 5.1 为什么是「转译代理」而不是 302 重定向

需求原文是「把用户的瓦片请求重定向到正确的位置」。这句话字面上可以理解成 HTTP 302，
**但 302 在几何上做不到正确**，原因就是上面那条：两套网格永远对不齐。

具体地，一张 XYZ 瓦片要么跨越多张 Dynmap 瓦片（本例 z=19 跨 3×3 张），
要么只覆盖某张 Dynmap 瓦片的一角。302 只能把请求原样指向**一张**上游图片，
浏览器/GIS 客户端会把它**拉伸铺满**整个 XYZ 瓦片的位置——于是：

* 图像覆盖的**地面范围**与客户端以为的不一致（错位）；
* 缩放比例最多差 2 倍（因为 `L` 只能取整数，而理想值是小数）。

要让它成立，必须同时满足两个条件：
① 渲染比例取到 §6.4 说的对齐值；② 上游瓦片边长恰好 256（`tilescale = 1`），
这样一张 XYZ 瓦片才刚好等于一张 Dynmap 瓦片。本默认源是 `tilescale = 0`（128px），
所以一张 XYZ 瓦片等于 2×2 张 Dynmap 瓦片——仍然无法用单次 302 表达。

因此这里实现的是**转译**：抓多张、拼起来、重采样成客户端期望的那一块。
它满足需求里"不存储瓦片""唯一的工作是转译"这两条硬约束，
只是在"重定向"这个词上做了必要的工程修正。

> 如果你的场景确实只想要 302（例如上游就在同一个内网、想省掉这层 CPU），
> 那就需要把 `meters_per_block` 设成对齐值、并换用 `tilescale = 1` 的 Dynmap 图；
> 这两个条件都可以用 `--check` 输出核对。

---

## 6. 验证

本程序的几何推导不是"看起来对"，而是与真实上游逐项核对过的。
测试用的黄金向量是**实测确认返回 200 的真实 URL**。

### 6.1 单元测试（不联网，34 项）

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

其中最有价值的一组：对同一处方块 (3500, −500)，程序算出的各层级相对路径必须与
**实测存在的上游 URL 逐字符一致**：

| L | 实测存在的 URL | 程序算出的 |
|---|---|---|
| 0 | `Paralon/flat/3_0/109_15.jpg` | ✅ |
| 1 | `Paralon/flat/3_0/z_108_16.jpg` | ✅ |
| 2 | `Paralon/flat/3_0/zz_108_16.jpg` | ✅ |
| 3 | `Paralon/flat/3_0/zzz_104_16.jpg` | ✅ |
| 4 | `Paralon/flat/3_0/zzzz_96_16.jpg` | ✅ |
| 5 | `Paralon/flat/3_1/zzzzz_96_32.jpg` | ✅（正是需求里给的那条样例 URL） |

### 6.2 上游金字塔一致性（联网）

```bash
python3 tests/verify_upstream_geometry.py
```

原理：Dynmap 的金字塔严格是 2 倍的，所以一个 level-L 瓦片应当约等于 4 个 level-(L−1)
瓦片拼起来再降采样。用平均色差（MAD）检验，并跑几个**故意算错**的对照组：

```
正确对齐（本程序推导）        MAD =  4.18
对照组：上下翻转（y 搞反）     MAD = 39.62
对照组：左右翻转              MAD = 57.97
对照组：横向错开一个子瓦片     MAD = 51.75
```

正确对齐比最好的错误对照组好 **9.5 倍**。这同时确认了原点、y 取反、分片目录与
"行 0 = 北"这几件事。

### 6.3 端到端管线验证（联网）

```bash
python3 tests/check_pipeline.py config/dyn2xyz.toml 14,16,17,18,19
```

在多个缩放层级上跑三项检查（**只测单一层级是不够的**——开发过程中有一个 bug 只在
L>0 时发作，L=0 完全正常）：

1. **非空**：有数据的地方渲染结果必须是实心的；
2. **接缝统计**：把若干张独立渲染的相邻瓦片拼起来，接缝处的色差不应比**独立实现**
   自己的接缝更差。这是"逐瓦片渲染有没有引入相对错位"的直接检验；
3. **2×2 拼图 vs 独立实现**：与「直接抓上游 + 一次性裁剪缩放」的独立实现对比。
   默认源上的实测结果：降采样色差 0.6 ~ 1.9，各象限最大 11.4。

### 6.4 零容差验证：精确对齐下必须是纯裁剪

```bash
python3 tests/check_aligned.py
```

这是**最强的一项**。Dynmap 与 XYZ 的网格在默认渲染比例下永远对不齐，但如果把渲染比例
特意取成

```
meters_per_block = WORLD_WIDTH · scale_x / (tile_px · 2^k)      # k = 20 时约 1.1943286
```

两套网格就会**精确对齐**：此时 `native_zoom_exact` 恰好是整数 19，一张 XYZ 瓦片恰好等于
2×2 张 L=0 瓦片、共 256×256 源像素，而输出也是 256×256——于是缩放因子恰为 1.0、
亚像素偏移恰为整数（残差 ~1e-9，来自墨卡托三角函数），**输出必须是纯裁剪**。

实测：

```
[A] 窗口偏移为整数（残差 1.57e-09 / 7.56e-10）: ✅
[D] 与参考裁剪的最大通道差 = 0，平均差 = 0.000000  ✅ 逐像素完全相同
    对照：若整体偏 1 像素，平均差会是 11.787
```

这条断言对半像素修正是**零容差**的：仿射系数里的 `-0.5` 若写错，输出会整体偏一个像素，
平均差立刻从 0 跳到 11.8 量级。（顺带说明：这个比例下程序会自动走
「直接裁剪」分支而跳过重采样——既能更快，也避免 ±1 色阶抖动。）

### 6.5 并排肉眼核对

```bash
python3 tools/sidebyside.py config/dyn2xyz.toml
# 生成 compare.png：左 = dyn2xyz 输出，右 = 直接裁剪上游
```

两张图应当看起来完全一样。

### 6.6 人工核对

```bash
python3 -m app.main --sample 3500,-500 config/dyn2xyz.toml
```

会打印该方块的经纬度、XYZ 瓦片坐标，以及 L=0…5 各层级的上游 URL——
把最后一行贴进浏览器，应当正好是包含该方块的那张 Dynmap 瓦片。

---

## 7. 环境变量

配置文件的多数键都能被环境变量覆盖，方便在 Docker 里不重建镜像就改行为：

`DYN2XYZ_HOST` · `DYN2XYZ_PORT` · `DYN2XYZ_PUBLIC_URL` · `DYN2XYZ_ALLOW_ORIGIN` ·
`DYN2XYZ_SAMPLE_URL` · `DYN2XYZ_ORIGIN_LON` · `DYN2XYZ_ORIGIN_LAT` ·
`DYN2XYZ_METERS_PER_BLOCK` · `DYN2XYZ_OVERZOOM` · `DYN2XYZ_UNDERZOOM` ·
`DYN2XYZ_CACHE_TILES` · `DYN2XYZ_CONFIG` · `DYN2XYZ_LOG_LEVEL`

---

## 8. 目录结构

```
dyn2xyz/
├── app/
│   ├── geo.py       墨卡托 / XYZ / 方块坐标的纯数学（无依赖、无状态）
│   ├── dynmap.py    Dynmap URL 解析、worldtomap 校验、瓦片寻址
│   ├── render.py    源瓦片抓取 + LRU 缓存 + 拼接 + 仿射重采样
│   ├── config.py    TOML 配置 + 环境变量覆盖 + 来源自检
│   ├── server.py    HTTP 端点、TileJSON、预览页
│   └── main.py      CLI（--check / --sample）
├── config/dyn2xyz.toml   默认配置（含来源格式的完整说明）
├── tools/
│   ├── scan_extent.py    扫描上游数据范围，生成 bounds_blocks
│   └── sidebyside.py     生成并排对比图（肉眼核对用）
├── tests/                单元测试 + 三项联网验证（上游金字塔 / 管线 / 零容差对齐）
├── Dockerfile
└── requirements.txt      只有 Pillow
```

---

## 8.5 排错：瓦片大面积空白 / 出图极慢

按可能性从高到低排：

### (0) 地图数据不在方块原点附近 —— 最常见的「打开就是一片空白」

**症状**：iD / QGIS 里能看到底图选项、也能请求到瓦片（HTTP 200），
但画面是一片空白（在 iD 里表现为黑底上有零星几个白点）。

**原因**：默认约定是「方块 (0,0) → 经纬度 (0,0)」。但 Minecraft 服务器**真正渲染过的
区域通常不在方块原点附近**——本默认源实测数据分布在方块
X ∈ [-1024, 23552]、Z ∈ [-17440, 6112]，也就是说方块原点落在地图的**边角**上。
层级越高（放得越大），一张瓦片覆盖的方块越少，就越只能看到空地。

**诊断**：直接看一张瓦片的透明度就知道是"没数据"还是"没请求到"：

```bash
curl -s -o /tmp/t.png http://localhost:8090/tiles/17/65536/65536.png
python3 -c "from PIL import Image,ImageStat; im=Image.open('/tmp/t.png').convert('RGBA'); \
print('不透明占比', ImageStat.Stat(im.getchannel('A')).mean[0]/255)"
```
接近 0% 就是"这里本来就没有地图数据"。

**修法**：扫出数据的真实范围，把 `geo.origin_block_x` / `origin_block_z` 设成数据中心：

```bash
# --seed 给一个**确定有数据**的方块坐标（在网页地图上找一个建筑，看它的坐标）
python3 tools/scan_extent.py config/dyn2xyz.toml --seed 3500,-500
```

它会打印数据的外接矩形、连通据点，以及可以直接粘进配置的两行：
`bounds_blocks` 和 `origin_block_x/z`。填好后重启容器，地图就正落在经纬度 (0,0) 了。

> 顺带一提，`tools/scan_extent.py` 早期版本有个 bug：它在层级 L 上按 1 递增去探邻居，
> 而 **L 层的合法瓦片索引必须是 2^L 的倍数**，所以探到的索引根本不存在、永远 404，
> 于是得出"周围都是空的"这个假结论（当时只扫到 1 张瓦片，实际有 507 张）。
> 现在步长已改为 `2^L`。

### (1) 容器网络的 MTU 太大 —— 最容易踩，也最隐蔽

**症状**：本地 `curl` 上游正常，但容器里去抓就大量失败，日志里出现
`<urlopen error [SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol>`；
表现为瓦片大面积透明、或单张要几十秒。

**原因**：Docker 的 bridge 网络走 NAT，而 PMTU 发现依赖的 ICMP
`fragmentation needed` 过不了 NAT，于是 1500 字节的 TLS 握手包被黑洞掉。
宿主机自己不受影响（PMTU 正常），所以很容易误判成"上游挂了"。

**验证**（同时跑两边，别分先后，上游本身也会抖）：

```bash
# 宿主机
python3 -c "import urllib.request; urllib.request.urlopen('<某个上游瓦片URL>', timeout=10)"
# 容器内
docker compose exec dyn2xyz python -c "import urllib.request; urllib.request.urlopen('<同一个URL>', timeout=10)"
```
如果容器里失败率远高于宿主机，就是这个问题。

**修法**：给容器换一个 MTU 更小的网络。本仓库已经在 `docker-compose.yml` 里
给 dyn2xyz 建了 `dyn2xyz-net`（`com.docker.network.driver.mtu: "1400"`）。
实测 1500 时 10 张里 3~10 张失败，1400 时 **10/10 成功**。
1450 大概也行，1400 是留了余量的稳妥值。

### (2) 上游自己的源站有问题

日志里出现 `HTTP Error 525: SSL Handshake Failed with Origin Server`——
这是 **Cloudflare 报的**，意思是它连不上上游的源站，跟你的网络无关，只能等。
程序会重试；重试仍失败的那张源瓦片就留空，几秒后刷新通常就好了。

### (3) 请求的缩放层级超出了数据源

`X-Dyn2xyz-State: unavailable` 表示这张 XYZ 瓦片需要的源瓦片数超过了
`render.max_source_tiles`。用 `--check` 看真实的可用范围，
或把 `render.underzoom` 保持为 0、不要在 TileJSON 声明的范围外请求。

### (4) 看诊断端点

```bash
curl -s http://localhost:8090/status.json   # 几何、层级映射、缓存命中率
docker compose logs dyn2xyz | grep 抓取失败   # 上游抓取失败明细
```

每个响应还带 `X-Dyn2xyz-Level`（用了哪个 Dynmap 层级）与
`X-Dyn2xyz-State`（`ok` / `out-of-bounds` / `unavailable`），排查时很有用。

### 调参速查

| 症状 | 调整 |
|---|---|
| 出图慢、浏览器干等 | 调小 `render.render_deadline`（宁可先给局部空洞） |
| 瓦片空洞多、上游只是慢 | 调大 `fetch_timeout`，但要让它满足 `fetch_timeout × (fetch_retries+1) ≤ render_deadline`，否则重试还没跑完就被截止时间掐掉 |
| 反复浏览同一片区域仍慢 | 调大 `render.cache_tiles` |
| 上游被请求得太狠 | 调小 `fetch_workers` |

---

## 9. 已知限制

1. **只支持俯视（flat）地图**。等轴测（surface）图会被明确拒绝，理由见 §4。
2. **最粗只到 Dynmap 的 `mapzoomout` 层**。再往外缩需要拼接数十张源瓦片，
   会把上游打挂；`underzoom` 可以放宽，但请自行评估。
3. **不存储瓦片**意味着每个进程重启后缓存为空，且无法离线工作。
   这是需求明确要求的取舍。
4. **上游缺瓦片时输出透明**（Dynmap 对未渲染/越界区域返回 404）。
   可用 `output.background` 改成不透明底色。
5. **`bounds_blocks` 默认未设置**，所以 TileJSON 的 `bounds` 是整个世界。
   建议跑一次 `tools/scan_extent.py` 把范围填进配置，QGIS 打开时就能直接定位到你的世界。
6. **未做鉴权**。默认 `allow_origin = "*"` 且不对上游做任何凭据注入；
   如果要暴露到公网，请自行加反代与访问控制。
