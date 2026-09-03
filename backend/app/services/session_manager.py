"""
MetaTrader 5 Production Session Manager & Background Heartbeat Engine
Handles credential validation, real live MT5 account preview, continuous heartbeat monitoring,
auto-recovery, state diff detection for mobile/desktop MT5 trades, saved accounts, and login history.
"""

import asyncio
import logging
import os
import sys
import platform
import socket
import subprocess
import json
import base64
import hashlib
from pathlib import Path

# Guard winreg on non-Windows platforms
WINREG_AVAILABLE = False
if platform.system() == "Windows":
    try:
        import winreg
        WINREG_AVAILABLE = True
    except ImportError:
        WINREG_AVAILABLE = False

from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

from app.core.config import settings
from app.domain.models import (
    AccountInfo, AccountPreview, SavedAccount, LoginHistoryEntry,
    ConnectionState, TradeRecord, PendingOrderRecord, ConnectionDiagnostics
)

logger = logging.getLogger("ApexTrader.SessionManager")

# ── PYTHON ARCHITECTURE CHECK ────────────────────────────────────────────────
PYTHON_ARCH = platform.architecture()[0]  # '64bit' or '32bit'
IS_64BIT_PYTHON = (PYTHON_ARCH == "64bit")

if not IS_64BIT_PYTHON:
    logger.warning(
        f"[SESSION MANAGER] WARNING: Running 32-bit Python ({PYTHON_ARCH}). "
        "MetaTrader5 requires 64-bit Python. Install Python 3.11+ 64-bit from python.org and restart."
    )

# ── MT5 PACKAGE AVAILABILITY CHECK ──────────────────────────────────────────
MT5_AVAILABLE = False
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
except ImportError:
    MT5_AVAILABLE = False
except Exception as _mt5_import_err:
    logger.error(f"[SESSION MANAGER] MetaTrader5 import error: {_mt5_import_err}")
    MT5_AVAILABLE = False

# ── PATHS & PASSWORD ENCRYPTION ──────────────────────────────────────────────
ACCOUNTS_PATH = Path(__file__).parent.parent.parent / "data" / "saved_accounts.json"


def encrypt_password(password: str) -> str:
    if not password:
        return ""
    # Derive key from settings.SECRET_KEY
    key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    encrypted_bytes = bytearray(password.encode())
    for i in range(len(encrypted_bytes)):
        encrypted_bytes[i] ^= key[i % len(key)]
    return base64.b64encode(encrypted_bytes).decode()


def decrypt_password(encrypted_str: str) -> str:
    if not encrypted_str:
        return ""
    try:
        key = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
        encrypted_bytes = bytearray(base64.b64decode(encrypted_str.encode()))
        for i in range(len(encrypted_bytes)):
            encrypted_bytes[i] ^= key[i % len(key)]
        return encrypted_bytes.decode()
    except Exception:
        return ""


# ── STANDARD INSTALLATION PATHS ─────────────────────────────────────────────
MT5_STANDARD_PATHS = [
    r"C:\Program Files\MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\MetaTrader 5\terminal64.exe",
    r"C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe",
    r"C:\Program Files\EXNESS-MetaTrader 5\terminal64.exe",
    r"C:\Program Files\Alpari MetaTrader 5\terminal64.exe",
    r"C:\Program Files\ICMarkets - MetaTrader 5\terminal64.exe",
    r"C:\Program Files\FBS MetaTrader 5\terminal64.exe",
    r"C:\Program Files\Pepperstone Group Limited\terminal64.exe",
    r"C:\Program Files\FTMO Global Markets\terminal64.exe",
    r"C:\Program Files\Deriv\terminal64.exe",
    r"C:\Program Files\XM Global Limited\terminal64.exe",
    r"C:\Program Files\Admiral Markets MT5\terminal64.exe",
    r"C:\Program Files (x86)\EXNESS-MetaTrader 5\terminal64.exe",
    r"C:\Program Files (x86)\ICMarkets - MetaTrader 5\terminal64.exe",
]

# ── MT5 LOGIN ERROR CODES (only after terminal is connected) ─────────────────
MT5_LOGIN_ERROR_HINTS = {
    -10003: "Check your login ID, password, and server name.",
    -10004: "Trade server is unreachable. Check your internet connection.",
    -10005: "Trade server is busy. Wait a few seconds and retry.",
    -10006: "Account does not exist on this server.",
    -10007: "Server name not found. Verify the broker server address.",
    -10008: "Authorization timed out. Retry the connection.",
    -10009: "MT5 terminal build is too old. Update MetaTrader 5.",
    -10010: "Account trading is blocked. Contact your broker.",
    -10012: "Connection to trade server was lost during login.",
    -10015: "IPC connection failed — terminal closed unexpectedly.",
    -10016: "Connection error during login.",
    -10017: "Not connected to a trading server.",
}


def _ts() -> str:
    """Return current HH:MM:SS timestamp string for diagnostic logs."""
    return datetime.now().strftime("%H:%M:%S")


class MT5SessionManager:
    """
    Production MT5 Session Manager.
    Correct two-step MT5 API workflow:
      Step A: mt5.initialize(path=terminal_path)  — connects to MT5 terminal process
      Step B: mt5.login(login, password, server)  — authenticates the trading account
      Step C: mt5.account_info()                  — retrieves live account data

    Always uses raw mt5.last_error() messages — never generic fallbacks.
    """
    def __init__(self):
        self.connection_state: ConnectionState = ConnectionState.DISCONNECTED
        self.active_login: int = 0
        self.active_password: str = ""
        self.active_server: str = ""
        self.active_broker: str = ""
        self.use_live_mt5: bool = False
        self._mt5_terminal_path: Optional[str] = None
        self.latest_validated_credentials: Optional[Dict[str, Any]] = None

        self.saved_accounts: Dict[str, SavedAccount] = {}
        self.login_history: List[LoginHistoryEntry] = []
        self._load_saved_accounts()

        self.last_positions_hash: str = ""
        self.last_orders_hash: str = ""
        self.last_balance: float = 0.0
        self.last_equity: float = 0.0

    # ── PERSISTENCE METHODS ───────────────────────────────────────────────────

    def _load_saved_accounts(self):
        try:
            if ACCOUNTS_PATH.exists():
                with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        # Decrypt password before loading into memory
                        if "password" in v and v["password"]:
                            v["password"] = decrypt_password(v["password"])
                        
                        # Handle last_connected conversion
                        if v.get("last_connected"):
                            v["last_connected"] = datetime.fromisoformat(v["last_connected"])
                        self.saved_accounts[k] = SavedAccount(**v)
                logger.info(f"[SESSION MANAGER] Loaded saved accounts from {ACCOUNTS_PATH}")
        except Exception as e:
            logger.error(f"[SESSION MANAGER] Failed to load saved accounts: {e}")

    def _save_saved_accounts(self):
        try:
            ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for k, sa in self.saved_accounts.items():
                sa_dict = sa.model_dump()
                # Encrypt password before writing to disk
                if sa_dict.get("password"):
                    sa_dict["password"] = encrypt_password(sa_dict["password"])
                # Serialize datetimes to string
                if isinstance(sa_dict.get("last_connected"), datetime):
                    sa_dict["last_connected"] = sa_dict["last_connected"].isoformat()
                data[k] = sa_dict
                
            with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.info(f"[SESSION MANAGER] Persisted saved accounts to {ACCOUNTS_PATH}")
        except Exception as e:
            logger.error(f"[SESSION MANAGER] Failed to persist saved accounts: {e}")

    # ── TERMINAL DISCOVERY ────────────────────────────────────────────────────

    def _find_mt5_terminal(self) -> Optional[str]:
        candidates = []
        candidates.extend(MT5_STANDARD_PATHS)

        username = os.environ.get("USERNAME", "")
        userprofile = os.environ.get("USERPROFILE", f"C:\\Users\\{username}")
        if userprofile:
            candidates += [
                os.path.join(userprofile, "AppData", "Roaming", "MetaQuotes", "Terminal", "terminal64.exe"),
                os.path.join(userprofile, "AppData", "Local", "MetaQuotes", "Terminal", "terminal64.exe"),
                os.path.join(userprofile, "Desktop", "MetaTrader 5", "terminal64.exe"),
                os.path.join(userprofile, "Documents", "MetaTrader 5", "terminal64.exe"),
            ]
            mq_terminal_dir = os.path.join(userprofile, "AppData", "Roaming", "MetaQuotes", "Terminal")
            if os.path.isdir(mq_terminal_dir):
                try:
                    for entry in os.scandir(mq_terminal_dir):
                        if entry.is_dir():
                            candidate = os.path.join(entry.path, "terminal64.exe")
                            candidates.insert(0, candidate)  # prioritize AppData installs
                except Exception:
                    pass

        if WINREG_AVAILABLE:
            registry_keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\MetaQuotes Software Corp.\MetaTrader 5"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\MetaQuotes Software Corp.\MetaTrader 5"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\MetaQuotes Software Corp.\MetaTrader 5"),
            ]
            for hive, key_path in registry_keys:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                        if install_dir:
                            candidate = os.path.join(install_dir, "terminal64.exe")
                            candidates.insert(0, candidate)
                except (FileNotFoundError, OSError):
                    pass

            uninstall_keys = [
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ]
            for hive, key_path in uninstall_keys:
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        i = 0
                        while True:
                            try:
                                subkey_name = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, subkey_name) as subkey:
                                    try:
                                        name, _ = winreg.QueryValueEx(subkey, "DisplayName")
                                        if "MetaTrader" in str(name) or "MetaQuotes" in str(name):
                                            try:
                                                loc, _ = winreg.QueryValueEx(subkey, "InstallLocation")
                                                if loc:
                                                    candidates.insert(0, os.path.join(loc, "terminal64.exe"))
                                            except (FileNotFoundError, OSError):
                                                pass
                                    except (FileNotFoundError, OSError):
                                                pass
                                i += 1
                            except OSError:
                                break
                except (FileNotFoundError, OSError):
                    pass
        else:
            logger.info("[MT5 DISCOVERY] Registry lookup skipped: not running on Windows or winreg not available")

        for folder in os.environ.get("PATH", "").split(";"):
            if folder and ("MetaTrader" in folder or "MT5" in folder or "MetaQuotes" in folder):
                candidates.insert(0, os.path.join(folder.strip(), "terminal64.exe"))

        seen = set()
        for path in candidates:
            try:
                norm = os.path.normpath(path)
                if norm not in seen:
                    seen.add(norm)
                    if os.path.isfile(norm):
                        logger.info(f"[MT5 DISCOVERY] Terminal found: {norm}")
                        return norm
            except Exception:
                pass

        logger.warning("[MT5 DISCOVERY] MT5 terminal not found. Install MetaTrader 5 from https://www.metatrader5.com")
        return None

    def _is_mt5_process_running(self) -> bool:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5
            )
            return "terminal64.exe" in result.stdout
        except Exception as e:
            logger.warning(f"[MT5 PROCESS CHECK] Could not query processes: {e}")
            return False

    def _check_internet(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect(("1.1.1.1", 53))
            s.close()
            return True
        except Exception:
            return False

    def _check_server_reachable(self, server: str) -> bool:
        try:
            socket.setdefaulttimeout(8)
            host = server.split(":")[0]
            addr = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            if addr:
                ip = addr[0][4][0]
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(8)
                s.connect((ip, 443))
                s.close()
                return True
        except Exception:
            pass
        return False

    def _resolve_shortcut_target(self, shortcut_path: str) -> Optional[str]:
        """Resolves target executable path from a Windows .lnk shortcut file."""
        if not shortcut_path or not os.path.isfile(shortcut_path):
            return None
            
        if not shortcut_path.lower().endswith(".lnk"):
            return None

        # 1. Try PowerShell WScript.Shell
        try:
            ps_cmd = f'$sh = New-Object -ComObject WScript.Shell; $sh.CreateShortcut(\'{shortcut_path}\').TargetPath'
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=5)
            target = res.stdout.strip()
            if target and os.path.isfile(target) and target.lower().endswith("terminal64.exe"):
                return target
        except Exception:
            pass

        # 2. Try raw binary scanning for terminal64.exe path in shortcut binary
        try:
            import re
            with open(shortcut_path, "rb") as f:
                content = f.read()
                matches = re.findall(rb'[A-Za-z]:\\[^:\*\?"<>\|\r\n]+\ terminal64\.exe', content, re.IGNORECASE)
                for m in matches:
                    path_candidate = m.decode('latin1', errors='ignore')
                    if os.path.isfile(path_candidate):
                        return path_candidate
        except Exception:
            pass

        return None

    # ── MAIN VERIFICATION & CONNECTION SERVICE ────────────────────────────────

    def resolve_terminal_path(self, custom_path: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """
        Validates and resolves the terminal path.
        Handles direct terminal64.exe paths, shortcut (.lnk) files, Start Menu folders,
        installation directories, and auto-discovery fallbacks.
        Returns:
            resolved_path: Path to terminal64.exe or None
            error_reason: Validation error message or None
        """
        if not custom_path or not custom_path.strip():
            discovered = self._find_mt5_terminal()
            if discovered:
                return discovered, None
            return None, "MT5 terminal64.exe not found in standard locations. Please specify the terminal path."

        # Strip surrounding quotes and whitespace
        cleaned_path = os.path.normpath(custom_path.strip().strip('"\''))

        # Check if the path exists
        if os.path.exists(cleaned_path):
            # Case 1: Path is a .lnk shortcut file
            if cleaned_path.lower().endswith(".lnk"):
                resolved = self._resolve_shortcut_target(cleaned_path)
                if resolved:
                    logger.info(f"[MT5 RESOLUTION] Resolved shortcut '{cleaned_path}' -> '{resolved}'")
                    return resolved, None

            # Case 2: Path is a file
            if os.path.isfile(cleaned_path):
                if cleaned_path.lower().endswith("terminal64.exe"):
                    return cleaned_path, None
                # If user selected another executable (e.g. metaeditor64.exe) in the MT5 directory
                sibling_terminal = os.path.join(os.path.dirname(cleaned_path), "terminal64.exe")
                if os.path.isfile(sibling_terminal):
                    logger.info(f"[MT5 RESOLUTION] Resolved executable in folder '{cleaned_path}' -> '{sibling_terminal}'")
                    return sibling_terminal, None

            # Case 3: Path is a directory
            if os.path.isdir(cleaned_path):
                # Check direct child terminal64.exe
                direct_candidate = os.path.join(cleaned_path, "terminal64.exe")
                if os.path.isfile(direct_candidate):
                    return direct_candidate, None
                
                # Check subdirectories for terminal64.exe or .lnk shortcuts
                for root, dirs, files in os.walk(cleaned_path):
                    if "terminal64.exe" in files:
                        return os.path.join(root, "terminal64.exe"), None
                    for f in files:
                        if f.lower().endswith(".lnk"):
                            lnk_path = os.path.join(root, f)
                            resolved = self._resolve_shortcut_target(lnk_path)
                            if resolved:
                                logger.info(f"[MT5 RESOLUTION] Resolved shortcut in folder '{lnk_path}' -> '{resolved}'")
                                return resolved, None

        # Fallback: If custom path failed to resolve directly, check auto-discovery
        discovered = self._find_mt5_terminal()
        if discovered:
            logger.info(f"[MT5 RESOLUTION] Custom path '{custom_path}' could not be resolved directly, using auto-discovered terminal at '{discovered}'")
            return discovered, None

        return None, f"Specified terminal path does not contain terminal64.exe: {custom_path}"

    def _establish_mt5_connection(
        self,
        login: int,
        password: str,
        server: str,
        custom_terminal_path: Optional[str] = None,
        log_callback=None
    ) -> Tuple[bool, str, Optional[AccountPreview], ConnectionDiagnostics]:
        logs: List[str] = []

        def log(msg: str) -> None:
            entry = f"[{_ts()}] {msg}"
            logs.append(entry)
            logger.info(f"[SESSION MANAGER] {msg}")
            if log_callback:
                log_callback(entry)

        diagnostics = ConnectionDiagnostics(
            terminal_path=custom_terminal_path or "AUTO_DISCOVER",
            terminal_exists="NO",
            mt5_initialize="FAIL",
            mt5_initialize_error="",
            login="FAIL",
            login_error="",
            requested_account=login,
            connected_account=0,
            requested_server=server,
            connected_server="",
            account_info="FAIL",
            final_status="FAILED"
        )

        if not login or login <= 0:
            return False, "Invalid Login ID.", None, diagnostics
        if not password or len(password.strip()) < 3:
            return False, "Invalid Password.", None, diagnostics
        if not server or len(server.strip()) < 2:
            return False, "Invalid Server.", None, diagnostics

        log(f"Diagnostics: Python Architecture: {PYTHON_ARCH}")
        if not IS_64BIT_PYTHON:
            msg = "Running 32-bit Python — MetaTrader5 requires 64-bit Python."
            log(msg)
            return False, msg, None, diagnostics

        if not MT5_AVAILABLE:
            msg = "MetaTrader5 Python package not installed."
            log(msg)
            return False, msg, None, diagnostics

        # 1. Validate & resolve terminal path
        terminal_path, path_err = self.resolve_terminal_path(custom_terminal_path)
        if path_err:
            log(f"Terminal path validation failed: {path_err}")
            diagnostics.mt5_initialize_error = path_err
            return False, path_err, None, diagnostics

        diagnostics.terminal_path = terminal_path
        diagnostics.terminal_exists = "YES" if (terminal_path and os.path.exists(terminal_path)) else "NO"

        # Check process list
        is_running = self._is_mt5_process_running()
        log(f"Terminal process running status: {'RUNNING' if is_running else 'NOT RUNNING'}")

        # 2. Initialize MT5
        try:
            # Shutdown current first if connected to a different account/server
            try:
                current_acc = mt5.account_info()
                if current_acc:
                    if current_acc.login != login or current_acc.server != server:
                        log(f"Switching account. Shutting down current session ({current_acc.login} on {current_acc.server})...")
                        mt5.shutdown()
            except Exception:
                pass

            init_kwargs = {"login": login, "server": server}
            if password and password != "demo_password":
                init_kwargs["password"] = password
            if terminal_path:
                init_kwargs["path"] = terminal_path

            log(f"Initializing MT5 terminal with path: {terminal_path or 'default'}")
            init_ok = mt5.initialize(**init_kwargs)

            if not init_ok:
                log("Unified initialize failed — falling back to path-only initialize...")
                init_ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()

            if not init_ok:
                err = mt5.last_error()
                err_code = err[0] if err else -1
                err_msg = err[1] if err else "Unknown initialize error"
                log(f"initialize() failed. Code: {err_code}, Msg: {err_msg}")
                diagnostics.mt5_initialize = "FAIL"
                diagnostics.mt5_initialize_error = f"{err_msg} (Code {err_code})"
                return False, f"MT5 initialize failed: {err_msg}", None, diagnostics

            diagnostics.mt5_initialize = "PASS"
            log("initialize() succeeded")

            # 3. Login using account number + password + server
            acc = mt5.account_info()
            if acc and acc.login == login and acc.server == server:
                log("Already logged in to correct account")
                login_ok = True
            else:
                log(f"Logging in to account {login} on server {server}...")
                login_ok = mt5.login(login=login, password=password, server=server)

            if not login_ok:
                err = mt5.last_error()
                err_code = err[0] if err else -1
                err_msg = err[1] if err else "Unknown login error"
                hint = MT5_LOGIN_ERROR_HINTS.get(err_code, "")
                log(f"login() failed. Code: {err_code}, Msg: {err_msg}. {hint}")
                diagnostics.login = "FAIL"
                diagnostics.login_error = f"{err_msg} (Code {err_code}). {hint}"
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                return False, f"Login failed: {err_msg}. {hint}", None, diagnostics

            diagnostics.login = "PASS"
            log("login() succeeded")

            # 4. Verify Account Info
            acc = mt5.account_info()
            term = mt5.terminal_info()

            if not acc:
                err = mt5.last_error()
                err_code = err[0] if err else -1
                err_msg = err[1] if err else "Unknown error"
                log(f"account_info() returned None. Code: {err_code}")
                diagnostics.account_info = "FAIL"
                diagnostics.login_error = f"Account info unavailable after login: {err_msg}"
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                return False, "Failed to retrieve account data after login.", None, diagnostics

            diagnostics.connected_account = acc.login
            diagnostics.connected_server = acc.server

            # 5. Verify account login and server match
            if acc.login != login:
                msg = f"Account mismatch. Expected {login}, got {acc.login}."
                log(msg)
                diagnostics.account_info = "FAIL"
                diagnostics.login_error = msg
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                return False, msg, None, diagnostics

            if acc.server != server:
                msg = f"Server mismatch. Expected {server}, got {acc.server}."
                log(msg)
                diagnostics.account_info = "FAIL"
                diagnostics.login_error = msg
                try:
                    mt5.shutdown()
                except Exception:
                    pass
                return False, msg, None, diagnostics

            diagnostics.account_info = "PASS"
            diagnostics.final_status = "CONNECTED"

            trade_mode = "Demo" if acc.trade_mode == 0 else ("Contest" if acc.trade_mode == 1 else "Real")
            margin_lvl = round(acc.margin_level, 1) if acc.margin > 0 else 0.0

            preview = AccountPreview(
                success=True,
                account_number=acc.login,
                account_name=acc.name or f"Account {acc.login}",
                broker=acc.company or "MetaQuotes Ltd.",
                server=acc.server or server,
                account_type=trade_mode,
                currency=acc.currency or "USD",
                leverage=f"1:{acc.leverage}",
                balance=round(acc.balance, 2),
                equity=round(acc.equity, 2),
                free_margin=round(acc.margin_free, 2),
                margin=round(acc.margin, 2),
                margin_level_pct=margin_lvl,
                company=acc.company or "MetaQuotes Software Corp.",
                terminal_build=f"Build {getattr(term, 'build', 'MT5')}" if term else "Build MT5",
                status="Verified",
                error_reason=None,
                diagnostic_logs=logs
            )

            return True, "MT5 connected successfully", preview, diagnostics

        except Exception as e:
            err_msg = f"Unexpected exception during MT5 connection: {str(e)}"
            log(f"ERROR: {err_msg}")
            diagnostics.login_error = err_msg
            try:
                mt5.shutdown()
            except Exception:
                pass
            return False, err_msg, None, diagnostics

    def fetch_account_preview(self, login: int, password: str, server: str, broker: str = "", custom_terminal_path: Optional[str] = None) -> AccountPreview:
        success, msg, preview, diagnostics = self._establish_mt5_connection(
            login, password, server, custom_terminal_path
        )
        if success and preview:
            # Store validated credentials in memory for Step 4
            self.latest_validated_credentials = {
                "login": login,
                "password": password,
                "server": server,
                "broker": preview.broker or broker,
                "terminal_path": diagnostics.terminal_path or custom_terminal_path
            }
            return preview
        else:
            return AccountPreview(
                success=False,
                account_number=login,
                status="Failed",
                error_reason=msg,
                diagnostic_logs=preview.diagnostic_logs if preview else [f"Failed: {msg}"]
            )

    def auto_connect_running_terminal(self) -> Tuple[bool, str, AccountInfo]:
        if not MT5_AVAILABLE:
            return False, "MetaTrader5 Python package not available", self.get_account_info()

        try:
            terminal_path = self._find_mt5_terminal()
            init_ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
            if init_ok:
                acc = mt5.account_info()
                if acc and acc.login > 0:
                    self.active_login = acc.login
                    self.active_server = acc.server
                    self.active_broker = acc.company or "MetaQuotes Ltd."
                    self.use_live_mt5 = True
                    self.connection_state = ConnectionState.CONNECTED
                    
                    # Sync history immediately on connection
                    self.sync_history_to_sqlite()
                    
                    logger.info(f"[SESSION MANAGER] Auto-connected running MT5 terminal account: {acc.login} ({acc.name})")
                    return True, f"Auto-connected to running MT5 account {acc.login}", self.get_account_info()
        except Exception as e:
            logger.warning(f"[SESSION MANAGER] Auto-connect attempt exception: {e}")

        return False, "No active MT5 terminal session detected at startup", self.get_account_info()

    def connect_session(self, login: int, password: str, server: str, broker: str = "", name: str = None, custom_terminal_path: Optional[str] = None) -> Tuple[bool, str, AccountInfo, ConnectionDiagnostics]:
        self.connection_state = ConnectionState.CONNECTING
        logger.info(f"[SESSION MANAGER] Connecting Live MT5 Session for Account {login} on {server}...")

        # Part 13: Stop auto trading, stop worker safely
        try:
            from app.services.trading_worker import trading_worker
            if trading_worker.is_running():
                logger.info("[SESSION MANAGER] Active trading worker detected. Stopping worker before switching accounts...")
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(trading_worker.stop(), loop)
                else:
                    asyncio.run(trading_worker.stop())
        except Exception as e:
            logger.error(f"[SESSION MANAGER] Error stopping active worker: {e}")

        # Part 4: Retrieve password and terminal path from cached credentials if matching
        target_password = password
        target_terminal_path = custom_terminal_path
        if self.latest_validated_credentials:
            cached = self.latest_validated_credentials
            if cached.get("login") == login and cached.get("server") == server:
                target_password = cached.get("password")
                target_terminal_path = cached.get("terminal_path")
                logger.info(f"[SESSION MANAGER] Reusing verified credentials for account {login} on {server}.")

        success, msg, preview, diagnostics = self._establish_mt5_connection(
            login, target_password, server, target_terminal_path
        )

        if not success or not preview:
            self.connection_state = ConnectionState.RECONNECT_FAILED
            self.log_login_history(login, broker or server, server, False, msg)
            acc_info = self.get_account_info()
            acc_info.last_error = msg
            return False, msg, acc_info, diagnostics

        self.active_login = login
        self.active_password = target_password
        self.active_server = server
        self.active_broker = preview.company or broker or "MetaQuotes Ltd."
        self.use_live_mt5 = True
        self.connection_state = ConnectionState.CONNECTED

        # Sync history immediately on connection
        self.sync_history_to_sqlite()

        self.log_login_history(login, self.active_broker, server, True, "Connected successfully")

        acc_id = f"{login}@{server}"
        self.saved_accounts[acc_id] = SavedAccount(
            id=acc_id,
            name=name or preview.account_name or f"Account {login}",
            login=login,
            password=target_password,
            server=server,
            broker=self.active_broker,
            account_type=preview.account_type,
            currency=preview.currency,
            leverage=preview.leverage,
            terminal_path=target_terminal_path,
            last_connected=datetime.now(),
            is_active=True
        )
        self._save_saved_accounts()

        logger.info(f"[SESSION MANAGER] Live MT5 Session established for {login} ({preview.account_name})!")
        return True, "MT5 Session Connected Successfully", self.get_account_info(), diagnostics

    def refresh_saved_account(self, account_id: str) -> Optional[AccountInfo]:
        if account_id not in self.saved_accounts:
            return None
        sa = self.saved_accounts[account_id]
        
        # Store active session info to restore later
        active_login_backup = self.active_login
        active_password_backup = self.active_password
        active_server_backup = self.active_server
        active_broker_backup = self.active_broker
        use_live_backup = self.use_live_mt5
        state_backup = self.connection_state
        
        try:
            # Disconnect current
            if MT5_AVAILABLE:
                mt5.shutdown()
            
            # Connect target
            terminal_path, _ = self.resolve_terminal_path(sa.terminal_path)
            init_ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
            if init_ok:
                login_ok = mt5.login(login=sa.login, password=sa.password, server=sa.server)
                if login_ok:
                    acc = mt5.account_info()
                    term = mt5.terminal_info()
                    if acc:
                        margin_lvl = round(acc.margin_level, 1) if acc.margin > 0 else 0.0
                        trade_mode_str = "Demo" if acc.trade_mode == 0 else ("Contest" if acc.trade_mode == 1 else "Real")
                        
                        from app.services.trade_learner import trade_learner
                        daily_pnl, weekly_pnl = trade_learner.get_realized_pnl()
                        
                        acc_info = AccountInfo(
                            account_number=acc.login,
                            account_name=acc.name or f"Account {acc.login}",
                            broker=acc.company or sa.broker,
                            server=acc.server or sa.server,
                            account_type=trade_mode_str,
                            currency=acc.currency or "USD",
                            leverage=f"1:{acc.leverage}",
                            company=acc.company or "MetaQuotes Ltd.",
                            terminal_build=f"Build {getattr(term, 'build', 'MT5')}" if term else "Build MT5",
                            balance=round(acc.balance, 2),
                            equity=round(acc.equity, 2),
                            margin=round(acc.margin, 2),
                            free_margin=round(acc.margin_free, 2),
                            margin_level_pct=margin_lvl,
                            floating_profit=round(acc.profit, 2),
                            daily_pnl_usd=daily_pnl,
                            weekly_pnl_usd=weekly_pnl,
                            peak_equity=round(acc.equity, 2),
                            drawdown_pct=0.0,
                            open_positions_count=mt5.positions_total() or 0,
                            pending_orders_count=mt5.orders_total() or 0,
                            is_connected=True,
                            connection_mode="LIVE_MT5",
                            detailed_status=ConnectionState.CONNECTED
                        )
                        
                        mt5.shutdown()
                        return acc_info
        except Exception as e:
            logger.error(f"[SESSION MANAGER] Failed to refresh saved account {account_id}: {e}")
        finally:
            # Restore previous session
            if use_live_backup:
                try:
                    terminal_path, _ = self.resolve_terminal_path(None)
                    init_ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
                    if init_ok:
                        mt5.login(login=active_login_backup, password=active_password_backup, server=active_server_backup)
                        self.use_live_mt5 = True
                        self.connection_state = state_backup
                except Exception as e:
                    logger.error(f"[SESSION MANAGER] Failed to restore active session after refresh: {e}")
                    self.use_live_mt5 = False
                    self.connection_state = ConnectionState.DISCONNECTED
            else:
                self.use_live_mt5 = False
                self.connection_state = ConnectionState.DISCONNECTED
                
        return None

    def disconnect_session(self) -> bool:
        if MT5_AVAILABLE:
            try:
                mt5.shutdown()
            except Exception as e:
                logger.error(f"Error shutting down MT5: {e}")
        self.use_live_mt5 = False
        self.connection_state = ConnectionState.DISCONNECTED
        logger.info("[SESSION MANAGER] Session Disconnected.")
        return True

    def reconnect_terminal(self) -> bool:
        """Robust reconnect method to restore terminal connection and login state."""
        if not MT5_AVAILABLE or not self.use_live_mt5:
            return False
            
        try:
            # 1. Try raw login first
            if mt5.login(login=self.active_login, password=self.active_password, server=self.active_server):
                self.connection_state = ConnectionState.CONNECTED
                logger.info(f"[RECONNECT] Fast login restored for account {self.active_login}")
                return True
                
            # 2. Fully shutdown and re-initialize terminal
            logger.info("[RECONNECT] Fast login failed. Attempting full terminal re-initialization...")
            mt5.shutdown()
            
            terminal_path = self._find_mt5_terminal()
            init_ok = mt5.initialize(path=terminal_path) if terminal_path else mt5.initialize()
            if init_ok:
                if mt5.login(login=self.active_login, password=self.active_password, server=self.active_server):
                    self.connection_state = ConnectionState.CONNECTED
                    logger.info(f"[RECONNECT] Full connection re-established for account {self.active_login}")
                    return True
                    
            self.connection_state = ConnectionState.CONNECTION_LOST
            logger.error("[RECONNECT] Full terminal reconnect failed.")
        except Exception as e:
            logger.error(f"[RECONNECT] Exception during reconnect: {e}")
            self.connection_state = ConnectionState.CONNECTION_LOST
            
        return False

    # ── LIVE ACCOUNT INFO ─────────────────────────────────────────────────────

    def sync_history_to_sqlite(self):
        if self.use_live_mt5 and MT5_AVAILABLE:
            try:
                from datetime import timedelta
                from app.services.trade_learner import trade_learner
                
                now_server = trade_learner.get_broker_current_time()
                from_date = now_server - timedelta(days=14)
                to_date = now_server + timedelta(days=2)
                
                history_deals = mt5.history_deals_get(from_date, to_date)
                if history_deals:
                    for d in history_deals:
                        # entry == 1 means Deal out (closed position)
                        if getattr(d, 'entry', 0) == 1:
                            position_id = d.position_id
                            
                            # Fetch all deals related to this position
                            pos_deals = mt5.history_deals_get(position=position_id)
                            entry_price = d.price
                            open_time = datetime.fromtimestamp(d.time)
                            
                            if pos_deals:
                                # Find the entry deal (entry == 0)
                                entry_deal = next((pd for pd in pos_deals if pd.entry == 0), None)
                                if entry_deal:
                                    entry_price = entry_deal.price
                                    open_time = datetime.fromtimestamp(entry_deal.time)
                                    
                            trade_id = str(position_id or d.ticket)
                            side = "BUY" if d.type == 0 else "SELL"
                            if pos_deals:
                                entry_deal = next((pd for pd in pos_deals if pd.entry == 0), None)
                                if entry_deal:
                                    side = "BUY" if entry_deal.type == 0 else "SELL"
                                else:
                                    side = "BUY" if d.type == 1 else "SELL"
                            else:
                                side = "BUY" if d.type == 1 else "SELL"
                                
                            exit_price = d.price
                            lots = d.volume
                            pnl_usd = round(d.profit + d.swap + d.commission, 2)
                            close_time = datetime.fromtimestamp(d.time)
                            setup_name_val = "Unknown"
                            from app.services.trade_manager import trade_manager
                            if trade_id in trade_manager.active_trades:
                                setup_name_val = trade_manager.active_trades[trade_id].setup_name or "Unknown"

                            deal_comment = getattr(d, 'comment', 'CLOSED') or 'CLOSED'
                            trade_learner.sync_deal_to_db(
                                trade_id=trade_id,
                                symbol=d.symbol,
                                side=side,
                                entry_price=entry_price,
                                exit_price=exit_price,
                                lots=lots,
                                pnl_usd=pnl_usd,
                                open_time=open_time,
                                close_time=close_time,
                                comment=deal_comment,
                                setup_name=setup_name_val
                            )

                # Reconciliation discrepancy check for pending database trades
                with trade_learner._get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT trade_id, symbol FROM trade_history_memory WHERE win_loss = 'PENDING'")
                    pending_db_trades = cursor.fetchall()
                    
                live_position_tickets = {str(p.ticket) for p in (mt5.positions_get() or [])}
                
                for pt in pending_db_trades:
                    tid = pt["trade_id"]
                    if tid not in live_position_tickets:
                        # Check if it was recorded in deals
                        deals = None
                        if str(tid).isdigit():
                            deals = mt5.history_deals_get(position=int(tid))

                        if deals:
                            exit_deal = next((d for d in deals if getattr(d, 'entry', 0) == 1), None)
                            if exit_deal:
                                entry_deal = next((d for d in deals if getattr(d, 'entry', 0) == 0), None)
                                entry_price = entry_deal.price if entry_deal else exit_deal.price
                                open_time = datetime.fromtimestamp(entry_deal.time) if entry_deal else datetime.fromtimestamp(exit_deal.time)
                                side = "BUY" if (entry_deal and entry_deal.type == 0) else ("BUY" if exit_deal.type == 1 else "SELL")
                                pnl_usd = round(exit_deal.profit + exit_deal.swap + exit_deal.commission, 2)
                                close_time = datetime.fromtimestamp(exit_deal.time)
                                comment = getattr(exit_deal, 'comment', 'CLOSED') or 'CLOSED'
                                setup_name_val = "Unknown"
                                from app.services.trade_manager import trade_manager
                                if str(tid) in trade_manager.active_trades:
                                    setup_name_val = trade_manager.active_trades[str(tid)].setup_name or "Unknown"
                                
                                trade_learner.sync_deal_to_db(
                                    trade_id=str(tid),
                                    symbol=exit_deal.symbol,
                                    side=side,
                                    entry_price=entry_price,
                                    exit_price=exit_deal.price,
                                    lots=exit_deal.volume,
                                    pnl_usd=pnl_usd,
                                    open_time=open_time,
                                    close_time=close_time,
                                    comment=comment,
                                    setup_name=setup_name_val
                                )
                                logger.info(f"[RECONCILIATION SUCCESS] Synced exit deal for pending trade #{tid} ({exit_deal.symbol}) to DB")
                                continue

                        logger.error(
                            f"[RECONCILIATION DISCREPANCY] Trade #{tid} ({pt['symbol']}) is no longer active, "
                            f"but no exit deals were found in MT5 history! Marking as discrepancy."
                        )
                        trade_learner.mark_trade_discrepancy(str(tid), reason="RECONCILIATION_DISCREPANCY")

            except Exception as e:
                logger.error(f"[SESSION MANAGER] Error in sync_history_to_sqlite: {e}")

    def get_account_info(self) -> AccountInfo:
        if self.use_live_mt5 and MT5_AVAILABLE:
            try:
                acc = mt5.account_info()
                term = mt5.terminal_info()
                if acc:
                    margin_lvl = round(acc.margin_level, 1) if acc.margin > 0 else 0.0
                    trade_mode_str = "Demo" if acc.trade_mode == 0 else ("Contest" if acc.trade_mode == 1 else "Real")
                    
                    # Fetch peak_equity and compute drawdown_pct
                    from app.services.trade_learner import trade_learner
                    key = f"peak_equity_{acc.login}"
                    peak_equity_str = trade_learner.get_metadata(key)
                    
                    if peak_equity_str is not None:
                        peak_equity = float(peak_equity_str)
                    else:
                        peak_equity = acc.equity
                        trade_learner.set_metadata(key, str(peak_equity))
                        
                    if acc.equity > peak_equity:
                        peak_equity = acc.equity
                        trade_learner.set_metadata(key, str(peak_equity))
                        
                    drawdown_pct = 0.0
                    if peak_equity > 0:
                        drawdown_pct = ((peak_equity - acc.equity) / peak_equity) * 100.0
                        
                    # Fetch realized daily/weekly P&L from SQLite
                    daily_pnl, weekly_pnl = trade_learner.get_realized_pnl()
                    
                    return AccountInfo(
                        account_number=acc.login,
                        account_name=acc.name or f"Account {acc.login}",
                        broker=acc.company or self.active_broker,
                        server=acc.server or self.active_server,
                        account_type=trade_mode_str,
                        currency=acc.currency or "USD",
                        leverage=f"1:{acc.leverage}",
                        company=acc.company or "MetaQuotes Ltd.",
                        terminal_build=f"Build {getattr(term, 'build', 'MT5')}" if term else "Build MT5",
                        balance=round(acc.balance, 2),
                        equity=round(acc.equity, 2),
                        margin=round(acc.margin, 2),
                        free_margin=round(acc.margin_free, 2),
                        margin_level_pct=margin_lvl,
                        floating_profit=round(acc.profit, 2),
                        daily_pnl_usd=daily_pnl,
                        weekly_pnl_usd=weekly_pnl,
                        peak_equity=round(peak_equity, 2),
                        drawdown_pct=round(drawdown_pct, 2),
                        open_positions_count=mt5.positions_total() or 0,
                        pending_orders_count=mt5.orders_total() or 0,
                        is_connected=True,
                        connection_mode="LIVE_MT5",
                        detailed_status=self.connection_state
                    )
            except Exception as e:
                logger.error(f"Error getting live MT5 account info: {e}")
 
        # Fallback offline values
        from app.services.trade_learner import trade_learner
        daily_pnl, weekly_pnl = trade_learner.get_realized_pnl()
        
        peak_equity = 0.0
        drawdown_pct = 0.0
        if self.active_login > 0:
            key = f"peak_equity_{self.active_login}"
            peak_equity_str = trade_learner.get_metadata(key)
            if peak_equity_str is not None:
                peak_equity = float(peak_equity_str)
 
        return AccountInfo(
            account_number=self.active_login,
            account_name="Not Connected",
            broker=self.active_broker or "MT5",
            server=self.active_server or "Offline",
            account_type="",
            currency="",
            leverage="",
            company="",
            terminal_build="",
            balance=0.0, equity=0.0, margin=0.0, free_margin=0.0,
            margin_level_pct=0.0, floating_profit=0.0,
            daily_pnl_usd=daily_pnl,
            weekly_pnl_usd=weekly_pnl,
            peak_equity=round(peak_equity, 2),
            drawdown_pct=round(drawdown_pct, 2),
            open_positions_count=0, pending_orders_count=0,
            is_connected=False,
            connection_mode="SIMULATION",
            detailed_status=ConnectionState.DISCONNECTED,
            last_error="No active live MT5 account session"
        )

    # ── LOGIN HISTORY & SAVED ACCOUNTS ───────────────────────────────────────

    def log_login_history(self, login: int, broker: str, server: str, success: bool, reason: str):
        entry = LoginHistoryEntry(
            timestamp=datetime.now(),
            account_number=login,
            broker=broker,
            server=server,
            success=success,
            reason=reason
        )
        self.login_history.insert(0, entry)
        if len(self.login_history) > 100:
            self.login_history.pop()

    def get_saved_accounts(self) -> List[SavedAccount]:
        return list(self.saved_accounts.values())

    def remove_saved_account(self, account_id: str) -> bool:
        if account_id in self.saved_accounts:
            self.saved_accounts.pop(account_id)
            self._save_saved_accounts()
            return True
        return False

    # ── BACKGROUND HEARTBEAT ─────────────────────────────────────────────────

    async def start_heartbeat_loop(self, broadcast_callback=None):
        logger.info("[SESSION MANAGER] Starting continuous background heartbeat loop...")
        while True:
            try:
                await asyncio.sleep(2.0)

                if self.use_live_mt5 and MT5_AVAILABLE:
                    try:
                        acc = mt5.account_info()
                        if not acc:
                            self.connection_state = ConnectionState.RECONNECTING
                            logger.warning("[HEARTBEAT] Live MT5 connection dropped. Attempting recovery...")
                            self.reconnect_terminal()
                        else:
                            self.connection_state = ConnectionState.CONNECTED
                    except Exception as e:
                        logger.error(f"[HEARTBEAT] Live MT5 check exception: {e}")
                        self.connection_state = ConnectionState.CONNECTION_LOST

                current_acc = self.get_account_info()
                pos_hash = (
                    f"{current_acc.open_positions_count}_{current_acc.pending_orders_count}_"
                    f"{current_acc.balance}_{current_acc.equity}"
                )

                if pos_hash != self.last_positions_hash:
                    self.last_positions_hash = pos_hash
                    # Sync history immediately on state change
                    self.sync_history_to_sqlite()
                    
                    logger.info(
                        f"[HEARTBEAT] State change: Balance={current_acc.balance} {current_acc.currency}, "
                        f"Equity={current_acc.equity}, Positions={current_acc.open_positions_count}"
                    )
                    if broadcast_callback:
                        await broadcast_callback()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[HEARTBEAT] Loop exception: {e}")


# Global Session Manager Singleton
session_manager = MT5SessionManager()
