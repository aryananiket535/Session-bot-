# ==============================================================================
# AccStore Session Manager — Unified IMAP Proxy Pool & Auto-Failover Engine
# ==============================================================================
# IMPORTANT ARCHITECTURAL DIRECTIVE:
# - Telegram Accounts / Sessions MUST NOT process through proxies (direct VPS connection).
# - This proxy pool is CONFIGURED EXCLUSIVELY FOR IMAP MAIL PROTOCOL.
# - Provides HTTP CONNECT SSL tunneling for IMAP4_SSL (Gmail, Outlook, Yahoo, etc.).
# - Auto-Failover: If a proxy fails during IMAP polling, it immediately switches to another.
# - Password Fallback: If 407 Proxy Auth Required occurs, automatically tries fallback credentials.
# - Multi-User Load Balancing: Distributes 100+ concurrent user mail checks across proxy nodes.
# ==============================================================================

import os
import re
import ssl
import time
import socket
import base64
import imaplib
import logging
import asyncio
import threading
from typing import Optional, Dict, List, Tuple, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    import socks
except ImportError:
    socks = None

logger = logging.getLogger("IMAPProxyManager")


class ProxyNode:
    """Represents a single HTTP proxy endpoint with health metrics, CONNECT tunneling, and failover state."""

    def __init__(
        self,
        host: str,
        port: int,
        user: Optional[str] = None,
        password: Optional[str] = None,
        scheme: str = "http",
        fallback_user: Optional[str] = "versenautobot",
        fallback_password: Optional[str] = "sliamhxywwwsf",
    ):
        self.host = str(host).strip()
        self.port = int(port)
        self.user = str(user).strip() if user else None
        self.password = str(password).strip() if password else None
        self.scheme = scheme.lower()
        self.fallback_user = str(fallback_user).strip() if fallback_user else None
        self.fallback_password = str(fallback_password).strip() if fallback_password else None

        self.is_alive: bool = True
        self.consecutive_fails: int = 0
        self.total_requests: int = 0
        self.total_fails: int = 0
        self.cooldown_until: float = 0.0
        self.active_conns: int = 0
        self.latency_ms: float = 0.0
        self.country: str = ""
        self.country_code: str = ""
        self.last_error: str = ""
        self.last_checked: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.host}:{self.port}"

    def create_tunnel_socket(self, target_host: str, target_port: int = 993, timeout: float = 15.0) -> socket.socket:
        """
        Establish a raw TCP connection through this HTTP proxy using the HTTP CONNECT method.
        Returns a plain TCP socket with the bidirectional tunnel established, ready for SSL wrapping.
        """
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect((self.host, self.port))
            auth_str = f"{self.user}:{self.password}" if (self.user and self.password) else ""
            headers = [
                f"CONNECT {target_host}:{target_port} HTTP/1.1",
                f"Host: {target_host}:{target_port}",
            ]
            if auth_str:
                b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
                headers.append(f"Proxy-Authorization: Basic {b64}")
            headers.append("\r\n")

            s.sendall("\r\n".join(headers).encode("utf-8"))

            # Read HTTP response until headers complete
            resp = b""
            while b"\r\n\r\n" not in resp:
                chunk = s.recv(1024)
                if not chunk:
                    raise ConnectionError(f"Proxy {self.key} closed connection before tunnel established")
                resp += chunk

            status_line = resp.split(b"\r\n")[0].decode("utf-8", errors="replace")
            if " 200 " not in status_line and not status_line.endswith(" 200"):
                if "407" in status_line:
                    # Try fallback credentials immediately
                    if self.swap_to_fallback():
                        s.close()
                        return self.create_tunnel_socket(target_host, target_port, timeout=timeout)
                raise ConnectionError(f"Proxy {self.key} CONNECT tunnel failed: {status_line}")

            self.mark_success()
            return s
        except Exception as e:
            s.close()
            self.mark_failure(str(e))
            raise

    def mark_failure(self, error_msg: Optional[str] = None, cooldown_secs: int = 60):
        """Mark proxy as failed and place in temporary cooldown."""
        self.consecutive_fails += 1
        self.total_fails += 1
        self.last_error = str(error_msg or "Unknown error")
        self.is_alive = False
        actual_cooldown = min(cooldown_secs * (2 ** (self.consecutive_fails - 1)), 300)
        self.cooldown_until = time.time() + actual_cooldown
        logger.warning(
            f"[IMAP_PROXY] Node {self.key} failed ({self.last_error}). "
            f"Fails: {self.consecutive_fails}. Cooldown: {actual_cooldown}s"
        )

    def mark_success(self, latency_ms: Optional[float] = None):
        """Reset failures and restore alive status."""
        self.consecutive_fails = 0
        self.is_alive = True
        self.cooldown_until = 0.0
        self.total_requests += 1
        if latency_ms is not None:
            self.latency_ms = round(latency_ms, 1)

    def swap_to_fallback(self) -> bool:
        """Swap to fallback credentials if current credentials differ."""
        if not self.fallback_user or not self.fallback_password:
            return False
        if self.user == self.fallback_user and self.password == self.fallback_password:
            return False
        old_user = self.user
        self.user = self.fallback_user
        self.password = self.fallback_password
        logger.info(f"[IMAP_PROXY] Swapped fallback credentials for {self.key}: {old_user} -> {self.user}")
        return True

    def matches(self, spec: Any) -> bool:
        """Check if spec matches this node."""
        if isinstance(spec, ProxyNode):
            return self.key == spec.key
        if isinstance(spec, dict):
            h = spec.get("hostname") or spec.get("host")
            p = spec.get("port")
            return self.host == str(h) and self.port == int(p)
        if isinstance(spec, tuple) and len(spec) >= 3:
            return self.host == str(spec[1]) and self.port == int(spec[2])
        if isinstance(spec, str):
            clean = spec.replace("http://", "").replace("https://", "").replace("socks5://", "")
            if "@" in clean:
                clean = clean.split("@")[-1]
            return clean.startswith(self.key)
        return False

    def to_pyro_dict(self) -> Dict[str, Any]:
        """Pyrogram format representation for compatibility."""
        p = {"scheme": self.scheme, "hostname": self.host, "port": self.port}
        if self.user:
            p["username"] = self.user
        if self.password:
            p["password"] = self.password
        return p

    def to_tele_tuple(self) -> Optional[Tuple]:
        """Telethon format representation for compatibility."""
        if not socks:
            return None
        ptype = socks.HTTP if self.scheme == "http" else socks.SOCKS5
        return (ptype, self.host, self.port, True, self.user, self.password)

    def __repr__(self):
        status = "ALIVE" if (self.is_alive and time.time() >= self.cooldown_until) else "COOLING"
        return f"<ProxyNode {self.key} [{status}] cc={self.country_code} lat={self.latency_ms}ms fails={self.consecutive_fails}>"


class ProxiedIMAP4_SSL(imaplib.IMAP4_SSL):
    """
    Subclass of imaplib.IMAP4_SSL that connects through an HTTP CONNECT proxy tunnel.
    Fully transparent drop-in replacement for standard IMAP4_SSL.
    """

    def __init__(self, host: str, port: int = 993, proxy_node: Optional[ProxyNode] = None, timeout: float = 15.0, **kwargs):
        self.proxy_node = proxy_node
        self._custom_timeout = timeout
        super().__init__(host, port, timeout=timeout, **kwargs)

    def _create_socket(self, timeout):
        """Create socket connected via proxy tunnel and wrap in SSL."""
        if not self.proxy_node:
            return super()._create_socket(timeout)
        
        # Connect raw TCP through HTTP CONNECT proxy tunnel
        raw_sock = self.proxy_node.create_tunnel_socket(self.host, self.port, timeout=self._custom_timeout)
        # Wrap raw tunnel socket in TLS/SSL
        return self.ssl_context.wrap_socket(raw_sock, server_hostname=self.host)


class ProxyPool:
    """
    Unified IMAP Proxy Pool & Failover Manager.
    Merges capabilities of proxy_rotator.py and proxy_manager.py.
    """

    def __init__(self):
        self._nodes: Dict[str, ProxyNode] = {}
        self._dead_proxies: Dict[str, float] = {}
        self._rr_counter: int = 0
        self._lock = threading.RLock()
        self._bg_task: Optional[asyncio.Task] = None
        self.rotation_enabled: bool = True
        self.fallback_user: str = "versenautobot"
        self.fallback_pass: str = "sliamhxywwwsf"
        self.workspace_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
        self.reload_proxies()

    def reload_proxies(self) -> int:
        """Load and parse proxies from proxies.txt, .env, and environment variables."""
        with self._lock:
            self.fallback_user = (os.getenv("FALLBACK_PROXY_USER") or "versenautobot").strip()
            self.fallback_pass = (os.getenv("FALLBACK_PROXY_PASS") or "sliamhxywwwsf").strip()
            rot_env = (os.getenv("PROXY_ROTATION_ENABLED") or "true").strip().lower()
            self.rotation_enabled = rot_env not in ("false", "0", "no")

            # 1. Load from proxies.txt
            proxy_file = (os.getenv("PROXY_FILE") or "proxies.txt").strip()
            candidates = [
                proxy_file,
                os.path.join(self.workspace_dir, "proxies.txt"),
                os.path.join(self.workspace_dir, "alive_proxies.txt"),
                os.path.join(os.getcwd(), "proxies.txt"),
            ]
            for ppath in candidates:
                if os.path.isfile(ppath) and os.path.getsize(ppath) > 0:
                    try:
                        with open(ppath, "r", encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                self._parse_and_add_line(line)
                        break
                    except Exception as e:
                        logger.warning(f"[IMAP_PROXY] Could not read {ppath}: {e}")

            # 2. Load from PROXY_LIST env var
            raw_list = os.getenv("PROXY_LIST") or ""
            if raw_list:
                for item in re.split(r"[,\n\r]+", raw_list):
                    self._parse_and_add_line(item)

            # 3. Load from primary PROXY_HOST / PROXY_PORT
            p_host = (os.getenv("PROXY_HOST") or "").strip()
            p_port = (os.getenv("PROXY_PORT") or "").strip()
            p_user = (os.getenv("PROXY_USER") or "").strip() or None
            p_pass = (os.getenv("PROXY_PASS") or "").strip() or None
            p_type = (os.getenv("PROXY_TYPE") or "http").strip().lower()
            if p_host and p_port and p_type != "none":
                try:
                    self.add_proxy(
                        host=p_host,
                        port=int(p_port),
                        user=p_user,
                        password=p_pass,
                        scheme=p_type if p_type in ("http", "socks5", "socks4") else "http",
                    )
                except Exception:
                    pass

            count = len(self._nodes)
            logger.info(f"[IMAP_PROXY_POOL] Loaded {count} proxies for IMAP. Rotation={self.rotation_enabled}")
            return count

    def _parse_and_add_line(self, line: str):
        """Parse varied proxy line formats."""
        line = line.strip()
        if not line or line.startswith("#"):
            return

        scheme = "http"
        if "://" in line:
            parts = line.split("://", 1)
            scheme = parts[0].lower()
            line = parts[1]

        if "@" in line:
            m = re.match(r"(?:([^:]+):([^@]+)@)?([^:]+):(\d+)", line)
            if m:
                user, passwd, host, port = m.group(1), m.group(2), m.group(3), m.group(4)
            else:
                return
        else:
            parts = line.split(":")
            if len(parts) == 4:
                host, port, user, passwd = parts[0], parts[1], parts[2], parts[3]
            elif len(parts) == 2:
                host, port = parts[0], parts[1]
                user, passwd = self.fallback_user, self.fallback_pass
            else:
                return

        try:
            port_int = int(port)
            self.add_proxy(
                host=host,
                port=port_int,
                user=user or self.fallback_user,
                password=passwd or self.fallback_pass,
                scheme=scheme,
            )
        except Exception:
            pass

    def add_proxy(
        self,
        host: str,
        port: int,
        user: Optional[str] = None,
        password: Optional[str] = None,
        scheme: str = "http",
    ) -> ProxyNode:
        """Add or update a proxy node in the pool."""
        with self._lock:
            key = f"{host.strip()}:{int(port)}"
            if key not in self._nodes:
                node = ProxyNode(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    scheme=scheme,
                    fallback_user=self.fallback_user,
                    fallback_password=self.fallback_pass,
                )
                self._nodes[key] = node
            else:
                node = self._nodes[key]
                if user:
                    node.user = user
                if password:
                    node.password = password
            return node

    @property
    def total_count(self) -> int:
        with self._lock:
            return len(self._nodes)

    @property
    def proxies(self) -> List[Dict[str, Any]]:
        """Return dict representation of all proxies for backward compatibility."""
        with self._lock:
            return [
                {
                    "scheme": n.scheme,
                    "host": n.host,
                    "port": n.port,
                    "username": n.user,
                    "password": n.password,
                    "raw": n.key,
                }
                for n in self._nodes.values()
            ]

    def get_all_nodes(self) -> List[ProxyNode]:
        with self._lock:
            return list(self._nodes.values())

    def get_alive_nodes(self, exclude_keys: Optional[set] = None) -> List[ProxyNode]:
        """Return alive nodes not currently in cooldown."""
        now = time.time()
        with self._lock:
            alive = [
                n for n in self._nodes.values()
                if (n.is_alive and now >= n.cooldown_until)
                and (not exclude_keys or n.key not in exclude_keys)
            ]
            if alive:
                return alive

            # Graceful degradation: if all cooling down, pick earliest
            all_cands = [n for n in self._nodes.values() if not exclude_keys or n.key not in exclude_keys]
            if all_cands:
                earliest = min(all_cands, key=lambda n: n.cooldown_until)
                earliest.is_alive = True
                earliest.cooldown_until = 0.0
                return [earliest]

            return list(self._nodes.values())

    def get_node_for_session(
        self,
        session_id: Optional[str] = None,
        exclude_spec: Any = None,
    ) -> Optional[ProxyNode]:
        """
        Multi-User Load Balancing for 100+ Users:
        - If session_id (e.g. email or user_id) provided: Sticky hash ensures consistent IP per user.
        - If session_id is None: Round-robin across alive nodes.
        - If exclude_spec given: Skips that proxy node (Failover!).
        """
        exclude_keys = set()
        if exclude_spec:
            if isinstance(exclude_spec, (list, tuple, set)):
                for item in exclude_spec:
                    match = self.find_node(item)
                    if match:
                        exclude_keys.add(match.key)
            else:
                match = self.find_node(exclude_spec)
                if match:
                    exclude_keys.add(match.key)

        alive = self.get_alive_nodes(exclude_keys=exclude_keys)
        if not alive:
            return None

        with self._lock:
            if session_id:
                h = abs(hash(str(session_id)))
                return alive[h % len(alive)]
            else:
                self._rr_counter = (self._rr_counter + 1) % len(alive)
                return alive[self._rr_counter]

    def get_next(self) -> Optional[Dict[str, Any]]:
        """Get next proxy dict for backward compatibility with proxy_rotator."""
        node = self.get_node_for_session()
        if not node:
            return None
        return {
            "scheme": node.scheme,
            "host": node.host,
            "port": node.port,
            "username": node.user,
            "password": node.password,
            "raw": node.key,
        }

    def mark_failed(self, proxy_spec: Any):
        """Mark proxy as failed for backward compatibility."""
        self.report_failure(proxy_spec, error="Reported failed via mark_failed")

    def find_node(self, spec: Any) -> Optional[ProxyNode]:
        """Find a node matching a given spec."""
        with self._lock:
            for node in self._nodes.values():
                if node.matches(spec):
                    return node
        return None

    def report_failure(self, spec: Any, error: Optional[Any] = None, cooldown_secs: int = 60):
        """Report a proxy failure and place in cooldown."""
        node = self.find_node(spec)
        if not node:
            return

        err_str = str(error or "").lower()
        if "407" in err_str or "proxy authentication" in err_str:
            if node.swap_to_fallback():
                logger.info(f"[IMAP_PROXY] 407 on {node.key} — Swapped to fallback credentials.")
                node.cooldown_until = time.time() + 5
                return

        node.mark_failure(error_msg=error, cooldown_secs=cooldown_secs)

    def report_success(self, spec: Any, latency_ms: Optional[float] = None):
        """Report a successful request through proxy."""
        node = self.find_node(spec)
        if node:
            node.mark_success(latency_ms=latency_ms)

    def connect_imap(
        self,
        imap_host: str,
        imap_port: int = 993,
        session_key: Optional[str] = None,
        timeout: float = 15.0,
        max_retries: int = 3,
    ) -> imaplib.IMAP4_SSL:
        """
        Connect to an IMAP SSL server through the rotating proxy pool with auto-failover.
        If a proxy is dead or fails, it automatically switches to another alive proxy.
        If the entire proxy pool is empty/disabled, falls back safely to direct connection.
        """
        if not self.rotation_enabled or not self._nodes:
            logger.info(f"[IMAP] Connecting directly to {imap_host}:{imap_port} (Proxy disabled or empty)")
            return imaplib.IMAP4_SSL(imap_host, imap_port, timeout=timeout)

        excluded = set()
        last_error = None

        for attempt in range(max_retries):
            node = self.get_node_for_session(session_id=session_key, exclude_spec=excluded)
            if not node:
                break

            excluded.add(node.key)
            try:
                logger.info(f"[IMAP] Connecting to {imap_host}:{imap_port} via proxy {node.key} (attempt {attempt+1}/{max_retries})...")
                conn = ProxiedIMAP4_SSL(imap_host, imap_port, proxy_node=node, timeout=timeout)
                self.report_success(node)
                return conn
            except Exception as e:
                last_error = e
                logger.warning(f"[IMAP] Proxy {node.key} failed for {imap_host}: {e}. Switching to next proxy...")
                self.report_failure(node, error=str(e))

        # Fallback to direct connection if all proxy attempts fail
        logger.warning(f"[IMAP] All {max_retries} proxy attempts failed for {imap_host} ({last_error}). Falling back to direct connection.")
        return imaplib.IMAP4_SSL(imap_host, imap_port, timeout=timeout)

    async def check_node_async(self, node: ProxyNode, timeout: float = 4.0) -> Tuple[bool, str]:
        """Probe proxy health and country asynchronously."""
        if not aiohttp:
            return True, "aiohttp not installed"

        url = "http://ip-api.com/json"
        proxy_url = f"{node.scheme}://{node.user}:{node.password}@{node.host}:{node.port}" if node.user else f"{node.scheme}://{node.host}:{node.port}"
        t0 = time.time()
        try:
            timeout_cfg = aiohttp.ClientTimeout(total=timeout)
            async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
                async with session.get(url, proxy=proxy_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        node.country = data.get("country", "")
                        node.country_code = data.get("countryCode", "")
                        lat = (time.time() - t0) * 1000
                        node.mark_success(latency_ms=lat)
                        node.last_checked = time.time()
                        return True, f"{node.country} ({node.country_code}) - {int(lat)}ms"
                    elif resp.status == 407:
                        if node.swap_to_fallback():
                            fallback_url = f"{node.scheme}://{node.user}:{node.password}@{node.host}:{node.port}"
                            async with session.get(url, proxy=fallback_url) as resp2:
                                if resp2.status == 200:
                                    data2 = await resp2.json()
                                    node.country = data2.get("country", "")
                                    node.country_code = data2.get("countryCode", "")
                                    lat = (time.time() - t0) * 1000
                                    node.mark_success(latency_ms=lat)
                                    node.last_checked = time.time()
                                    return True, f"{node.country} (Fallback OK) - {int(lat)}ms"
                        node.mark_failure("HTTP 407 Proxy Authentication Required")
                        return False, "407 Proxy Auth Required"
                    else:
                        node.mark_failure(f"HTTP {resp.status}")
                        return False, f"HTTP {resp.status}"
        except Exception as e:
            node.mark_failure(str(e))
            return False, type(e).__name__

    async def health_check_all_async(self) -> Dict[str, Any]:
        """Probe all proxies concurrently and return status summary."""
        with self._lock:
            nodes = list(self._nodes.values())
        if not nodes:
            return {"total": 0, "alive": 0, "dead": 0, "results": []}

        tasks = [self.check_node_async(n) for n in nodes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        alive_count = 0
        dead_count = 0
        summary_list = []
        for n, res in zip(nodes, results):
            if isinstance(res, tuple) and res[0]:
                alive_count += 1
                summary_list.append((n.key, True, res[1]))
            else:
                dead_count += 1
                err_text = str(res[1]) if isinstance(res, tuple) else str(res)
                summary_list.append((n.key, False, err_text))

        return {
            "total": len(nodes),
            "alive": alive_count,
            "dead": dead_count,
            "results": summary_list,
        }

    def start_background_worker(self, interval_secs: int = 180):
        """Start non-blocking periodic health check loop to recover cooling proxies."""
        async def _worker():
            while True:
                try:
                    await asyncio.sleep(interval_secs)
                    now = time.time()
                    with self._lock:
                        targets = [
                            n for n in self._nodes.values()
                            if (not n.is_alive or now - n.last_checked > 600)
                        ]
                    if targets:
                        logger.debug(f"[IMAP_PROXY_WORKER] Checking {len(targets)} cooling proxies...")
                        await asyncio.gather(*[self.check_node_async(n) for n in targets], return_exceptions=True)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"[IMAP_PROXY_WORKER] Exception: {e}")

        try:
            loop = asyncio.get_running_loop()
            if self._bg_task is None or self._bg_task.done():
                self._bg_task = loop.create_task(_worker())
        except RuntimeError:
            pass

    def get_status_text(self) -> str:
        """Format an elegant status report for Telegram admin UI."""
        with self._lock:
            nodes = list(self._nodes.values())

        if not nodes:
            return "⚠️ **IMAP Proxy Pool:** No proxies loaded (`Direct VPS Connection`)"

        now = time.time()
        alive_nodes = [n for n in nodes if n.is_alive and now >= n.cooldown_until]
        cooling_nodes = [n for n in nodes if not (n.is_alive and now >= n.cooldown_until)]

        country_counts: Dict[str, int] = {}
        for n in alive_nodes:
            cc = n.country_code or n.country or "Global"
            country_counts[cc] = country_counts.get(cc, 0) + 1

        dist = " | ".join(f"`{k}`: {v}" for k, v in sorted(country_counts.items())) or "Active"

        status_lines = [
            f"📧 **Dedicated IMAP Proxy Pool (100+ Users Engine)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f" • **Proxy Scope:** `IMAP Only` (Telegram sessions connect direct)",
            f" • **Pool Capacity:** `{len(nodes)}` total proxies configured",
            f" • **Healthy & Active:** `🟢 {len(alive_nodes)}` proxies available",
            f" • **In Cooldown / Dead:** `🔴 {len(cooling_nodes)}` proxies penalized",
            f" • **Distribution:** {dist}",
            f" • **Auto-Failover:** `Active` (Instant failover on dead proxy/error)",
            f" • **Multi-User Routing:** `Enabled` (Sticky hash per user email)",
            f" • **Password Fallback:** `Enabled` (Automatic 407 bypass)",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        return "\n".join(status_lines)

    # Static helpers for backward compatibility
    @staticmethod
    def to_pyro(proxy_dict: dict) -> Optional[dict]:
        if not proxy_dict:
            return None
        return {
            "scheme": proxy_dict.get("scheme", "http"),
            "hostname": proxy_dict.get("host") or proxy_dict.get("hostname"),
            "port": proxy_dict.get("port"),
            "username": proxy_dict.get("username"),
            "password": proxy_dict.get("password"),
        }

    @staticmethod
    def to_tele(proxy_dict: dict) -> Optional[tuple]:
        if not proxy_dict or not socks:
            return None
        scheme = str(proxy_dict.get("scheme", "http")).lower()
        ptype = socks.HTTP if scheme == "http" else socks.SOCKS5
        return (
            ptype,
            proxy_dict.get("host") or proxy_dict.get("hostname"),
            proxy_dict.get("port"),
            True,
            proxy_dict.get("username"),
            proxy_dict.get("password"),
        )


# Global singleton instances
proxy_pool = ProxyPool()
GLOBAL_PROXY_POOL = proxy_pool
ProxyPoolManager = ProxyPool  # Compatibility alias
