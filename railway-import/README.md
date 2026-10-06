# 帕拉伦国有铁路导入（railwaymap → 本地 OSM）

本目录保存 2026-10-02 那次「按 railwaymap.big-brother.top 的数据把帕拉伦国有铁路
画进本地 OSM 实例」所用的脚本、产物与核对图。

## 结果概览

| 项目 | 数量 |
| --- | --- |
| 线路（way，railway=rail） | 1739 条（其中 bridge=yes 945 / tunnel=yes 794） |
| 线路总长 | 531.9 km（可见 320.8 km / 不可见 211.1 km） |
| 节点 | 8444 个新建 + 4 个复用你手绘线路的端点（608/622/623/636） |
| 车站 | 74 座（railway=station + public_transport=station + train=yes） |
| 停车点 | 193 个（railway=stop + public_transport=stop_position） |
| changeset | 58（节点 + 前 1500 条路径）、61（补传 239 条路径） |

范围：`railwaySystemId = paralon-railway` 的全部线路 + 你要求一并绘制的
`contact`（联络线）。DRT / SFRT / 公交 / 轮渡 / 航线等其它系统没有动。

## 数据来源与处理

1. **几何**：`https://railwaymap.big-brother.top/api/v1/geojson`
   （978 条边、669 个图节点），按「同一条线 + 度为 2 的中间节点」串成物理轨道链，
   在交叉点、车站、尽头断开 → 972 条链。
2. **平滑**：1 格重采样 → 滑动平均去台阶锯齿 → Douglas-Peucker(0.6 格) →
   按累计转角抽稀（弯道约 **20°** 一个点，直线最多 200 格一个点，弦偏差 ≤1.2 格）。
   结果：平均每公里 15.9 个顶点，最长线段 200 格。
3. **高架/地下判定**：抓上游 Dynmap 的 level-0 瓦片（4 像素/格，25563 张，
   缓存在 `.work/railwaymap/l0cache/`），沿线路取垂直剖面（±16 格、沿轨道 ±24 格平均），
   以「中心是否灰、亮度是否落在轨道结构范围、与两侧地表是否有明显色差」判定
   可见（→ `bridge=yes` + `layer=1`）或不可见（→ `tunnel=yes` + `layer=-1`）。
   48 个抽样点人工核对：可见 24/29 判对、不可见 13/13 判对（无误判为可见者）。
4. **与既有手绘数据的关系**：长岛站一带你画的 way 22–27 / 节点 604–606 全部保留，
   导入时把与之重叠的约 9.3 km 区段挖掉，并在端点复用你的节点 id 做拓扑衔接。
5. **上传**：本地 API（直连 Rails 3001，绕开 edge 的 300s 代理超时），
   分批 2500 节点 / 300 路径；完成后手工把关掉/漏掉的 changeset 补进渲染库。

## 已知取舍（需要你判断的地方）

* **可见 ≠ 一定高架**：按你的要求，只要卫星影像上看得见轨道就写成 `bridge=yes`；
  真正属于地面线的，请你按自己的判断改（把 `bridge`/`layer` 去掉即可）。
* **被建筑/顶棚盖住的轨道判成了地下**：判定依据是"从上往下能不能看见"，
  所以站房顶棚下的股道会得到 `tunnel=yes`。若那里实际是地面/高架，
  按你的规则需要手改。
* **联络线命名**：`contact` 的边按 railwaymap 的线路名写成 `name=联络线`，
  `usage=branch`；如果你希望按物理所属线路（普朗/芙德…）命名，改 `name` 即可。
* **没有画站台**：railwaymap 只有车站点位，没有站台几何，所以只建了
  `railway=station` 节点与停车点，没有 `railway=platform`（你手工画的两条站台仍在）。
* **没有建线路关系（route relation）**：数据里上下行是两套独立的轨道/运行图，
  需要的话可以按 lines.json 的方向再补 relation。

## 复现

```bash
cd railway-import/scripts
python3 prefetch_l0.py ../out/tiles_l0.txt 12     # 抓影像（约 1 小时，可跳过：已有缓存）
python3 run_import.py --dry-run --out /tmp/x.xml   # 只构建，不上传
python3 run_import.py --upload                     # 构建并上传（需 token.txt）
```

上传需要 `.work/railwaymap/token.txt`（本次为 aoiaihana 的 OAuth2 write_api token）。

## 目录

* `scripts/`：全部脚本（network 抽链、smooth 平滑、classify 影像判定、
  build_osm 生成 OSM、run_import 驱动、verify_render 核对）。
* `out/paralon-railway.osmchange.xml`：本次上传的完整 osmChange（8444 节点 + 1739 路径）。
* `out/chains_raw.json`：从 railwaymap 抽出的原始轨道链。

核对图（都用 Minecraft 影像 + 导入数据叠加）：

* `out/carto_check.png` / `out/carto_zoom.png`：本地 carto 渲染出的成品图
  （实线=bridge，虚线=tunnel），含站名。
* `out/verify_render.png` / `out/verify_random.png`：影像上叠加渲染库里的线路
  （绿=bridge，橙=tunnel），用来看线位是否贴着轨道。
* `out/overview_z12.png`：全网总览。
