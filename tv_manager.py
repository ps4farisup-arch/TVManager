#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
مدير الشاشات الذكية — Samsung (Tizen) و LG (webOS)

التشغيل:
    pip install paramiko          (مطلوب لتثبيت التطبيقات على LG فقط)
    python tv_manager.py          (شغّله كمسؤول/Administrator لتفعيل حجب الإعلانات)

بعدها تنفتح لوحة التحكم في المتصفح، وتقدر تفتحها من الجوال بالرابط أو رمز QR الظاهر.
"""

import http.server
import io
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor

PARAMIKO_ERR = ""
try:
    import paramiko
except Exception as _e:          # على الجوال قد يفشل استيراد مكتبات التشفير الأصلية
    paramiko = None
    PARAMIKO_ERR = "%s: %s" % (type(_e).__name__, _e)


def _jsch_ok():
    """على تطبيق الأندرويد نستخدم مكتبة JSch (جافا) للاتصال بشاشات webOS."""
    try:
        from java import jclass
        jclass("com.jcraft.jsch.JSch")
        return True
    except Exception:
        return False

import tv_screen_app as screen

APP_PORT = 8765
IS_ANDROID = bool(os.environ.get("TVM_ANDROID")) or hasattr(sys, "getandroidapilevel")
HOME = os.path.expanduser("~")
CONFIG_PATH = os.path.join(HOME, ".tv_manager.json")
EXTRA_LIST_PATH = os.path.join(HOME, ".tv_manager_blocklist.txt")
TOKEN = secrets.token_urlsafe(9)
MAX_UPLOAD = 600 * 1024 * 1024

# نطاقات إعلانات وتتبع معروفة في شاشات سامسونج و LG (تُطابق النطاق وكل ما تحته)
DEFAULT_BLOCK = [
    # Samsung
    "samsungads.com", "samsungacr.com", "samsungadhub.com", "ads.samsung.com",
    "samsung-com.112.2o7.net", "pavv.co.kr",
    # LG
    "lgsmartad.com", "ad.lgappstv.com", "ngfts.lge.com", "aic-ngfts.lge.com",
    "lgad.cjpowercast.com", "lgtvsdp.com",
    # عام
    "doubleclick.net", "googlesyndication.com", "googleadservices.com",
    "adservice.google.com", "app-measurement.com", "amazon-adsystem.com",
    "adcolony.com", "applovin.com", "unityads.unity3d.com", "ads.yahoo.com",
    "freewheel.tv", "fwmrm.net", "innovid.com", "samba.tv", "alphonso.tv",
]
BLOCKLIST_URL = "https://raw.githubusercontent.com/Perflyst/PiHoleBlocklist/master/SmartTV.txt"


# ---------------------------------------------------------------- أدوات عامة
def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"custom": [], "allow": [], "upstream": "1.1.1.1"}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def http_get(url, timeout=2.0):
    req = urllib.request.Request(url, headers={"User-Agent": "TVManager/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def port_open(ip, port, timeout=0.6):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


# ---------------------------------------------------------------- اكتشاف الشاشات
SSDP_ADDR = ("239.255.255.250", 1900)
SSDP_STS = [
    "ssdp:all",
    "urn:dial-multiscreen-org:service:dial:1",
    "urn:lge-com:service:webos-second-screen:1",
    "urn:samsung.com:device:RemoteControlReceiver:1",
]


def _msearch(st):
    return ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\nMX: 2\r\nST: %s\r\n\r\n' % st).encode()


def _parse_ssdp(data):
    headers = {}
    for line in data.decode(errors="ignore").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers


def ssdp_discover(timeout=3.0, target=None):
    """target=None: بحث جماعي في الشبكة. target=IP: سؤال جهاز واحد مباشرة."""
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind((local_ip(), 0))
    except OSError:
        pass
    sock.settimeout(0.4)
    dest = (target, 1900) if target else SSDP_ADDR
    for st in SSDP_STS:
        try:
            sock.sendto(_msearch(st), dest)
        except OSError:
            pass
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break
        if target and addr[0] != target:
            continue
        found.setdefault(addr[0], []).append(_parse_ssdp(data))
    sock.close()
    return found


def read_description(url):
    try:
        x = http_get(url, 2).decode(errors="ignore")
    except Exception:
        return {}
    out = {}
    for tag in ("friendlyName", "modelName", "manufacturer", "modelNumber"):
        m = re.search(r"<%s>(.*?)</%s>" % (tag, tag), x, re.S)
        out[tag] = m.group(1).strip() if m else ""
    return out


TV_MAKERS = {
    "samsung": "Samsung", "lg electronics": "LG", "lge": "LG", "sony": "Sony", "tcl": "TCL", "hisense": "Hisense",
    "xiaomi": "Xiaomi", "toshiba": "Toshiba", "philips": "Philips", "tpvision": "Philips", "tp vision": "Philips",
    "panasonic": "Panasonic", "sharp": "Sharp", "skyworth": "Skyworth", "haier": "Haier", "konka": "Konka",
    "changhong": "Changhong", "nikai": "Nikai", "kmc": "KMC", "wansa": "Wansa", "geepas": "Geepas",
    "aiwa": "Aiwa", "jvc": "JVC", "rca": "RCA", "hyundai": "Hyundai", "vestel": "Vestel", "nokia": "Nokia",
    "realme": "Realme", "oneplus": "OnePlus", "vizio": "Vizio", "grundig": "Grundig", "loewe": "Loewe",
    "amazon": "Amazon Fire TV", "google": "Google TV",
}


def _maker(*texts):
    blob = " ".join(t or "" for t in texts).lower()
    for k, v in TV_MAKERS.items():
        if k in blob:
            return v
    return ""


def _ssdp_info(ip, headers=None):
    headers = list(headers or [])
    if not headers:
        headers = ssdp_discover(1.5, target=ip).get(ip, [])
    blob = " ".join(" ".join(h.values()) for h in headers).lower()
    loc = next((h["location"] for h in headers if h.get("location")), None)
    desc = read_description(loc) if loc else {}
    return blob, desc


def probe_samsung(ip):
    """أي شاشة تايزن (سامسونج أو الشركات اللي ترخّص النظام منها)."""
    try:
        raw = http_get("http://%s:8001/api/v2/" % ip, 2)
        info = json.loads(raw)
    except Exception:
        return None
    d = info.get("device") or {}
    if not d and "name" not in info:
        return None
    return {
        "ip": ip,
        "brand": "samsung",
        "maker": "Samsung" if b"samsung" in raw.lower() else (_maker(d.get("name", "")) or "Tizen TV"),
        "name": d.get("name") or info.get("name") or "Tizen TV",
        "model": d.get("modelName", ""),
        "os": d.get("OS", "Tizen"),
        "devMode": str(d.get("developerMode", "0")) == "1",
        "devIP": d.get("developerIP", ""),
    }


def _eureka_name(ip):
    try:
        j = json.loads(http_get("http://%s:8008/setup/eureka_info?options=detail" % ip, 2))
        return j.get("name", ""), j.get("device_info", {}).get("manufacturer", "") if isinstance(j.get("device_info"), dict) else ""
    except Exception:
        return "", ""


def identify_device(ip, headers=None):
    tz = probe_samsung(ip)
    if tz:
        return tz
    blob, desc = _ssdp_info(ip, headers)
    maker = _maker(desc.get("manufacturer", ""), desc.get("modelName", ""), desc.get("friendlyName", ""))
    name = desc.get("friendlyName", "")
    model = desc.get("modelName") or desc.get("modelNumber", "")
    # webOS: إل جي وأي شركة ترخّص النظام منها
    if "webos" in blob or "lge-com" in blob or maker == "LG":
        return {"ip": ip, "brand": "lg", "maker": maker or "webOS TV", "name": name or "webOS TV", "model": model,
                "os": "webOS", "devMode": port_open(ip, 9922), "rooted": port_open(ip, 22)}
    adb, cast = port_open(ip, 5555, 0.6), port_open(ip, 8009, 0.6)
    if adb or cast:
        en, em = _eureka_name(ip) if cast else ("", "")
        mk = maker or _maker(em, en) or ""
        tvish = adb or (mk and mk != "Google TV") or "tv" in (name + en + model).lower()
        if tvish:
            return {"ip": ip, "brand": "android", "maker": mk or "Android TV", "name": name or en or "Android / Google TV",
                    "model": model, "os": "Android TV", "adb": adb}
    if maker and maker not in ("Google TV",):
        return {"ip": ip, "brand": "other", "maker": maker, "name": name or maker, "model": model,
                "os": "VIDAA" if "vidaa" in blob or maker == "Hisense" else "نظام آخر"}
    return None


def scan(deep=False):
    ssdp = ssdp_discover(3.0)
    candidates = set(ssdp.keys())
    if deep:
        net = ipaddress.ip_network(local_ip() + "/24", strict=False)

        def check(host):
            h = str(host)
            if any(port_open(h, p, 0.4) for p in (8001, 3000, 3001, 5555, 8009)):
                return h
            return None

        with ThreadPoolExecutor(64) as ex:
            for r in ex.map(check, net.hosts()):
                if r:
                    candidates.add(r)

    devices = []
    with ThreadPoolExecutor(16) as ex:
        for d in ex.map(lambda ip: identify_device(ip, ssdp.get(ip)), candidates):
            if d:
                devices.append(d)
    devices.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    return devices


# ---------------------------------------------------------------- أندرويد تي في / قوقل تي في (ADB)
ADB_DIR = os.path.join(HOME, ".tv_manager_adb")
ADB_ZIP = {
    "win32": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
}.get(sys.platform, "https://dl.google.com/android/repository/platform-tools-latest-linux.zip")
PRIVATE_DNS = "dns.adguard-dns.com"
_ADB_LOCK = threading.Lock()


def find_adb(download=True, log=None):
    p = shutil.which("adb")
    if p:
        return p
    exe = os.path.join(ADB_DIR, "platform-tools", "adb.exe" if sys.platform == "win32" else "adb")
    if os.path.isfile(exe):
        return exe
    if not download:
        return None
    with _ADB_LOCK:
        if os.path.isfile(exe):
            return exe
        if log is not None:
            log.append("أحمّل أداة ADB الرسمية من قوقل (مرة وحدة)…")
        import zipfile
        data = http_get(ADB_ZIP, 120)
        os.makedirs(ADB_DIR, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(ADB_DIR)
        if sys.platform != "win32":
            os.chmod(exe, 0o755)
    return exe


def _use_py_adb():
    """على الجوال ما فيه أداة adb، فنتكلم مع الشاشة ببروتوكول ADB مكتوب بالبايثون (adb-shell)."""
    if IS_ANDROID:
        return True
    try:
        return find_adb(download=False) is None and __import__("adb_shell") is not None
    except Exception:
        return False


def _py_adb(ip, port, log):
    try:
        from adb_shell.adb_device import AdbDeviceTcp
        from adb_shell.auth.keygen import keygen
        from adb_shell.auth.sign_pythonrsa import PythonRSASigner
    except ImportError:
        raise RuntimeError("مكتبة adb-shell غير موجودة في هذا الإصدار.")
    os.makedirs(ADB_DIR, exist_ok=True)
    key = os.path.join(ADB_DIR, "adbkey")
    if not (os.path.isfile(key) and os.path.isfile(key + ".pub")):
        keygen(key)
    with open(key) as f:
        priv = f.read()
    with open(key + ".pub") as f:
        pub = f.read()
    dev = AdbDeviceTcp(ip, int(port or 5555), default_transport_timeout_s=10.0)
    log.append("أتصل بالشاشة %s:%s… إذا ظهرت عليها رسالة «السماح بتصحيح الأخطاء» اختر «السماح دائماً»." % (ip, port or 5555))
    try:
        dev.connect(rsa_keys=[PythonRSASigner(pub, priv)], auth_timeout_s=45)
    except Exception as e:
        raise RuntimeError("ما قدرت أتصل (%s). تأكد إن «تصحيح أخطاء USB» أو «تصحيح الأخطاء عبر الشبكة» مفعّل، "
                           "واختر «السماح» في الرسالة اللي تظهر على الشاشة خلال ٤٥ ثانية." % e)
    return dev


def android_connect(ip, port, log):
    adb = find_adb(log=log)
    target = "%s:%s" % (ip, port or 5555)
    out = _run([adb, "connect", target], 20)
    log.append("$ adb connect " + target + "\n" + out)
    low = out.lower()
    if "unauthorized" in low or "authenticate" in low:
        raise RuntimeError("ظهرت رسالة «السماح بتصحيح الأخطاء» على الشاشة؟ اختر «السماح دائماً» ثم أعد المحاولة.")
    if "connected" not in low:
        raise RuntimeError("ما قدرت أتصل. تأكد من تفعيل «تصحيح الأخطاء عبر الشبكة» أو «التصحيح اللاسلكي» ورقم المنفذ الظاهر.")
    state = _run([adb, "-s", target, "get-state"], 10)
    if "device" not in state:
        raise RuntimeError("الشاشة تنتظر موافقتك: اختر «السماح» في الرسالة الظاهرة عليها ثم أعد المحاولة.")
    return adb, target


def android_pair(ip, port, code):
    log = []
    if _use_py_adb():
        raise RuntimeError("الإقران برمز غير مدعوم من الجوال. فعّل «تصحيح أخطاء USB» في الشاشة، وغالباً تقبل الاتصال "
                           "مباشرة على المنفذ 5555 بدون إقران.")
    adb = find_adb(log=log)
    out = _run([adb, "pair", "%s:%s" % (ip, port), code], 30)
    log.append(out)
    return "success" in out.lower(), log


def android_install(ip, port, data, filename):
    log = []
    if _use_py_adb():
        dev = _py_adb(ip, port, log)
        fd, path = tempfile.mkstemp(suffix=".apk")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        try:
            remote = "/data/local/tmp/tvm_upload.apk"
            log.append("أرفع %s (%.1f ميجا)…" % (filename, len(data) / 1048576))
            dev.push(path, remote, read_timeout_s=600)
            out = dev.shell("pm install -r " + remote, read_timeout_s=600)
            dev.shell("rm -f " + remote)
            log.append(str(out).strip())
            return "success" in str(out).lower(), log
        finally:
            try:
                dev.close()
            except Exception:
                pass
            try:
                os.remove(path)
            except OSError:
                pass
    adb, target = android_connect(ip, port, log)
    fd, path = tempfile.mkstemp(suffix=".apk")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        out = _run([adb, "-s", target, "install", "-r", path], 300)
        log.append("$ adb install " + filename + "\n" + out)
        return "success" in out.lower(), log
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def android_adblock(ip, port, enable):
    log = []
    if _use_py_adb():
        dev = _py_adb(ip, port, log)
        try:
            if enable:
                dev.shell("settings put global private_dns_mode hostname")
                dev.shell("settings put global private_dns_specifier " + PRIVATE_DNS)
            else:
                dev.shell("settings put global private_dns_mode off")
            mode = str(dev.shell("settings get global private_dns_mode")).strip()
        finally:
            try:
                dev.close()
            except Exception:
                pass
        log.append("وضع DNS الحالي: " + mode)
        return ((mode == "hostname") if enable else (mode in ("off", "opportunistic", "null"))), log
    adb, target = android_connect(ip, port, log)
    if enable:
        cmds = [["settings", "put", "global", "private_dns_mode", "hostname"],
                ["settings", "put", "global", "private_dns_specifier", PRIVATE_DNS]]
    else:
        cmds = [["settings", "put", "global", "private_dns_mode", "off"]]
    for c in cmds:
        log.append("$ " + " ".join(c) + "\n" + _run([adb, "-s", target, "shell"] + c, 20))
    mode = _run([adb, "-s", target, "shell", "settings", "get", "global", "private_dns_mode"], 10).strip()
    ok = (mode == "hostname") if enable else (mode in ("off", "opportunistic", "null"))
    log.append("وضع DNS الحالي: " + mode)
    return ok, log


# ---------------------------------------------------------------- تثبيت على سامسونج
def find_sdb():
    p = shutil.which("sdb")
    if p:
        return p
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    for c in (os.path.join(here, "sdb.exe"), os.path.join(here, "sdb"),os.path.join(HOME, "tizen-studio", "tools", "sdb"),
              os.path.join(HOME, "tizen-studio", "tools", "sdb.exe"),
              r"C:\tizen-studio\tools\sdb.exe"):
        if os.path.isfile(c):
            return c
    return None


def _run(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return "انتهت المهلة"


def samsung_install(ip, data, filename):
    log = []
    sdb = find_sdb()
    if not sdb:
        raise RuntimeError("أداة sdb غير موجودة. ثبّت Tizen Studio (أو أداة sdb فقط) ثم أعد تشغيل البرنامج.")
    info = probe_samsung(ip)
    me = local_ip()
    if info and not info["devMode"]:
        log.append("تنبيه: وضع المطور غير مفعّل في الشاشة، غالباً سيفشل التثبيت.")
    elif info and info["devIP"] and info["devIP"] != me:
        log.append("تنبيه: وضع المطور مربوط بـ %s وليس بهذا الكمبيوتر (%s)." % (info["devIP"], me))
    ext = os.path.splitext(filename)[1].lower() or ".wgt"
    fd, path = tempfile.mkstemp(suffix=ext)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        target = "%s:26101" % ip
        log.append("$ sdb connect " + target)
        log.append(_run([sdb, "connect", target], 30))
        log.append("$ sdb install " + filename)
        out = _run([sdb, "-s", target, "install", path], 300)
        log.append(out)
        low = out.lower()
        ok = ("fail" not in low and "error" not in low and "closed" not in low)
        if not ok and ("signature" in low or "certificate" in low or "author" in low):
            log.append("السبب غالباً التوقيع: الشاشات الحديثة تقبل فقط تطبيقات موقّعة بشهادة سامسونج "
                       "الخاصة بشاشتك. استخدم نسخة موقّعة مسبقاً أو وقّع الملف عبر Tizen Studio.")
        return ok, log
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def find_tizen():
    for n in ("tizen", "tizen.bat"):
        p = shutil.which(n)
        if p:
            return p
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    for base in (os.path.join(HOME, "tizen-studio"), r"C:\tizen-studio", here):
        for n in ("tizen.bat", "tizen"):
            c = os.path.join(base, "tools", "ide", "bin", n)
            if os.path.isfile(c):
                return c
    return None


def samsung_screen_install(ip, country, city, profile, features=None):
    tz = find_tizen()
    if not tz:
        raise RuntimeError("سامسونج تقبل فقط تطبيقات موقّعة بشهادة خاصة بشاشتك. ثبّت Tizen Studio "
                           "وأنشئ شهادة Samsung من Certificate Manager (مرة وحدة)، ثم أعد تشغيل البرنامج.")
    work = tempfile.mkdtemp(prefix="shashati_")
    try:
        font_log = []
        for name, data in screen.tizen_files(country, city, font_log, features=features).items():
            os.makedirs(os.path.dirname(os.path.join(work, name)), exist_ok=True)
            with open(os.path.join(work, name), "wb") as f:
                f.write(data)
        cmd = [tz, "package", "-t", "wgt"]
        if profile:
            cmd += ["-s", profile]
        cmd += ["--", work]
        out = _run(cmd, 240)
        wgts = [n for n in os.listdir(work) if n.endswith(".wgt")]
        if not wgts:
            raise RuntimeError("فشل توقيع التطبيق. تأكد من اسم ملف الشهادة (Profile) في Certificate Manager.\n" + out[-800:])
        with open(os.path.join(work, wgts[0]), "rb") as f:
            data = f.read()
        ok, log = samsung_install(ip, data, "Shashati.wgt")
        log[0:0] = font_log + ["تم توقيع «شاشتي» بشهادتك."]
        return ok, log
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- مفتاح webOS (قراءة متسامحة)
def _der_read(buf, i):
    tag = buf[i]
    n = buf[i + 1]
    i += 2
    if n & 0x80:
        cnt = n & 0x7F
        n = int.from_bytes(buf[i:i + cnt], "big")
        i += cnt
    return tag, buf[i:i + n], i + n


def _der_ints(buf):
    """يقرأ كل الأعداد داخل SEQUENCE بدون تشدد في الترميز."""
    tag, body, _ = _der_read(buf, 0)
    if tag != 0x30:
        raise ValueError("not a sequence")
    out, i = [], 0
    while i < len(body):
        t, v, i = _der_read(body, i)
        if t == 0x02:
            out.append(int.from_bytes(v, "big"))
        elif t == 0x04:                      # PKCS#8: المفتاح داخل OCTET STRING
            return _der_ints(v)
    return out


def load_webos_key(pem, passphrase):
    """بعض شاشات LG الحديثة ترمّز مفتاحها بشكل ترفضه مكتبة cryptography الجديدة؛ نفكه يدوياً."""
    import base64
    import hashlib
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    lines = [l.strip() for l in pem.strip().splitlines()]
    headers, body = {}, []
    for l in lines[1:-1]:
        if ":" in l:
            k, v = l.split(":", 1)
            headers[k.strip()] = v.strip()
        elif l:
            body.append(l)
    data = base64.b64decode("".join(body))
    dek = headers.get("DEK-Info")
    if dek:
        cipher_name, iv_hex = [x.strip() for x in dek.split(",")]
        iv = bytes.fromhex(iv_hex)
        sizes = {"AES-128-CBC": 16, "AES-192-CBC": 24, "AES-256-CBC": 32, "DES-EDE3-CBC": 24}
        if cipher_name not in sizes:
            raise ValueError("تشفير غير مدعوم: " + cipher_name)
        pw, key, prev = passphrase.encode(), b"", b""
        while len(key) < sizes[cipher_name]:
            prev = hashlib.md5(prev + pw + iv[:8]).digest()
            key += prev
        key = key[:sizes[cipher_name]]
        if cipher_name.startswith("AES"):
            algo = algorithms.AES(key)
        else:
            try:
                from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
            except ImportError:
                TripleDES = algorithms.TripleDES
            algo = TripleDES(key)
        dec = Cipher(algo, modes.CBC(iv)).decryptor()
        plain = dec.update(data) + dec.finalize()
        pad = plain[-1]
        if not 1 <= pad <= len(iv) or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError("الـ Passphrase لا تطابق المفتاح")
        data = plain[:-pad]
    ints = _der_ints(data)
    if len(ints) >= 9:
        ints = ints[1:]
    n, e, d, p, q = ints[:5]
    if p * q != n:
        raise ValueError("المفتاح بعد فكه غير سليم")
    pub = rsa.RSAPublicNumbers(e, n)
    return rsa.RSAPrivateNumbers(p, q, d, rsa.rsa_crt_dmp1(d, p), rsa.rsa_crt_dmq1(d, q),
                                 rsa.rsa_crt_iqmp(p, q), pub).private_key()


# ---------------------------------------------------------------- تثبيت على LG
def _lg_ssh(ip, mode, secret):
    if paramiko is None:
        if IS_ANDROID:
            raise RuntimeError("مكتبة الاتصال ما اشتغلت على الجوال: " + PARAMIKO_ERR)
        raise RuntimeError("مكتبة paramiko غير مثبتة. نفّذ: pip install paramiko ثم أعد تشغيل البرنامج.")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    common = dict(timeout=12, allow_agent=False, look_for_keys=False)
    if mode == "root":
        client.connect(ip, port=22, username="root", password=secret or "alpine", **common)
        return client
    try:
        pem = http_get("http://%s:9991/webos_rsa" % ip, 5).decode()
    except Exception:
        raise RuntimeError("ما قدرت أجيب مفتاح الشاشة. افتح تطبيق Developer Mode في الشاشة "
                           "وفعّل Key Server ثم حاول مرة ثانية.")
    if "PRIVATE KEY" not in pem:
        raise RuntimeError("الشاشة رجّعت شيء غير المفتاح. أطفئ Key Server وشغّله من جديد في تطبيق Developer Mode.\n"
                           "أول سطر وصل: " + (pem.strip().splitlines() or ["(فاضي)"])[0][:80])
    key, errors = None, []
    raw = secret.strip()
    for cls_name in ("RSAKey", "Ed25519Key", "ECDSAKey"):
        cls = getattr(paramiko, cls_name, None)
        if cls is None:
            continue
        for pw in dict.fromkeys([raw, raw.upper(), raw.lower()]):
            try:
                key = cls.from_private_key(io.StringIO(pem), password=pw)
                break
            except Exception as e:
                errors.append("%s: %s" % (cls_name, e))
        if key:
            break
    if key is None:
        try:
            key = paramiko.RSAKey(key=load_webos_key(pem, raw))
        except Exception as e:
            errors.append("webOS: %s" % e)
    if key is None:
        raise RuntimeError("ما قدرت أفتح مفتاح الشاشة بالـ Passphrase هذي.\n"
                           "نوع المفتاح: " + pem.strip().splitlines()[0][:60] + "\n"
                           "التفاصيل: " + " | ".join(dict.fromkeys(errors))[-600:])
    try:
        client.connect(ip, port=9922, username="prisoner", pkey=key, **common)
    except paramiko.AuthenticationException:
        # بعض إصدارات webOS تقبل ssh-rsa القديم فقط
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(ip, port=9922, username="prisoner", pkey=key,
                       disabled_algorithms={"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}, **common)
    return client


def _ssh_exec(client, cmd, timeout=60):
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (stdout.read() + stderr.read()).decode(errors="ignore").strip()


# ---------------------------------------------------------------- LG عبر JSch (تطبيق الأندرويد)
def _der_len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _der_int(v):
    b = v.to_bytes(v.bit_length() // 8 + 1, "big")
    return b"\x02" + _der_len(len(b)) + b


def _java_bytes(arr):
    return bytes((x + 256) % 256 for x in arr)


def clean_webos_pem(pem, passphrase):
    """يفك مفتاح الشاشة بأدوات جافا ويعيد كتابته بترميز سليم بدون تشفير (لـ JSch)."""
    import base64
    import hashlib
    from java import jclass
    lines = [l.strip() for l in pem.strip().splitlines()]
    headers, body = {}, []
    for l in lines[1:-1]:
        if ":" in l:
            k, v = l.split(":", 1)
            headers[k.strip()] = v.strip()
        elif l:
            body.append(l)
    data = base64.b64decode("".join(body))
    dek = headers.get("DEK-Info")
    if dek:
        name, iv_hex = [x.strip() for x in dek.split(",")]
        iv = bytes.fromhex(iv_hex)
        size = {"AES-128-CBC": 16, "AES-192-CBC": 24, "AES-256-CBC": 32, "DES-EDE3-CBC": 24}.get(name)
        if not size:
            raise ValueError("تشفير غير مدعوم: " + name)
        pw, key, prev = passphrase.encode(), b"", b""
        while len(key) < size:
            prev = hashlib.md5(prev + pw + iv[:8]).digest()
            key += prev
        key = key[:size]
        Cipher = jclass("javax.crypto.Cipher")
        Spec = jclass("javax.crypto.spec.SecretKeySpec")
        IvSpec = jclass("javax.crypto.spec.IvParameterSpec")
        algo = "AES" if name.startswith("AES") else "DESede"
        c = Cipher.getInstance(algo + "/CBC/NoPadding")
        c.init(Cipher.DECRYPT_MODE, Spec(key, algo), IvSpec(iv))
        plain = _java_bytes(c.doFinal(data))
        pad = plain[-1]
        if not 1 <= pad <= len(iv) or plain[-pad:] != bytes([pad]) * pad:
            raise ValueError("الـ Passphrase لا تطابق المفتاح")
        data = plain[:-pad]
    ints = _der_ints(data)
    if len(ints) >= 9:
        ints = ints[1:]
    n, e, d, p, q = ints[:5]
    if p * q != n:
        raise ValueError("المفتاح بعد فكه غير سليم")
    vals = [0, n, e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p)]
    body = b"".join(_der_int(v) for v in vals)
    der = b"\x30" + _der_len(len(body)) + body
    b64 = base64.b64encode(der).decode()
    return "-----BEGIN RSA PRIVATE KEY-----\n" + "\n".join(b64[i:i + 64] for i in range(0, len(b64), 64)) + \
           "\n-----END RSA PRIVATE KEY-----\n"


def _jsch_session(ip, mode, secret):
    from java import jclass
    JSch = jclass("com.jcraft.jsch.JSch")
    jsch = JSch()
    if mode == "root":
        session = jsch.getSession("root", ip, 22)
        session.setPassword(secret or "alpine")
    else:
        try:
            pem = http_get("http://%s:9991/webos_rsa" % ip, 5).decode()
        except Exception:
            raise RuntimeError("ما قدرت أجيب مفتاح الشاشة. افتح تطبيق Developer Mode في الشاشة "
                               "وفعّل Key Server ثم حاول مرة ثانية.")
        if "PRIVATE KEY" not in pem:
            raise RuntimeError("الشاشة رجّعت شيء غير المفتاح. أطفئ Key Server وشغّله من جديد.")
        try:
            clean = clean_webos_pem(pem, secret.strip())
        except Exception as e:
            raise RuntimeError("ما قدرت أفتح مفتاح الشاشة بالـ Passphrase هذي (%s)." % e)
        jsch.addIdentity("webos", clean.encode(), None, None)
        session = jsch.getSession("prisoner", ip, 9922)
    session.setConfig("StrictHostKeyChecking", "no")
    session.setConfig("server_host_key", "ssh-ed25519,ecdsa-sha2-nistp256,rsa-sha2-512,rsa-sha2-256,ssh-rsa")
    session.setConfig("PubkeyAcceptedAlgorithms", "rsa-sha2-512,rsa-sha2-256,ssh-rsa")
    session.setConfig("kex", str(JSch.getConfig("kex")) +
                      ",diffie-hellman-group14-sha1,diffie-hellman-group-exchange-sha1,diffie-hellman-group1-sha1")
    session.connect(15000)
    return session


def _jsch_exec(session, cmd, data=None, until=None, timeout=60):
    from java import jarray
    ch = session.openChannel("exec")
    ch.setCommand(cmd)
    ins, err = ch.getInputStream(), ch.getErrStream()
    outs = ch.getOutputStream() if data is not None else None
    ch.connect(15000)
    if data is not None:
        for i in range(0, len(data), 65536):
            outs.write(data[i:i + 65536])
        outs.flush()
        outs.close()
    buf, end, jb = bytearray(), time.time() + timeout, jarray("B")(8192)
    while time.time() < end:
        got = False
        for st in (ins, err):
            while st.available() > 0:
                n = st.read(jb, 0, 8192)
                if n <= 0:
                    break
                buf.extend(_java_bytes(jb[:n]))
                got = True
        if until and until(buf.decode("utf-8", "ignore")):
            break
        if ch.isClosed() and ins.available() == 0 and err.available() == 0:
            break
        if not got:
            time.sleep(0.2)
    status = ch.getExitStatus() if ch.isClosed() else -1
    ch.disconnect()
    return buf.decode("utf-8", "ignore"), status


def lg_install_jsch(ip, data, filename, mode, secret):
    log = []
    session = _jsch_session(ip, mode, secret)
    try:
        log.append("تم الاتصال بالشاشة (%s)." % ("روت" if mode == "root" else "وضع المطور"))
        folder = "/tmp" if mode == "root" else "/media/developer/temp"
        remote = folder + "/tvm_upload.ipk"
        _jsch_exec(session, "mkdir -p %s" % folder)
        _, st = _jsch_exec(session, "cat > %s" % remote, data=data, timeout=300)
        if st not in (0, -1):
            raise RuntimeError("فشل رفع الملف إلى الشاشة (قد تكون المساحة ممتلئة).")
        log.append("تم رفع %s (%.1f ميجا)." % (filename, len(data) / 1048576))
        luna = "luna-send" if mode == "root" else "luna-send-pub"
        payload = json.dumps({"id": "com.ares.defaultName", "ipkUrl": remote, "subscribe": True})
        done = lambda t: '"installed"' in t.lower() or "fail" in t.lower() or '"returnvalue": false' in t.lower()
        buf, _ = _jsch_exec(session, "%s -i -f luna://com.webos.appInstallService/dev/install '%s'" % (luna, payload),
                            until=done, timeout=180)
        ok = '"installed"' in buf.lower()
        states = re.findall(r'"state"\s*:\s*"([^"]+)"', buf)
        errors = re.findall(r'"errorText"\s*:\s*"([^"]+)"', buf)
        if states:
            log.append("حالة التثبيت: " + " ← ".join(dict.fromkeys(states)))
        if errors:
            log.append("الخطأ: " + errors[-1])
        if not states and not errors:
            log.append(buf[-1500:] or "لم يصل رد من الشاشة.")
        _jsch_exec(session, "rm -f %s" % remote)
        return ok, log
    finally:
        session.disconnect()


def lg_install(ip, data, filename, mode, secret):
    if paramiko is None and _jsch_ok():
        return lg_install_jsch(ip, data, filename, mode, secret)
    log = []
    client = _lg_ssh(ip, mode, secret)
    try:
        log.append("تم الاتصال بالشاشة (%s)." % ("روت" if mode == "root" else "وضع المطور"))
        folder = "/tmp" if mode == "root" else "/media/developer/temp"
        remote = folder + "/tvm_upload.ipk"
        _ssh_exec(client, "mkdir -p %s" % folder)
        ch = client.get_transport().open_session()
        ch.exec_command("cat > %s" % remote)
        ch.sendall(data)
        ch.shutdown_write()
        if ch.recv_exit_status() != 0:
            raise RuntimeError("فشل رفع الملف إلى الشاشة (قد تكون المساحة ممتلئة).")
        ch.close()
        log.append("تم رفع %s (%.1f ميجا)." % (filename, len(data) / 1048576))

        luna = "luna-send" if mode == "root" else "luna-send-pub"
        payload = json.dumps({"id": "com.ares.defaultName", "ipkUrl": remote, "subscribe": True})
        ch = client.get_transport().open_session()
        ch.exec_command("%s -i -f luna://com.webos.appInstallService/dev/install '%s'" % (luna, payload))
        buf, end = "", time.time() + 180
        while time.time() < end:
            if ch.recv_ready():
                buf += ch.recv(4096).decode(errors="ignore")
                low = buf.lower()
                if '"installed"' in low or "fail" in low or '"returnvalue": false' in low:
                    break
            elif ch.exit_status_ready():
                break
            else:
                time.sleep(0.3)
        ch.close()
        ok = '"installed"' in buf.lower()
        states = re.findall(r'"state"\s*:\s*"([^"]+)"', buf)
        errors = re.findall(r'"errorText"\s*:\s*"([^"]+)"', buf)
        if states:
            log.append("حالة التثبيت: " + " ← ".join(dict.fromkeys(states)))
        if errors:
            log.append("الخطأ: " + errors[-1])
        if not states and not errors:
            log.append(buf[-1500:] or "لم يصل رد من الشاشة.")
        _ssh_exec(client, "rm -f %s" % remote)
        return ok, log
    finally:
        client.close()


# ---------------------------------------------------------------- حاجب الإعلانات (DNS)
def parse_question(data):
    i, labels = 12, []
    while True:
        n = data[i]
        if n == 0:
            i += 1
            break
        if n & 0xC0:
            raise ValueError("compressed name in question")
        labels.append(data[i + 1:i + 1 + n].decode(errors="ignore"))
        i += 1 + n
    qtype = struct.unpack(">H", data[i:i + 2])[0]
    return ".".join(labels).lower(), qtype, i + 4


def blocked_reply(data, qend):
    # رد NXDOMAIN: النطاق غير موجود
    return data[:2] + struct.pack(">HHHHH", 0x8183, 1, 0, 0, 0) + data[12:qend]


class DNSBlocker:
    def __init__(self):
        self.cfg = load_config()
        self.server = None
        self.thread = None
        self.error = ""
        self.blocked = 0
        self.forwarded = 0
        self.recent = deque(maxlen=40)
        self.clients = {}
        self.lock = threading.Lock()
        self.extra = set()
        self._load_extra()

    @property
    def upstream(self):
        return self.cfg.get("upstream") or "1.1.1.1"

    def _load_extra(self):
        self.extra = set()
        try:
            with open(EXTRA_LIST_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    d = self._clean(line)
                    if d:
                        self.extra.add(d)
        except OSError:
            pass

    @staticmethod
    def _clean(line):
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            return ""
        parts = line.split()
        d = parts[-1] if parts[0] in ("0.0.0.0", "127.0.0.1", "::") or len(parts) > 1 else parts[0]
        d = d.strip(".")
        return d if "." in d and d not in ("localhost", "0.0.0.0") else ""

    def domains(self):
        return set(DEFAULT_BLOCK) | self.extra | {self._clean(x) for x in self.cfg.get("custom", []) if self._clean(x)}

    def is_blocked(self, name, block, allow):
        parts = name.rstrip(".").split(".")
        suffixes = [".".join(parts[i:]) for i in range(len(parts) - 1)]
        if any(s in allow for s in suffixes):
            return False
        return any(s in block for s in suffixes)

    def start(self):
        if self.server:
            return True
        self.error = ""
        blocker = self
        self._block = self.domains()
        self._allow = {self._clean(x) for x in self.cfg.get("allow", []) if self._clean(x)}

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                data, sock = self.request
                client = self.client_address[0]
                try:
                    name, _, qend = parse_question(data)
                except Exception:
                    name, qend = None, 0
                if name and blocker.is_blocked(name, blocker._block, blocker._allow):
                    with blocker.lock:
                        blocker.blocked += 1
                        blocker.clients[client] = blocker.clients.get(client, 0) + 1
                        blocker.recent.appendleft({"name": name, "client": client, "t": time.strftime("%H:%M:%S")})
                    sock.sendto(blocked_reply(data, qend), self.client_address)
                    return
                up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    up.settimeout(4)
                    up.sendto(data, (blocker.upstream, 53))
                    resp, _ = up.recvfrom(4096)
                    sock.sendto(resp, self.client_address)
                    with blocker.lock:
                        blocker.forwarded += 1
                except OSError:
                    pass
                finally:
                    up.close()

        class Server(socketserver.ThreadingUDPServer):
            allow_reuse_address = True
            daemon_threads = True

        last_err = None
        for host in (local_ip(), "0.0.0.0"):
            try:
                self.server = Server((host, 53), Handler)
                break
            except PermissionError:
                last_err = "يحتاج صلاحيات المسؤول: أغلق البرنامج وشغّله كمسؤول (Run as administrator) أو بـ sudo."
            except OSError as e:
                last_err = "المنفذ 53 مستخدم من برنامج آخر على هذا الكمبيوتر (%s)." % e
        if not self.server:
            self.error = last_err or "تعذّر تشغيل حاجب الإعلانات."
            return False
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return True

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def reload(self):
        running = bool(self.server)
        self.stop()
        if running:
            self.start()

    def download_list(self):
        raw = http_get(BLOCKLIST_URL, 15).decode(errors="ignore")
        lines = [self._clean(l) for l in raw.splitlines()]
        lines = [l for l in lines if l]
        if len(lines) < 10:
            raise RuntimeError("القائمة اللي وصلت فاضية أو غير صالحة.")
        with open(EXTRA_LIST_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self._load_extra()
        self.reload()
        return len(lines)

    def status(self):
        with self.lock:
            return {
                "running": bool(self.server),
                "error": self.error,
                "blocked": self.blocked,
                "forwarded": self.forwarded,
                "recent": list(self.recent)[:25],
                "clients": self.clients,
                "listSize": len(self.domains()),
                "extraSize": len(self.extra),
                "custom": self.cfg.get("custom", []),
                "allow": self.cfg.get("allow", []),
                "upstream": self.upstream,
            }

    def save_lists(self, custom, allow, upstream):
        self.cfg["custom"] = [x.strip() for x in custom if x.strip()]
        self.cfg["allow"] = [x.strip() for x in allow if x.strip()]
        try:
            ipaddress.ip_address(upstream.strip())
            self.cfg["upstream"] = upstream.strip()
        except ValueError:
            pass
        save_config(self.cfg)
        self.reload()


DNS = DNSBlocker()


# ---------------------------------------------------------------- لوحة التحكم
class Panel(http.server.BaseHTTPRequestHandler):
    server_version = "TVManager/1.0"

    def log_message(self, *args):
        pass

    def _send(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8", code)

    def _body(self, limit=1 << 20):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ValueError("الملف أكبر من المسموح.")
        return self.rfile.read(n) if n else b""

    def _authed(self):
        return self.headers.get("X-Key") == TOKEN

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            q = urllib.parse.parse_qs(u.query)
            if q.get("k", [""])[0] != TOKEN:
                msg = ("<meta charset=utf-8><body dir=rtl style='font-family:sans-serif;padding:2rem'>"
                       "<h2>الرابط ناقص</h2><p>افتح الرابط الكامل الظاهر في نافذة البرنامج على الكمبيوتر.</p>")
                return self._send(msg.encode(), "text/html; charset=utf-8", 403)
            return self._send(PAGE.encode(), "text/html; charset=utf-8")
        if u.path == "/screen-preview":
            q = urllib.parse.parse_qs(u.query)
            if q.get("k", [""])[0] != TOKEN:
                return self._send(b"forbidden", "text/plain", 403)
            try:
                feats = json.loads(q.get("f", ["{}"])[0] or "{}")
            except ValueError:
                feats = {}
            html = screen.page(int(q.get("country", ["0"])[0] or 0), int(q.get("city", ["0"])[0] or 0), features=feats,
                               photo_base="/screen-photo/", photo_suffix="?k=" + TOKEN,
                               keep_src="data:video/mp4;base64," + screen.KEEP_MP4_B64)
            return self._send(html.encode(), "text/html; charset=utf-8")
        if u.path.startswith("/screen-photo/"):
            q = urllib.parse.parse_qs(u.query)
            name = os.path.basename(u.path)
            path = os.path.join(screen.PHOTO_DIR, name)
            if q.get("k", [""])[0] != TOKEN or name not in screen.photo_names():
                return self._send(b"not found", "text/plain", 404)
            with open(path, "rb") as f:
                return self._send(f.read(), "image/jpeg")
        if not self._authed():
            return self._json({"error": "غير مصرح"}, 403)
        if u.path == "/api/info":
            ip = local_ip()
            return self._json({
                "ip": ip,
                "phoneUrl": "http://%s:%d/?k=%s" % (ip, APP_PORT, TOKEN),
                "sdb": bool(find_sdb()),
                "tizen": bool(find_tizen()),
                "countries": [[c[0], [x[0] for x in c[3]]] for c in screen.COUNTRIES],
                "leagues": [list(x) for x in screen.LEAGUES],
                "defaultLeagues": screen.DEFAULT_LEAGUES,
                "paramiko": paramiko is not None or _jsch_ok(),
                "paramikoErr": PARAMIKO_ERR,
                "os": sys.platform,
                "android": IS_ANDROID,
            })
        if u.path == "/api/dns":
            return self._json(DNS.status())
        if u.path == "/api/quran/reciters":
            return self._json({"reciters": [{"id": r["id"], "label": r["label"], "count": len(r["surahs"])}
                                            for r in screen.quran_reciters()]})
        if u.path == "/api/photos":
            return self._json({"count": len(screen.photo_names())})
        if u.path == "/api/photos/list":
            return self._json({"photos": [{"name": n, "title": screen.photo_meta(n).get("name", ""),
                                           "stock": bool(screen.photo_meta(n).get("stock"))} for n in screen.photo_names()]})
        self._json({"error": "غير موجود"}, 404)

    def do_POST(self):
        if not self._authed():
            return self._json({"error": "غير مصرح"}, 403)
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/scan":
                req = json.loads(self._body() or b"{}")
                return self._json({"devices": scan(bool(req.get("deep")))})
            if path == "/api/install":
                brand = self.headers.get("X-Brand", "")
                ip = self.headers.get("X-Ip", "")
                ipaddress.ip_address(ip)
                filename = urllib.parse.unquote(self.headers.get("X-Filename", "app"))
                data = self._body(MAX_UPLOAD)
                if not data:
                    raise ValueError("ما وصل أي ملف.")
                if brand == "samsung":
                    ok, log = samsung_install(ip, data, filename)
                elif brand == "lg":
                    ok, log = lg_install(ip, data, filename,
                                         self.headers.get("X-Mode", "dev"),
                                         self.headers.get("X-Secret", ""))
                elif brand == "android":
                    ok, log = android_install(ip, self.headers.get("X-Port", "5555"), data, filename)
                else:
                    raise ValueError("نوع شاشة غير معروف.")
                return self._json({"ok": ok, "log": log})
            if path in ("/api/android/pair", "/api/android/adblock"):
                req = json.loads(self._body() or b"{}")
                ip = req.get("ip", "")
                ipaddress.ip_address(ip)
                if path.endswith("pair"):
                    ok, log = android_pair(ip, int(req.get("port", 0)), str(req.get("code", "")).strip())
                else:
                    ok, log = android_adblock(ip, req.get("port") or "5555", bool(req.get("enable", True)))
                return self._json({"ok": ok, "log": log})
            if path == "/api/screen/install":
                req = json.loads(self._body() or b"{}")
                ip = req.get("ip", "")
                ipaddress.ip_address(ip)
                country, city = int(req.get("country", 0)), int(req.get("city", 0))
                stock_log = []
                screen.fetch_stock_photos(stock_log, only_if_empty=True)
                if req.get("brand") == "lg":
                    font_log = []
                    pkg = screen.lg_ipk(country, city, font_log, features=req.get("features"))
                    ok, log = lg_install(ip, pkg, "Shashati.ipk", req.get("mode", "dev"), req.get("secret", ""))
                    log[0:0] = font_log
                elif req.get("brand") == "samsung":
                    ok, log = samsung_screen_install(ip, country, city, req.get("profile", "").strip(), req.get("features"))
                else:
                    raise ValueError("نوع شاشة غير معروف.")
                log[0:0] = stock_log
                return self._json({"ok": ok, "log": log})
            if path == "/api/screen/package":
                req = json.loads(self._body() or b"{}")
                country, city = int(req.get("country", 0)), int(req.get("city", 0))
                if req.get("brand") == "lg":
                    data, name = screen.lg_ipk(country, city, features=req.get("features")), "Shashati.ipk"
                else:
                    data, name = screen.tizen_unsigned_zip(country, city, features=req.get("features")), "Shashati-tizen-unsigned.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/photos/add":
                data = self._body(4 * 1024 * 1024)
                if data[:3] != b"\xff\xd8\xff":
                    raise ValueError("الملف مو صورة JPEG.")
                names = screen.photo_names()
                if len(names) >= 20:
                    raise ValueError("الحد الأقصى ٢٠ صورة.")
                screen.save_photo(data)
                return self._json({"ok": True, "count": len(screen.photo_names())})
            if path == "/api/photos/delete":
                req = json.loads(self._body() or b"{}")
                name = os.path.basename(str(req.get("name", "")))
                if name in screen.photo_names():
                    for ext in (".jpg", ".json"):
                        try:
                            os.remove(os.path.join(screen.PHOTO_DIR, name[:-4] + ext))
                        except OSError:
                            pass
                return self._json({"ok": True, "count": len(screen.photo_names())})
            if path == "/api/matches/test":
                req = json.loads(self._body() or b"{}")
                matches, errors = screen.fetch_matches([str(x) for x in req.get("leagues", [])][:12],
                                                       int(req.get("tz", 180)))
                return self._json({"ok": True, "matches": matches, "errors": errors})
            if path == "/api/photos/clear":
                screen.clear_photos()
                return self._json({"ok": True, "count": 0})
            if path == "/api/photos/stock":
                log = []
                screen.fetch_stock_photos(log)
                return self._json({"ok": True, "count": len(screen.photo_names()), "log": log})
            if path == "/api/dns/start":
                ok = DNS.start()
                return self._json({"ok": ok, **DNS.status()})
            if path == "/api/dns/stop":
                DNS.stop()
                return self._json({"ok": True, **DNS.status()})
            if path == "/api/dns/download":
                n = DNS.download_list()
                return self._json({"ok": True, "count": n, **DNS.status()})
            if path == "/api/dns/lists":
                req = json.loads(self._body() or b"{}")
                DNS.save_lists(req.get("custom", []), req.get("allow", []), req.get("upstream", ""))
                return self._json({"ok": True, **DNS.status()})
        except Exception as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        self._json({"error": "غير موجود"}, 404)


PAGE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1222">
<title>مدير الشاشات</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#0b1222; --panel:#121b2e; --panel2:#18233a; --line:#25324d; --ink:#eef1f6; --muted:#9ba7bb;
  --gold:#d9b46a; --gold-ink:#1a1408; --gold-soft:rgba(217,180,106,.14);
  --ok:#4fc38a; --warn:#e7b04b; --bad:#ff7b72; --sam:#8ea2ff; --lg:#ff7aa0; --and:#3ddc84; --r:18px;
  color-scheme:dark;
}
@media (prefers-color-scheme:light){:root{
  --bg:#f3f0e9; --panel:#ffffff; --panel2:#f7f4ee; --line:#e3dccf; --ink:#1b1915; --muted:#6a6356;
  --gold:#a07a2c; --gold-ink:#ffffff; --gold-soft:rgba(160,122,44,.12);
  --ok:#1f8a55; --warn:#9a6200; --bad:#c0362c; --sam:#2747c7; --lg:#b0123f; --and:#1b8a4a;
  color-scheme:light;}}
*{box-sizing:border-box;min-width:0}
html,body{margin:0;max-width:100%;overflow-x:hidden}
body{background:var(--bg);color:var(--ink);font:16px/1.6 "IBM Plex Sans Arabic","Segoe UI",Tahoma,sans-serif;-webkit-tap-highlight-color:transparent}
.app{max-width:820px;margin:0 auto;padding:calc(env(safe-area-inset-top,0px) + 14px) 16px calc(100px + env(safe-area-inset-bottom,0px))}
p{margin:0}
svg{flex:none}

header.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:44px;height:44px;border-radius:14px;background:var(--gold-soft);display:grid;place-items:center;color:var(--gold)}
h1{font-size:1.3rem;margin:0;font-weight:700;line-height:1.3}
.sub{color:var(--muted);font-size:.86rem}

.view{display:none}
.view.on{display:block;animation:rise .28s ease}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}

.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:18px;margin-bottom:14px}
.card h2{font-size:1.06rem;margin:0 0 4px;font-weight:600}
.card .desc{color:var(--muted);font-size:.9rem;margin-bottom:14px}
.hero{display:grid;gap:14px}
.hero .ic{width:52px;height:52px;border-radius:16px;background:var(--gold-soft);color:var(--gold);display:grid;place-items:center}

.btn{appearance:none;-webkit-appearance:none;border:1px solid var(--line);background:var(--panel2);color:var(--ink);font:inherit;font-weight:500;
  padding:12px 16px;border-radius:14px;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:48px;text-align:center}
.btn.primary{background:var(--gold);border-color:var(--gold);color:var(--gold-ink);font-weight:600}
.btn.block{width:100%}
.btn.ghost{background:transparent}
.btn.danger{color:var(--bad)}
.btn.sm{min-height:38px;padding:7px 12px;font-size:.9rem;border-radius:11px}
.btn:disabled{opacity:.55;cursor:progress}
.btn:active{transform:scale(.98)}
.row{display:flex;gap:10px;flex-wrap:wrap}
.row > .btn{flex:1 1 140px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
label.f{display:block;font-size:.84rem;color:var(--muted);margin-bottom:6px}
input[type=text],input[type=search],select,textarea{width:100%;font:inherit;color:var(--ink);background:var(--panel2);border:1px solid var(--line);border-radius:12px;padding:11px 12px;min-height:48px}
textarea{min-height:110px;direction:ltr;text-align:left;font-size:.85rem}
:focus-visible{outline:3px solid var(--gold);outline-offset:2px}

.toggle{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 0;border-bottom:1px solid var(--line);cursor:pointer}
.toggle:last-child{border-bottom:0;padding-bottom:2px}
.toggle b{display:block;font-weight:500}
.toggle span{display:block;color:var(--muted);font-size:.84rem}
input.sw{appearance:none;-webkit-appearance:none;flex:none;width:52px;height:30px;border-radius:30px;background:var(--line);position:relative;cursor:pointer;transition:background .2s;margin:0}
input.sw::after{content:"";position:absolute;top:3px;right:3px;width:24px;height:24px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:transform .2s}
input.sw:checked{background:var(--gold)}
input.sw:checked::after{transform:translateX(-22px)}

.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{appearance:none;font:inherit;font-size:.9rem;border:1px solid var(--line);background:var(--panel2);color:var(--ink);padding:8px 14px;border-radius:999px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
.chip[aria-pressed=true]{background:var(--gold-soft);border-color:var(--gold);color:var(--gold)}
.chip.x{background:var(--gold-soft);border-color:var(--gold);color:var(--gold)}

.tv{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);overflow:hidden;margin-bottom:14px}
.tv-head{display:flex;gap:12px;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line)}
.tv-ic{width:46px;height:46px;border-radius:14px;background:var(--panel2);display:grid;place-items:center;color:var(--brand);flex:none}
.tv-head .t{flex:1}
.tv-head b{display:block;font-weight:600;overflow-wrap:anywhere;line-height:1.35}
.tv-head small{color:var(--muted);display:block;font-size:.82rem}
.tag{font-size:.75rem;padding:3px 9px;border-radius:999px;background:var(--panel2);color:var(--brand);border:1px solid var(--line);white-space:nowrap}
.tv-body{padding:14px 16px 16px;display:grid;gap:12px}
.tv.samsung{--brand:var(--sam)} .tv.lg{--brand:var(--lg)} .tv.android{--brand:var(--and)} .tv.other{--brand:var(--muted)}
.pill{display:flex;align-items:flex-start;gap:10px;padding:11px 13px;border-radius:12px;font-size:.9rem;border:1px solid;line-height:1.5}
.pill::before{content:"";width:9px;height:9px;border-radius:50%;background:currentColor;flex:none;margin-top:.45em}
.pill.ok{color:var(--ok);border-color:rgba(79,195,138,.35);background:rgba(79,195,138,.07)}
.pill.warn{color:var(--warn);border-color:rgba(231,176,75,.35);background:rgba(231,176,75,.07)}
.pill.bad{color:var(--bad);border-color:rgba(255,123,114,.35);background:rgba(255,123,114,.07)}
details.more{border:1px solid var(--line);border-radius:14px;padding:0 14px;background:var(--panel2)}
details.more>summary{cursor:pointer;padding:13px 0;font-weight:500;list-style:none;display:flex;justify-content:space-between;align-items:center;gap:8px}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::after{content:"+";color:var(--muted);font-size:1.3rem;line-height:1}
details[open].more>summary::after{content:"−"}
details.more .in{padding-bottom:14px;display:grid;gap:10px}
details.more ol{margin:0;padding-inline-start:20px;color:var(--muted);font-size:.92rem;display:grid;gap:4px}
pre.log{margin:0;background:#070b14;color:#cfe3ef;border-radius:12px;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-size:.84rem;max-height:240px;overflow:auto}
.empty{text-align:center;padding:30px 14px;color:var(--muted)}
.spin{width:18px;height:18px;border:2px solid currentColor;border-inline-end-color:transparent;border-radius:50%;animation:spin .8s linear infinite;display:inline-block}
@keyframes spin{to{transform:rotate(360deg)}}
.ltr{direction:ltr;unicode-bidi:isolate}

.actionbar{position:sticky;bottom:calc(84px + env(safe-area-inset-bottom,0px));background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:12px;display:grid;gap:10px;box-shadow:0 12px 32px rgba(0,0,0,.28);z-index:5}

.photos{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.ph{position:relative;border-radius:14px;overflow:hidden;background:var(--panel2);aspect-ratio:16/10;margin:0}
.ph img{width:100%;height:100%;object-fit:cover;display:block}
.ph figcaption{position:absolute;left:0;right:0;bottom:0;padding:18px 8px 6px;font-size:.76rem;line-height:1.4;background:linear-gradient(transparent,rgba(0,0,0,.78));color:#fff}
.ph button{position:absolute;top:6px;left:6px;width:34px;height:34px;border-radius:50%;border:0;background:rgba(0,0,0,.6);color:#fff;font-size:1.05rem;cursor:pointer}

.rlist{max-height:320px;overflow:auto;border:1px solid var(--line);border-radius:14px;background:var(--panel2)}
.rlist label{display:flex;gap:12px;align-items:center;padding:11px 13px;border-bottom:1px solid var(--line);cursor:pointer;font-size:.93rem}
.rlist label:last-child{border-bottom:0}
.rlist input{width:21px;height:21px;accent-color:var(--gold);flex:none;margin:0}
.rlist small{color:var(--muted);margin-inline-start:auto;white-space:nowrap}

.match{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--line);font-size:.92rem}
.match:last-child{border-bottom:0}
.match small{color:var(--muted);display:block}
.live{color:var(--bad);font-weight:600}

.dns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:14px}
.dns div{background:var(--panel2);border:1px solid var(--line);border-radius:14px;padding:12px;text-align:center}
.dns b{display:block;font-size:1.15rem;direction:ltr}
.dns small{color:var(--muted)}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:12px 0}
.stats div{background:var(--panel2);border-radius:14px;padding:12px;text-align:center}
.stats b{display:block;font-size:1.4rem;line-height:1.2}
.stats small{color:var(--muted);font-size:.78rem}
.feed{list-style:none;margin:0;padding:0;max-height:200px;overflow:auto;border:1px solid var(--line);border-radius:12px}
.feed li{display:flex;justify-content:space-between;gap:10px;padding:7px 12px;border-bottom:1px solid var(--line);font-size:.84rem}
.feed li span:first-child{direction:ltr;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:10px;height:10px;border-radius:50%;background:var(--muted);display:inline-block}
.dot.on{background:var(--ok);box-shadow:0 0 0 4px rgba(79,195,138,.2)}

nav.tabs{position:fixed;bottom:0;left:0;right:0;background:var(--panel);border-top:1px solid var(--line);padding:6px 8px calc(6px + env(safe-area-inset-bottom,0px));display:flex;justify-content:center;z-index:20}
nav.tabs .inner{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;width:100%;max-width:820px}
.tab{appearance:none;border:0;background:transparent;color:var(--muted);font:inherit;font-size:.78rem;padding:7px 4px;border-radius:14px;display:flex;flex-direction:column;align-items:center;gap:2px;cursor:pointer}
.tab svg{width:24px;height:24px}
.tab[aria-selected=true]{color:var(--gold);background:var(--gold-soft);font-weight:600}

.sheet{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:flex-end;justify-content:center;z-index:30}
.sheet.on{display:flex}
.sheet .box{background:var(--panel);border-radius:22px 22px 0 0;padding:22px;width:100%;max-width:520px;padding-bottom:calc(22px + env(safe-area-inset-bottom,0px));display:grid;gap:14px;justify-items:center;text-align:center}
#qr{background:#fff;padding:10px;border-radius:12px;line-height:0}
.link{direction:ltr;word-break:break-all;font-size:.85rem;color:var(--muted)}
.toast{position:fixed;left:50%;bottom:calc(92px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:11px 18px;border-radius:14px;font-size:.92rem;opacity:0;transition:opacity .25s;pointer-events:none;z-index:40;max-width:92vw;text-align:center}
.toast.on{opacity:1}
@media (min-width:760px){.app{padding-top:28px}.photos{grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}}
@media (max-width:380px){.grid2{grid-template-columns:1fr}.stats b{font-size:1.15rem}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style>
</head>
<body>
<div class="app">
  <header class="top">
    <div class="brand">
      <div class="logo"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg></div>
      <div><h1>مدير الشاشات</h1><p class="sub" id="topSub">يتعرّف على شاشاتك ويجهّز «شاشتي»</p></div>
    </div>
    <button class="btn ghost sm pc-only" onclick="openSheet()" aria-label="افتح اللوحة من الجوال">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2"/><path d="M11 18.5h2"/></svg>الجوال
    </button>
  </header>

  <!-- الشاشات -->
  <section class="view on" id="v-tvs" role="tabpanel">
    <div class="card hero">
      <div class="row" style="align-items:center;flex-wrap:nowrap">
        <div class="ic"><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a14.5 14.5 0 0 1 20 0"/><circle cx="12" cy="19.5" r="1"/></svg></div>
        <div style="flex:1"><h2>الشاشات في شبكتك</h2><p class="sub" id="scanNote">تأكد إن الشاشة شغّالة ومتصلة بنفس الواي فاي.</p></div>
      </div>
      <div class="row">
        <button class="btn primary" id="scanBtn" onclick="scan(false)">ابحث عن الشاشات</button>
        <button class="btn" id="deepBtn" onclick="scan(true)">بحث أعمق</button>
      </div>
    </div>
    <div id="tvs"></div>
  </section>

  <!-- شاشتي -->
  <section class="view" id="v-screen" role="tabpanel">
    <div class="card">
      <h2>الموقع</h2>
      <p class="desc">لمواقيت الصلاة والطقس. وتقدر تغيّره من الشاشة نفسها.</p>
      <div class="grid2">
        <div><label class="f" for="screenCountry">الدولة</label><select id="screenCountry" onchange="fillCities(); saveFeats()"></select></div>
        <div><label class="f" for="screenCity">المدينة</label><select id="screenCity" onchange="saveFeats()"></select></div>
      </div>
    </div>

    <div class="card">
      <h2>وش يظهر على الشاشة</h2>
      <p class="desc">الساعة والتاريخ الهجري ومواقيت الصلاة تظهر دائماً.</p>
      <label class="toggle"><div><b>آيات قرآنية</b><span>تتبدّل كل ٥ دقائق</span></div><input type="checkbox" class="sw" id="fVerses" checked onchange="saveFeats()"></label>
      <label class="toggle"><div><b>أحاديث صحيح البخاري</b><span>بالتناوب مع الآيات، مع رقم الحديث</span></div><input type="checkbox" class="sw" id="fHadith" checked onchange="saveFeats()"></label>
      <label class="toggle"><div><b>الطقس</b><span>اليوم واليومين الجايين</span></div><input type="checkbox" class="sw" id="fWeather" checked onchange="saveFeats()"></label>
      <label class="toggle"><div><b>مباريات اليوم</b><span>نتائج مباشرة للدوريات اللي تختارها</span></div><input type="checkbox" class="sw" id="fMatches" checked onchange="saveFeats()"></label>
      <label class="toggle"><div><b>تلاوة القرآن الكريم</b><span>تشغيل وإيقاف من ريموت الشاشة</span></div><input type="checkbox" class="sw" id="fQuran" checked onchange="saveFeats()"></label>
    </div>

    <div class="card" id="leaguesCard">
      <h2>الدوريات</h2>
      <p class="desc">اضغط لاختيار الدوريات. تقدر بعدها تخفي أي دوري من إعدادات الشاشة.</p>
      <div class="chips" id="leagueChips"></div>
      <button class="btn block" id="testBtn" onclick="testMatches()" style="margin-top:14px">تأكد من مباريات اليوم</button>
      <div id="testOut" style="margin-top:10px"></div>
    </div>

    <div class="card" id="recCard">
      <h2>القرّاء</h2>
      <p class="desc">اختر حتى ٦ قرّاء، وتبدّل بينهم من قائمة الشاشة. التلاوة تُشغَّل من mp3quran.net وتحتاج إنترنت.</p>
      <div class="chips" id="recChosen"></div>
      <input type="search" id="recSearch" placeholder="ابحث باسم القارئ" oninput="renderReciters()" style="margin:12px 0" aria-label="ابحث عن قارئ">
      <div class="rlist" id="recList"><div class="empty">جارٍ تحميل القرّاء…</div></div>
    </div>

    <div class="card pc-only">
      <details class="more"><summary>شاشات سامسونج (Tizen)</summary><div class="in">
        <label class="f" for="tzProfile">اسم ملف الشهادة (Profile) من Certificate Manager</label>
        <input type="text" id="tzProfile" class="ltr" autocomplete="off" placeholder="مثال: MyTV">
        <p class="sub">سامسونج تقبل فقط تطبيقات موقّعة بشهادتك، ويحتاج Tizen Studio على هذا الكمبيوتر.</p>
      </div></details>
    </div>
    <div class="card">
      <details class="more"><summary>تفتح تلقائياً مع تشغيل الشاشة</summary><div class="in">
        <p class="sub"><b>سامسونج:</b> الإعدادات ← عام ← ميزات ذكية ← فعّل «تشغيل آخر تطبيق تلقائياً»، وخلّ «شاشتي» آخر تطبيق قبل ما تطفي.</p>
        <p class="sub"><b>LG وwebOS:</b> ثبّت «شاشتي» أول شريط التطبيقات عشان توصل لها بضغطة.</p>
      </div></details>
    </div>

    <div class="actionbar">
      <select id="targetTv" aria-label="اختر الشاشة"></select>
      <div class="grid2">
        <button class="btn" onclick="previewScreen()">معاينة</button>
        <button class="btn primary" id="installBtn" onclick="installFromScreen()">ثبّت شاشتي</button>
      </div>
      <pre class="log" id="screenLog" hidden></pre>
    </div>
    <div class="row pc-only" style="margin-top:14px">
      <button class="btn ghost sm" onclick="downloadScreen('lg')">ملف webOS ‎(.ipk)</button>
      <button class="btn ghost sm" onclick="downloadScreen('samsung')">ملفات تايزن (غير موقّعة)</button>
    </div>
  </section>

  <!-- الخلفيات -->
  <section class="view" id="v-photos" role="tabpanel">
    <div class="card">
      <h2>خلفيات الشاشة</h2>
      <p class="desc">صور حقيقية لمكة المكرمة والمدينة المنورة والتراث ورؤية ٢٠٣٠ من ويكيميديا كومنز (مرخّصة، واسم المصوّر يظهر على الشاشة)، أو صورك الخاصة. وتقدر تخفي أي صورة من إعدادات الشاشة نفسها.</p>
      <div class="grid2">
        <button class="btn primary" id="stockBtn" onclick="stockPhotos()">حمّل صوراً جاهزة</button>
        <label class="btn" for="photoFiles" id="photoBtn" tabindex="0" onkeydown="if(event.key==='Enter')$('photoFiles').click()">أضف صورك</label>
      </div>
      <input type="file" id="photoFiles" accept="image/*" multiple hidden onchange="addPhotos()">
      <p class="sub" id="photoNote" style="margin-top:12px"></p>
    </div>
    <div class="photos" id="photoGrid"></div>
    <button class="btn block ghost danger" onclick="clearPhotos()">احذف كل الصور</button>
  </section>

  <!-- الإعلانات -->
  <section class="view" id="v-ads" role="tabpanel">
    <div class="card">
      <h2>منع الإعلانات بشكل دائم</h2>
      <p class="desc">أسهل طريقة وتشتغل بدون كمبيوتر ولا جوال: غيّر خادم DNS في الشاشة إلى خادم AdGuard المجاني.</p>
      <div class="dns">
        <div><small>الأساسي</small><b>94.140.14.14</b><button class="btn sm ghost" onclick="copy('94.140.14.14')">نسخ</button></div>
        <div><small>الثانوي</small><b>94.140.15.15</b><button class="btn sm ghost" onclick="copy('94.140.15.15')">نسخ</button></div>
      </div>
      <div style="display:grid;gap:10px">
        <details class="more"><summary>سامسونج</summary><div class="in"><ol>
          <li>الإعدادات ← عام ← الشبكة ← حالة الشبكة ← إعدادات IP.</li>
          <li>إعداد DNS ← إدخال يدوي، واكتب الرقم الأساسي واحفظ.</li>
          <li>الإعدادات ← الدعم ← الشروط والخصوصية: ألغِ الموافقة على الإعلانات المخصّصة.</li></ol></div></details>
        <details class="more"><summary>LG وشاشات webOS</summary><div class="in"><ol>
          <li>الإعدادات ← عام ← الشبكة ← اختر الاتصال ← الإعدادات المتقدمة ← تعديل.</li>
          <li>ألغِ «تلقائي» عند DNS، واكتب الرقمين واحفظ.</li>
          <li>الإعدادات ← عام ← النظام ← الإعلانات: فعّل «تقييد تتبع الإعلانات».</li></ol></div></details>
        <details class="more"><summary>أندرويد وقوقل تي في</summary><div class="in"><ol>
          <li>الإعدادات ← الشبكة والإنترنت ← DNS الخاص.</li>
          <li>اختر «اسم مضيف المزوّد» واكتب: <b class="ltr">dns.adguard-dns.com</b></li>
          <li>أو من بطاقة الشاشة في تبويب «الشاشات» بضغطة زر.</li></ol></div></details>
        <details class="more"><summary>كل أجهزة البيت (المودم)</summary><div class="in"><ol>
          <li>ادخل صفحة إعدادات المودم (غالباً 192.168.1.1).</li>
          <li>في إعدادات DNS حط الرقمين بدل التلقائي واحفظ.</li></ol></div></details>
      </div>
    </div>

    <div class="card pc-only">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div><h2>فلتر الكمبيوتر</h2><p class="sub"><span class="dot" id="dnsDot"></span> <span id="dnsPill">متوقف</span></p></div>
        <button class="btn primary" id="dnsBtn" onclick="toggleDns()" style="flex:0 0 auto">شغّل</button>
      </div>
      <p class="desc" style="margin-top:10px">طريقة بديلة: الكمبيوتر نفسه يصير فلتر. اكتب في DNS الشاشة: <b class="ltr" id="pcIp">…</b> — ولازم يبقى الكمبيوتر شغّال.</p>
      <div id="dnsErr"></div>
      <div class="stats">
        <div><b id="sBlocked">0</b><small>إعلان محجوب</small></div>
        <div><b id="sFwd">0</b><small>طلب سليم</small></div>
        <div><b id="sList">0</b><small>نطاق في القائمة</small></div>
      </div>
      <ul class="feed" id="feed"><li><span>لا شيء بعد</span></li></ul>
      <details class="more" style="margin-top:12px"><summary>القوائم المتقدمة</summary><div class="in">
        <button class="btn sm" id="dlBtn" onclick="downloadList()">حدّث القائمة الموسّعة</button>
        <p class="sub" id="extraNote"></p>
        <label class="f" for="custom">نطاقات إضافية للحجب</label><textarea id="custom"></textarea>
        <label class="f" for="allow">استثناءات</label><textarea id="allow"></textarea>
        <label class="f" for="upstream">DNS للطلبات السليمة</label><input type="text" id="upstream" class="ltr">
        <button class="btn sm" onclick="saveLists()">احفظ القوائم</button>
      </div></details>
    </div>
  </section>
</div>

<nav class="tabs" aria-label="الأقسام"><div class="inner" role="tablist">
  <button class="tab" role="tab" aria-selected="true" data-v="v-tvs" onclick="show('v-tvs')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>الشاشات</button>
  <button class="tab" role="tab" aria-selected="false" data-v="v-screen" onclick="show('v-screen')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/><path d="M17 3v4M15 5h4"/></svg>شاشتي</button>
  <button class="tab" role="tab" aria-selected="false" data-v="v-photos" onclick="show('v-photos')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="M3 16l5-5 4 4 3-3 6 6"/><circle cx="16" cy="9" r="1.5"/></svg>الخلفيات</button>
  <button class="tab" role="tab" aria-selected="false" data-v="v-ads" onclick="show('v-ads')">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6z"/><path d="M9 12l2 2 4-4"/></svg>الإعلانات</button>
</div></nav>

<div class="sheet" id="sheet" onclick="if (event.target === this) closeSheet()">
  <div class="box">
    <h2 style="margin:0">افتح اللوحة من الجوال</h2>
    <div id="qr"></div>
    <p class="sub">صوّر الرمز بكاميرا الجوال وهو على نفس الشبكة، أو افتح الرابط:</p>
    <p class="link" id="phoneUrl"></p>
    <button class="btn block" onclick="closeSheet()">تم</button>
  </div>
</div>
<div class="toast" id="toast" role="status" aria-live="polite"></div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
const K = new URLSearchParams(location.search).get('k');
let INFO = {}, DEVS = [], RECS = [], dnsFilled = false, DNSRUN = false;
const ST = loadState();

async function api(path, opts = {}) {
  opts.headers = Object.assign({'X-Key': K}, opts.headers || {});
  try {
    const r = await fetch(path, opts);
    const j = await r.json();
    if (!r.ok && !j.error) j.error = 'خطأ ' + r.status;
    return j;
  } catch (e) { return {error: 'انقطع الاتصال بالبرنامج.'}; }
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $ = id => document.getElementById(id);
function toast(msg) { const t = $('toast'); t.textContent = msg; t.classList.add('on'); clearTimeout(toast.h); toast.h = setTimeout(() => t.classList.remove('on'), 2600); }
function copy(txt) { (navigator.clipboard ? navigator.clipboard.writeText(txt) : Promise.reject()).then(() => toast('تم النسخ: ' + txt), () => toast(txt)); }
function show(v) {
  document.querySelectorAll('.view').forEach(el => el.classList.toggle('on', el.id === v));
  document.querySelectorAll('.tab').forEach(t => t.setAttribute('aria-selected', t.dataset.v === v ? 'true' : 'false'));
  try { localStorage.setItem('tvm_tab', v); } catch (e) {}
  window.scrollTo(0, 0);
  if (v === 'v-photos') loadPhotos();
}
function openSheet() { $('sheet').classList.add('on'); }
function closeSheet() { $('sheet').classList.remove('on'); }

/* الحالة المحفوظة */
function loadState() {
  let s = {};
  try { s = JSON.parse(localStorage.getItem('tvm_state') || '{}'); } catch (e) {}
  if (!s.f) { try { const old = JSON.parse(localStorage.getItem('tvm_feats') || '{}'); s.f = old; if (old.reciter) s.reciters = [String(old.reciter)]; } catch (e) { s.f = {}; } }
  s.reciters = s.reciters || [];
  return s;
}
function saveState() { try { localStorage.setItem('tvm_state', JSON.stringify(ST)); } catch (e) {} }
const FEAT_IDS = {verses: 'fVerses', hadith: 'fHadith', matches: 'fMatches', weather: 'fWeather', quran: 'fQuran'};
function feats() {
  const o = {};
  for (const k in FEAT_IDS) o[k] = $(FEAT_IDS[k]).checked;
  o.reciters = ST.reciters.slice();
  o.leagues = (ST.leagues || INFO.defaultLeagues || []).slice();
  return o;
}
function saveFeats() {
  ST.f = {}; for (const k in FEAT_IDS) ST.f[k] = $(FEAT_IDS[k]).checked;
  ST.country = $('screenCountry').value; ST.city = $('screenCity').value;
  saveState(); syncCards();
}
function restoreFeats() { for (const k in FEAT_IDS) if (ST.f && k in ST.f) $(FEAT_IDS[k]).checked = !!ST.f[k]; syncCards(); }
function syncCards() {
  $('leaguesCard').hidden = !$('fMatches').checked;
  $('recCard').hidden = !$('fQuran').checked;
}

/* المعلومات العامة */
async function loadInfo() {
  INFO = await api('/api/info');
  if (INFO.android) document.querySelectorAll('.pc-only').forEach(el => el.style.display = 'none');
  $('pcIp').textContent = INFO.ip || '—';
  $('phoneUrl').textContent = INFO.phoneUrl || '';
  try { if (!INFO.android) new QRCode($('qr'), {text: INFO.phoneUrl, width: 170, height: 170}); } catch (e) { $('qr').style.display = 'none'; }
  $('screenCountry').innerHTML = (INFO.countries || []).map((c, i) => `<option value="${i}">${esc(c[0])}</option>`).join('');
  if (ST.country != null && $('screenCountry').options[ST.country]) $('screenCountry').value = ST.country;
  fillCities();
  if (ST.city != null && $('screenCity').options[ST.city]) $('screenCity').value = ST.city;
  renderLeagues();
}
function fillCities() {
  const c = (INFO.countries || [])[+$('screenCountry').value || 0];
  $('screenCity').innerHTML = c ? c[1].map((n, i) => `<option value="${i}">${esc(n)}</option>`).join('') : '';
}
function screenCountry() { return +($('screenCountry').value || 0); }
function screenCity() { return +($('screenCity').value || 0); }

/* الدوريات */
function renderLeagues() {
  const chosen = ST.leagues || INFO.defaultLeagues || [];
  $('leagueChips').innerHTML = (INFO.leagues || []).map(l =>
    `<button class="chip" aria-pressed="${chosen.includes(l[0])}" onclick="toggleLeague('${l[0]}')">${chosen.includes(l[0]) ? '✓ ' : ''}${esc(l[1])}</button>`).join('');
}
function toggleLeague(slug) {
  const cur = (ST.leagues || INFO.defaultLeagues || []).slice(), i = cur.indexOf(slug);
  if (i >= 0) cur.splice(i, 1); else cur.push(slug);
  ST.leagues = cur; saveState(); renderLeagues(); $('testOut').innerHTML = '';
}
async function testMatches() {
  const lg = feats().leagues;
  if (!lg.length) { $('testOut').innerHTML = '<div class="pill warn">اختر دوري واحد على الأقل.</div>'; return; }
  $('testBtn').disabled = true; $('testOut').innerHTML = '<p class="sub"><span class="spin"></span> أتحقق من مباريات اليوم…</p>';
  const j = await api('/api/matches/test', {method: 'POST', body: JSON.stringify({leagues: lg, tz: -new Date().getTimezoneOffset()})});
  $('testBtn').disabled = false;
  if (j.error) { $('testOut').innerHTML = `<div class="pill bad">${esc(j.error)}</div>`; return; }
  const m = j.matches || [];
  let html = m.length ? `<div class="pill ok">لقيت ${m.length} مباراة اليوم، وبتظهر في الشاشة بنفس الطريقة.</div>` :
                       '<div class="pill warn">ما فيه مباريات اليوم في الدوريات المختارة.</div>';
  html += m.map(x => {
    const st = x.state === 'in' ? `<span class="live">مباشر ${esc(x.clock)}</span>` : x.state === 'post' ? 'انتهت' : esc(x.local);
    const sc = x.state === 'pre' ? '×' : `${esc(x.hs)} - ${esc(x.as)}`;
    return `<div class="match"><div><b class="ltr">${esc(x.home)} ${sc} ${esc(x.away)}</b><small>${esc(x.league)}</small></div><div>${st}</div></div>`;
  }).join('');
  if ((j.errors || []).length) html += `<p class="sub" style="margin-top:8px">ما قدرت أتحقق من: ${esc(j.errors.join('، '))}</p>`;
  $('testOut').innerHTML = html;
}

/* القرّاء */
async function loadReciters() {
  const j = await api('/api/quran/reciters');
  RECS = j.reciters || [];
  if (!RECS.length) { $('recList').innerHTML = '<div class="empty">تعذّر تحميل القرّاء — تأكد من الإنترنت.</div>'; return; }
  ST.reciters = ST.reciters.filter(id => RECS.some(r => String(r.id) === String(id)));
  if (!ST.reciters.length) {
    const def = RECS.find(r => r.label.includes('العفاسي') && r.label.includes('حفص')) || RECS[0];
    ST.reciters = [String(def.id)]; saveState();
  }
  renderReciters();
}
function renderReciters() {
  const q = $('recSearch').value.trim();
  const list = q ? RECS.filter(r => r.label.includes(q)) : RECS;
  $('recChosen').innerHTML = ST.reciters.map(id => {
    const r = RECS.find(x => String(x.id) === String(id));
    return r ? `<button class="chip x" onclick="toggleRec('${r.id}')" aria-label="إزالة ${esc(r.label)}">${esc(r.label.split(' — ')[0])} ✕</button>` : '';
  }).join('') || '<p class="sub">ما اخترت قارئ.</p>';
  $('recList').innerHTML = list.slice(0, 250).map(r => {
    const on = ST.reciters.includes(String(r.id));
    return `<label><input type="checkbox" ${on ? 'checked' : ''} onchange="toggleRec('${r.id}')"><span>${esc(r.label)}</span>${r.count < 114 ? `<small>${r.count} سورة</small>` : ''}</label>`;
  }).join('') || '<div class="empty">ما فيه نتائج.</div>';
}
function toggleRec(id) {
  id = String(id);
  const i = ST.reciters.indexOf(id);
  if (i >= 0) ST.reciters.splice(i, 1);
  else if (ST.reciters.length >= 6) { toast('الحد الأقصى ٦ قرّاء'); renderReciters(); return; }
  else ST.reciters.push(id);
  saveState(); renderReciters();
}

/* الشاشات */
async function scan(deep) {
  $('scanBtn').disabled = $('deepBtn').disabled = true;
  $('scanNote').innerHTML = '<span class="spin"></span> ' + (deep ? 'أفحص كل أجهزة الشبكة… قد يأخذ دقيقة.' : 'أبحث عن الشاشات…');
  const j = await api('/api/scan', {method: 'POST', body: JSON.stringify({deep})});
  $('scanBtn').disabled = $('deepBtn').disabled = false;
  if (j.error) { $('scanNote').textContent = j.error; return; }
  DEVS = j.devices || [];
  $('scanNote').textContent = DEVS.length ? 'لقيت ' + DEVS.length + (DEVS.length === 1 ? ' شاشة.' : ' شاشات.')
    : 'ما لقيت شاشات. تأكد إنها شغّالة (مو وضع الاستعداد) وعلى نفس الشبكة، وجرّب «بحث أعمق».';
  render();
}
const TV_ICON = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></svg>';
function head(d) {
  return `<div class="tv-head"><div class="tv-ic">${TV_ICON}</div><div class="t"><b>${esc(d.name)}</b>
    <small><span class="ltr">${esc(d.model)}${d.model ? ' · ' : ''}${esc(d.ip)}</span></small></div><span class="tag">${esc(d.maker || '')} · ${esc(d.os)}</span></div>`;
}
function samsungState(d) {
  if (!d.devMode) return `<div class="pill bad">وضع المطور غير مفعّل — لازم تفعّله قبل التثبيت.</div>
    <details class="more" open><summary>طريقة التفعيل</summary><div class="in"><ol>
    <li>افتح «التطبيقات» في الشاشة واضغط بالريموت <b>12345</b>.</li>
    <li>فعّل Developer Mode واكتب هذا الرقم: <b class="ltr">${esc(INFO.ip)}</b></li>
    <li>أطفئ الشاشة وشغّلها، ثم ابحث عن الشاشات مرة ثانية.</li></ol></div></details>`;
  if (d.devIP && d.devIP !== INFO.ip) return `<div class="pill warn">وضع المطور مربوط بجهاز آخر (<span class="ltr">${esc(d.devIP)}</span>). غيّره إلى <b class="ltr">${esc(INFO.ip)}</b> بطريقة 12345.</div>`;
  return '<div class="pill ok">وضع المطور مفعّل ومربوط بهذا الجهاز.</div>';
}
function lgState(d) {
  if (d.devMode) return '<div class="pill ok">تطبيق Developer Mode شغّال في الشاشة.</div>';
  if (d.rooted) return '<div class="pill ok">الشاشة فيها روت — اختر «روت» في إعدادات الاتصال.</div>';
  return `<div class="pill warn">ما لقيت وضع المطور شغّال.</div>
    <details class="more" open><summary>طريقة التفعيل</summary><div class="in"><ol>
    <li>من متجر التطبيقات في الشاشة ثبّت <b>Developer Mode</b>.</li>
    <li>سجّل دخول بحساب مطور LG (مجاني من webostv.developer.lge.com).</li>
    <li>فعّل Dev Mode Status و Key Server، والشاشة بتعيد التشغيل.</li>
    <li>انسخ الـ Passphrase الظاهرة في التطبيق للخانة تحت.</li></ol>
    <p class="sub">وضع المطور ينتهي بعد مدة؛ افتح التطبيق واضغط Extend لتمديده.</p></div></details>`;
}
function cardWebosTizen(d, i) {
  const sam = d.brand === 'samsung';
  if (sam && INFO.android) return `<article class="tv samsung">${head(d)}<div class="tv-body">${samsungState(d)}
      <div class="pill warn">التثبيت على شاشات تايزن يحتاج أدوات سامسونج، ويتم من برنامج الكمبيوتر.</div></div></article>`;
  const needs = sam ? (INFO.sdb ? '' : '<div class="pill warn">للتثبيت على سامسونج لازم Tizen Studio على هذا الكمبيوتر.</div>')
    : (INFO.paramiko ? '' : (INFO.android
        ? `<div class="pill bad">مكتبة الاتصال ما اشتغلت: <span class="ltr">${esc(INFO.paramikoErr)}</span></div>`
        : '<div class="pill warn">للتثبيت نفّذ: <span class="ltr">pip install paramiko</span> ثم أعد تشغيل البرنامج.</div>'));
  const lgFields = sam ? '' : `<div><label class="f" for="secret${i}">الـ Passphrase من تطبيق Developer Mode</label>
      <input type="text" id="secret${i}" class="ltr" autocomplete="off" placeholder="مثال: DF7813" value="${esc((ST.secrets || {})[d.ip] || '')}"
        oninput="(ST.secrets = ST.secrets || {})['${d.ip}'] = this.value.trim(); saveState()"></div>`;
  return `<article class="tv ${d.brand}">${head(d)}<div class="tv-body">
      ${sam ? samsungState(d) : lgState(d)}${needs}${lgFields}
      <button class="btn primary block" id="scr${i}" onclick="installScreen(${i})">ثبّت شاشتي على هذي الشاشة</button>
      <details class="more"><summary>تثبيت تطبيق خارجي</summary><div class="in">
        <input type="file" id="file${i}" accept="${sam ? '.wgt,.tpk' : '.ipk'}" aria-label="ملف التطبيق">
        <button class="btn" id="inst${i}" onclick="install(${i})">ثبّت الملف</button>
        <p class="sub">${sam ? 'ملفات ‎.wgt مثل TizenBrew.' : 'ملفات ‎.ipk مثل Homebrew Channel أو YouTube AdFree من webosbrew.'}</p>
      </div></details>
      ${sam ? '' : `<details class="more"><summary>إعدادات الاتصال</summary><div class="in">
        <label class="f" for="mode${i}">طريقة الاتصال</label>
        <select id="mode${i}"><option value="dev">وضع المطور</option><option value="root"${d.rooted && !d.devMode ? ' selected' : ''}>روت (Homebrew)</option></select>
        <p class="sub">في وضع الروت اكتب كلمة مرور الروت بدل الـ Passphrase.</p></div></details>`}
      <pre class="log" id="log${i}" hidden></pre>
    </div></article>`;
}
function cardAndroid(d, i) {
  return `<article class="tv android">${head(d)}<div class="tv-body">
      ${d.adb ? '<div class="pill ok">تصحيح الأخطاء عبر الشبكة شغّال في الشاشة.</div>' : '<div class="pill warn">فعّل تصحيح الأخطاء في الشاشة مرة وحدة.</div>'}
      <details class="more"${d.adb ? '' : ' open'}><summary>طريقة التفعيل</summary><div class="in"><ol>
        <li>الإعدادات ← تفضيلات الجهاز ← حول ← اضغط «البنية» ٧ مرات.</li>
        <li>خيارات المطوّر: فعّل «تصحيح أخطاء USB» و«تصحيح الأخطاء عبر الشبكة» إن وجد.</li>
        <li>أول اتصال تطلع رسالة على الشاشة: اختر «السماح دائماً».</li></ol></div></details>
      <div class="grid2">
        <button class="btn primary" onclick="androidAdblock(${i}, true)">فعّل حجب الإعلانات</button>
        <button class="btn" onclick="androidAdblock(${i}, false)">ألغِ الحجب</button>
      </div>
      <details class="more"><summary>تثبيت تطبيق APK</summary><div class="in">
        <input type="file" id="file${i}" accept=".apk" aria-label="ملف التطبيق">
        <button class="btn" id="inst${i}" onclick="install(${i})">ثبّت الملف</button>
        <p class="sub">مثل SmartTube ليوتيوب بدون إعلانات. حمّل الملفات من مواقعها الرسمية فقط.</p></div></details>
      <details class="more"><summary>إعدادات الاتصال</summary><div class="in">
        <label class="f" for="aport${i}">منفذ الاتصال</label><input type="text" id="aport${i}" class="ltr" value="5555" autocomplete="off">
        ${INFO.android ? '<p class="sub">من الجوال يكون الاتصال على المنفذ 5555 بدون رمز إقران.</p>' : `
        <div class="grid2"><div><label class="f" for="ppport${i}">منفذ الإقران</label><input type="text" id="ppport${i}" class="ltr" autocomplete="off"></div>
        <div><label class="f" for="pcode${i}">رمز الإقران</label><input type="text" id="pcode${i}" class="ltr" autocomplete="off"></div></div>
        <button class="btn sm" onclick="androidPair(${i})">اقترن</button>`}</div></details>
      <div class="pill warn">«شاشتي» لشاشات أندرويد بتجي في تحديث قادم.</div>
      <pre class="log" id="log${i}" hidden></pre>
    </div></article>`;
}
function cardOther(d) {
  return `<article class="tv other">${head(d)}<div class="tv-body">
      <div class="pill warn">نظام هذي الشاشة (${esc(d.os)}) مغلق، وما يسمح بتثبيت تطبيقات أو «شاشتي».</div>
      <button class="btn" onclick="show('v-ads')">طريقة منع الإعلانات عليها</button></div></article>`;
}
function render() {
  $('tvs').innerHTML = DEVS.length ? DEVS.map((d, i) =>
    d.brand === 'android' ? cardAndroid(d, i) : d.brand === 'other' ? cardOther(d) : cardWebosTizen(d, i)).join('')
    : '<div class="card empty">ما فيه شاشات بعد. اضغط «ابحث عن الشاشات».</div>';
  const opts = DEVS.map((d, i) => ({d, i})).filter(x => x.d.brand === 'lg' || (x.d.brand === 'samsung' && !INFO.android));
  $('targetTv').innerHTML = opts.length ? opts.map(x => `<option value="${x.i}">${esc(x.d.name)} — ${esc(x.d.ip)}</option>`).join('')
    : '<option value="">ابحث عن الشاشات أول (تبويب الشاشات)</option>';
}
function logBox(id) { const el = $(id); el.hidden = false; return el; }
async function install(i) {
  const d = DEVS[i], f = $('file' + i).files[0], log = logBox('log' + i);
  if (!f) { log.textContent = 'اختر ملف التطبيق أولاً.'; return; }
  const headers = {'X-Brand': d.brand, 'X-Ip': d.ip, 'X-Filename': encodeURIComponent(f.name)};
  if (d.brand === 'android') headers['X-Port'] = $('aport' + i).value.trim() || '5555';
  if (d.brand === 'lg') {
    headers['X-Mode'] = $('mode' + i).value; headers['X-Secret'] = $('secret' + i).value.trim();
    if (headers['X-Mode'] === 'dev' && !headers['X-Secret']) { log.textContent = 'اكتب الـ Passphrase من تطبيق Developer Mode.'; return; }
  }
  $('inst' + i).disabled = true; log.textContent = 'أرفع ' + f.name + ' وأثبّته… لا تطفي الشاشة.';
  const j = await api('/api/install', {method: 'POST', headers, body: f});
  $('inst' + i).disabled = false;
  log.textContent = j.error || ((j.log || []).join('\n') + '\n\n' + (j.ok ? 'تم التثبيت.' : 'ما اكتمل التثبيت. شوف التفاصيل فوق.'));
  if (j.ok) toast('تم تثبيت التطبيق');
}
async function installScreen(i, out) {
  const d = DEVS[i], log = out || logBox('log' + i);
  log.hidden = false;
  const body = {brand: d.brand, ip: d.ip, country: screenCountry(), city: screenCity(), profile: ($('tzProfile') || {}).value || '', features: feats()};
  if (d.brand === 'lg') {
    body.mode = $('mode' + i) ? $('mode' + i).value : 'dev';
    body.secret = (($('secret' + i) || {}).value || (ST.secrets || {})[d.ip] || '').trim();
    if (body.mode === 'dev' && !body.secret) {
      log.textContent = 'اكتب الـ Passphrase في بطاقة الشاشة (تبويب الشاشات) ثم ارجع.';
      toast('اكتب الـ Passphrase أول'); show('v-tvs'); const s = $('secret' + i); if (s) s.focus(); return;
    }
  }
  const btns = [$('scr' + i), $('installBtn')]; btns.forEach(b => b && (b.disabled = true));
  log.textContent = 'أجهّز «شاشتي» لمدينة ' + $('screenCity').selectedOptions[0].textContent + ' وأثبّتها… قد يأخذ دقيقة.';
  const j = await api('/api/screen/install', {method: 'POST', body: JSON.stringify(body)});
  btns.forEach(b => b && (b.disabled = false));
  log.textContent = j.error || ((j.log || []).join('\n') + '\n\n' + (j.ok ? 'تم تثبيت «شاشتي». تلقاها في قائمة التطبيقات.' : 'ما اكتمل التثبيت. شوف التفاصيل فوق.'));
  if (j.ok) toast('تم تثبيت شاشتي ✓');
}
function installFromScreen() {
  const v = $('targetTv').value;
  if (v === '') { toast('ابحث عن الشاشات أول'); show('v-tvs'); return; }
  installScreen(+v, $('screenLog'));
}
async function androidPair(i) {
  const d = DEVS[i], log = logBox('log' + i);
  const port = $('ppport' + i).value.trim(), code = $('pcode' + i).value.trim();
  if (!port || !code) { log.textContent = 'اكتب منفذ ورمز الإقران من شاشة «إقران جهاز برمز».'; return; }
  log.textContent = 'أقترن بالشاشة…';
  const j = await api('/api/android/pair', {method: 'POST', body: JSON.stringify({ip: d.ip, port, code})});
  log.textContent = j.error || ((j.log || []).join('\n') + '\n\n' + (j.ok ? 'تم الإقران.' : 'ما نجح الإقران. تأكد من الرمز والمنفذ.'));
}
async function androidAdblock(i, enable) {
  const d = DEVS[i], log = logBox('log' + i);
  log.textContent = enable ? 'أفعّل حجب الإعلانات… وافق على رسالة السماح في الشاشة إن ظهرت.' : 'ألغي الحجب…';
  const j = await api('/api/android/adblock', {method: 'POST', body: JSON.stringify({ip: d.ip, port: $('aport' + i).value.trim(), enable})});
  log.textContent = j.error || ((j.log || []).join('\n') + '\n\n' + (j.ok ? (enable ? 'تم. الحجب صار دائم على هذي الشاشة.' : 'تم إلغاء الحجب.') : 'ما اكتمل.'));
  if (j.ok) toast(enable ? 'الحجب صار دائم ✓' : 'تم إلغاء الحجب');
}

/* المعاينة والتحميل */
async function ensurePhotos() {
  const c = await api('/api/photos');
  if (c.count) return;
  toast('أحمّل صور الخلفية لأول مرة…');
  await api('/api/photos/stock', {method: 'POST'});
}
async function previewScreen() {
  const w = INFO.android ? null : window.open('', '_blank');
  if (w) w.document.write('<meta charset="utf-8"><body style="background:#0b1222;color:#eee;font-family:sans-serif;display:grid;place-items:center;height:90vh" dir="rtl"><h2>أجهّز المعاينة…</h2></body>');
  await ensurePhotos();
  const url = '/screen-preview?k=' + encodeURIComponent(K) + '&country=' + screenCountry() + '&city=' + screenCity() +
              '&f=' + encodeURIComponent(JSON.stringify(feats()));
  if (w) w.location = url; else location.href = url;
}
async function downloadScreen(brand) {
  const r = await fetch('/api/screen/package', {method: 'POST', headers: {'X-Key': K},
    body: JSON.stringify({brand, country: screenCountry(), city: screenCity(), features: feats()})});
  if (!r.ok) { toast('ما قدرت أجهّز الملف'); return; }
  const blob = await r.blob(), a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = brand === 'lg' ? 'Shashati.ipk' : 'Shashati-tizen-unsigned.zip';
  document.body.appendChild(a); a.click(); a.remove();
}

/* الخلفيات */
async function loadPhotos() {
  const j = await api('/api/photos/list');
  const list = j.photos || [];
  $('photoNote').textContent = list.length ? 'عندك ' + list.length + ' صورة. تقدر تخفي أي وحدة من إعدادات الشاشة، أو تحذفها هنا.'
    : 'ما فيه صور — بتظهر المناظر المرسومة. اضغط «حمّل صوراً جاهزة».';
  $('photoGrid').innerHTML = list.map(p => `<figure class="ph"><img loading="lazy" src="/screen-photo/${encodeURIComponent(p.name)}?k=${encodeURIComponent(K)}" alt="${esc(p.title || 'صورة')}">
      <figcaption>${esc(p.title || 'صورتك')}</figcaption><button onclick="delPhoto('${esc(p.name)}')" aria-label="احذف الصورة">✕</button></figure>`).join('');
}
async function delPhoto(name) { await api('/api/photos/delete', {method: 'POST', body: JSON.stringify({name})}); loadPhotos(); toast('انحذفت الصورة'); }
function shrink(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const s = Math.min(1, 1920 / img.width, 1200 / img.height), c = document.createElement('canvas');
      c.width = Math.round(img.width * s); c.height = Math.round(img.height * s);
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(img.src);
      c.toBlob(b => b ? resolve(b) : reject(), 'image/jpeg', 0.85);
    };
    img.onerror = reject; img.src = URL.createObjectURL(file);
  });
}
async function addPhotos() {
  const files = [...$('photoFiles').files];
  let done = 0;
  for (const f of files) {
    $('photoNote').innerHTML = '<span class="spin"></span> أجهّز الصورة ' + (done + 1) + ' من ' + files.length + '…';
    try {
      const j = await api('/api/photos/add', {method: 'POST', body: await shrink(f)});
      if (j.error) { toast(j.error); break; }
      done++;
    } catch (e) { toast('تعذّرت قراءة ' + f.name); }
  }
  $('photoFiles').value = ''; loadPhotos(); if (done) toast('أضفت ' + done + ' صورة');
}
async function stockPhotos() {
  $('stockBtn').disabled = true; $('photoNote').innerHTML = '<span class="spin"></span> أحمّل الصور… قد يأخذ دقيقة.';
  const j = await api('/api/photos/stock', {method: 'POST'});
  $('stockBtn').disabled = false; loadPhotos();
  if (j.log) toast(j.log[j.log.length - 1]);
}
async function clearPhotos() {
  if (!confirm('تحذف كل الصور؟')) return;
  await api('/api/photos/clear', {method: 'POST'}); loadPhotos();
}

/* فلتر الكمبيوتر */
function paintDns(j) {
  if (!j || INFO.android) return;
  if (j.error && !('running' in j)) { $('dnsErr').innerHTML = `<div class="pill bad">${esc(j.error)}</div>`; return; }
  DNSRUN = j.running;
  $('dnsDot').classList.toggle('on', j.running);
  $('dnsPill').textContent = j.running ? 'شغّال' : 'متوقف';
  $('dnsBtn').textContent = j.running ? 'أوقف' : 'شغّل';
  $('dnsErr').innerHTML = j.error ? `<div class="pill bad">${esc(j.error)}</div>` : '';
  $('sBlocked').textContent = (j.blocked || 0).toLocaleString('ar');
  $('sFwd').textContent = (j.forwarded || 0).toLocaleString('ar');
  $('sList').textContent = (j.listSize || 0).toLocaleString('ar');
  $('extraNote').textContent = j.extraSize ? 'القائمة الموسّعة فيها ' + j.extraSize.toLocaleString('ar') + ' نطاق.' : 'تستخدم القائمة الأساسية.';
  const r = j.recent || [];
  $('feed').innerHTML = r.length ? r.map(x => `<li><span>${esc(x.name)}</span><span class="sub ltr">${esc(x.t)}</span></li>`).join('') : '<li><span>لا شيء بعد</span></li>';
  if (!dnsFilled) { $('custom').value = (j.custom || []).join('\n'); $('allow').value = (j.allow || []).join('\n'); $('upstream').value = j.upstream || '1.1.1.1'; dnsFilled = true; }
}
async function toggleDns() { $('dnsBtn').disabled = true; paintDns(await api(DNSRUN ? '/api/dns/stop' : '/api/dns/start', {method: 'POST'})); $('dnsBtn').disabled = false; }
async function downloadList() {
  $('dlBtn').disabled = true; $('extraNote').textContent = 'أحمّل القائمة…';
  const j = await api('/api/dns/download', {method: 'POST'});
  $('dlBtn').disabled = false; if (j.error) { $('extraNote').textContent = j.error; return; } paintDns(j);
}
async function saveLists() {
  const lines = id => $(id).value.split('\n').map(s => s.trim()).filter(Boolean);
  paintDns(await api('/api/dns/lists', {method: 'POST', body: JSON.stringify({custom: lines('custom'), allow: lines('allow'), upstream: $('upstream').value})}));
  toast('انحفظت القوائم');
}

restoreFeats();
loadInfo().then(() => {
  scan(false);
  if (!INFO.android) { api('/api/dns').then(paintDns); setInterval(() => api('/api/dns').then(paintDns), 5000); }
  let t = 'v-tvs'; try { t = localStorage.getItem('tvm_tab') || 'v-tvs'; } catch (e) {}
  if ($(t)) show(t);
});
loadReciters();
</script>
</body>
</html>
"""


def main():
    ip = local_ip()
    server = http.server.ThreadingHTTPServer(("0.0.0.0", APP_PORT), Panel)
    pc_url = "http://127.0.0.1:%d/?k=%s" % (APP_PORT, TOKEN)
    phone_url = "http://%s:%d/?k=%s" % (ip, APP_PORT, TOKEN)
    print("=" * 60)
    print(" TV Manager is running")
    print(" PC:    " + pc_url)
    print(" Phone: " + phone_url)
    print(" Close this window to stop.")
    print("=" * 60)
    if paramiko is None:
        print(" [!] paramiko not installed (needed for LG): pip install paramiko")
    threading.Timer(1.0, lambda: webbrowser.open(pc_url)).start()
    threading.Thread(target=lambda: (screen.bukhari_hadiths(), screen.fetch_stock_photos([], only_if_empty=True)),
                     daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        DNS.stop()
        server.server_close()


def start_background():
    """لتطبيق الأندرويد: يشغّل اللوحة داخل الجوال ويرجّع رابطها لعرضه في التطبيق."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", APP_PORT), Panel)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=lambda: (screen.bukhari_hadiths(), screen.fetch_stock_photos([], only_if_empty=True)),
                     daemon=True).start()
    return "http://127.0.0.1:%d/?k=%s" % (APP_PORT, TOKEN)


if __name__ == "__main__":
    main()
