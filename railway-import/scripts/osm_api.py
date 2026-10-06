"""极简 OSM API 客户端（对接本地 rails port）。

* create_changeset / upload(osmChange) / close_changeset
* 上传按批切分，返回服务器 diffResult 里的新 id 映射。
"""
from __future__ import annotations

import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


class OsmApi:
    def __init__(self, base: str = "http://localhost:3000", token: str | None = None, timeout: int = 180):
        self.base = base.rstrip("/")
        if token is None:
            with open(os.path.join(HERE, "token.txt")) as fh:
                token = fh.read().strip()
        self.token = token
        self.timeout = timeout

    def _req(self, method: str, path: str, body: bytes | None = None, ctype: str = "application/xml"):
        url = self.base + path
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if body is not None:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    # -- changeset -------------------------------------------------------
    def create_changeset(self, tags: dict[str, str]) -> int:
        root = ET.Element("osm")
        cs = ET.SubElement(root, "changeset")
        for k, v in tags.items():
            t = ET.SubElement(cs, "tag")
            t.set("k", k)
            t.set("v", v)
        xml = ET.tostring(root, encoding="utf-8")
        status, text = self._req("PUT", "/api/0.6/changeset/create", xml)
        if status != 200:
            raise RuntimeError(f"changeset create failed {status}: {text[:500]}")
        return int(text.strip())

    def close_changeset(self, cs_id: int) -> None:
        status, text = self._req("PUT", f"/api/0.6/changeset/{cs_id}/close")
        if status != 200:
            raise RuntimeError(f"changeset close failed {status}: {text[:300]}")

    # -- upload ----------------------------------------------------------
    def upload(self, cs_id: int, osmchange: str, retries: int = 3) -> ET.Element:
        for attempt in range(retries):
            status, text = self._req("POST", f"/api/0.6/changeset/{cs_id}/upload", osmchange.encode("utf-8"))
            if status == 200:
                return ET.fromstring(text)
            if status in (500, 502, 503, 504, 429):
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"upload failed {status}: {text[:2000]}")
        raise RuntimeError(f"upload failed after {retries} retries: {text[:500]}")


def build_osmchange(creates: list[str] | None = None, modifies: list[str] | None = None,
                    deletes: list[str] | None = None) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<osmChange version="0.6" generator="railway-import">']
    for tag, items in (("create", creates), ("modify", modifies), ("delete", deletes)):
        if not items:
            continue
        parts.append(f"<{tag}>")
        parts.extend(items)
        parts.append(f"</{tag}>")
    parts.append("</osmChange>")
    return "\n".join(parts)


def parse_diff_result(xml_root: ET.Element) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {"node": {}, "way": {}, "relation": {}}
    for el in xml_root:
        tag = el.tag
        if tag not in out:
            continue
        old = el.get("old_id")
        new = el.get("new_id")
        if old is not None and new is not None:
            out[tag][old] = int(new)
    return out
