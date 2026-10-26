#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动晚签（晚归签到 type=2）
逻辑复刻自前端 dormService-student-checkIn-index chunk：
  查规则 -> 查今晚状态 -> 相机拍照 -> 压缩+md5 -> dms 分片上传 -> 提交签到
用法：
  set XGUNI_TOKEN=你的xgUniToken && python auto_checkin.py
  或 python auto_checkin.py --token 你的xgUniToken
  或把 token 写进同目录 token.txt（勿传到 git）
依赖：pip install requests pillow
注意：本脚本只发 token + 签到必需字段，不带任何本机设备信息；
  生成图会 stripping 掉 EXIF；token 永不打印、永不写进代码。
"""

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime

_HERE = os.path.dirname(os.path.abspath(__file__))

# 防止系统代理（如科学上网、校园网中间人）把请求拦截导致 ProxyError
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ.pop(_k, None)
del _k


def _bootstrap_log(msg):
    """解释器级保底日志：只用标准库，import 炸了也能留下字"""
    try:
        with open(os.path.join(_HERE, "checkin.log"), "a", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + msg + "\n")
    except OSError:
        pass


try:
    import requests
    from PIL import Image, ImageEnhance
except ImportError as _e:
    _bootstrap_log("ERROR: 缺少依赖 %s，请先执行 pip install -r requirements.txt" % _e)
    sys.exit(99)

BASE = "https://xg.tjise.edu.cn/dr/api"
TYPE = 2  # 1=晨练打卡，2=晚归签到
TIMEOUT = 20
VER = "2026.10.15f"  # 改动一次 bump 一次，对版本号就知道是不是旧拷贝

HERE = _HERE
TOKEN_FILE = os.path.join(HERE, "token.txt")
CRED_FILE = os.path.join(HERE, "cred.txt")
HASH_LOG = os.path.join(HERE, "used_hashes.log")
TOKEN_HIST = os.path.join(HERE, "token_history.log")


def token_fp(t):
    """token 指纹（sha256 前 16 位）：只记指纹不记原文，可安全进日志"""
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def hist_note(fp, event):
    try:
        with open(TOKEN_HIST, "a", encoding="utf-8") as f:
            f.write("%s fp=%s %s\n"
                    % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), fp, event))
    except OSError:
        pass


def hist_seen(fp):
    try:
        if os.path.exists(TOKEN_HIST):
            with open(TOKEN_HIST, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if "fp=%s" % fp in line and " seen" in line:
                        return
    except OSError:
        pass
    hist_note(fp, "seen")  # 首次见到该 token：寿命起点

# 工作日=校内（学校地址），六日=校外（地址请按实填写，为空则提交 null）
WEEKDAY_ZXZT = "校内"
WEEKDAY_DKDD = "宾水西道"
WEEKDAY_DKDZ = "天津市西青区宾水西道399号"
WEEKEND_ZXZT = "校外"
WEEKEND_DKDD = ""
WEEKEND_DKDZ = ""

# 通用移动端 UA，仅为过检伪装，不含本机任何真实信息
UA = ("Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36")


CHECKIN_LOG = os.path.join(HERE, "checkin.log")


def log(*a):
    line = "%s %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      " ".join(str(x) for x in a))
    try:  # 文件优先：pythonw 无窗口时 stdout 可能是 None，print 会炸
        with open(CHECKIN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def notify(title, msg):
    """Send a local desktop notification when supported by the current OS."""
    if sys.platform.startswith("linux"):
        notify_send = shutil.which("notify-send")
        if not notify_send:
            log("桌面通知未发送：未安装 notify-send；Server酱手机推送不受影响")
            return
        try:
            subprocess.run([notify_send, title, msg], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                           text=True)
        except (OSError, subprocess.CalledProcessError) as e:
            log("桌面通知发送失败：", e)
        return

    if sys.platform == "win32":
        try:
            from windows_toasts import Toast, ToastAudio, WindowsToaster
        except ImportError:
            log("桌面通知未发送：未安装 windows-toasts")
            return
        try:
            t = Toast()
            t.text_fields = [title, msg]
            t.audio = ToastAudio(silent=True)
            WindowsToaster("自动晚签").show_toast(t)
        except Exception as e:
            log("桌面通知发送失败：", e)
        return

    log("桌面通知未发送：当前平台不支持本地通知")


class APIHTTPError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _raise_for_status(response):
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        path = requests.utils.urlparse(response.url).path
        message = "学工 API 返回 HTTP %s %s" % (response.status_code, path)
        if response.status_code == 404:
            message += "；受保护接口缺少/失效 token 时网关会用 404 隐藏路由"
        try:
            snippet = response.text[:200].replace("\n", " ")
        except Exception:
            snippet = ""
        if snippet:
            message += "；响应：" + snippet
        raise APIHTTPError(message, response.status_code) from e


SENDKEY_FILE = os.path.join(HERE, "sendkey.txt")
PUSH_LOG = os.path.join(HERE, "push.log")
PUSH_DAILY_CAP = 3  # 每天最多推 3 条，防刷屏也防额度烧完
_SENDKEY = ""


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


def pushed_success_today():
    """今日是否已推过签到成功（防重复打扰）"""
    if not os.path.exists(PUSH_LOG):
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    with open(PUSH_LOG, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith(today) and "success" in line:
                return True
    return False


def mark_push(kind):
    with open(PUSH_LOG, "a", encoding="utf-8") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " " + kind + "\n")


def push_sent_today():
    if not os.path.exists(PUSH_LOG):
        return 0
    today = datetime.now().strftime("%Y-%m-%d")
    n = 0
    with open(PUSH_LOG, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith(today):
                n += 1
    return n


def push_phone(sendkey, title, desp, kind="alert"):
    """Server酱推微信（免费每天 5 条）；
    自身失败只记日志绝不抛异常；SendKey 永不打印。"""
    if not sendkey:
        return
    try:
        if push_sent_today() >= PUSH_DAILY_CAP:
            log("推送限流：今日已达上限，跳过")
            return
        if sendkey.startswith("sctp"):
            uid = sendkey[4:].split("t")[0]
            url = "https://%s.push.ft07.com/send/%s.send" % (uid, sendkey)
        else:
            url = "https://sctapi.ftqq.com/%s.send" % sendkey
        r = requests.post(url, json={"title": title[:32], "desp": desp},
                          timeout=20)
        ok = r.json().get("code") == 0
        log("手机推送：", "成功" if ok else ("失败 " + r.text[:150]))
        if ok:
            mark_push(kind)
    except Exception as e:
        log("手机推送异常：", e)


def alert(title, msg):
    """要你动手的事：本机 Toast + 手机推送双发"""
    notify(title, msg)
    push_phone(_SENDKEY, title, msg)


def refresh_token_or_alert(reason):
    """token 失效时自动登录续期；无 cred.txt 则走老路报警。
    返回新 token；续不出来直接 alert 并 exit（绝不裸奔）。"""
    if not os.path.exists(CRED_FILE):
        alert("自动晚签：token 已过期",
              reason + "，且无 cred.txt 可自动续期，请手动更新 token.txt")
        sys.exit(1)
    log("token 失效，尝试自动登录续期…")
    try:
        import auto_login
        new = auto_login.do_login(CRED_FILE)
    except ImportError:
        alert("自动晚签：缺登录依赖", "请 pip install -r requirements.txt 后重跑")
        sys.exit(1)
    except Exception as e:
        alert("自动晚签：自动续期失败", "%s，请手动更新 token" % str(e)[:200])
        sys.exit(1)
    new = new.strip()
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(new)
    hist_seen(token_fp(new))
    log("自动续期成功，已写回 token.txt")
    return new


def normalize_token(raw):
    """兼容从浏览器复制的各种形态：空白、引号、xgUniToken= 前缀。"""
    t = (raw or "").strip()
    for prefix in ("xguniToken=", "xguniToken:", "token=", "token:"):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
            break
    if len(t) >= 2 and t[0] == t[-1] and t[0] in ("'", '"'):
        t = t[1:-1].strip()
    return t


def load_token(cli_token):
    if cli_token:
        return normalize_token(cli_token)
    t = normalize_token(os.environ.get("XGUNI_TOKEN", ""))
    if t:
        return t
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, encoding="utf-8-sig") as f:
            return normalize_token(f.read())
    return ""


def api_get(sess, path, params=None):
    r = sess.get(BASE + path, params=params, timeout=TIMEOUT)
    _raise_for_status(r)
    return r.json()


def api_post(sess, path, data=None):
    r = sess.post(BASE + path, json=data or {}, timeout=TIMEOUT)
    _raise_for_status(r)
    return r.json()


def in_window(rule):
    """按前端同款逻辑：当前 HH:mm 是否落在正常或晚归窗口内"""
    now = datetime.now().strftime("%H:%M")
    s, e = rule["evenCheckInStartTime"][:5], rule["evenCheckInEndTime"][:5]
    ls, le = rule["evenLateCheckInStartTime"][:5], rule["evenLateCheckInEndTime"][:5]
    ok = (s <= now <= e) or (ls <= now <= le)
    return ok, now, (s, e, ls, le)


def pick_photo(rng):
    cands = sorted(
        f for f in os.listdir(HERE)
        if f.lower().endswith((".jpg", ".jpeg")) and not f.startswith("out_")
    )
    if not cands:
        log("ERROR: 目录里没有可用照片")
        sys.exit(1)
    return os.path.join(HERE, rng.choice(cands))


def used_hashes():
    if not os.path.exists(HASH_LOG):
        return set()
    with open(HASH_LOG, encoding="utf-8") as f:
        return set(x.strip() for x in f if x.strip())


def perturb(src, dst, rng):
    """细微改动：随机裁边还原 + 亮度/对比度抖动 + 随机 JPEG 质量，不写 EXIF"""
    img = Image.open(src).convert("RGB")
    w, h = img.size
    ox, oy = rng.randint(0, 6), rng.randint(0, 6)
    img = img.crop((ox, oy, w - (rng.randint(0, 6)), h - (rng.randint(0, 6))))
    img = img.resize((w, h), Image.BILINEAR)
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.94, 1.06))
    img = ImageEnhance.Contrast(img).enhance(rng.uniform(0.95, 1.05))
    img.save(dst, "JPEG", quality=rng.randint(58, 78))  # 不带 exif
    with open(dst, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def make_unique_photo(rng):
    tried = used_hashes()
    for _ in range(10):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(HERE, "out_%s.jpg" % stamp)
        src = pick_photo(rng)
        md5 = perturb(src, dst, rng)
        if md5 not in tried:
            with open(HASH_LOG, "a", encoding="utf-8") as f:
                f.write(md5 + "\n")
            log("选用底图：", os.path.basename(src), "->", os.path.basename(dst))
            return dst, md5
        os.remove(dst)
        time.sleep(1.1)  # 换时间戳+随机流重试
    log("ERROR: 10 次改图 md5 仍重复，放弃")
    sys.exit(1)


def dms_upload(sess, path, md5):
    """复刻 uploader.js：check -> chunk(check) -> 传文件 -> merge，返回文件 id"""
    size = os.path.getsize(path)
    name = os.path.basename(path)
    rel = "/" + name
    chk = api_post(sess, "/dms/file/upload/check", {
        "key": "4", "parent": "0", "relativePath": rel,
        "size": size, "suffix": ".jpg", "type": "4",
    })
    if chk.get("code") != 1:
        if chk.get("data") and isinstance(chk["data"], dict) and chk["data"].get("id"):
            return chk["data"]["id"]  # 秒传命中，直接复用
        raise RuntimeError("upload/check 失败: %s" % json.dumps(chk, ensure_ascii=False)[:300])
    # 实测：JSON 形式的 chunk 检查服务端不认（固定返回特征码/坐标/类型验证失败），
    # 真机走的也是“有文件就直接 multipart 传”这条路（code 2001/2003/2005 均视为成功）
    with open(path, "rb") as f:
        up = requests.post(BASE + "/dms/file/upload/chunk", timeout=60,
                           headers={"token": sess.headers["token"],
                                    "User-Agent": UA},
                           files={"file": (name, f, "image/jpeg")}, data={
                               "chunkNumber": 1, "chunkSize": 10485760,
                               "currentChunkSize": size, "filename": name,
                               "identifier": md5, "key": "4", "parent": "0",
                               "relativePath": rel, "totalChunks": 1,
                               "totalSize": size, "type": "4"})
    _raise_for_status(up)
    uj = up.json()
    if uj.get("code") not in (2001, 2003, 2005):
        raise RuntimeError("chunk 上传失败: %s" % json.dumps(uj, ensure_ascii=False)[:300])
    mg = api_post(sess, "/dms/file/upload/merge", {
        "filename": name, "identifier": md5, "key": "4",
        "parent": "0", "relativePath": rel, "totalSize": size, "type": "4",
    })
    if mg.get("code") != 1 or not (mg.get("data") or {}).get("id"):
        raise RuntimeError("merge 失败: %s" % json.dumps(mg, ensure_ascii=False)[:300])
    return mg["data"]["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default="")
    ap.add_argument("--sendkey", default="", help="Server酱 SendKey（或环境变量 SERVERCHAN_SENDKEY / sendkey.txt）")
    ap.add_argument("--dry-run", action="store_true", help="只查状态不提交")
    ap.add_argument("--test-toast", action="store_true", help="只发一条测试桌面通知")
    ap.add_argument("--test-push", action="store_true", help="只发一条测试推送到手机")
    ap.add_argument("--test-login", action="store_true", help="只测自动登录并写回 token.txt")
    args = ap.parse_args()

    global _SENDKEY
    _SENDKEY = load_sendkey(args.sendkey)
    log("版本：", VER)
    if _SENDKEY:
        log("推送通道：Server酱已配置")
    else:
        log("推送通道未配置：缺 sendkey（--sendkey / SERVERCHAN_SENDKEY / sendkey.txt），手机推送将静默跳过")

    if args.test_push:
        if not _SENDKEY:
            log("ERROR: 没找到 SendKey，先去 https://sct.ftqq.com 免费领一个")
            return
        push_phone(_SENDKEY, "自动晚签：测试推送",
                   "手机能收到这条，失败推送链路就通了。\n\n收到 token 过期推送时：电脑上更新 token.txt 即可。")
        return

    if args.test_toast:
        notify("自动晚签：测试通知", "如果你看到这条，通知链路正常（无声、不抢焦点）")
        return

    if args.test_login:
        import auto_login
        new = auto_login.do_login(CRED_FILE)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(new)
        hist_seen(token_fp(new))
        log("登录成功，token 已写回 token.txt（指纹", token_fp(new) + "）")
        return

    seed = int(time.time())          # random seed 取时间
    rng = random.Random(seed)
    log("seed =", seed)

    token = load_token(args.token)
    if not token:
        if os.path.exists(CRED_FILE):
            log("本地 token 为空，尝试自动登录续期…")
            token = refresh_token_or_alert("本地 token 为空")
        else:
            log("ERROR: 没有可用 token（--token / 环境变量 XGUNI_TOKEN / token.txt）")
            alert("自动晚签：缺 token",
                  "没找到可用 token，请更新 token.txt，或放入 cred.txt 以便自动续期")
            sys.exit(1)
    fp = token_fp(token)
    hist_seen(fp)  # 记录该 token 寿命起点（只存指纹）
    sess = requests.Session()
    sess.headers.update({"token": token, "content-type": "application/json",
                         "User-Agent": UA})

    def api_get_auth(path, params=None, reason="查询"):
        """受保护接口遇到 401/403/404 时按 token 失效处理，续期后重试一次。
        元小元网关在缺少/空 token 时会用 404 隐藏路由，不能当成接口不存在。"""
        nonlocal token, fp
        try:
            return api_get(sess, path, params)
        except APIHTTPError as e:
            status = getattr(e, "status", None)
            if status not in (401, 403, 404) or not os.path.exists(CRED_FILE):
                raise
            log("%s HTTP %s，按 token 失效处理，尝试自动续期…" % (reason, status))
            hist_note(fp, "dead")
            token = refresh_token_or_alert("%s被拒（HTTP %s）" % (reason, status))
            fp = token_fp(token)
            sess.headers.update({"token": token})
            return api_get(sess, path, params)

    today = datetime.now()
    date_str = today.strftime("%Y-%m-%d")
    is_weekday = today.weekday() < 5  # 周一~周五
    zxzt = WEEKDAY_ZXZT if is_weekday else WEEKEND_ZXZT
    dkdd = (WEEKDAY_DKDD if is_weekday else WEEKEND_DKDD) or None
    dkdz = (WEEKDAY_DKDZ if is_weekday else WEEKEND_DKDZ) or None
    log("日期：", date_str, "周内" if is_weekday else "周末", "->", zxzt)

    rule = api_get_auth("/dorm/dormCheckInRule/detail", reason="规则查询")
    if rule.get("code") != 1:
        # 该接口无任何参数，非 1 只可能是 token 失效：先自动续一次
        log("ERROR: 规则查询失败：", json.dumps(rule, ensure_ascii=False)[:200])
        hist_note(fp, "dead")
        token = refresh_token_or_alert("规则查询被拒")
        fp = token_fp(token)
        sess.headers.update({"token": token})
        rule = api_get(sess, "/dorm/dormCheckInRule/detail")
        if rule.get("code") != 1:
            log("ERROR: 续期后仍失败：", json.dumps(rule, ensure_ascii=False)[:200])
            alert("自动晚签：续期后仍失败", "请手动检查后重跑")
            sys.exit(1)
    ok, now_hm, (s, e, ls, le) = in_window(rule["data"])
    log("当前 %s，窗口 正常 %s-%s / 晚归 %s-%s" % (now_hm, s, e, ls, le))
    if not ok:
        log("未在打卡时间内，不提交")
        sys.exit(2)

    st = api_get_auth("/dorm/dormCheckInRecord/stuCheckInDetail",
                      {"date": date_str, "type": TYPE}, reason="状态查询")
    if st.get("code") != 1:
        # 该接口只读今晚状态，非 1 只可能是 token 失效：先自动续一次
        log("ERROR: 状态查询失败：", json.dumps(st, ensure_ascii=False)[:200])
        hist_note(fp, "dead")
        token = refresh_token_or_alert("状态查询被拒")
        fp = token_fp(token)
        sess.headers.update({"token": token})
        st = api_get(sess, "/dorm/dormCheckInRecord/stuCheckInDetail",
                     {"date": date_str, "type": TYPE})
        if st.get("code") != 1:
            log("ERROR: 续期后仍失败：", json.dumps(st, ensure_ascii=False)[:200])
            alert("自动晚签：续期后仍失败", "请手动检查后重跑")
            sys.exit(1)
    hist_note(fp, "ok")  # token 存活心跳：连续 ok 的天数即寿命下限
    if st.get("data") and st["data"].get("dkzt") not in (0, None):
        d = st["data"]
        log("今晚已签（dkzt=%s），不重复提交" % d.get("dkzt"))
        if not pushed_success_today() and not args.dry_run:  # dry-run 零副作用
            push_phone(_SENDKEY, "自动晚签：今日已签到",
                       "日期：%s\n状态：%s（%s）\n签到时间：%s\n（本次为复查确认，未重复提交）" % (
                           date_str,
                           {1: "正常", 2: "晚归"}.get(d.get("dkzt"), d.get("dkzt")),
                           d.get("zxzt"), d.get("dksj")),
                       kind="success")
        else:
            log("今日已推过成功通知，跳过")
        sys.exit(3)
    log("今晚未签，继续")

    if args.dry_run:
        log("dry-run：到此为止，不传图不提交")
        return

    photo, md5 = make_unique_photo(rng)
    log("图片 md5 =", md5)
    fid = dms_upload(sess, photo, md5)
    log("上传成功，文件 id =", fid)

    body = {"dkdd": dkdd, "dkdz": dkdz, "dktp": fid, "type": TYPE, "zxzt": zxzt}
    sub = api_post(sess, "/dorm/dormCheckInRecord/editCheckIn", body)
    msg = str(sub.get("message") or "")
    if sub.get("code") != 1:
        if "无需签到" in msg:
            # 今晚没有下发签到任务：不是 token 问题，删图后静默跳过
            log("今晚无需签到（无任务），跳过")
            os.remove(photo)
            sys.exit(4)
        log("ERROR: 提交失败：", json.dumps(sub, ensure_ascii=False)[:300])
        alert("自动晚签：提交失败",
              "提交被拒，请看 checkin.log；若 token 失效请同步更新 token.txt，必要时手动签到")
        sys.exit(1)
    log("提交成功")

    chk = api_get(sess, "/dorm/dormCheckInRecord/stuCheckInDetail",
                  {"date": date_str, "type": TYPE})
    log("复查：", json.dumps(chk.get("data"), ensure_ascii=False)[:300])
    d = chk.get("data") or {}
    push_phone(_SENDKEY, "自动晚签：签到成功",
               "日期：%s\n状态：%s（%s）\n签到时间：%s" % (
                   date_str, {1: "正常", 2: "晚归"}.get(d.get("dkzt"), d.get("dkzt")),
                   zxzt, d.get("dksj")),
               kind="success")
    os.remove(photo)  # 成功后删图，文件夹不膨胀（md5 已记入 used_hashes.log）
    log("已删除：", os.path.basename(photo))


if __name__ == "__main__":
    try:
        main()
    except APIHTTPError as e:
        log("ERROR:", e)
        alert("自动晚签：学工 API 请求失败", str(e))
        sys.exit(1)
    except Exception:
        import traceback
        log("FATAL:", traceback.format_exc()[-1500:])
        try:
            notify("自动晚签：脚本崩溃", "请看 checkin.log 末尾")
        except Exception:
            pass
        sys.exit(9)
