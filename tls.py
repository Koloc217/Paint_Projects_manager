"""Basic HTTPS for the hub: a self-signed certificate and the uvicorn options.

Settings come from .env (all optional):
  HTTPS=1                 serve over HTTPS (default: off, plain HTTP)
  PORT=8000               port for both modes
  TLS_NAMES=a.local,10.0.0.5   extra host names / IPs for the certificate
  SSL_CERTFILE, SSL_KEYFILE    your own certificate (paths relative to the project folder allowed)

Without SSL_CERTFILE / SSL_KEYFILE the hub uses certs/server.crt and certs/server.key and creates them
with the openssl command on first start. A self-signed certificate gives encryption, but browsers warn
about it once per device (see README: "HTTPS").

  python tls.py status          show settings and certificate validity
  python tls.py cert [--force]  create (or re-create) the self-signed certificate
"""
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(BASE_DIR, "certs")
DEFAULT_CERT = os.path.join(CERT_DIR, "server.crt")
DEFAULT_KEY = os.path.join(CERT_DIR, "server.key")
CERT_DAYS = 825          # the longest lifetime that Apple devices accept for a trusted certificate
TRUE_VALUES = {"1", "true", "yes", "on"}
NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$")


class TlsError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _path(value: str, default: str) -> str:
    value = value or default
    return value if os.path.isabs(value) else os.path.join(BASE_DIR, value)


def settings() -> dict:
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    try:
        port = int(_env("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        raise TlsError("PORT in .env must be a number from 1 to 65535")
    custom = bool(_env("SSL_CERTFILE") or _env("SSL_KEYFILE"))
    if custom and not (_env("SSL_CERTFILE") and _env("SSL_KEYFILE")):
        raise TlsError("Set both SSL_CERTFILE and SSL_KEYFILE in .env, or neither")
    return {
        "https": _env("HTTPS").lower() in TRUE_VALUES,
        "port": port,
        "cert": _path(_env("SSL_CERTFILE"), DEFAULT_CERT),
        "key": _path(_env("SSL_KEYFILE"), DEFAULT_KEY),
        "custom": custom,
        "names": [n.strip() for n in _env("TLS_NAMES").split(",") if n.strip()],
    }


def _is_ip(value: str) -> bool:
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(family, value)
            return True
        except OSError:
            pass
    return False


def _local_names(extra) -> list:
    """Names the certificate should cover: localhost, this host's names and its LAN addresses."""
    names = ["localhost", "127.0.0.1"]
    host = socket.gethostname()
    if host:
        names += [host, host + ".local"]
        try:
            names += socket.gethostbyname_ex(host)[2]
        except OSError:
            pass
    try:   # the address of the default route (a UDP "connect" sends no packets)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            names.append(s.getsockname()[0])
    except OSError:
        pass
    result = []
    for n in names + list(extra):
        if n not in result and (_is_ip(n) or NAME_RE.match(n)) and n != "0.0.0.0":
            result.append(n)
    return result


def create_certificate(cert_path: str, key_path: str, extra_names=(), force: bool = False) -> bool:
    """Creates a self-signed certificate with openssl. Returns False if one already exists."""
    if not force and os.path.exists(cert_path) and os.path.exists(key_path):
        return False
    names = _local_names(extra_names)
    san = ",".join(("IP:" if _is_ip(n) else "DNS:") + n for n in names)
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    cmd = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", str(CERT_DAYS),
        "-keyout", key_path, "-out", cert_path, "-subj", "/CN=Project Hub",
        "-addext", "subjectAltName=" + san,
        "-addext", "basicConstraints=critical,CA:FALSE",
        "-addext", "keyUsage=critical,digitalSignature,keyEncipherment",
        "-addext", "extendedKeyUsage=serverAuth",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise TlsError("the openssl command was not found (sudo apt install openssl)")
    except subprocess.CalledProcessError as e:
        raise TlsError("openssl failed: " + (e.stderr or "").strip()[:300])
    os.chmod(key_path, 0o600)
    print("Certificate created for: " + ", ".join(names))
    return True


def days_left(cert_path: str):
    """Days until the certificate expires (negative if it already has), or None if it cannot be read."""
    try:
        out = subprocess.run(["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
                             check=True, capture_output=True, text=True, timeout=20).stdout.strip()
        end = datetime.strptime(out.split("=", 1)[1], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        return (end - datetime.now(timezone.utc)).days
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def uvicorn_options() -> dict:
    """Arguments for uvicorn.run: host, port and, with HTTPS=1, the certificate files."""
    cfg = settings()
    opts = {"host": "0.0.0.0", "port": cfg["port"]}
    if not cfg["https"]:
        return opts
    if cfg["custom"]:
        for label, path in (("SSL_CERTFILE", cfg["cert"]), ("SSL_KEYFILE", cfg["key"])):
            if not os.path.isfile(path):
                raise TlsError(f"{label} points to a missing file: {path}")
    else:
        create_certificate(cfg["cert"], cfg["key"], cfg["names"])
    left = days_left(cfg["cert"])
    if left is not None and left < 0:
        raise TlsError("the certificate has expired; run: python tls.py cert --force")
    if left is not None and left < 30:
        print(f"Warning: the certificate expires in {left} days (python tls.py cert --force)")
    opts.update(ssl_certfile=cfg["cert"], ssl_keyfile=cfg["key"])
    return opts


def _status() -> None:
    cfg = settings()
    print(f"HTTPS: {'on' if cfg['https'] else 'off (set HTTPS=1 in .env)'}   port: {cfg['port']}")
    print(f"certificate: {cfg['cert']}{'' if os.path.exists(cfg['cert']) else '  (missing)'}")
    if os.path.exists(cfg["cert"]):
        left = days_left(cfg["cert"])
        print("valid for: " + (f"{left} more days" if left is not None else "unknown"))


if __name__ == "__main__":
    try:
        command = sys.argv[1] if len(sys.argv) > 1 else "status"
        if command == "status":
            _status()
        elif command == "cert":
            c = settings()
            if c["custom"]:
                raise TlsError("SSL_CERTFILE is set in .env: your own certificate is used, nothing to create")
            if not create_certificate(c["cert"], c["key"], c["names"], force="--force" in sys.argv):
                print("The certificate already exists; use --force to re-create it.")
        else:
            print(__doc__)
            sys.exit(2)
    except TlsError as e:
        print("Error: " + str(e))
        sys.exit(1)
