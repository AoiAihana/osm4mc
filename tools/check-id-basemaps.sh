#!/usr/bin/env bash
#
# 校验 iD 编辑器（/id）实际会拿到哪些底图。
#
# 它一步步复刻浏览器的加载链路，所以能在没有浏览器的情况下发现问题：
#
#   1. 取 /id 页面里的 data-asset-map
#   2. 按 iD 的规则解析 "data/imagery.min.json"（context.asset() 会把
#      assetPath 前缀拼上再查表）
#   3. 抓那个 URL，确认它就是 config/id_imagery.yml 渲染出来的清单
#   4. 对清单里的每个源，按 iD 的 "tms" 规则替换 {zoom}/{x}/{y}，
#      真的去取一张瓦片，必须返回 200
#   5. 检查 CSP 头允许这些瓦片 origin
#
# 需要一个已登录的会话（/id 要登录）。用法：
#
#   ./tools/check-id-basemaps.sh                     # 用环境变量里的 cookie
#   OSM_COOKIE_JAR=/tmp/cj.txt ./tools/check-id-basemaps.sh
#
# 不带 cookie 时会直接告诉你需要先登录，并把「未登录也能查」的部分（CSP、清单本身）查完。
#
set -uo pipefail

BASE="${OSM_BASE:-http://localhost:3000}"
JAR="${OSM_COOKIE_JAR:-}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail=0
ok()   { printf '  \033[32m✅\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m❌\033[0m %s\n' "$1"; fail=1; }
info() { printf '     %s\n' "$1"; }

# 取一张瓦片，返回 HTTP 状态码。
# 超时给得比较宽松：dyn2xyz 会为一张 XYZ 瓦片去上游抓 3×3 张源瓦片，
# 上游抖动时会触发重试，几十秒是可能的（见 dyn2xyz/README.md 的调参说明）。
TILE_TIMEOUT="${TILE_TIMEOUT:-90}"
http_code() {
    local code
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time "$TILE_TIMEOUT" "$1" 2>/dev/null) || true
    echo "${code:-000}"
}

echo "═══ 1. iD 底图清单本身 ═══"
if curl -sS --max-time 30 -o "$TMP/imagery.json" -w '' "$BASE/id/imagery.json" \
   && [ -s "$TMP/imagery.json" ]; then
    count=$(python3 -c "import json,sys; print(len(json.load(open('$TMP/imagery.json'))))" 2>/dev/null || echo "?")
    ok "/id/imagery.json 可访问，共 $count 个条目"
    # iD 只会把「没有 polygon」或「polygon 覆盖当前视野」的源列进「背景设置」，
    # 所以带 polygon 的条目是隐藏占位（见 config/id_imagery.yml 里 Bing 那段的说明）。
    python3 - "$TMP/imagery.json" <<'PY'
import json, sys
visible = hidden = 0
for s in json.load(open(sys.argv[1])):
    hid = bool(s.get("polygon"))
    hidden += hid
    visible += not hid
    tag = "隐藏占位" if hid else "会显示"
    print(f"       [{tag}] {s.get('id'):<14} {s.get('name'):<30} {s.get('template')}")
print(f"       => 背景设置里会看到 {visible} 项，另有 {hidden} 项隐藏占位")
PY
else
    bad "/id/imagery.json 不可访问（IdImageryController / 路由是否生效？）"
fi

echo
echo "═══ 2. CSP 是否允许这些瓦片 origin ═══"
# development 下默认是 Content-Security-Policy-Report-Only（见 settings.csp_enforce），
# 所以两种头都要认，否则会误判成「没有 CSP」。
#
# 清单里的 template 现在是根相对路径（/tile/... 、/tiles/...），被 CSP 的
# 'self' 覆盖；只有写绝对地址的源才需要单独出现在 img-src 里。脚本两种都查。
CSP=$(curl -sS -D - -o /dev/null --max-time 30 "$BASE/" 2>/dev/null | tr -d '\r' | grep -i '^content-security-policy' | head -1)
if [ -z "$CSP" ]; then
    info "没看到 CSP 头——跳过"
else
    for origin in $(python3 -c "
import json,re
try: srcs=json.load(open('$TMP/imagery.json'))
except Exception: srcs=[]
for s in srcs:
    tpl=(s.get('template','') or '')
    if tpl.startswith('/'):
        print('SELF')           # 相对地址：同源，由 'self' 覆盖
    else:
        m=re.match(r'(https?://[^/]+)', tpl)
        if m: print(m.group(1))
" 2>/dev/null | sort -u); do
        if [ "$origin" = "SELF" ]; then
            if echo "$CSP" | grep -q "'self'"; then
                ok "相对路径瓦片由 CSP 的 'self' 覆盖"
            else
                bad "CSP 里没有 'self'，相对路径瓦片会被拦截"
            fi
        elif echo "$CSP" | grep -q -- "$origin"; then
            ok "CSP 允许 $origin"
        else
            bad "CSP 未包含 $origin（检查 config/layers.yml 与 config/id_imagery.yml 的派生逻辑）"
        fi
    done
fi

echo
echo "═══ 3. /id 页面的 asset map 是否指向我们的清单 ═══"
if [ -n "$JAR" ] && [ -f "$JAR" ]; then
    code=$(curl -sS -L -b "$JAR" -c "$JAR" --max-time 40 -o "$TMP/id.html" -w '%{http_code}' "$BASE/id")
    if [ "$code" != "200" ]; then
        bad "/id 返回 $code（会话过期？）"
    else
        python3 - "$TMP/id.html" >>"$TMP/am.log" 2>&1 <<'PY'
import re, html, json, sys
t = open(sys.argv[1], encoding='utf-8', errors='replace').read()
m = re.search(r'data-asset-map="(.*?)"', t, re.S)
if not m:
    print("NO_ASSET_MAP"); raise SystemExit
d = json.loads(html.unescape(m.group(1)))
key = "@openstreetmap/id/dist/data/imagery.min.json"
print("VALUE=" + str(d.get(key)))
print("COUNT=" + str(len(d)))
PY
        val=$(grep '^VALUE=' "$TMP/am.log" | cut -d= -f2-)
        if [ -z "$val" ] || [ "$val" = "None" ]; then
            bad "asset map 里没有覆盖 imagery 项（app/views/site/id.html.erb 是否生效？）"
            grep -q NO_ASSET_MAP "$TMP/am.log" && info "页面里根本没找到 data-asset-map"
        elif [ "$val" = "/id/imagery.json" ]; then
            ok "imagery 已重定向到 $val"
        else
            bad "imagery 指向了 $val（期望 /id/imagery.json）"
        fi
    fi
else
    info "未提供 OSM_COOKIE_JAR，跳过（/id 需要登录）"
fi

echo
echo "═══ 4. 清单里每个源都真能取到瓦片 ═══"
# 用 iD 的 "tms" 规则替换占位符：{x} {y} {zoom}（{z} 亦可）。
# 相对路径（/tile/...）按浏览器的规则解析到 $BASE 上——清单里现在用的就是
# 相对路径，因为绝对地址在别的设备上会指向设备自己的 localhost。
python3 - "$TMP/imagery.json" "$BASE" >"$TMP/urls.txt" <<'PY'
import json, sys
srcs, base = json.load(open(sys.argv[1])), sys.argv[2].rstrip("/")
for s in srcs:
    tpl = s.get("template", "")
    if s.get("type") != "tms" or "{x}" not in tpl:
        continue
    ext = s.get("zoomExtent") or [0, 19]
    z = max(int(ext[0]), min(int(ext[1]), 15))     # 取一个落在范围内的层级
    n = 2 ** z
    # 赤道与本初子午线交点所在的瓦片
    x = y = n // 2
    url = (tpl.replace("{x}", str(x)).replace("{y}", str(y))
              .replace("{zoom}", str(z)).replace("{z}", str(z)))
    if url.startswith("/"):
        url = base + url
    print(f"{s.get('id')}\t{url}")
PY
while IFS=$'\t' read -r id url; do
    [ -z "${url:-}" ] && continue
    code=$(http_code "$url")
    if [ "$code" = "200" ]; then
        ok "$id 取到瓦片 (HTTP 200)"
        info "$url"
    else
        bad "$id 取瓦片失败 (HTTP $code)"
        info "$url"
    fi
done <"$TMP/urls.txt"

echo
if [ "$fail" = "0" ]; then
    echo "✅ 全部通过"
else
    echo "❌ 存在失败项"
fi
exit "$fail"
