# ==============================================================================
# AccStore Session Manager — Mail Engine
# ==============================================================================
# Provides:
#   1. PermutationGenerator — Case + Gmail dot multiplexer
#   2. TempMailClient — Free automated temp mail (Mail.tm + Tempmail.plus)
#   3. IMAPMailClient — Self-mail IMAP auto code fetcher (Gmail, Outlook, etc.)
# ==============================================================================

import asyncio
import imaplib
import email
import email.header
import email.utils
import datetime
import itertools
import heapq
import json
import logging
import os
import random
import re
import string
import time
import urllib.request
import urllib.error

try:
    from proxy_manager import proxy_pool
except ImportError:
    proxy_pool = None

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: EMAIL PERMUTATION GENERATOR
# ─────────────────────────────────────────────────────────────────────────────
# Generates unique case + dot variants of a single email address.
# All variants deliver to the same inbox, but Telegram treats each as unique.

class PermutationGenerator:
    """
    Generates deterministic email permutations using:
    1. Case variations: Each alphabetic character → upper or lower (2^L combos)
    2. Dot variations (Gmail only): Dots between characters (2^(N-1) combos)
    
    Evaluates permutations on-demand (lazy stream) with memoized cache to avoid
    combinatorial explosion and huge memory spikes on long email handles.
    """
    
    # Gmail-like providers where dots are ignored
    GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}
    
    def __init__(self, base_email: str):
        """Initialize with a base email address. Case is preserved as-is."""
        self.base_email = base_email.strip()
        parts = self.base_email.split("@")
        if len(parts) != 2:
            raise ValueError(f"Invalid email format: {base_email}")
        self.local_part = parts[0]
        self.domain = parts[1].lower()  # Domain is always case-insensitive
        self.is_gmail = self.domain in self.GMAIL_DOMAINS
        
        # Calculate total permutations mathematically in O(1)
        clean = self.local_part.replace(".", "") if self.is_gmail else self.local_part
        num_letters = sum(1 for ch in clean if ch.isalpha())
        case_combos = 2 ** num_letters
        if self.is_gmail and len(clean) > 1:
            dot_combos = 2 ** (len(clean) - 1)
        else:
            dot_combos = 1
        self._total_permutations = case_combos * dot_combos
        
        # Lazy generator stream and cache
        self._cache = []
        self._generator = self._build_stream()
        self._exhausted = False
    
    def _build_stream(self):
        """Yield unique permutations deterministically in natural priority order."""
        clean = self.local_part.replace(".", "") if self.is_gmail else self.local_part
        slots = len(clean) - 1
        
        if not self.is_gmail or slots <= 0:
            choices = []
            for ch in clean:
                if ch.isalpha():
                    choices.append([ch.upper(), ch.lower()])
                else:
                    choices.append([ch])
            for combo in itertools.product(*choices):
                yield f"{''.join(combo)}@{self.domain}"
            return

        for k in range(slots + 1):
            if k == 0:
                choices = []
                for ch in clean:
                    if ch.isalpha():
                        choices.append([ch.upper(), ch.lower()])
                    else:
                        choices.append([ch])
                for combo in itertools.product(*choices):
                    yield f"{''.join(combo)}@{self.domain}"
                continue

            generators = []
            for dot_pos in itertools.combinations(range(slots), k):
                dot_set = set(dot_pos)
                choices = []
                for i, ch in enumerate(clean):
                    if ch.isalpha():
                        ch_choices = [ch.upper(), ch.lower()]
                    else:
                        ch_choices = [ch]
                    if i in dot_set:
                        choices.append([c + "." for c in ch_choices])
                    else:
                        choices.append(ch_choices)
                gen = (f"{''.join(combo)}@{self.domain}" for combo in itertools.product(*choices))
                generators.append(gen)
            
            for item in heapq.merge(*generators):
                yield item

    def _ensure_generated(self, count: int):
        """Generate up to `count` items into self._cache."""
        while len(self._cache) < count and not self._exhausted:
            try:
                item = next(self._generator)
                self._cache.append(item)
            except StopIteration:
                self._exhausted = True
                break

    @property
    def total_permutations(self) -> int:
        """Total available unique permutations."""
        return self._total_permutations

    @property
    def _all_permutations(self):
        """Backwards compatibility property for existing tests/references."""
        self._ensure_generated(min(self._total_permutations, 50000))
        return self._cache

    def get_permutation(self, index: int) -> str:
        """Get permutation by index (0-based). Raises IndexError if exhausted."""
        if index < 0 or index >= self._total_permutations:
            raise IndexError(
                f"Permutation index {index} out of range. "
                f"Max available: {self._total_permutations}"
            )
        self._ensure_generated(index + 1)
        if index < len(self._cache):
            return self._cache[index]
        raise IndexError(f"Permutation index {index} out of range.")

    def get_next_unused(self, used: list) -> str:
        """Get next unused permutation. Raises ValueError if all exhausted."""
        used_set = set(used)
        for perm in self._cache:
            if perm not in used_set:
                return perm
        while not self._exhausted:
            try:
                item = next(self._generator)
                self._cache.append(item)
                if item not in used_set:
                    return item
            except StopIteration:
                self._exhausted = True
                break
        raise ValueError(
            f"All {self._total_permutations} permutations exhausted for {self.base_email}"
        )

    def get_batch_unused(self, used: list, count: int) -> list:
        """Get up to `count` unused permutations. Returns list (may be shorter than count)."""
        used_set = set(used)
        result = []
        for perm in self._cache:
            if perm not in used_set:
                result.append(perm)
                if len(result) >= count:
                    return result
        while len(result) < count and not self._exhausted:
            try:
                item = next(self._generator)
                self._cache.append(item)
                if item not in used_set:
                    result.append(item)
            except StopIteration:
                self._exhausted = True
                break
        return result

    def remaining_count(self, used: list) -> int:
        """Count remaining unused permutations."""
        return max(0, self._total_permutations - len(set(used)))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: FREE TEMP MAIL CLIENT (Mail.tm + Tempmail.plus)
# ─────────────────────────────────────────────────────────────────────────────
# Zero-cost, zero-API-key temporary email with automatic inbox polling.

def extract_telegram_code(text: str, expected_length: int = None) -> str:
    """
    Extract Telegram verification code from email text with priority scoring:
    1. Contextual matches: code/código/код/رمز/your code followed by 5-6 digits (e.g. 'Your Code - 123456', 'code: 123456', 'login code 123 456')
    2. Split 6-digit codes: '123 456' or '123-456'
    3. Standalone 5-6 digit numbers matching expected_length
    """
    if not text:
        return None

    cleaned = text.replace("&nbsp;", " ").replace("\u00a0", " ").replace("\u200b", " ")
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)

    # 1. High-confidence contextual patterns
    context_patterns = [
        # Split codes with context (e.g. "Your Code - 123 456" or "login code: 123-456")
        r"(?:your\s+(?:telegram\s+)?code|verification\s+code|login\s+code|telegram\s+code|security\s+code|confirm(?:ation)?\s+code|código|код|رمز\w*)(?:\s+(?:is|was|to|هو))?[\s:=-]{1,10}(\d{3})[\s-](\d{3})\b",
        # 5-6 digit codes with context (e.g. "Your Code - 271513", "Your login code is 849201", "رمزك هو: 16401")
        r"(?:your\s+(?:telegram\s+)?code|verification\s+code|login\s+code|telegram\s+code|security\s+code|confirm(?:ation)?\s+code|código|код|رمز\w*)(?:\s+(?:is|was|to|هو))?[\s:=-]{1,10}(\d{5,6})\b",
        # Suffix context: "123456 is your Telegram code"
        r"\b(\d{3})[\s-](\d{3})\b\s+(?:is\s+your\s+telegram|is\s+your\s+verification|is\s+your\s+login|is\s+your\s+code)",
        r"\b(\d{5,6})\b\s+(?:is\s+your\s+telegram|is\s+your\s+verification|is\s+your\s+login|is\s+your\s+code)",
    ]

    for pat in context_patterns:
        m = re.search(pat, cleaned, re.IGNORECASE)
        if m:
            cand = "".join(m.groups())
            if expected_length is None or len(cand) == expected_length:
                return cand

    # 2. Pattern for split 6-digit codes: "123 456" or "123-456"
    split_match = re.search(r"\b(\d{3})[\s-](\d{3})\b", cleaned)
    if split_match:
        cand = f"{split_match.group(1)}{split_match.group(2)}"
        if expected_length is None or len(cand) == expected_length:
            return cand

    # 3. Fallback: Search all 5-6 digit candidates
    all_codes = re.findall(r"\b(\d{5,6})\b", cleaned)
    if all_codes:
        if expected_length:
            for c in all_codes:
                if len(c) == expected_length:
                    return c
        return all_codes[0]

    return None


class TempMailClient:
    """
    Free temporary email service client.
    Primary: Mail.tm (https://api.mail.tm)
    Fallback: Tempmail.plus (https://tempmail.plus)
    """
    
    MAILTM_BASE = "https://api.mail.tm"
    TEMPMAIL_PLUS_BASE = "https://tempmail.plus/api"
    
    # Regex to extract Telegram's 6-digit verification code
    CODE_REGEX = re.compile(r"\b(\d{5,6})\b")
    
    _mailtm_cooldown_until = 0
    
    def __init__(self):
        self._active_email = None
        self._active_password = None
        self._active_token = None
        self._active_account_id = None
        self._provider = None
    
    def _http_request(self, url, data=None, headers=None, method=None):
        """Synchronous HTTP request helper."""
        hdrs = {"User-Agent": "Mozilla/5.0 (AccStore/1.0)", "Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        
        if data and isinstance(data, dict):
            data = json.dumps(data).encode("utf-8")
        
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            logging.warning(f"[TEMPMAIL] HTTP {e.code} from {url}: {body[:200]}")
            raise
        except Exception as e:
            logging.warning(f"[TEMPMAIL] Request failed {url}: {e}")
            raise
    
    async def _async_http(self, url, data=None, headers=None, method=None):
        """Async wrapper around synchronous HTTP (runs in thread pool)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._http_request, url, data, headers, method)
    
    async def get_available_domains(self) -> list:
        """Fetch available domains from Mail.tm."""
        try:
            result = await self._async_http(f"{self.MAILTM_BASE}/domains")
            members = result.get("hydra:member", [])
            return [d["domain"] for d in members if d.get("isActive")]
        except Exception as e:
            logging.error(f"[TEMPMAIL] Failed to fetch domains: {e}")
            return []
    
    TEMPMAIL_PLUS_DOMAINS = [
        "fexbox.org",
        "fexpost.com",
        "mailbox.in.ua",
        "chitthi.in",
        "rover.info",
        "mailto.plus",
        "fextemp.com",
        "anyalias.com",
        "worldmail.fun",
    ]

    async def create_temp_email(self, exclude_domains: list = None) -> str:
        """Create a new temporary email account, automatically avoiding rejected domains."""
        exclude = set(d.lower().strip() for d in (exclude_domains or []))

        # 1. Try Mail.tm first if its domain is not excluded and not in cooldown
        now = time.time()
        if now >= TempMailClient._mailtm_cooldown_until:
            domains = await self.get_available_domains()
            valid_mailtm = [d for d in domains if d.lower() not in exclude]
            if valid_mailtm:
                domain = random.choice(valid_mailtm)
                username = "tg_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
                email_addr = f"{username}@{domain}"
                password = "AccStore_" + "".join(random.choices(string.ascii_letters + string.digits, k=14))
                
                try:
                    # Create account
                    acc_resp = await self._async_http(
                        f"{self.MAILTM_BASE}/accounts",
                        data={"address": email_addr, "password": password}
                    )
                    
                    # Get auth token
                    token_resp = await self._async_http(
                        f"{self.MAILTM_BASE}/token",
                        data={"address": email_addr, "password": password}
                    )
                    
                    self._active_email = email_addr
                    self._active_password = password
                    self._active_token = token_resp.get("token")
                    self._active_account_id = acc_resp.get("id") if isinstance(acc_resp, dict) else None
                    self._provider = "mailtm"
                    
                    # Persist credentials to disk so they are never lost on restart
                    try:
                        vault_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_mail_vault.json")
                        vault = {}
                        if os.path.exists(vault_path):
                            with open(vault_path, "r", encoding="utf-8") as vf:
                                vault = json.load(vf)
                        vault[email_addr] = {
                            "password": password,
                            "token": self._active_token,
                            "account_id": self._active_account_id,
                            "provider": "mailtm",
                            "created_at": datetime.datetime.now().isoformat()
                        }
                        with open(vault_path, "w", encoding="utf-8") as vf:
                            json.dump(vault, vf, indent=2)
                    except Exception as ve:
                        logging.warning(f"[TEMPMAIL] Failed to persist mail credentials: {ve}")

                    logging.info(f"[TEMPMAIL] Created Mail.tm account: {email_addr}")
                    return email_addr
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        TempMailClient._mailtm_cooldown_until = time.time() + 600
                        logging.warning(f"[TEMPMAIL] Mail.tm 429 rate limit hit. 10m cooldown active, failing over to Tempmail.plus.")
                    else:
                        logging.warning(f"[TEMPMAIL] Mail.tm creation failed: {e}")
                except Exception as e:
                    logging.warning(f"[TEMPMAIL] Mail.tm creation failed: {e}")
        
        # 2. Try Tempmail.plus domains (fexbox.org, fexpost.com, mailbox.in.ua, chitthi.in, rover.info, mailto.plus, etc.)
        valid_plus = [d for d in self.TEMPMAIL_PLUS_DOMAINS if d.lower() not in exclude]
        if not valid_plus:
            # If all known domains were excluded, fallback to any domain
            valid_plus = list(self.TEMPMAIL_PLUS_DOMAINS)

        domain = random.choice(valid_plus)
        username = "tg_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
        email_addr = f"{username}@{domain}"
        self._active_email = email_addr
        self._provider = "tempmail_plus"
        logging.info(f"[TEMPMAIL] Using Tempmail.plus domain @{domain}: {email_addr}")
        return email_addr
    
    async def fetch_verification_code(self, target_email: str = None, timeout: int = 90, poll_interval: float = 3.0, expected_length: int = None) -> str:
        """
        Poll inbox for Telegram verification code.
        Returns the 6-digit code string.
        Raises TimeoutError if code not received within timeout.
        """
        target = target_email or self._active_email
        if not target:
            raise ValueError("No active email to check")
        
        start = time.time()
        
        while time.time() - start < timeout:
            try:
                code = await self._check_inbox(target, expected_length=expected_length)
                if code:
                    logging.info(f"[TEMPMAIL] Got code for {target}: {code}")
                    return code
            except Exception as e:
                logging.warning(f"[TEMPMAIL] Inbox check error: {e}")
            
            await asyncio.sleep(poll_interval)
        
        raise TimeoutError(f"No verification code received within {timeout}s for {target}")
    
    async def _check_inbox(self, target_email: str, expected_length: int = None) -> str:
        """Check inbox for Telegram verification code. Returns code or None."""
        target_domain = target_email.split("@")[-1].lower() if "@" in target_email else ""
        if any(target_domain == d.lower() for d in self.TEMPMAIL_PLUS_DOMAINS):
            return await self._check_tempmail_plus_inbox(target_email, expected_length=expected_length)
        elif self._provider == "mailtm" and self._active_token:
            return await self._check_mailtm_inbox(expected_length=expected_length)
        else:
            return await self._check_tempmail_plus_inbox(target_email, expected_length=expected_length)
    
    async def _check_mailtm_inbox(self, expected_length: int = None) -> str:
        """Check Mail.tm inbox for verification code."""
        try:
            messages = await self._async_http(
                f"{self.MAILTM_BASE}/messages",
                headers={"Authorization": f"Bearer {self._active_token}"}
            )
            
            for msg in messages.get("hydra:member", []):
                msg_id = msg.get("id")
                if not msg_id:
                    continue
                
                # Fetch full message
                full_msg = await self._async_http(
                    f"{self.MAILTM_BASE}/messages/{msg_id}",
                    headers={"Authorization": f"Bearer {self._active_token}"}
                )
                
                # Check subject and body for Telegram code
                subject = full_msg.get("subject", "")
                body = full_msg.get("text", "") or full_msg.get("html", "")
                combined = f"{subject} {body}"
                
                # Look for verification code
                code = extract_telegram_code(combined, expected_length=expected_length)
                if not code:
                    match = self.CODE_REGEX.search(combined)
                    if match:
                        code = match.group(1)
                if code:
                    return code
        except Exception as e:
            logging.warning(f"[TEMPMAIL] Mail.tm inbox check failed: {e}")
        
        return None
    
    async def _check_tempmail_plus_inbox(self, email_addr: str, expected_length: int = None) -> str:
        """Check Tempmail.plus inbox for verification code."""
        try:
            result = await self._async_http(
                f"{self.TEMPMAIL_PLUS_BASE}/mails?email={email_addr}&limit=5"
            )
            
            for mail in result.get("mail_list", []):
                subject = mail.get("subject", "")
                body = mail.get("text", "") or mail.get("body", "")
                combined = f"{subject} {body}"
                code = extract_telegram_code(combined, expected_length=expected_length)
                if code:
                    return code
                
                msg_id = mail.get("mail_id") or mail.get("id")
                if msg_id:
                    try:
                        detail = await self._async_http(
                            f"{self.TEMPMAIL_PLUS_BASE}/mails/{msg_id}?email={email_addr}"
                        )
                        d_text = detail.get("text", "") or detail.get("html", "")
                        d_combined = f"{subject} {d_text}"
                        d_code = extract_telegram_code(d_combined, expected_length=expected_length)
                        if not d_code:
                            match = self.CODE_REGEX.search(d_text)
                            if match:
                                d_code = match.group(1)
                        if d_code:
                            return d_code
                    except Exception:
                        pass
        except Exception as e:
            logging.warning(f"[TEMPMAIL] Tempmail.plus inbox check failed: {e}")
        
        return None
    
    async def cleanup(self):
        """Delete the temporary email account (best-effort)."""
        if self._provider == "mailtm" and self._active_token and getattr(self, "_active_account_id", None):
            try:
                await self._async_http(
                    f"{self.MAILTM_BASE}/accounts/{self._active_account_id}",
                    headers={"Authorization": f"Bearer {self._active_token}"},
                    method="DELETE"
                )
                logging.info(f"[TEMPMAIL] Deleted account: {self._active_email}")
            except Exception:
                pass
        self._active_email = None
        self._active_token = None
        self._active_password = None
        self._active_account_id = None
        self._provider = None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: IMAP MAIL CLIENT (Self-Mail with auto code retrieval)
# ─────────────────────────────────────────────────────────────────────────────
# Connects to user's own mailbox via IMAP SSL to auto-read Telegram codes.

# Auto-detection matrix for popular email providers
IMAP_PROVIDERS = {
    "gmail.com":       {"host": "imap.gmail.com",          "port": 993},
    "googlemail.com":  {"host": "imap.gmail.com",          "port": 993},
    "outlook.com":     {"host": "outlook.office365.com",   "port": 993},
    "hotmail.com":     {"host": "outlook.office365.com",   "port": 993},
    "live.com":        {"host": "outlook.office365.com",   "port": 993},
    "yahoo.com":       {"host": "imap.mail.yahoo.com",     "port": 993},
    "mail.ru":         {"host": "imap.mail.ru",            "port": 993},
    "bk.ru":           {"host": "imap.mail.ru",            "port": 993},
    "inbox.ru":        {"host": "imap.mail.ru",            "port": 993},
    "list.ru":         {"host": "imap.mail.ru",            "port": 993},
    "rambler.ru":      {"host": "imap.rambler.ru",         "port": 993},
    "yandex.ru":       {"host": "imap.yandex.ru",          "port": 993},
    "yandex.com":      {"host": "imap.yandex.com",         "port": 993},
    "icloud.com":      {"host": "imap.mail.me.com",        "port": 993},
    "me.com":          {"host": "imap.mail.me.com",        "port": 993},
    "protonmail.com":  {"host": "127.0.0.1",               "port": 1143},  # ProtonMail Bridge
    "proton.me":       {"host": "127.0.0.1",               "port": 1143},
    "zoho.com":        {"host": "imap.zoho.com",           "port": 993},
    "gmx.com":         {"host": "imap.gmx.com",            "port": 993},
    "aol.com":         {"host": "imap.aol.com",            "port": 993},
}


class IMAPMailClient:
    """
    Self-mail IMAP client that connects to the user's own email inbox
    and auto-reads Telegram verification codes.
    
    Supports auto-detection for Gmail, Outlook, Yahoo, Mail.ru, etc.
    Uses SSL (IMAP4_SSL) by default.
    """
    
    # Regex for 5-6 digit code
    CODE_REGEX = re.compile(r"\b(\d{5,6})\b")
    
    # Telegram sender patterns to look for
    TELEGRAM_SENDERS = [
        "noreply@telegram.org",
        "notifications@telegram.org",
        "verify@telegram.org",
        "login@telegram.org",
        "service@telegram.org",
        "security@telegram.org",
        "support@telegram.org",
        "@telegram.org",
        "@telegram.me",
        "@stel.com",
    ]
    TELEGRAM_SUBJECT_PATTERNS = [
        "verification code",
        "login code",
        "confirm",
        "security code",
        "telegram code",
        "telegram",
        "código",
        "код",
        "passcode",
    ]
    
    def __init__(self, email_address: str, app_password: str, 
                 imap_host: str = None, imap_port: int = None):
        """
        Initialize with email credentials.
        If imap_host/port not provided, auto-detect from domain.
        """
        self.email_address = email_address.strip()
        self.app_password = app_password.strip()
        self.seen_uids = set()
        self.seen_message_ids = self.seen_uids
        
        domain = self.email_address.split("@")[-1].lower()
        
        if imap_host and imap_port:
            self.imap_host = imap_host
            self.imap_port = imap_port
        elif domain in IMAP_PROVIDERS:
            provider = IMAP_PROVIDERS[domain]
            self.imap_host = provider["host"]
            self.imap_port = provider["port"]
        else:
            # Fallback: try imap.<domain>
            self.imap_host = f"imap.{domain}"
            self.imap_port = 993
        
        self._conn = None
    
    def _connect(self) -> imaplib.IMAP4_SSL:
        """Establish IMAP SSL connection directly via VPS first (fast & reliable), falling back to proxy pool if needed."""
        # 1. Connect directly first (fastest, avoids proxy delays or 407 proxy auth blocks)
        try:
            conn = imaplib.IMAP4_SSL(self.imap_host, self.imap_port, timeout=12)
            conn.login(self.email_address, self.app_password)
            return conn
        except imaplib.IMAP4.error as e:
            # Login error from the server directly (wrong app password or IMAP disabled)
            raise ConnectionError(
                f"IMAP login failed for {self.email_address} on {self.imap_host}:{self.imap_port}. "
                f"Error: {e}. "
                f"Make sure you're using an App Password (not your regular password) "
                f"and IMAP access is enabled in your email provider settings."
            )
        except Exception as direct_err:
            logging.info(f"[IMAP] Direct connection failed for {self.email_address} on {self.imap_host}: {direct_err}. Attempting proxy pool failover...")
            if proxy_pool and proxy_pool.total_count > 0:
                try:
                    conn = proxy_pool.connect_imap(
                        self.imap_host,
                        self.imap_port,
                        session_key=self.email_address,
                        timeout=15,
                        max_retries=3,
                    )
                    conn.login(self.email_address, self.app_password)
                    return conn
                except imaplib.IMAP4.error as e:
                    raise ConnectionError(
                        f"IMAP login failed for {self.email_address} on {self.imap_host}:{self.imap_port}. "
                        f"Error: {e}. "
                        f"Make sure you're using an App Password (not your regular password) "
                        f"and IMAP access is enabled in your email provider settings."
                    )
                except Exception as proxy_err:
                    logging.warning(f"[IMAP] Proxy fallback also failed: {proxy_err}")
            raise ConnectionError(
                f"Cannot connect to {self.imap_host}:{self.imap_port}: {direct_err}"
            )
    
    async def test_connection(self) -> bool:
        """Test IMAP connection and login. Returns True on success."""
        loop = asyncio.get_running_loop()
        def _test():
            conn = self._connect()
            conn.select("INBOX")
            conn.close()
            conn.logout()
            return True
        return await loop.run_in_executor(None, _test)

    def get_max_uid_sync(self) -> int:
        """Synchronously get current max UID in INBOX to snapshot pre-existing messages."""
        conn = None
        try:
            conn = self._connect()
            conn.select("INBOX", readonly=True)
            status, data = conn.uid("search", None, "ALL")
            if status == "OK" and data and data[0]:
                uids = [int(u) for u in data[0].split() if u.isdigit()]
                return max(uids) if uids else 0
            return 0
        except Exception as e:
            logging.warning(f"[IMAP] get_max_uid failed: {e}")
            return 0
        finally:
            if conn:
                try:
                    conn.close()
                    conn.logout()
                except Exception:
                    pass

    async def get_max_uid(self) -> int:
        """Asynchronously snapshot current max UID in INBOX."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.get_max_uid_sync)

    def _match_recipient(self, msg, target_permutation: str) -> tuple:
        """
        Precise recipient matching against target email permutation.
        Differentiates case and dot permutations so sibling accounts never steal OTPs.
        """
        target = target_permutation.strip()
        target_lower = target.lower()
        t_local, t_domain = target.split("@") if "@" in target else (target, "")

        to_header = self._decode_header(msg.get("To", "") or "")
        delivered_to = self._decode_header(msg.get("Delivered-To", "") or "")
        x_orig = self._decode_header(msg.get("X-Original-To", "") or "")

        primary_recipients = f"{to_header} {x_orig}".strip()

        if primary_recipients:
            # 1. Exact case-sensitive match (handles case permutations!)
            if target in primary_recipients:
                return True, "exact_match"
            
            # 2. Case-insensitive match on full address
            if target_lower in primary_recipients.lower():
                target_has_upper = any(c.isupper() for c in target)
                prim_has_upper = any(c.isupper() for c in primary_recipients)
                # If both use uppercase letters but exact match failed, they are different permutations
                if target_has_upper and prim_has_upper:
                    return False, "case_permutation_mismatch"
                return True, "case_normalized_match"
            
            # Primary recipient has an explicit different address -> reject
            return False, "recipient_mismatch"

        # Fallback ONLY when To / X-Original-To headers are absent
        if delivered_to:
            d_local, d_domain = delivered_to.lower().split("@") if "@" in delivered_to else (delivered_to, "")
            clean_target = t_local.lower().replace(".", "") + "@" + t_domain.lower()
            clean_deliv = d_local.lower().replace(".", "") + "@" + d_domain.lower()
            if clean_target == clean_deliv:
                return True, "delivered_to_fallback"

        return False, "no_recipient_match"

    async def fetch_verification_code(self, target_email_permutation: str,
                                       timeout: int = 90, poll_interval: float = 2.5,
                                       min_timestamp: float = None,
                                       expected_length: int = None,
                                       min_uid: int = None) -> str:
        """
        Poll IMAP inbox for Telegram verification code.
        
        Args:
            target_email_permutation: Exact case-permuted email the code was sent to.
                                      Used to match the recipient header for disambiguation.
            timeout: Max seconds to wait for code.
            poll_interval: Seconds between inbox checks.
            min_timestamp: Optional minimum timestamp the email must have arrived after.
            expected_length: Optional expected code length (e.g. 5 or 6 digits).
            min_uid: Optional baseline UID; only emails with UID > min_uid are accepted.
        
        Returns:
            The verification code string.
        
        Raises:
            TimeoutError if no code found within timeout.
        """
        start = time.time()
        loop = asyncio.get_running_loop()
        
        while time.time() - start < timeout:
            try:
                code = await loop.run_in_executor(
                    None, self._scan_inbox, target_email_permutation, min_timestamp, expected_length, min_uid
                )
                if code:
                    logging.info(f"[IMAP] Got code for {target_email_permutation}: {code}")
                    return code
            except Exception as e:
                logging.warning(f"[IMAP] Scan error: {e}")
            
            await asyncio.sleep(poll_interval)
        
        raise TimeoutError(
            f"No Telegram verification code found within {timeout}s "
            f"for {target_email_permutation}"
        )
    
    def _scan_inbox(self, target_email_permutation: str, min_timestamp: float = None,
                    expected_length: int = None, min_uid: int = None) -> str:
        """
        Synchronous: connect to IMAP, search recent messages from Telegram,
        match recipient headers against target_email_permutation, extract code.
        Uses RFC 3501 UIDs for strict monotonicity and monotonicity tracking.
        """
        conn = None
        try:
            conn = self._connect()
            conn.select("INBOX")
            
            candidate_uids = []

            # 1. Search by UID if min_uid is provided
            if min_uid is not None and min_uid > 0:
                try:
                    status, uid_data = conn.uid("search", None, f"UID {min_uid + 1}:*")
                    if status == "OK" and uid_data and uid_data[0]:
                        candidate_uids.extend([int(u) for u in uid_data[0].split() if u.isdigit() and int(u) > min_uid])
                except Exception as e:
                    logging.debug(f"[IMAP] UID range search failed: {e}")

            # 2. Also search UNSEEN and recent ALL as fallback or if min_uid is 0/None
            if not candidate_uids:
                try:
                    status_unseen, unseen_data = conn.uid("search", None, "UNSEEN")
                    if status_unseen == "OK" and unseen_data and unseen_data[0]:
                        candidate_uids.extend([int(u) for u in unseen_data[0].split() if u.isdigit()])
                except Exception:
                    pass
                
                try:
                    status_all, all_data = conn.uid("search", None, "ALL")
                    if status_all == "OK" and all_data and all_data[0]:
                        recent = [int(u) for u in all_data[0].split() if u.isdigit()][-25:]
                        candidate_uids.extend(recent)
                except Exception:
                    pass

            if not candidate_uids:
                return None

            # Deduplicate while preserving sequence order (newest last)
            uids = sorted(list(set(u for u in candidate_uids if u not in self.seen_uids and (min_uid is None or u > min_uid))))
            if not uids:
                return None

            for uid in reversed(uids):
                status, msg_data = conn.uid("fetch", str(uid).encode(), "(RFC822)")
                if status != "OK" or not msg_data or not msg_data[0] or not isinstance(msg_data[0], tuple):
                    continue
                
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)
                
                # Check From header — must be from Telegram
                from_addr = (msg.get("From", "") or "").lower()
                is_telegram_sender = (
                    "telegram" in from_addr
                    or any(s in from_addr for s in self.TELEGRAM_SENDERS)
                )
                
                # Check Subject for Telegram-like patterns
                subject_raw = self._decode_header(msg.get("Subject", ""))
                subject_lower = subject_raw.lower()
                is_telegram_subject = (
                    "telegram" in subject_lower
                    or any(p in subject_lower for p in self.TELEGRAM_SUBJECT_PATTERNS)
                )
                
                if not (is_telegram_sender or is_telegram_subject):
                    continue
                
                # Check message date if min_timestamp is specified and min_uid was not available
                if min_timestamp and not min_uid:
                    date_hdr = msg.get("Date")
                    if date_hdr:
                        try:
                            msg_dt = email.utils.parsedate_to_datetime(date_hdr)
                            if msg_dt.tzinfo is None:
                                msg_dt = msg_dt.replace(tzinfo=datetime.timezone.utc)
                            # Allow max 5s clock skew
                            if msg_dt.timestamp() < (min_timestamp - 5):
                                continue  # Message was sent before our code request
                        except Exception:
                            pass
                
                # Check Recipient headers against target permutation
                matched, match_reason = self._match_recipient(msg, target_email_permutation)
                if not matched:
                    continue
                
                # Priority 1: Extract verification code from Subject header
                code = extract_telegram_code(subject_raw, expected_length=expected_length)
                
                # Priority 2: Extract from Email Body if subject didn't yield code
                body = None
                if not code:
                    body = self._get_email_body(msg)
                    code = extract_telegram_code(body, expected_length=expected_length)
                
                # Priority 3: Fallback regex on combined subject + body
                if not code:
                    if body is None:
                        body = self._get_email_body(msg)
                    combined = f"{subject_raw} {body}"
                    match = self.CODE_REGEX.search(combined)
                    if match:
                        cand = match.group(1)
                        if expected_length is None or len(cand) == expected_length:
                            code = cand

                if code:
                    self.seen_uids.add(uid)
                    # Mark as read
                    try:
                        conn.uid("store", str(uid).encode(), "+FLAGS", "(\\Seen)")
                    except Exception:
                        pass
                    return code
            
            return None
        except Exception as e:
            logging.warning(f"[IMAP] Scan failed: {e}")
            return None
        finally:
            if conn:
                try:
                    conn.close()
                    conn.logout()
                except Exception:
                    pass
    
    def _decode_header(self, header_value: str) -> str:
        """Decode MIME-encoded email header."""
        if not header_value:
            return ""
        decoded_parts = email.header.decode_header(header_value)
        result = []
        for part, charset in decoded_parts:
            if isinstance(part, bytes):
                result.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                result.append(str(part))
        return " ".join(result)
    
    def _get_email_body(self, msg) -> str:
        """Extract text body from email message."""
        body_parts = []
        
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        body_parts.append(payload.decode(charset, errors="replace"))
                elif content_type == "text/html" and not body_parts:
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        html = payload.decode(charset, errors="replace")
                        # Strip HTML tags for code extraction
                        text = re.sub(r"<[^>]+>", " ", html)
                        body_parts.append(text)
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))
        
        return " ".join(body_parts)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: MAIL VAULT STORAGE (per-user isolated credential store)
# ─────────────────────────────────────────────────────────────────────────────
# Each user's credentials are keyed by their Telegram user_id.
# Only admins can see other users' credentials via the Admin Vault.

def _load_mail_vault(meta: dict) -> dict:
    """Get or create the user_mail_vault section of registry."""
    return meta.setdefault("user_mail_vault", {})


def get_user_mail_config(meta: dict, user_id: int) -> dict:
    """Get a specific user's mail configuration. Returns empty dict if not set."""
    vault = _load_mail_vault(meta)
    return vault.get(str(user_id), {})


def set_user_mail_config(meta: dict, user_id: int, config: dict):
    """Set a user's mail configuration."""
    vault = _load_mail_vault(meta)
    vault[str(user_id)] = config


def delete_user_mail_config(meta: dict, user_id: int):
    """Remove a user's mail configuration."""
    vault = _load_mail_vault(meta)
    vault.pop(str(user_id), None)


def add_used_permutation(meta: dict, user_id: int, permutation: str):
    """Record a permutation as used for a specific user."""
    config = get_user_mail_config(meta, user_id)
    used = config.setdefault("used_permutations", [])
    if permutation not in used:
        used.append(permutation)
    config["accounts_bound"] = len(used)
    config["last_used"] = int(time.time())


def get_used_permutations(meta: dict, user_id: int) -> list:
    """Get all permutations already used by a user."""
    config = get_user_mail_config(meta, user_id)
    return config.get("used_permutations", [])


def list_all_mail_configs(meta: dict) -> dict:
    """Admin function: get all users' mail configs. Returns {user_id_str: config}."""
    return _load_mail_vault(meta)


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: PROVIDER AUTO-DETECTION HELPER
# ─────────────────────────────────────────────────────────────────────────────

def detect_imap_provider(email_address: str) -> dict:
    """
    Auto-detect IMAP server settings for an email address.
    Returns dict with host, port, provider name, and setup instructions.
    """
    domain = email_address.split("@")[-1].lower()
    
    # Known providers
    if domain in IMAP_PROVIDERS:
        provider_info = IMAP_PROVIDERS[domain].copy()
        provider_info["provider"] = domain.split(".")[0].capitalize()
        provider_info["detected"] = True
        
        # Provider-specific setup instructions & format examples
        setup_hints = {
            "gmail.com": (
                "1️⃣ **Enable IMAP**: Open Gmail in a browser → click ⚙️ Settings → 'See all settings' → 'Forwarding and POP/IMAP' tab → select **'Enable IMAP'** → click **'Save Changes'** at the bottom.\n"
                "2️⃣ **Turn on 2-Step Verification**: Go to `myaccount.google.com/signinoptions/two-step-verification`.\n"
                "3️⃣ **Generate App Password**: Go to `myaccount.google.com/apppasswords` → Enter App Name (e.g. `Telegram Bot`) → Click 'Create'.\n"
                "4️⃣ **Copy 16-letter code**: Google displays 16 characters (e.g. `tjko hczw xvlw xpyr`). Paste it here!"
            ),
            "googlemail.com": (
                "1️⃣ **Enable IMAP**: Open Gmail in a browser → click ⚙️ Settings → 'See all settings' → 'Forwarding and POP/IMAP' tab → select **'Enable IMAP'** → click **'Save Changes'** at the bottom.\n"
                "2️⃣ **Turn on 2-Step Verification**: Go to `myaccount.google.com/signinoptions/two-step-verification`.\n"
                "3️⃣ **Generate App Password**: Go to `myaccount.google.com/apppasswords` → Enter App Name (e.g. `Telegram Bot`) → Click 'Create'.\n"
                "4️⃣ **Copy 16-letter code**: Google displays 16 characters (e.g. `tjko hczw xvlw xpyr`). Paste it here!"
            ),
            "outlook.com": (
                "1️⃣ Go to `account.microsoft.com/security` (ensure 2-Step Verification is active).\n"
                "2️⃣ Click **'Advanced security options'** → scroll to **'App passwords'** → click **'Create a new app password'**.\n"
                "3️⃣ Copy the generated 16-character password and paste it here."
            ),
            "hotmail.com": (
                "1️⃣ Go to `account.microsoft.com/security` (ensure 2-Step Verification is active).\n"
                "2️⃣ Click **'Advanced security options'** → scroll to **'App passwords'** → click **'Create a new app password'**.\n"
                "3️⃣ Copy the generated 16-character password and paste it here."
            ),
            "live.com": (
                "1️⃣ Go to `account.microsoft.com/security` (ensure 2-Step Verification is active).\n"
                "2️⃣ Click **'Advanced security options'** → scroll to **'App passwords'** → click **'Create a new app password'**.\n"
                "3️⃣ Copy the generated 16-character password and paste it here."
            ),
            "yahoo.com": (
                "1️⃣ Sign in to Yahoo Account Security page.\n"
                "2️⃣ Click **'Generate app password'** → select 'Other app' → enter 'Telegram Bot'.\n"
                "3️⃣ Copy the generated 16-letter code and paste it here."
            ),
            "mail.ru": (
                "1️⃣ Go to Mail.ru Settings → **'Security'** → **'App Passwords'**.\n"
                "2️⃣ Click **'Add password for external app'**.\n"
                "3️⃣ Copy the generated code and paste it here."
            ),
            "yandex.ru": (
                "1️⃣ Go to Yandex ID → **'Security'** → **'App passwords'** → select **'Mail'**.\n"
                "2️⃣ Generate and copy the app password, then paste it here."
            ),
            "yandex.com": (
                "1️⃣ Go to Yandex ID → **'Security'** → **'App passwords'** → select **'Mail'**.\n"
                "2️⃣ Generate and copy the app password, then paste it here."
            ),
        }
        
        examples = {
            "gmail.com": "`tjko hczw xvlw xpyr` (or `tjkohczwxvlwxpyr`)",
            "googlemail.com": "`tjko hczw xvlw xpyr` (or `tjkohczwxvlwxpyr`)",
            "outlook.com": "`abcd1234efgh5678`",
            "hotmail.com": "`abcd1234efgh5678`",
            "live.com": "`abcd1234efgh5678`",
            "yahoo.com": "`kdfjlsdkjfhskdjf`",
            "mail.ru": "`a1b2c3d4e5f6g7h8`",
            "yandex.ru": "`abcdefghijklmnop`",
            "yandex.com": "`abcdefghijklmnop`",
        }
        
        provider_info["setup_hint"] = setup_hints.get(domain, "Please generate an App Password in your email provider's security settings.")
        provider_info["example"] = examples.get(domain, "`your_16_char_app_password`")
        return provider_info
    
    # Unknown provider — try generic
    return {
        "host": f"imap.{domain}",
        "port": 993,
        "provider": domain,
        "detected": False,
        "setup_hint": f"IMAP host auto-guessed as imap.{domain}:993. Please ensure IMAP SSL is enabled and generate an App Password.",
        "example": "`your_app_specific_password`"
    }
