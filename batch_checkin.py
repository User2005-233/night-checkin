#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量晚签：读取 accounts.json，顺序执行 auto_checkin.py，最后只推一条汇总。

accounts.json 示例：
{
  "sendkey": "SCT汇总SendKey",
  "defaults": {
    "photos": "photos",
    "delay_seconds": 1
  },
  "accounts": [
    {"alias": "room301", "user": "2023xxxx", "password": "******",
     "photos": "photos/room301", "enabled": true}
  ]
}

规则：
- alias 必填、唯一，仅 A-Z a-z 0-9 . _ -，用作 .state/<alias>/ 目录名
- token 只写 .state/<alias>/token.txt，不写回 JSON
- 每账号照片独立目录（不同宿舍不能共用底图）
- 子进程一律 --no-notify；成功/失败只由本脚本汇总推送一条
- 每天最多 1 条“正常汇总”；当天出现失败时最多再补 1 条失败汇总
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

# 防止系统代理（如科学上网、校园网中间人）拦截请求
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
del _k

try:
    import requests
except ImportError:
    print("缺少依赖 requests，请先执行 setup.sh / setup.bat")
    sys.exit(99)

HERE = os.path.dirname(os.path.abspath(__file__))
ACCOUNTS_FILE = os.path.join(HERE, "accounts.json")
STATE_DIR = os.path.join(HERE, ".state")
CHECKIN_LOG = os.path.join(HERE, "checkin.log")
PUSH_LOG = os.path.join(HERE, "push.log")
SENDKEY_FILE = os.path.join(HERE, "sendkey.txt")
PUSH_DAILY_CAP = 3
TIMEOUT = 180
ALIAS_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")

EXIT_TEXT = {
    0: "签到成功",
    1: "失败",
    2: "不在签到窗口",
    3: "今日已签到",
    4: "今晚无任务",
    9: "脚本崩溃",
    99: "缺少依赖",
}


def log(*a):
    line = "%s [BATCH] %s" % (
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        " ".join(str(x) for x in a))
    try:
        with open(CHECKIN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def load_config(path):
    with open(path, encoding="utf-8-sig") as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        raise ValueError("顶层必须是 JSON 对象")
    raw_accounts = cfg.get("accounts")
    if not isinstance(raw_accounts, list) or not raw_accounts:
        raise ValueError("accounts 必须是非空数组")
    defaults = cfg.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError("defaults 必须是对象")
    default_photos = str(defaults.get("photos") or ".").strip()
    if not os.path.isabs(default_photos):
        default_photos = os.path.join(HERE, default_photos)

    try:
        delay = max(0.0, float(defaults.get("delay_seconds", 1)))
    except (TypeError, ValueError):
        delay = 1.0

    seen = set()
    accounts = []
    for i, acc in enumerate(raw_accounts, 1):
        if not isinstance(acc, dict):
            raise ValueError("第 %d 个账号不是对象" % i)
        alias = str(acc.get("alias") or "").strip()
        user = str(acc.get("user") or "").strip()
        password = str(acc.get("password") or "")
        if not ALIAS_RE.match(alias):
            raise ValueError(
                "第 %d 个账号 alias 不合法（仅 A-Z a-z 0-9 . _ -，1-32 位）：%r"
                % (i, alias))
        if alias in (".", "..") or alias.endswith("."):
            raise ValueError("[%s] alias 不能是 . / .. 或以点结尾" % alias)
        if alias.lower() in seen:
            raise ValueError("alias 重复（不区分大小写）：%s" % alias)
        seen.add(alias.lower())
        if not user or not password:
            raise ValueError("[%s] user/password 不能为空" % alias)
        enabled = acc.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("[%s] enabled 必须是 true/false" % alias)
        photos = str(acc.get("photos") or default_photos).strip()
        if not os.path.isabs(photos):
            photos = os.path.join(HERE, photos)
        accounts.append({
            "alias": alias,
            "user": user,
            "password": password,
            "sendkey": str(acc.get("sendkey") or "").strip(),
            "photos": os.path.normpath(photos),
            "enabled": enabled,
        })
    return cfg, accounts, delay


def photo_ok(path):
    if not os.path.isdir(path):
        return False, "照片目录不存在：%s" % path
    try:
        names = os.listdir(path)
    except OSError as e:
        return False, "照片目录无法读取：%s" % e
    if not any(n.lower().endswith((".jpg", ".jpeg")) and not n.startswith("out_")
               for n in names):
        return False, "照片目录没有 jpg：%s" % path
    return True, ""


def load_sendkey(cli_key):
    if cli_key:
        return cli_key.strip()
    k = os.environ.get("SERVERCHAN_SENDKEY", "").strip()
    if k:
        return k
    if os.path.exists(SENDKEY_FILE):
        with open(SENDKEY_FILE, encoding="utf-8-sig") as f:
            return f.read().strip()
    return ""


def push_sent_today(kind):
    if not os.path.exists(PUSH_LOG):
        return 0
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    n = 0
    with open(PUSH_LOG, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith(today) and kind in line:
                n += 1
    return n


def mark_push(kind):
    with open(PUSH_LOG, "a", encoding="utf-8") as f:
        f.write("%s %s\n"
                % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), kind))


def push_phone(sendkey, title, desp, kind):
    if not sendkey:
        log("推送通道未配置：缺 sendkey，汇总推送跳过")
        return False
    if push_sent_today("") >= PUSH_DAILY_CAP:
        log("推送限流：今日已达 %d 条上限，跳过汇总推送" % PUSH_DAILY_CAP)
        return False
    try:
        if sendkey.startswith("sctp"):
            uid = sendkey[4:].split("t")[0]
            url = "https://%s.push.ft07.com/send/%s.send" % (uid, sendkey)
        else:
            url = "https://sctapi.ftqq.com/%s.send" % sendkey
        r = requests.post(url, json={"title": title[:32], "desp": desp},
                          timeout=20)
        ok = r.json().get("code") == 0
        log("汇总推送：", "成功" if ok else ("失败 " + r.text[:150]))
        if ok:
            mark_push(kind)
        return ok
    except Exception as e:
        log("汇总推送异常：", e)
        return False


def build_env(acc, token_file):
    env = os.environ.copy()
    env["NIGHT_LABEL"] = acc["alias"]
    env["NIGHT_TOKEN_FILE"] = token_file
    env["NIGHT_CRED_FILE"] = os.path.join(os.path.dirname(token_file), "cred.txt")
    env["NIGHT_USER"] = acc["user"]
    env["NIGHT_PASS"] = acc["password"]
    env["NIGHT_PHOTOS_DIR"] = acc["photos"]
    env["NIGHT_PUSH_LOG"] = os.path.join(os.path.dirname(token_file), "push.log")
    env["PYTHONIOENCODING"] = "utf-8"
    person_key = acc.get("sendkey") or ""
    if person_key:
        env["SERVERCHAN_SENDKEY"] = person_key
    else:
        env.pop("SERVERCHAN_SENDKEY", None)
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        env.pop(k, None)
    return env


def strip_log_prefix(line):
    return re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+(\[[^\]]+\]\s+)?",
                  "", line)


def run_one(acc, dry_run):
    state = os.path.join(STATE_DIR, acc["alias"])
    os.makedirs(state, exist_ok=True)
    token_file = os.path.join(state, "token.txt")
    env = build_env(acc, token_file)
    cmd = [sys.executable, os.path.join(HERE, "auto_checkin.py"),
           "--no-toast", "--push-success-only"]
    if not acc.get("sendkey"):
        cmd.append("--no-push")
    if dry_run:
        cmd.append("--dry-run")
    try:
        p = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=TIMEOUT)
        code = p.returncode
        detail = ""
        lines = [x.strip() for x in (p.stdout or "").splitlines() if x.strip()]
        if lines:
            detail = strip_log_prefix(lines[-1])
        if code not in (0, 2, 3, 4):
            errs = [x.strip() for x in (p.stderr or "").splitlines() if x.strip()]
            if errs:
                detail = strip_log_prefix(errs[-1])
    except subprocess.TimeoutExpired:
        code = 9
        detail = "超时（%ds）" % TIMEOUT
    except Exception as e:
        code = 9
        detail = repr(e)
    log("[%s] %s%s" % (acc["alias"], EXIT_TEXT.get(code, "退出码 %s" % code),
                       ("：" + detail) if detail else ""))
    return code, detail


def summary_text(results, disabled):
    lines = ["时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    for r in results:
        lines.append("[%s] %s" % (r["alias"], r["text"]))
    for alias in disabled:
        lines.append("[%s] 已停用" % alias)
    return "\n".join(lines)


def run_batch(accounts, delay, dry_run, sendkey, test_push):
    if test_push:
        if dry_run:
            log("dry-run 不推送测试消息")
            return 0
        ok = push_phone(sendkey, "自动晚签：批量测试",
                        "这是一条批量汇总测试消息。\n收到说明汇总推送链路正常。",
                        "batch-test")
        return 0 if ok else 1

    enabled = [a for a in accounts if a["enabled"]]
    disabled = [a["alias"] for a in accounts if not a["enabled"]]
    person_push = sum(1 for a in enabled if a.get("sendkey"))
    log("开始批量签到：启用 %d 个（本人推送 %d 个），停用 %d 个%s"
        % (len(enabled), person_push, len(disabled),
           "（dry-run）" if dry_run else ""))
    if not enabled:
        log("没有启用账号，结束")
        return 0

    results = []
    for idx, acc in enumerate(enabled):
        ok, why = photo_ok(acc["photos"])
        if not ok:
            log("[%s] 跳过：%s" % (acc["alias"], why))
            results.append({"alias": acc["alias"], "code": 1,
                            "text": "失败：%s" % why})
        else:
            code, detail = run_one(acc, dry_run)
            text = EXIT_TEXT.get(code, "退出码 %s" % code)
            if code not in (0, 2, 3, 4) and detail:
                text += "：" + detail[-160:]
            results.append({"alias": acc["alias"], "code": code, "text": text})
        if idx + 1 < len(enabled) and delay:
            time.sleep(delay)

    fails = [r for r in results if r["code"] not in (0, 2, 3, 4)]
    ok_n = sum(1 for r in results if r["code"] == 0)
    already_n = sum(1 for r in results if r["code"] == 3)
    outwin_n = sum(1 for r in results if r["code"] == 2)
    notask_n = sum(1 for r in results if r["code"] == 4)

    log("汇总：新签 %d，已签 %d，无任务 %d，窗口外 %d，失败 %d"
        % (ok_n, already_n, notask_n, outwin_n, len(fails)))

    if dry_run:
        log("dry-run 不推送")
        return 0 if not fails else 1

    if results and outwin_n == len(results):
        log("全部账号不在签到窗口，跳过汇总推送（避免占用当天正常汇总额度）")
        return 0

    if fails:
        title = "自动晚签：%d账号/%d失败" % (len(results), len(fails))
    else:
        title = "自动晚签：新签%d/已签%d" % (ok_n, already_n)

    desp = summary_text(results, disabled)
    kind = "batch-fail" if fails else "batch-ok"
    if push_sent_today(kind) > 0:
        log("今日已发过同类汇总（%s），跳过本次推送" % kind)
    else:
        push_phone(sendkey, title, desp, kind)
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default=ACCOUNTS_FILE)
    ap.add_argument("--sendkey", default="",
                    help="汇总 SendKey（默认 accounts.json / SERVERCHAN_SENDKEY / sendkey.txt）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-push", action="store_true", help="只发一条测试汇总")
    args = ap.parse_args()

    if not os.path.exists(args.accounts):
        log("ERROR: 找不到配置文件：%s" % args.accounts)
        return 9
    try:
        cfg, accounts, delay = load_config(args.accounts)
    except Exception as e:
        log("ERROR: 配置读取失败：%s" % e)
        return 9

    if os.name == "posix":
        try:
            mode = os.stat(args.accounts).st_mode & 0o777
            if mode & 0o077:
                log("WARN: %s 权限为 %o，建议 chmod 600（内含密码/SendKey）"
                    % (args.accounts, mode))
        except OSError:
            pass

    sendkey = args.sendkey.strip() or str(cfg.get("sendkey") or "").strip()
    if not sendkey:
        sendkey = load_sendkey("")
    if not sendkey and not args.dry_run:
        log("WARN: 没有汇总 SendKey，推送将跳过")

    try:
        return run_batch(accounts, delay, args.dry_run, sendkey, args.test_push)
    except Exception:
        import traceback
        log("FATAL:", traceback.format_exc()[-1500:])
        return 9


if __name__ == "__main__":
    sys.exit(main())
