#!/usr/bin/env python3
"""
ProxyIP Checker

读取 proxyip.txt（IP:port:CC，IPv6 用 [addr]:port:CC）。
对每条记录执行：
    curl --resolve api64.ipify.org:PORT:IP  https://api64.ipify.org:PORT
拿到的响应体就是出口 IP。
再用 ipinfo.io 查询该出口 IP 的国家，作为最新国家；
若查询失败 / 429 / 无 token，则回退到 proxyip.txt 中记录的国家。

结果写入 public/proxyips.json 与 data/history.json。
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from country_map import COUNTRY_MAP

BASE          = Path(__file__).resolve().parent.parent
PROXYIP_FILE  = BASE / "proxyip.txt"
PUBLIC_FILE   = BASE / "public" / "proxyips.json"
HISTORY_FILE  = BASE / "data" / "history.json"

TIMEOUT       = 10
TARGET_HOST   = "api64.ipify.org"
IPINFO_TOKEN  = os.environ.get("IPINFO_TOKEN", "").strip()

# 支持 IPv4、[IPv6]
LINE_RE = re.compile(r"^(\[[0-9A-Fa-f:]+\]|[0-9A-Fa-f:.]+):(\d+):([A-Za-z]{2})$")


def parse_line(line):
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    m = LINE_RE.match(line)
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3).upper()


def curl_check(ip, port):
    """通过 proxyip 拉取 api64.ipify.org，返回 (exit_ip, latency_ms) 或 None。"""
    start = time.time()
    cmd = [
        "curl", "-sS",
        "--resolve", f"{TARGET_HOST}:{port}:{ip}",
        "--max-time", str(TIMEOUT),
        "--connect-timeout", str(TIMEOUT),
        f"https://{TARGET_HOST}:{port}",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT + 3)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0:
        return None
    exit_ip = r.stdout.strip()
    # api64.ipify.org 只会返回一行 IP；过滤掉奇怪响应
    if not exit_ip or "\n" in exit_ip or " " in exit_ip:
        return None
    return exit_ip, int((time.time() - start) * 1000)


_country_cache = {}


def ipinfo_lookup(ip):
    """查 IP 国家。返回两位国家码或 None（表示需要 fallback）。"""
    if not IPINFO_TOKEN:
        return None
    clean = ip.strip("[]")
    if clean in _country_cache:
        return _country_cache[clean]

    url = f"https://ipinfo.io/{urllib.parse.quote(clean)}?token={IPINFO_TOKEN}"
    country = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "proxyip-check/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            country = (data.get("country") or "").upper() or None
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print("    [ipinfo] 429 超限 → 回退到文件记录国家")
        else:
            print(f"    [ipinfo] HTTP {e.code}")
    except Exception as e:
        print(f"    [ipinfo] {e}")

    _country_cache[clean] = country
    return country


def main():
    history = {}
    if HISTORY_FILE.exists():
        try:
            for rec in json.loads(HISTORY_FILE.read_text(encoding="utf-8")):
                history[rec["id"]] = rec
        except Exception:
            pass

    results = []
    now = int(time.time())

    for raw in PROXYIP_FILE.read_text(encoding="utf-8").splitlines():
        parsed = parse_line(raw)
        if not parsed:
            if raw.strip() and not raw.strip().startswith("#"):
                print(f"[skip] 格式错误: {raw!r}")
            continue

        ip, port, cc = parsed
        pid = f"{ip}_{port}"

        record = history.get(pid) or {
            "id": pid,
            "ip": ip,
            "port": port,
            "country": cc,
            "country_cn": COUNTRY_MAP.get(cc, cc),
            "success": 0,
            "total": 0,
        }
        record["total"] += 1

        print(f"[*] {ip}:{port} ({cc}) ...")
        res = curl_check(ip, port)
        if not res:
            print("    ✗ 失败")
            history[pid] = record
            continue

        exit_ip, latency = res
        record["latency"]     = latency
        record["last_check"]  = now
        record["success"]    += 1
        record["exit_ip"]     = exit_ip

        new_cc = ipinfo_lookup(exit_ip)
        if new_cc:
            record["country"]        = new_cc
            record["country_cn"]     = COUNTRY_MAP.get(new_cc, new_cc)
            record["country_source"] = "ipinfo"
        else:
            record["country_source"] = "fallback"

        print(f"    ✓ {latency}ms  exit={exit_ip}  → {record['country']} ({record['country_source']})")
        results.append(record)
        history[pid] = record

    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PUBLIC_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(list(history.values()), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    PUBLIC_FILE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n完成：可用 {len(results)} / 累计 {len(history)}")


if __name__ == "__main__":
    main()
