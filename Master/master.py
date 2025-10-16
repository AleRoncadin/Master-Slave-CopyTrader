import MetaTrader5 as mt5
import time
import threading
import logging
from datetime import datetime, timezone
import os
import getpass
import socket

# supabase client
try:
    from supabase import create_client
except Exception:
    create_client = None

# ======= CONFIG =======
SUPABASE_URL = "https://tevsvjwhgsfzppzrjdwp.supabase.co"
SUPABASE_SERVICE_ROLE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRldnN2andoZ3NmenBwenJqZHdwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTgyMzA3OSwiZXhwIjoyMDc1Mzk5MDc5fQ.JuxRkDq-nyvzELiVcbiVLg_lu27z5tTRmz_3sZev8wY"
SUPABASE_TABLE = "orders"
SUPABASE_RETRIES = 2

POLL_INTERVAL = 0.05        # 50ms: reattivo, ma non stressa la CPU come 10ms
NET_CHECK_HOST = "tevsvjwhgsfzppzrjdwp.supabase.co"
NET_CHECK_PORT = 443
NET_CHECK_INTERVAL = 2.0     # controlla rete ogni 2s
WARN_COOLDOWN = 5.0          # non spammare il warning più spesso di così
# =======================

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
CRED_FILE = "mt5_credentials.txt"

# -------- rete --------
def has_internet(timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((NET_CHECK_HOST, NET_CHECK_PORT), timeout=timeout):
            return True
    except Exception:
        return False

class NetWatcher(threading.Thread):
    """Aggiorna lo stato 'online' e gestisce il cooldown dei warn."""
    def __init__(self, interval: float = NET_CHECK_INTERVAL):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop = threading.Event()
        self.online = has_internet()
        self._last_warn = 0.0

    def run(self):
        while not self._stop.is_set():
            self.online = has_internet()
            time.sleep(self.interval)

    def stop(self):
        self._stop.set()

    def warn_offline(self):
        now = time.time()
        if now - self._last_warn >= WARN_COOLDOWN:
            logging.warning("Internet mancante: impossibile inviare dati a Supabase (verrà ritentato automaticamente).")
            self._last_warn = now

# ---------- credenziali MT5 ----------
def load_mt5_credentials(file_path: str = CRED_FILE):
    try:
        if not os.path.exists(file_path):
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f.readlines()]
        if len(lines) < 4:
            logging.error("File credenziali incompleto (richiede 4 righe: path, login, password, server)")
            return None
        creds = {
            "path": lines[0],
            "login": int(lines[1]),
            "password": lines[2],
            "server": lines[3]
        }
        return creds
    except Exception as e:
        logging.exception("Errore caricamento credenziali MT5: %s", e)
        return None

def save_mt5_credentials(creds: dict, file_path: str = CRED_FILE):
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"{creds.get('path', '')}\n")
            f.write(f"{creds.get('login', '')}\n")
            f.write(f"{creds.get('password', '')}\n")
            f.write(f"{creds.get('server', '')}\n")
        try: os.chmod(file_path, 0o600)
        except Exception: pass
        logging.info("Credenziali MT5 salvate su %s", file_path)
    except Exception as e:
        logging.exception("Impossibile salvare credenziali MT5: %s", e)

def prompt_and_validate_mt5_credentials(max_attempts: int = 3, file_path: str = CRED_FILE):
    for attempt in range(1, max_attempts + 1):
        print(f"[MT5 credentials] Tentativo {attempt}/{max_attempts}")
        path = input("Percorso MT5 (INVIO per C:\\Program Files\\MetaTrader 5\\terminal64.exe): ").strip() or \
               "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
        login_raw = input("Login (numero): ").strip()
        try: login = int(login_raw)
        except Exception:
            print("Login non valido (numero)."); continue
        password = getpass.getpass("Password: ")
        server = input("Server (es. MetaQuotes-Demo): ").strip()

        try:
            try: mt5.shutdown()
            except Exception: pass
            # NON usare portable=True per evitare errori permessi WebView2
            ok = mt5.initialize(path=path, login=login, password=password, server=server)
            if ok:
                mt5.shutdown()
                creds = {"path": path, "login": login, "password": password, "server": server}
                save_mt5_credentials(creds, file_path=file_path)
                return creds
            else:
                logging.error("Validazione MT5 fallita: %s", mt5.last_error())
                print("Connessione MT5 fallita. Riprova.")
        except Exception as e:
            logging.exception("Errore validazione MT5: %s", e)
            print("Errore durante la validazione. Vedi log.")
    print("Credenziali MT5 non validate.")
    return None

def mt5_initialize(ask_if_missing: bool = True):
    creds = load_mt5_credentials()
    if creds is None and ask_if_missing:
        creds = prompt_and_validate_mt5_credentials()
    if not creds:
        logging.error("Credenziali MT5 non disponibili. Impossibile inizializzare.")
        return False
    try:
        # Fix permessi WebView2 prima di inizializzare
        #mt5_path = creds.get("path", "")
        #if mt5_path:
        #    fix_mt5_webview_permissions(mt5_path)

        try: mt5.shutdown()
        except Exception: pass
        # NON usare portable=True per evitare errori permessi WebView2
        ok = mt5.initialize(path=creds.get("path"), login=creds.get("login"),
                            password=creds.get("password"), server=creds.get("server"), portable=False)
        if not ok:
            logging.error("Impossibile inizializzare MT5: %s", mt5.last_error())
            if ask_if_missing:
                creds = prompt_and_validate_mt5_credentials()
                if not creds: return False

                # Fix permessi anche con nuove credenziali
                #mt5_path = creds.get("path", "")
                #if mt5_path:
                #    fix_mt5_webview_permissions(mt5_path)

                try: mt5.shutdown()
                except Exception: pass
                # NON usare portable=True
                if not mt5.initialize(path=creds.get("path"), login=creds.get("login"),
                                      password=creds.get("password"), server=creds.get("server")):
                    logging.error("Fallita inizializzazione: %s", mt5.last_error())
                    return False
            else:
                return False
        logging.info("MetaTrader5 inizializzato correttamente.")

        return True
    except Exception as e:
        logging.exception("Errore inizializzazione MT5: %s", e)
        return False

# ---------- helpers ----------
def safe_timestamp(ts):
    try:
        if ts is None: return None
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        try: return datetime.fromisoformat(str(ts)).isoformat()
        except Exception: return datetime.now(timezone.utc).isoformat()

def position_to_dict(p):
    return {
        "ticket": int(p.ticket), "symbol": p.symbol, "type": int(p.type),
        "volume": float(p.volume),
        "price_open": float(p.price_open) if p.price_open is not None else None,
        "sl": float(p.sl) if p.sl is not None else None,
        "tp": float(p.tp) if p.tp is not None else None,
        "price_current": float(getattr(p, "price_current", 0.0)),
        "time": safe_timestamp(getattr(p, "time", None)),
        "magic": int(getattr(p, "magic", 0)),
        "comment": getattr(p, "comment", ""),
        "profit": float(getattr(p, "profit", 0.0)),
        "swap": float(getattr(p, "swap", 0.0)),
        "storage": float(getattr(p, "storage", 0.0)),
    }


def map_type_to_text(t):
    try:
        if isinstance(t, (int, float)): return "buy" if int(t) == 0 else "sell"
        return str(t)
    except Exception:
        return str(t)

def build_base_context():
    acc = mt5.account_info()
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": {
            "login": int(acc.login) if acc else None,
            "name": getattr(acc, "name", None) if acc else None,
            "server": getattr(acc, "server", None) if acc else None,
            "currency": getattr(acc, "currency", None) if acc else None,
            "balance": float(getattr(acc, "balance", 0.0)) if acc else None,
            "equity": float(getattr(acc, "equity", 0.0)) if acc else None,
            "margin": float(getattr(acc, "margin", 0.0)) if acc else None,
        }
    }

# ---------- Storage Supabase (no crash se offline) ----------
class StorageSupabase:
    def __init__(self, url: str, key: str, table: str, netwatcher: NetWatcher):
        self.table = table
        self.net = netwatcher
        self._client = None
        if create_client is None:
            logging.warning("Package 'supabase' non installato: invio disabilitato.")
        elif not url or not key:
            logging.warning("SUPABASE_URL/KEY non configurati: invio disabilitato.")
        else:
            try:
                self._client = create_client(url, key)
                logging.info("Supabase client inizializzato.")
            except Exception as e:
                logging.exception("Errore inizializzazione Supabase: %s", e)
                self._client = None

    def _insert_supabase(self, record: dict) -> bool:
        if not self._client:
            return False
        for attempt in range(1, SUPABASE_RETRIES + 1):
            try:
                self._client.table(self.table).insert(record).execute()
                return True
            except Exception as e:
                logging.warning("Tentativo %d inserimento Supabase fallito: %s", attempt, e)
                time.sleep(0.2 * attempt)
        return False

    def store(self, event_type: str, payload: dict):
        # se offline → solo avviso e skip
        if not self.net.online:
            self.net.warn_offline()
            return

        # Solo position_opened viene salvato
        if event_type != "position_opened":
            return

        ts = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()

        # Estrai dati dalla position
        if isinstance(payload, dict) and "position" in payload and isinstance(payload["position"], dict):
            p = payload["position"]
            ticket = p.get("ticket")
            symbol = p.get("symbol")
            typ = map_type_to_text(p.get("type"))
        else:
            logging.warning("Payload non valido per position_opened")
            return

        # Struttura semplificata: solo ts, ticket, symbol, type
        record = {
            "ts": ts,
            "ticket": int(ticket) if ticket is not None else None,
            "symbol": symbol,
            "type": typ,
        }

        ok = self._insert_supabase(record)
        if not ok:
            logging.error("Invio a Supabase fallito (rete instabile o servizio indisponibile).")

# ---------- Monitor ----------
class MT5Monitor:
    def __init__(self, storage: StorageSupabase, poll_interval=POLL_INTERVAL):
        self.poll_interval = max(0.02, float(poll_interval))
        self._stop = threading.Event()
        self.storage = storage
        self.last_positions = {}

    def snapshot_positions(self):
        arr = mt5.positions_get()
        if arr is None: return {}
        out = {}
        for p in arr:
            d = position_to_dict(p)
            out[d["ticket"]] = d
        return out

    def detect_and_store(self):
        current_positions = self.snapshot_positions()
        # Rileva solo nuove posizioni aperte
        for t, pos in current_positions.items():
            if t not in self.last_positions:
                payload = build_base_context()
                payload.update({"event_type": "position_opened", "position": pos})
                self.storage.store("position_opened", payload)

        self.last_positions = current_positions

    def run(self):
        logging.info("Monitor avviato. Poll interval: %.3fs", self.poll_interval)
        try:
            self.last_positions = self.snapshot_positions()
        except Exception as e:
            logging.warning("Errore snapshot iniziale: %s", e)

        while not self._stop.is_set():
            try:
                self.detect_and_store()
            except Exception as e:
                logging.exception("Errore durante il polling: %s", e)
            time.sleep(self.poll_interval)

    def stop(self):
        self._stop.set()

# ---------- Main ----------
def main():
    # watcher rete
    net = NetWatcher()
    net.start()

    if not mt5_initialize():
        logging.error("MT5 non inizializzato. Uscita.")
        return

    storage = StorageSupabase(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, SUPABASE_TABLE, net)
    monitor = MT5Monitor(storage=storage, poll_interval=POLL_INTERVAL)
    th = threading.Thread(target=monitor.run, daemon=True)
    th.start()

    logging.info("Premi Ctrl+C per interrompere.")
    try:
        while True:
            time.sleep(0.05)
    except KeyboardInterrupt:
        logging.info("Arresto richiesto. Chiudo…")
        monitor.stop()
        th.join(timeout=2.0)
    finally:
        try: net.stop()
        except Exception: pass
        mt5.shutdown()
        logging.info("Programma terminato.")

if __name__ == "__main__":
    main()
