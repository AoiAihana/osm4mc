#!/bin/bash
#
# 检查 iD 元素预设的**接线**是否真的通了（data 侧由
# openstreetmap-website/scripts/verify_id_minecraft_presets.sh 负责，两者互补）。
#
# 为什么需要单独一条：preset 数据全都正确伺服、页面 asset_map 也写对了，
# **编辑器仍然可能整个起不来** —— 因为接线发生在
# app/assets/javascripts/id.js 里，而那段 JS 只有浏览器会执行，
# 前面那个脚本只 curl HTML/JSON，永远看不到它抛异常。
#
# 真实踩过的坑：`iD.fileFetcher` 是 coreFileFetcher 的**单例对象**，不是函数。
# 写成 `iD.fileFetcher()` 会在 DOMContentLoaded 回调里、coreContext() 之前抛
# `TypeError: iD.fileFetcher is not a function`，于是 /id 打开是一片空白，
# 而所有 HTTP 层检查依然全绿。
#
# 检查项：
#   1. 页面里的 asset map / data-id-schema-files 指向本实例（不是 jsDelivr）；
#   2. **预编译产物**里是修好的写法（同时也证明了「改了 JS 有重新 precompile」）；
#   3. 用 node 真正执行一遍产物里的 DOMContentLoaded 回调，
#      断言 iD 的 fileMap 被改成相对路径、且 asset() 解析到本实例的产物。
#
# 用法：tools/check-id-preset-wiring.sh [基地址] [用户名] [密码]
#   默认 http://localhost:3000 aoiaihana password
#
set -u

BASE="${1:-http://localhost:3000}"
USER_NAME="${2:-aoiaihana}"
PASSWORD="${3:-password}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
JAR="$TMP/cookies.txt"
fail=0
ok()  { printf '  \033[32m✓\033[0m %s\n' "$1"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$1"; fail=1; }

echo "== 1) 登录并抓 /id =="
curl -s -c "$JAR" -L "$BASE/login" -o "$TMP/login.html"
TOKEN="$(grep -o 'name="authenticity_token"[^>]*value="[^"]*"' "$TMP/login.html" | head -1 | sed 's/.*value="//;s/"//')"
[ -n "$TOKEN" ] || { echo "取不到 authenticity_token，无法登录"; exit 2; }
curl -s -b "$JAR" -c "$JAR" -o /dev/null -X POST "$BASE/login" \
  --data-urlencode "username=$USER_NAME" --data-urlencode "password=$PASSWORD" \
  --data-urlencode "authenticity_token=$TOKEN" --data-urlencode "referer=/id"
curl -s -b "$JAR" "$BASE/id" -o "$TMP/id.html"

SRC="$(grep -o '/assets/id-[a-f0-9]*\.js' "$TMP/id.html" | head -1)"
if [ -n "$SRC" ]; then ok "预编译产物 $SRC"; else bad "页面里找不到 /assets/id-*.js"; exit 1; fi
curl -s "$BASE$SRC" -o "$TMP/idasset.js"
ok "产物 $(wc -c <"$TMP/idasset.js") 字节"

echo "== 2) 页面下发的覆盖表 =="
python3 - "$TMP/id.html" "$TMP" <<'PY' > "$TMP/overrides.json"
import html, json, re, sys
page = open(sys.argv[1], encoding="utf-8").read()
out = sys.argv[2]
for attr, name in (("data-asset-map", "assetMap"), ("data-id-schema-files", "overrides")):
    m = re.search(attr + r'="([^"]*)"', page)
    if not m:
        print(f"页面里没有 {attr}", file=sys.stderr); sys.exit(3)
    data = json.loads(html.unescape(m.group(1)))
    json.dump(data, open(f"{out}/{name}.json", "w"))
print(json.dumps(json.load(open(f"{out}/overrides.json")), ensure_ascii=False))
PY
[ -s "$TMP/overrides.json" ] || { bad "取不到 data-id-schema-files"; exit 1; }

for key in preset_presets preset_fields preset_categories preset_defaults; do
  grep -qF "\"$key\"" "$TMP/overrides.json" && ok "fileMap 覆盖含 $key" || bad "fileMap 覆盖缺 $key"
done
if grep -qF '"/id/minecraft/' "$TMP/assetMap.json"; then
  ok "asset map 指向本实例 /id/minecraft/（不是 jsDelivr）"
else
  bad "asset map 里没有本实例的产物地址"
fi

echo "== 3) 产物里的接线写法（顺带证明改了 JS 有重新 precompile）=="
if grep -qF 'iD.fileFetcher().fileMap' "$TMP/idasset.js"; then
  bad "产物里还是 iD.fileFetcher().fileMap —— iD.fileFetcher 是对象不是函数，会抛 TypeError 让编辑器白屏"
elif grep -qF 'iD.fileFetcher.fileMap' "$TMP/idasset.js"; then
  ok "产物里是 iD.fileFetcher.fileMap（正确写法）"
else
  bad "产物里找不到 fileMap 覆盖代码（是不是没写？或者改了 JS 没跑 assets:precompile）"
fi

echo "== 4) 真正执行一遍产物里的 DOMContentLoaded 回调 =="
if ! command -v node >/dev/null 2>&1; then
  echo "  (跳过：宿主机没有 node)"
else
  node - "$TMP" <<'JS'
const fs = require('fs'), vm = require('vm'), path = process.argv[2];
const code = fs.readFileSync(path + '/idasset.js', 'utf8');
const overrides = JSON.parse(fs.readFileSync(path + '/overrides.json', 'utf8'));
const assetMap = JSON.parse(fs.readFileSync(path + '/assetMap.json', 'utf8'));

let domReady = null;
const mkEl = id => ({
  id, className: '', innerHTML: '', style: {}, dataset: {
    token: 'probe', locale: 'zh-CN', theme: 'light',
    assetMap: JSON.stringify(assetMap), idSchemaFiles: JSON.stringify(overrides)
  },
  addEventListener() {}, appendChild() {}, setAttribute() {},
  querySelector: () => null, querySelectorAll: () => [],
  classList: { add() {}, remove() {} },
  getBoundingClientRect: () => ({ width: 800, height: 600, top: 0, left: 0 })
});

const s = {
  console, URL, URLSearchParams, TextEncoder, TextDecoder, Blob, performance,
  setTimeout, clearTimeout, setInterval, clearInterval,
  navigator: {
    userAgent: 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
    languages: ['zh-CN'], language: 'zh-CN', platform: 'Linux x86_64',
    maxTouchPoints: 0, hardwareConcurrency: 8
  },
  location: {
    protocol: 'http:', host: 'localhost:3000', hostname: 'localhost', port: '3000',
    pathname: '/id', search: '', hash: '', href: 'http://localhost:3000/id',
    origin: 'http://localhost:3000'
  },
  document: {
    addEventListener(type, cb) { if (type === 'DOMContentLoaded') domReady = cb; },
    createElement: () => mkEl(), createElementNS: () => mkEl(),
    documentElement: mkEl(), body: mkEl(), head: mkEl(),
    getElementById: id => (id === 'id-container' ? mkEl(id) : null),
    querySelector: () => null, querySelectorAll: () => []
  },
  screen: { width: 1920, height: 1080, availWidth: 1920, availHeight: 1080, colorDepth: 24 },
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  XMLHttpRequest: function () {},
  fetch: () => Promise.reject(new Error('probe: 不需要网络')),
  MutationObserver: function () { this.observe = () => {}; },
  requestAnimationFrame: cb => setTimeout(cb, 0),
  getComputedStyle: () => ({ getPropertyValue: () => '' })
};
s.window = s; s.self = s; s.globalThis = s;

let exitCode = 0;
const say = (mark, msg) => console.log(`  ${mark} ${msg}`);

try {
  vm.createContext(s);
  vm.runInContext(code, s, { filename: 'id-asset.js' });
} catch (e) {
  say('\x1b[31m✗\x1b[0m', `产物加载就失败了：${e.message}`);
  process.exit(1);
}
if (typeof domReady !== 'function') {
  say('\x1b[31m✗\x1b[0m', '产物里没有注册 DOMContentLoaded 回调');
  process.exit(1);
}

let err = null;
try { domReady(); } catch (e) { err = e; }

const ff = s.iD && s.iD.fileFetcher;
if (!ff || typeof ff.fileMap !== 'function') {
  say('\x1b[31m✗\x1b[0m', 'iD.fileFetcher / fileMap() 拿不到（iD 版本变了？）');
  process.exit(1);
}
const map = ff.fileMap();
const resolved = ff.asset(map.preset_presets);

if (map.preset_presets === 'data/presets.min.json') {
  say('\x1b[32m✓\x1b[0m', 'handler 跑完后 fileMap.preset_presets = data/presets.min.json');
} else {
  say('\x1b[31m✗\x1b[0m', `handler 跑完后 fileMap.preset_presets 还是 ${map.preset_presets}`);
  if (err) say(' ', `（回调抛的异常：${err.message}）`);
  exitCode = 1;
}
if (resolved === '/id/minecraft/data/presets.min.json') {
  say('\x1b[32m✓\x1b[0m', `asset() 解析到 ${resolved}`);
} else {
  say('\x1b[31m✗\x1b[0m', `asset() 解析到 ${resolved}（应当是本实例的产物）`);
  exitCode = 1;
}
if (err) {
  // 桩 DOM 缺少浏览器 API，init() 往往还会在更深的地方抛异常，这属正常；
  // 关键看上面两条断言。只有异常发生在 fileMap 覆盖之前才致命。
  say('\x1b[33m·\x1b[0m', `回调后续异常（桩 DOM 的正常现象，已越过覆盖代码）：${err.message}`);
}
process.exit(exitCode);
JS
  [ "$?" = 0 ] && ok "产物里的接线真的生效" || bad "产物里的接线没生效（见上面的 ✗）"
fi

echo
if [ "$fail" = 0 ]; then echo "全部通过"; else echo "有检查未通过"; fi
exit "$fail"
