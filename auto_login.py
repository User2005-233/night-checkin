#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""学工自动登录：拿验证码 -> OCR -> 带签名登录 -> 返回 xgUniToken。
签名算法复刻自前端 index.*.js（K0/Y0/Q0/G0/X0），纯本地计算。
安全：密码只从 cred.txt 读（第1行账号，第2行密码），永不打印、永不记日志。
"""
import base64
import hashlib
import hmac
import json
import os
import random
import time

import requests

BASE = "https://xg.tjise.edu.cn/dr/api"
CLIENT = "NzVmNDdlMjJiMGNjMTFlZGExNGQ4MmUxNDExNWM2OWY6N2FkOTI4YTliMGNjMTFlZGExNGQ4MmUxNDExNWM2OWY="
RAW_KEY = "7afdca069faa4acb8c0ab09322636245"
CLIENT_KEY = "Pq3Zk2A9xML7"
TIMEOUT = 20
MAX_CAPTCHA_TRY = 3


def _h(s, key):
    return hmac.new(key.encode(), s.encode(), hashlib.sha256).hexdigest()


def _nonce():
    s = "%s-%s-%s" % (int(time.time() * 1000), random.random(), random.random())
    return hashlib.sha256(s.encode()).hexdigest()[:32]


def sign_headers(method, api_path, data, ts=None, nonce=None):
    """复刻 K0：返回 X-Page-Ticket/X-Timestamp/X-Nonce/X-Token。
    ts/nonce 留参只为用抓包真值验签，平时传 None 自动生成。"""
    ts = int(time.time() * 1000) if ts is None else ts
    nonce = _nonce() if nonce is None else nonce
    page_ticket = _h(RAW_KEY, CLIENT_KEY)
    if data is None:
        body_hash = hashlib.sha256(b"").hexdigest()
    elif method.upper() == "GET":
        canon = json.dumps({k: str(data[k]) for k in sorted(data)},
                           separators=(",", ":"), ensure_ascii=False)
        body_hash = hashlib.sha256(canon.encode()).hexdigest()
    else:
        raw = data if isinstance(data, str) else json.dumps(
            data, separators=(",", ":"), ensure_ascii=False)
        body_hash = hashlib.sha256(raw.encode()).hexdigest()
    joined = "\n".join([method.upper(), api_path, str(ts), nonce,
                        body_hash, page_ticket])
    l2 = _h("%s|%s|%s|%s" % (page_ticket, ts // 1000, api_path, nonce),
            CLIENT_KEY)
    inner = _h(joined, l2)
    b64 = base64.b64encode(inner.encode()).decode()
    key2 = _h("%s|%s" % (api_path, nonce), CLIENT_KEY)[:16]
    xored = bytes(b ^ ord(key2[i % len(key2)]) for i, b in enumerate(b64.encode()))
    token = base64.b64encode(xored).decode()[::-1]
    return {"X-Page-Ticket": page_ticket, "X-Timestamp": str(ts),
            "X-Nonce": nonce, "X-Token": token}


def fetch_captcha(sess):
    r = sess.post(BASE + "/auth/captcha", json={"client": CLIENT, "userType": "1"},
                  timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get("code") != 1:
        raise RuntimeError("拿验证码失败: %s" % json.dumps(d, ensure_ascii=True)[:200])
    img = d["data"]["image"].split(",", 1)[1]
    return d["data"]["key"], base64.b64decode(img)


_ocr = None


def solve(img_bytes):
    global _ocr
    if _ocr is None:
        import ddddocr
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr.classification(img_bytes).strip()


def load_cred(path):
    with open(path, encoding="utf-8") as f:
        lines = [x.strip() for x in f.read().splitlines()]
    if len(lines) < 2 or not lines[0] or not lines[1]:
        raise ValueError("cred.txt 格式不对：第1行账号，第2行密码")
    return lines[0], lines[1]


def do_login(cred_path):
    """返回新 token。验证码错换图重试；账号密码错立刻停手。"""
    user, pwd = load_cred(cred_path)
    sess = requests.Session()
    sess.headers.update({"content-type": "application/json",
                         "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0.0.0 Mobile Safari/537.36"})
    last_err = ""
    for _ in range(MAX_CAPTCHA_TRY):
        uuid, img = fetch_captcha(sess)
        try:
            code = solve(img)
        except Exception as e:
            last_err = "OCR 异常: %s" % e
            continue
        if not code:
            last_err = "验证码识别为空"
            continue
        body = {"userName": user, "pwd": pwd, "code": code, "uuid": uuid,
                "userType": "2", "client": CLIENT, "grantType": "pwd"}
        h = sign_headers("POST", "/auth/login", body)
        # 注意：签名是对紧凑 JSON 原始字节算的，必须原样发送，
        # 不能用 requests 的 json=（它默认加空格，会导致签名 mismatch）
        raw = json.dumps(body, separators=(",", ":"),
                         ensure_ascii=False).encode("utf-8")
        h["content-type"] = "application/json"
        r = sess.post(BASE + "/auth/login", data=raw, headers=h,
                      timeout=TIMEOUT)
        try:
            d = r.json()
        except Exception:
            last_err = "HTTP %s 非 JSON：%s" % (r.status_code, r.text[:150])
            continue  # 网络层抖动：换验证码重试
        if d.get("code") == 1 and (d.get("data") or {}).get("token"):
            return d["data"]["token"]
        last_err = "%s %s" % (d.get("code"), d.get("message"))
        msg = str(d.get("message") or "")
        if any(k in msg for k in ("密码", "账号", "用户不", "锁定", "冻结", "停用")):
            raise RuntimeError("停手：疑似账号密码问题（%s），请检查 cred.txt" % msg)
        # 疑似验证码错：换图重试
    raise RuntimeError("登录失败（已换 %d 次验证码）：%s" % (MAX_CAPTCHA_TRY, last_err))
