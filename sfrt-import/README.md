# 舒芙蕾地铁导入（railwaymap → 本地 OSM）

本目录保存 2026-10-03 那次「按 `railwaymap.big-brother.top` 的数据把舒芙蕾地铁画进本地 OSM 实例」
所用的脚本、产物与核对图。

## 结果概览

| 项目 | 数量 |
| --- | --- |
| 线路（way） | 504 条（236 高架 `bridge=yes` / 268 地下 `tunnel=yes`） |
| 里程 | 131.4 km（地铁 5 条数字线 + 湖区 1 号线 100.0 km / 轻轨 世界树线+西海岸线 33.3 km / 联络线 1.2 km） |
| 节点 | 2204 个新建 + 12 个复用既有节点（与用户已画的联络线做拓扑衔接） |
| 车站 | 54 座（`railway=station` + `public_transport=station`；44 `station=subway` / 10 `station=light_rail`） |
| 停车点 | 135 个（`railway=stop` + `public_transport=stop_position`） |
| changeset | **182**（全部线路与车站）、**183**（删掉 3 个与国铁同名且重合的车站节点）、**185**（455 个车站/停车点名称去掉末尾「站」字） |

### 各线明细

| 线路 | `railway` | way | km | 层 |
| --- | --- | --- | --- | --- |
| 舒芙蕾地铁 2 号线 | subway | 69 | 27.9 | -1 / 1 / 4 |
| 舒芙蕾地铁 1 号线 | subway | 90 | 25.8 | -1 / 1 / 2 |
| 西海岸线 | light_rail | 97 | 19.9 | -1 / 1 |
| 舒芙蕾地铁 4 号线 | subway | 68 | 13.9 | -1 / 1 / 2 |
| 舒芙蕾地铁 5 号线 | subway | 34 | 13.9 | -1 / 1 / 2 |
| 世界树线 | light_rail | 53 | 13.5 | -1 / 1 |
| 舒芙蕾地铁 3 号线 | subway | 45 | 13.2 | -1 / 1 |
| 湖区地铁 1 号线 | subway | 33 | 4.1 | -1 / 1 |
| 联络线 | subway | 15 | 1.2 | -1 / 1 |

## 数据来源与处理

1. **几何**：`https://railwaymap.big-brother.top/api/v1/geojson`，取 `railwaySystemId = SFRT`
   的 18 条运行线（上下行各自成线）+ `lineId = contact` 且 `owner` 属于 SFRT 的 22 条联络线，
   共 266 段 / 141.3 km。
2. **平滑**：1 格重采样 → 滑动平均去台阶锯齿 → Douglas-Peucker(0.6 格) →
   按累计转角抽稀（弯道约 20° 一点，直线最多 200 格一段，弦偏差 ≤1.2 格）。
3. **高架/地下判定**：抓上游 Dynmap level-0 瓦片（4 像素/格，缓存在
   `.work/railwaymap/l0cache/`，本次新下载约 4700 张），沿线路取垂直剖面
   （±16 格、沿轨道 ±24 格平均），判定地表可见（→ `bridge=yes`）或不可见（→ `tunnel=yes`）。
4. **层数**：`layer = max(1, 数据层号 - 1)`，地下统一 `layer=-1`。
5. **车站**：数据里的 `type=station` 点按名字归组，在重心建 `railway=station`，
   各股道节点加 `railway=stop`。

## 按用户要求做的取舍

* **数字线（1/2/3/4/5 号线 + 湖区 1 号线）标为地铁** → `railway=subway`；
  **世界树线、西海岸线标为轻轨** → `railway=light_rail`。
* **3 号线雪山庄园以北与湖区 1 号线共线**：数据里两者是同一组轨道节点
  （SF3n↔SFW1n、SF3s↔SFW1s）。这一段**只画一次**，写成 `name=湖区地铁1号线`，
  不写 3 号线信息（按用户选择）。共剔除 15 段链 / 4.21 km。
* **联络线**：22 条 SFRT 联络线里，6 条已经存在（3 条是 cs58 导入留下的 way
  3099/3135/3137，3 条是用户在 cs176 画的丰饶台地北联络线 3136/3138/3141/18468-18471），
  其余 **16 条已按「之前被删除则重新绘制」补齐** → `railway=subway` + `name=联络线`。
  这些全部是 SFRT 内部的联络线；SFRT 与国铁之间的那条就是丰饶台地北北侧联络线，
  用户已经画好（`railway=rail`、`name=联络线`、layer 2→3），本次未改动。
* **丰饶台地北的 layer**：本次绘制结果与该要求一致 ——
  普朗线（2 条国铁）`layer=3`、5 号线 `layer=2`、3 号线 `layer=1`；
  北侧联络线北端 layer=3 南端 layer=2、南侧联络线北端 layer=2 南端 layer=1。
* **同名车站**：57 座车站里有 3 座（丰饶台地北、海莲城、红松丘陵）与既有国铁车站
  同名且相距 ≤3 格，不再重复建点，已在 cs183 删除，避免同名重复标注。
* **车站/停车点名字不带「站」字**：455 个节点（127 车站 + 328 停车点，
  含国铁那批和本次地铁那批）在 cs185 里统一去掉末尾的「站」。
  唯一还带「站」的是 `name=诺亚火车站` 的 `building=transportation`（车站建筑，不是
  车站/停车点节点），去掉会变成「诺亚火车」，所以没动。
* **没有建线路关系（route relation）**：与国铁那次一致。
* **没有画站台**（数据里没有站台几何）。

## 已知取舍（需要你判断的地方）

* **可见≠一定高架**：按你之前的规则，影像上看得见就写 `bridge=yes`；
  真正属于地面线的，把 `bridge` 去掉、`layer` 改 0 即可。
* **层号整体比数据里的层号小 1**：数据里丰饶台地北是国铁 4 / 5 号线 3 / 3 号线 2，
  写成 3/2/1 才能和你已经设成 `layer=3` 的普朗线、以及 layer 1/2 的联络线对上。
  如果想让全网络整体 +1，改 `build_sfrt.py` 里的 `osm_layer()` 即可。
* **世界树线东段（SFwt2e/SFwt2w）**：数据里只有车站没有几何，没画。
* **少数车站同时被地铁和轻轨服务**（德芙、摸鱼小镇、春鱼港口）→ 统一写成 `station=subway`。

## 复现

```bash
cd sfrt-import/scripts
python3 run_sfrt.py                    # 只构建（写出 sfrt.osmchange.xml + 预览用数据）
python3 run_sfrt.py --upload           # 构建并上传（需要 token.txt）
python3 preview.py --level 5 --out preview_all.png
python3 upload_sfrt.py sfrt.osmchange.xml
python3 rename_stations.py --upload      # 车站/停车点去「站」字
```

依赖 `../railway-import/scripts/` 里的 `geohelper.py / smooth.py / classify.py /
l0imagery.py / osm_api.py`（`l0cache` 与 `token.txt` 已软链到 `.work/railwaymap/`）。
