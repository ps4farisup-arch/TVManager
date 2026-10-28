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

try:
    import paramiko
except ImportError:
    paramiko = None

import tv_screen_app as screen

APP_PORT = 8765
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


def probe_samsung(ip):
    try:
        info = json.loads(http_get("http://%s:8001/api/v2/" % ip, 2))
    except Exception:
        return None
    d = info.get("device") or {}
    if not d and "name" not in info:
        return None
    return {
        "ip": ip,
        "brand": "samsung",
        "name": d.get("name") or info.get("name") or "Samsung TV",
        "model": d.get("modelName", ""),
        "os": d.get("OS", "Tizen"),
        "devMode": str(d.get("developerMode", "0")) == "1",
        "devIP": d.get("developerIP", ""),
    }


def probe_lg(ip, headers=None):
    headers = list(headers or [])
    if not headers:
        headers = ssdp_discover(1.5, target=ip).get(ip, [])
    blob = " ".join(" ".join(h.values()) for h in headers).lower()
    loc = next((h["location"] for h in headers if h.get("location")), None)
    desc = read_description(loc) if loc else {}
    is_lg = ("webos" in blob or "lge-com" in blob
             or "lg electronics" in desc.get("manufacturer", "").lower())
    if not is_lg:
        return None
    return {
        "ip": ip,
        "brand": "lg",
        "name": desc.get("friendlyName") or "LG TV",
        "model": desc.get("modelName") or desc.get("modelNumber", ""),
        "os": "webOS",
        "devMode": port_open(ip, 9922),
        "rooted": port_open(ip, 22),
    }


def scan(deep=False):
    ssdp = ssdp_discover(3.0)
    candidates = set(ssdp.keys())
    if deep:
        net = ipaddress.ip_network(local_ip() + "/24", strict=False)

        def check(host):
            h = str(host)
            if any(port_open(h, p, 0.4) for p in (8001, 3000, 3001)):
                return h
            return None

        with ThreadPoolExecutor(64) as ex:
            for r in ex.map(check, net.hosts()):
                if r:
                    candidates.add(r)

    def identify(ip):
        return probe_samsung(ip) or probe_lg(ip, ssdp.get(ip))

    devices = []
    with ThreadPoolExecutor(16) as ex:
        for d in ex.map(identify, candidates):
            if d:
                devices.append(d)
    devices.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))
    return devices


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


def samsung_screen_install(ip, country, city, profile):
    tz = find_tizen()
    if not tz:
        raise RuntimeError("سامسونج تقبل فقط تطبيقات موقّعة بشهادة خاصة بشاشتك. ثبّت Tizen Studio "
                           "وأنشئ شهادة Samsung من Certificate Manager (مرة وحدة)، ثم أعد تشغيل البرنامج.")
    work = tempfile.mkdtemp(prefix="shashati_")
    try:
        for name, data in screen.tizen_files(country, city).items():
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
        log.insert(0, "تم توقيع «شاشتي» بشهادتك.")
        return ok, log
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- تثبيت على LG
def _lg_ssh(ip, mode, secret):
    if paramiko is None:
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
    try:
        key = paramiko.RSAKey.from_private_key(io.StringIO(pem), password=secret.strip())
    except Exception:
        raise RuntimeError("عبارة المرور (Passphrase) غير صحيحة. انسخها كما تظهر في تطبيق Developer Mode.")
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


def lg_install(ip, data, filename, mode, secret):
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
            return self._send(screen.page(int(q.get("country", ["0"])[0] or 0), int(q.get("city", ["0"])[0] or 0)).encode(), "text/html; charset=utf-8")
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
                "paramiko": paramiko is not None,
                "os": sys.platform,
            })
        if u.path == "/api/dns":
            return self._json(DNS.status())
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
                else:
                    raise ValueError("نوع شاشة غير معروف.")
                return self._json({"ok": ok, "log": log})
            if path == "/api/screen/install":
                req = json.loads(self._body() or b"{}")
                ip = req.get("ip", "")
                ipaddress.ip_address(ip)
                country, city = int(req.get("country", 0)), int(req.get("city", 0))
                if req.get("brand") == "lg":
                    ok, log = lg_install(ip, screen.lg_ipk(country, city), "Shashati.ipk",
                                         req.get("mode", "dev"), req.get("secret", ""))
                elif req.get("brand") == "samsung":
                    ok, log = samsung_screen_install(ip, country, city, req.get("profile", "").strip())
                else:
                    raise ValueError("نوع شاشة غير معروف.")
                return self._json({"ok": ok, "log": log})
            if path == "/api/screen/package":
                req = json.loads(self._body() or b"{}")
                country, city = int(req.get("country", 0)), int(req.get("city", 0))
                if req.get("brand") == "lg":
                    data, name = screen.lg_ipk(country, city), "Shashati.ipk"
                else:
                    data, name = screen.tizen_unsigned_zip(country, city), "Shashati-tizen-unsigned.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition", 'attachment; filename="%s"' % name)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
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
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>مدير الشاشات</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Readex+Pro:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#e9edf1; --panel:#ffffff; --ink:#152231; --muted:#5b6878; --line:#d3dae2;
  --accent:#0a6e8a; --accent-ink:#ffffff; --ok:#17803d; --warn:#a85d00; --bad:#b42318;
  --sam:#1d3fbf; --lg:#b0123f; --screen:#0f1a26; --screen-ink:#cfe8f0;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0c141c; --panel:#132130; --ink:#e4edf5; --muted:#93a3b4; --line:#243446;
  --accent:#4cc3e0; --accent-ink:#04222b; --ok:#5bd38a; --warn:#f3a740; --bad:#ff7b6e;
  --sam:#8aa2ff; --lg:#ff7aa0; --screen:#060b11; --screen-ink:#bfe4ee;
  color-scheme:dark;}}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:16px/1.65 "Readex Pro","Segoe UI",Tahoma,"Noto Sans Arabic",sans-serif}
main{max-width:980px;margin:0 auto;padding:1.25rem 1rem 4rem}
h1{font-size:1.9rem;margin:0;font-weight:700;letter-spacing:-.01em}
h2{font-size:1.25rem;margin:0 0 .25rem;font-weight:700}
p{margin:.25rem 0}
.muted{color:var(--muted)}
.hero{display:flex;gap:1.25rem;align-items:center;justify-content:space-between;flex-wrap:wrap;margin:.5rem 0 1.5rem}
.hero p{max-width:52ch}
.pill{display:inline-flex;align-items:center;gap:.45rem;padding:.3rem .8rem;border-radius:999px;background:var(--panel);border:1px solid var(--line);font-size:.9rem}
.dot{width:.6rem;height:.6rem;border-radius:50%;background:var(--muted)}
.dot.on{background:var(--ok);box-shadow:0 0 0 4px color-mix(in srgb,var(--ok) 25%,transparent)}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:1.25rem;margin-bottom:1.25rem}
.head{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap;margin-bottom:1rem}
button,.btn{font:inherit;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--ink);padding:.5rem 1rem;border-radius:9px}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:500}
button:disabled{opacity:.55;cursor:wait}
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
input,select,textarea{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:.45rem .6rem;max-width:100%}
textarea{width:100%;min-height:7rem;direction:ltr;text-align:left;font-size:.9rem}
.row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}

/* الشاشة: إطار يشبه التلفاز مع شريط بلون الشركة */
.tvs{display:grid;gap:1rem}
.tv{border:1px solid var(--line);border-radius:12px;overflow:hidden;display:grid;grid-template-columns:minmax(150px,210px) 1fr}
.tv .face{background:var(--screen);color:var(--screen-ink);padding:1rem;display:flex;flex-direction:column;justify-content:space-between;border-inline-start:6px solid var(--brand);min-height:150px}
.tv .face b{font-size:1.15rem;color:#fff;line-height:1.3}
.tv .face small{opacity:.8;direction:ltr;text-align:right}
.tv .body{padding:1rem;display:grid;gap:.7rem;align-content:start}
.tv.samsung{--brand:var(--sam)} .tv.lg{--brand:var(--lg)}
.brand{font-weight:700;letter-spacing:.02em}
.state{padding:.5rem .75rem;border-radius:8px;font-size:.92rem;border:1px solid}
.state.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,transparent)}
.state.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,transparent)}
.state.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,transparent)}
details{font-size:.92rem} details summary{cursor:pointer;color:var(--accent)}
details ol{margin:.4rem 0;padding-inline-start:1.3rem}
pre.log{background:var(--screen);color:var(--screen-ink);padding:.7rem;border-radius:8px;white-space:pre-wrap;font-size:.85rem;margin:0;max-height:240px;overflow:auto;direction:rtl}
.empty{padding:2rem 1rem;text-align:center;border:1px dashed var(--line);border-radius:12px}

.stats{display:flex;gap:2rem;flex-wrap:wrap;margin:.5rem 0 1rem}
.stat b{display:block;font-size:1.7rem;line-height:1.1}
.feed{list-style:none;margin:0;padding:0;max-height:220px;overflow:auto;border:1px solid var(--line);border-radius:8px}
.feed li{display:flex;justify-content:space-between;gap:1rem;padding:.35rem .7rem;border-bottom:1px solid var(--line);font-size:.88rem}
.feed li span:first-child{direction:ltr;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.steps{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.steps ol{margin:.3rem 0;padding-inline-start:1.3rem}
.ipbig{font-size:1.4rem;font-weight:700;direction:ltr;display:inline-block}
.phone{display:flex;gap:1.25rem;align-items:center;flex-wrap:wrap}
#qr{background:#fff;padding:8px;border-radius:8px;line-height:0}
.link{direction:ltr;word-break:break-all;font-size:.9rem}
@media (max-width:680px){
  .tv{grid-template-columns:1fr}
  .tv .face{min-height:auto;border-inline-start:0;border-top:6px solid var(--brand)}
  .cols,.steps{grid-template-columns:1fr}
  h1{font-size:1.5rem}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}}
</style>
</head>
<body>
<main>
  <div class="hero">
    <div>
      <h1>مدير الشاشات الذكية</h1>
      <p class="muted">يكتشف شاشات سامسونج و LG المتصلة بنفس الشبكة، يحجب إعلاناتها، يثبّت عليها تطبيقات من خارج المتجر، ويركّب عليها «شاشتي».</p>
    </div>
    <span class="pill"><span class="dot" id="dnsDot"></span><span id="dnsPill">حاجب الإعلانات متوقف</span></span>
  </div>

  <section>
    <div class="head">
      <div>
        <h2>الشاشات في شبكتك</h2>
        <p class="muted" id="scanNote">اضغط «ابحث عن الشاشات». تأكد أن الشاشة شغالة ومتصلة بنفس المودم.</p>
      </div>
      <div class="row">
        <button class="primary" id="scanBtn" onclick="scan(false)">ابحث عن الشاشات</button>
        <button id="deepBtn" onclick="scan(true)">بحث أعمق</button>
      </div>
    </div>
    <div class="tvs" id="tvs"><div class="empty muted">ما فيه شاشات بعد.</div></div>
  </section>

  <section>
    <div class="head">
      <div>
        <h2>شاشتي: واجهة إسلامية بمناظر سعودية</h2>
        <p class="muted">ساعة، تاريخ هجري، مواقيت الصلاة لكل الدول الإسلامية بطريقة حساب كل دولة، الطقس، وآية اليوم. الخلفية مناظر سعودية من التراث ورؤية ٢٠٣٠ تتبدّل كل ٢٠ دقيقة وتتلوّن مع أوقات الصلاة.</p>
      </div>
      <div class="row">
        <button onclick="previewScreen()">جرّبها على الكمبيوتر</button>
      </div>
    </div>
    <div class="row" style="margin-bottom:.8rem">
      <label>الدولة <select id="screenCountry" onchange="fillCities()"></select></label>
      <label>المدينة <select id="screenCity"></select></label>
      <label>ملف شهادة سامسونج <input id="tzProfile" placeholder="اسم الـ Profile" style="direction:ltr;width:11rem"></label>
    </div>
    <p class="muted" style="font-size:.92rem">تثبّتها من زر «ثبّت شاشتي» في بطاقة كل شاشة فوق. تفتحها من قائمة التطبيقات، وتغيّر المدينة من الشاشة نفسها بزر OK.</p>
    <details style="margin-top:.6rem">
      <summary>عشان تفتح تلقائياً مع تشغيل الشاشة</summary>
      <p><b class="brand" style="color:var(--sam)">سامسونج:</b> الإعدادات ← عام ← ميزات ذكية ← فعّل «تشغيل آخر تطبيق تلقائياً» (Autorun Last App)، وخلّ «شاشتي» آخر تطبيق تفتحه قبل ما تطفي.</p>
      <p><b class="brand" style="color:var(--lg)">LG:</b> ما فيها خيار رسمي لهذا. ثبّت «شاشتي» في أول شريط التطبيقات عشان توصل لها بضغطة.</p>
      <p class="muted">ملاحظة: نظاما سامسونج و LG ما يسمحان باستبدال الواجهة الرئيسية أو شاشة التوقف بتطبيق خارجي، لذلك «شاشتي» تطبيق تفتحه، مو بديل للواجهة.</p>
    </details>
    <div class="row" style="margin-top:.8rem">
      <button onclick="downloadScreen('lg')">حمّل ملف LG (‎.ipk)</button>
      <button onclick="downloadScreen('samsung')">حمّل ملفات سامسونج (غير موقّعة)</button>
    </div>
  </section>

  <section>
    <div class="head">
      <div>
        <h2>حجب الإعلانات</h2>
        <p class="muted">هذا الكمبيوتر يصير «فلتر» يمنع وصول الإعلانات للشاشة. لازم يبقى الكمبيوتر شغال.</p>
      </div>
      <div class="row">
        <button class="primary" id="dnsBtn" onclick="toggleDns()">شغّل حاجب الإعلانات</button>
      </div>
    </div>
    <div id="dnsErr"></div>
    <div class="stats">
      <div class="stat"><b id="sBlocked">0</b><span class="muted">طلب إعلان محجوب</span></div>
      <div class="stat"><b id="sFwd">0</b><span class="muted">طلب سليم مرّ</span></div>
      <div class="stat"><b id="sList">0</b><span class="muted">نطاق في القائمة</span></div>
    </div>

    <h2 style="font-size:1.05rem">اربط الشاشة بالحاجب</h2>
    <p>في إعدادات الشبكة بالشاشة، غيّر الـ DNS إلى: <span class="ipbig" id="pcIp">…</span></p>
    <div class="steps">
      <div><b class="brand" style="color:var(--sam)">سامسونج</b>
        <ol><li>الإعدادات ← عام ← الشبكة ← حالة الشبكة</li><li>إعدادات IP ← إعداد DNS ← إدخال يدوي</li><li>اكتب الرقم أعلاه واحفظ</li></ol></div>
      <div><b class="brand" style="color:var(--lg)">LG</b>
        <ol><li>الإعدادات ← الشبكة ← Wi‑Fi (أو سلكي)</li><li>الإعدادات المتقدمة ← تعديل ← ألغِ «تلقائي»</li><li>اكتب الرقم أعلاه في خانة DNS واحفظ</li></ol></div>
    </div>
    <p class="muted" style="font-size:.9rem">إذا طفّيت الكمبيوتر، رجّع الـ DNS في الشاشة إلى «تلقائي» وإلا ينقطع عنها الإنترنت. إعلانات يوتيوب ما تنحجب بهذي الطريقة لأنها تجي من نفس سيرفرات الفيديو؛ لها استخدم TizenTube (سامسونج) أو YouTube AdFree (LG) عن طريق التثبيت فوق.</p>

    <h2 style="font-size:1.05rem;margin-top:1rem">آخر الإعلانات المحجوبة</h2>
    <ul class="feed" id="feed"><li class="muted"><span>لا شيء بعد</span></li></ul>

    <details style="margin-top:1rem">
      <summary>القوائم والإعدادات المتقدمة</summary>
      <div class="row" style="margin:.8rem 0">
        <button id="dlBtn" onclick="downloadList()">حدّث القائمة الموسّعة من الإنترنت</button>
        <span class="muted" id="extraNote"></span>
      </div>
      <div class="cols">
        <label>نطاقات إضافية للحجب (سطر لكل نطاق)<textarea id="custom"></textarea></label>
        <label>استثناءات — إذا تعطّلت خدمة أضف نطاقها هنا<textarea id="allow"></textarea></label>
      </div>
      <div class="row" style="margin-top:.6rem">
        <label>DNS الأساسي للطلبات السليمة <input id="upstream" style="direction:ltr;width:9rem"></label>
        <button onclick="saveLists()">احفظ القوائم</button>
      </div>
    </details>
  </section>

  <section>
    <h2>افتح اللوحة من الجوال</h2>
    <div class="phone">
      <div id="qr"></div>
      <div>
        <p>صوّر الرمز بكاميرا الجوال، أو افتح هذا الرابط وهو متصل بنفس الشبكة:</p>
        <p class="link" id="phoneUrl">…</p>
        <p class="muted" style="font-size:.9rem">لا تشارك الرابط؛ فيه مفتاح يسمح بالتحكم في الشاشات.</p>
      </div>
    </div>
  </section>
</main>

<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
const K = new URLSearchParams(location.search).get('k');
let INFO = {}, DEVS = [], dnsFilled = false, DNSRUN = false;

async function api(path, opts = {}) {
  opts.headers = Object.assign({'X-Key': K}, opts.headers || {});
  try {
    const r = await fetch(path, opts);
    const j = await r.json();
    if (!r.ok && !j.error) j.error = 'خطأ ' + r.status;
    return j;
  } catch (e) { return {error: 'انقطع الاتصال بالبرنامج على الكمبيوتر.'}; }
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const $ = id => document.getElementById(id);

async function loadInfo() {
  INFO = await api('/api/info');
  $('pcIp').textContent = INFO.ip || '—';
  $('phoneUrl').textContent = INFO.phoneUrl || '';
  $('screenCountry').innerHTML = (INFO.countries || []).map((c, i) => `<option value="${i}">${esc(c[0])}</option>`).join('');
  fillCities();
  try { new QRCode($('qr'), {text: INFO.phoneUrl, width: 140, height: 140}); }
  catch (e) { $('qr').style.display = 'none'; }
}

async function scan(deep) {
  $('scanBtn').disabled = $('deepBtn').disabled = true;
  $('scanNote').textContent = deep ? 'أفحص كل أجهزة الشبكة… قد يأخذ حتى دقيقة.' : 'أبحث عن الشاشات…';
  const j = await api('/api/scan', {method: 'POST', body: JSON.stringify({deep})});
  $('scanBtn').disabled = $('deepBtn').disabled = false;
  if (j.error) { $('scanNote').textContent = j.error; return; }
  DEVS = j.devices || [];
  $('scanNote').textContent = DEVS.length
    ? 'وجدت ' + DEVS.length + (DEVS.length === 1 ? ' شاشة.' : ' شاشات.')
    : 'ما وجدت شاشات. تأكد أن الشاشة شغالة (مو وضع الاستعداد) وعلى نفس الشبكة، ثم جرّب «بحث أعمق».';
  render();
}

function samsungState(d) {
  if (!d.devMode) return `<div class="state bad">وضع المطور غير مفعّل — لازم تفعّله قبل تثبيت أي تطبيق.</div>
    <details open><summary>طريقة التفعيل</summary><ol>
    <li>افتح «التطبيقات» (Apps) في الشاشة.</li>
    <li>اضغط بالريموت الأرقام <b>12345</b> (أو من لوحة الأرقام على الشاشة).</li>
    <li>فعّل Developer Mode واكتب هذا الرقم: <b dir="ltr">${esc(INFO.ip)}</b></li>
    <li>أطفئ الشاشة وشغّلها، ثم اضغط «ابحث عن الشاشات» مرة ثانية.</li></ol></details>`;
  if (d.devIP && d.devIP !== INFO.ip) return `<div class="state warn">وضع المطور مفعّل لكنه مربوط بجهاز آخر (<span dir="ltr">${esc(d.devIP)}</span>). غيّره إلى <b dir="ltr">${esc(INFO.ip)}</b> بنفس طريقة 12345.</div>`;
  return `<div class="state ok">وضع المطور مفعّل ومربوط بهذا الكمبيوتر.</div>`;
}

function lgState(d) {
  if (d.devMode) return `<div class="state ok">تطبيق Developer Mode شغّال في الشاشة.</div>`;
  if (d.rooted) return `<div class="state ok">الشاشة فيها روت (Homebrew Channel) — اختر «روت» تحت.</div>`;
  return `<div class="state warn">ما لقيت وضع المطور شغّال.</div>
    <details open><summary>طريقة التفعيل</summary><ol>
    <li>من متجر LG ثبّت تطبيق <b>Developer Mode</b>.</li>
    <li>سجّل دخول بحساب مطور LG (مجاني من موقع webostv.developer.lge.com).</li>
    <li>فعّل Dev Mode Status و Key Server، والشاشة بتعيد التشغيل.</li>
    <li>انسخ «Passphrase» الظاهرة في التطبيق إلى الخانة تحت.</li></ol>
    <p class="muted">وضع المطور ينتهي بعد مدة؛ افتح التطبيق واضغط Extend لتمديده.</p></details>`;
}

function render() {
  if (!DEVS.length) { $('tvs').innerHTML = '<div class="empty muted">ما فيه شاشات بعد.</div>'; return; }
  $('tvs').innerHTML = DEVS.map((d, i) => {
    const sam = d.brand === 'samsung';
    const needs = sam
      ? (INFO.sdb ? '' : '<div class="state warn">للتثبيت على سامسونج لازم Tizen Studio (أداة sdb) على هذا الكمبيوتر.</div>')
      : (INFO.paramiko ? '' : '<div class="state warn">للتثبيت على LG نفّذ على الكمبيوتر: <span dir="ltr">pip install paramiko</span> ثم أعد تشغيل البرنامج.</div>');
    const lgFields = sam ? '' : `<div class="row">
        <select id="mode${i}" aria-label="طريقة الاتصال"><option value="dev">وضع المطور</option><option value="root"${d.rooted && !d.devMode ? ' selected' : ''}>روت</option></select>
        <input id="secret${i}" placeholder="Passphrase أو كلمة مرور الروت" style="direction:ltr" autocomplete="off">
      </div>`;
    return `<article class="tv ${d.brand}">
      <div class="face">
        <div><span class="brand">${sam ? 'Samsung' : 'LG'}</span><br><b>${esc(d.name)}</b></div>
        <small>${esc(d.model)}<br>${esc(d.ip)} · ${esc(d.os)}</small>
      </div>
      <div class="body">
        ${sam ? samsungState(d) : lgState(d)}
        ${needs}
        ${lgFields}
        <div class="row">
          <input type="file" id="file${i}" accept="${sam ? '.wgt,.tpk' : '.ipk'}" aria-label="ملف التطبيق">
          <button class="primary" id="inst${i}" onclick="install(${i})">ثبّت التطبيق</button>
        </div>
        <div class="row"><button id="scr${i}" onclick="installScreen(${i})">ثبّت شاشتي</button>${sam && !INFO.tizen ? '<span class="muted" style="font-size:.88rem">يحتاج Tizen Studio وشهادة سامسونج</span>' : ''}</div>
        <p class="muted" style="font-size:.88rem">${sam ? 'ملفات ‎.wgt — مثل TizenBrew (ومعه TizenTube ليوتيوب بدون إعلانات).' : 'ملفات ‎.ipk — مثل Homebrew Channel أو YouTube AdFree من مشروع webosbrew.'}</p>
        <pre class="log" id="log${i}" hidden></pre>
      </div>
    </article>`;
  }).join('');
}

async function install(i) {
  const d = DEVS[i], f = $('file' + i).files[0], log = $('log' + i);
  log.hidden = false;
  if (!f) { log.textContent = 'اختر ملف التطبيق أولاً.'; return; }
  const headers = {'X-Brand': d.brand, 'X-Ip': d.ip, 'X-Filename': encodeURIComponent(f.name)};
  if (d.brand === 'lg') {
    headers['X-Mode'] = $('mode' + i).value;
    headers['X-Secret'] = $('secret' + i).value.trim();
    if (headers['X-Mode'] === 'dev' && !headers['X-Secret']) { log.textContent = 'اكتب الـ Passphrase من تطبيق Developer Mode.'; return; }
  }
  $('inst' + i).disabled = true;
  log.textContent = 'أرفع ' + f.name + ' وأثبّته… لا تطفي الشاشة.';
  const j = await api('/api/install', {method: 'POST', headers, body: f});
  $('inst' + i).disabled = false;
  if (j.error) { log.textContent = j.error; return; }
  log.textContent = (j.log || []).join('\n') + '\n\n' + (j.ok ? 'تم التثبيت. تلقاه في قائمة التطبيقات بالشاشة.' : 'ما اكتمل التثبيت. شوف التفاصيل فوق.');
}

function screenCountry() { return +($('screenCountry').value || 0); }
function screenCity() { return +($('screenCity').value || 0); }
function fillCities() {
  const c = (INFO.countries || [])[screenCountry()];
  $('screenCity').innerHTML = c ? c[1].map((n, i) => `<option value="${i}">${esc(n)}</option>`).join('') : '';
}
function previewScreen() { window.open('/screen-preview?k=' + encodeURIComponent(K) + '&country=' + screenCountry() + '&city=' + screenCity(), '_blank'); }
async function downloadScreen(brand) {
  const r = await fetch('/api/screen/package', {method: 'POST', headers: {'X-Key': K}, body: JSON.stringify({brand, country: screenCountry(), city: screenCity()})});
  if (!r.ok) return;
  const blob = await r.blob(), a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = brand === 'lg' ? 'Shashati.ipk' : 'Shashati-tizen-unsigned.zip';
  document.body.appendChild(a); a.click(); a.remove();
}
async function installScreen(i) {
  const d = DEVS[i], log = $('log' + i);
  log.hidden = false;
  const body = {brand: d.brand, ip: d.ip, country: screenCountry(), city: screenCity(), profile: $('tzProfile').value};
  if (d.brand === 'lg') {
    body.mode = $('mode' + i).value; body.secret = $('secret' + i).value.trim();
    if (body.mode === 'dev' && !body.secret) { log.textContent = 'اكتب الـ Passphrase من تطبيق Developer Mode.'; return; }
  }
  $('scr' + i).disabled = true;
  log.textContent = 'أجهّز «شاشتي» لمدينة ' + $('screenCity').selectedOptions[0].textContent + ' وأثبّتها…';
  const j = await api('/api/screen/install', {method: 'POST', body: JSON.stringify(body)});
  $('scr' + i).disabled = false;
  if (j.error) { log.textContent = j.error; return; }
  log.textContent = (j.log || []).join('\n') + '\n\n' + (j.ok ? 'تم تثبيت «شاشتي». تلقاها في قائمة التطبيقات.' : 'ما اكتمل التثبيت. شوف التفاصيل فوق.');
}

function paintDns(j) {
  if (j.error && !('running' in j)) { $('dnsErr').innerHTML = `<div class="state bad">${esc(j.error)}</div>`; return; }
  DNSRUN = j.running;
  $('dnsDot').classList.toggle('on', j.running);
  $('dnsPill').textContent = j.running ? 'حاجب الإعلانات شغّال' : 'حاجب الإعلانات متوقف';
  $('dnsBtn').textContent = j.running ? 'أوقف حاجب الإعلانات' : 'شغّل حاجب الإعلانات';
  $('dnsErr').innerHTML = j.error ? `<div class="state bad">${esc(j.error)}</div>` : '';
  $('sBlocked').textContent = (j.blocked || 0).toLocaleString('ar');
  $('sFwd').textContent = (j.forwarded || 0).toLocaleString('ar');
  $('sList').textContent = (j.listSize || 0).toLocaleString('ar');
  $('extraNote').textContent = j.extraSize ? 'القائمة الموسّعة فيها ' + j.extraSize.toLocaleString('ar') + ' نطاق.' : 'تستخدم القائمة الأساسية حالياً.';
  const r = j.recent || [];
  $('feed').innerHTML = r.length
    ? r.map(x => `<li><span>${esc(x.name)}</span><span class="muted" dir="ltr">${esc(x.client)} ${esc(x.t)}</span></li>`).join('')
    : '<li class="muted"><span>لا شيء بعد</span></li>';
  if (!dnsFilled) {
    $('custom').value = (j.custom || []).join('\n');
    $('allow').value = (j.allow || []).join('\n');
    $('upstream').value = j.upstream || '1.1.1.1';
    dnsFilled = true;
  }
}

async function toggleDns() {
  $('dnsBtn').disabled = true;
  paintDns(await api(DNSRUN ? '/api/dns/stop' : '/api/dns/start', {method: 'POST'}));
  $('dnsBtn').disabled = false;
}
async function downloadList() {
  $('dlBtn').disabled = true; $('extraNote').textContent = 'أحمّل القائمة…';
  const j = await api('/api/dns/download', {method: 'POST'});
  $('dlBtn').disabled = false;
  if (j.error) { $('extraNote').textContent = j.error; return; }
  paintDns(j);
}
async function saveLists() {
  const lines = id => $(id).value.split('\n').map(s => s.trim()).filter(Boolean);
  paintDns(await api('/api/dns/lists', {method: 'POST', body: JSON.stringify({custom: lines('custom'), allow: lines('allow'), upstream: $('upstream').value})}));
}

loadInfo().then(() => scan(false));
api('/api/dns').then(paintDns);
setInterval(() => api('/api/dns').then(paintDns), 4000);
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
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        DNS.stop()
        server.server_close()


if __name__ == "__main__":
    main()
