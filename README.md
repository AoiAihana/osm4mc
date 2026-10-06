# osm4mc

一个自建的、**Minecraft 风格**的 OpenStreetMap 实例：把
[openstreetmap-website](https://github.com/openstreetmap/openstreetmap-website)（API + iD 编辑器）
与 [openstreetmap-carto](https://github.com/openstreetmap-carto/openstreetmap-carto)（渲染样式）
接在**同一个 PostgreSQL** 上，由本地渲染服务出瓦片——数据只在自己机器上，不依赖任何公共瓦片服务。

> **名称状态**：`osm4mc` 是暂定名。

---

## 声明（重要）

- **与 OpenStreetMap Foundation 无隶属关系**，也未获其背书。
  OpenStreetMap、OSM、OSMF 是 OpenStreetMap Foundation 的商标。
- **与 Mojang Studios / Microsoft 无隶属关系**，也未获其背书。
  「Minecraft」是 Mojang Studios 的商标。本项目的 Minecraft 风格样式、图标与标签
  均为**按该风格自行绘制/编写**，不包含任何游戏素材文件，也不代表 Mojang 的授权或认可。
- **本仓库不分发 OpenStreetMap 数据**。`gis` 库、外部 shapefile、字体等都由使用者自己
  导入/下载，属本地运行时状态；OSM 数据受 **ODbL-1.0** 约束。详见 [COPYRIGHT.md](COPYRIGHT.md) §4、§5。

---

## 组成

| 部分 | 位置 | 说明 |
|---|---|---|
| 部署编排 | `docker-compose.yml` · `docker-compose.prod.yml` · `edge/` · `.env*` | 单 PostgreSQL + web / sync / tileserver / dyn2xyz / edge |
| 同步服务 | `sync/` | 读 API 库的 changeset → 导出 osmChange → osm2pgsql `--append --slim` → `gis` 库 |
| 渲染服务 | `tileserver/` | renderd + mod_tile（Apache），直接读 `gis` |
| 网站容器入口 | `web/start.sh` | 资源重建、iD 补丁校验、关系成员守卫、启动 Puma |
| Dynmap 转译 | `dyn2xyz/` | 把 Dynmap 瓦片实时转成标准 `{z}/{x}/{y}` |
| 工具 | `tools/` | 环境切换、备份、端到端验证、发布前检查等 |
| 导入器 | `natural-import/` · `railway-import/` · `sfrt-import/` | 按需把外部数据画进本地 OSM（脚本入库；`out/` 不入库） |
| 上游分支 | `openstreetmap-website/` · `osm-carto4mc/` | **git submodule**，指向本项目在上游项目里的分支，便于随时拉取上游更新 |
| 文档 | `README-COMBINED.md` | 安装、依赖、运维的完整说明 |
| 许可 | [COPYRIGHT.md](COPYRIGHT.md) · [LICENSE](LICENSE) | 逐组件的著作权与许可声明 |

> 本仓库**不包含**两类本地目录（权利人决定不发布，已在 `.gitignore` 中整目录排除）：
> `coastline/`（海岸线提取的本地工程目录，内含 OAuth 令牌）与
> `mcmap/`（Minecraft 瓦片方案的研究产出，含第三方仓库克隆）。

`openstreetmap-website/` 与 `osm-carto4mc/` 是 submodule：本项目对上游的改动以**分支**形式
保存在各自的上游仓库里（见 [PUBLISHING.md](PUBLISHING.md)），因此可以 `git submodule update --remote`
跟上上游。

---

## 快速开始

完整步骤（依赖、安装、初始化、运维）见 **[README-COMBINED.md](README-COMBINED.md)**。
最短路径：

```bash
git clone --recurse-submodules <本仓库>
cd osm4mc
./tools/switch-env.sh init test     # 起测试栈
```

---

## 许可

| 内容 | 许可 |
|---|---|
| 本项目自行编写的内容（编排、工具、导入器、文档……） | **[CC0-1.0](LICENSE)** —— 可用可改可商用，无需署名 |
| `openstreetmap-website` 分支（含本项目对上游的 8 处修改） | **GPL-2.0** |
| `osm-carto4mc` 分支（Minecraft 样式） | **CC0-1.0** |
| OpenStreetMap 数据、外部 shapefile | **ODbL-1.0**（不随本仓库分发） |
| 字体、容器镜像内的第三方组件 | 各自许可，见 [COPYRIGHT.md](COPYRIGHT.md) §3、§5 |

> 逐组件的完整声明（含例外、第三方数据来源、商标）请看 **[COPYRIGHT.md](COPYRIGHT.md)**。
