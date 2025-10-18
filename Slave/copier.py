"""
MT5 Slave CopyTrader - Copy Trading Engine
Copia le operazioni dal master ai conti prop e broker
"""

import sys
import os
import time
import random
import threading
import socket
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
from setup import main_setup

# Importa configurazione
import config

# IMPORTANTE: Devi rinominare i file .pyd di MetaTrader5
# Nelle cartelle MT5_prop e MT5_broker, rinomina:
# MetaTrader5.cp3X-win_amd64.pyd -> Meta1.cp3X-win_amd64.pyd (per prop)
# MetaTrader5.cp3X-win_amd64.pyd -> Meta2.cp3X-win_amd64.pyd (per broker)

# Aggiungi i percorsi alle librerie
sys.path.insert(0, r'C:\Program Files\MT5_prop')
sys.path.insert(1, r'C:\Program Files\MT5_broker')

# Importa le due istanze separate
try:
    import MT5_Prop as mt5_prop
    import MT5_Broker as mt5_broker
    print("✓ Librerie MT5 importate")
except ImportError as e:
    print(f"✗ Errore import librerie MT5: {e}")
    print("\nNOTA: Se vedi questo errore, devi copiare e rinominare i file .pyd")
    print("Vedi le istruzioni nel README")
    sys.exit(1)

# ======= CONFIG RETE =======
NET_CHECK_HOST = "tevsvjwhgsfzppzrjdwp.supabase.co"
NET_CHECK_PORT = 443
NET_CHECK_INTERVAL = 2.0     # controlla rete ogni 2s
WARN_COOLDOWN = 5.0          # non spammare il warning più spesso di così
AUTOTRADING_CHECK_INTERVAL = 10.0  # controlla autotrading ogni 10s
# ===========================

# ======= WINDOWS API PER ALGO TRADING =======
WM_COMMAND = 0x0111
GA_ROOT = 2
MT5_WMCMD_EXPERTS = 32851  # Comando per toggle Algo Trading in MT5

# Definizione funzioni user32.dll
user32 = ctypes.windll.user32

# FindWindowW per trovare finestra MT5 per titolo
FindWindowW = user32.FindWindowW
FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
FindWindowW.restype = wintypes.HWND

# EnumWindows per enumerare tutte le finestre
EnumWindows = user32.EnumWindows
EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
EnumWindows.restype = wintypes.BOOL

# GetWindowTextW per ottenere titolo finestra
GetWindowTextW = user32.GetWindowTextW
GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GetWindowTextW.restype = ctypes.c_int

# GetWindowTextLengthW per lunghezza titolo
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowTextLengthW.argtypes = [wintypes.HWND]
GetWindowTextLengthW.restype = ctypes.c_int

GetAncestor = user32.GetAncestor
GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
GetAncestor.restype = wintypes.HWND

PostMessageW = user32.PostMessageW
PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
PostMessageW.restype = wintypes.BOOL
# ============================================

# Variabili globali
user_id = None
current_creds = None
prop_positions = {}  # {ticket: position_data}
broker_positions = {}  # {ticket: position_data}
is_running = True
phase3_starting_balance = None  # Balance iniziale per fase 3
autotrading_ok = True  # Flag per stato autotrading


# -------- Gestione rete --------
def has_internet(timeout: float = 1.0) -> bool:
    """Controlla se c'è connessione internet"""
    try:
        with socket.create_connection((NET_CHECK_HOST, NET_CHECK_PORT), timeout=timeout):
            return True
    except Exception:
        return False


class NetWatcher(threading.Thread):
    """Thread che monitora lo stato della connessione internet"""
    def __init__(self, interval: float = NET_CHECK_INTERVAL):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop = threading.Event()
        self.online = has_internet()
        self._last_warn = 0.0

    def run(self):
        while not self._stop.is_set():
            self.online = has_internet()
            if not self.online:
                self.warn_offline()
            time.sleep(self.interval)

    def stop(self):
        self._stop.set()

    def warn_offline(self):
        now = time.time()
        if now - self._last_warn >= WARN_COOLDOWN:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Internet mancante: impossibile comunicare con il database (verrà ritentato automaticamente)")
            self._last_warn = now


class AutoTradingWatcher(threading.Thread):
    """Thread che controlla lo stato dell'autotrading ogni 10 secondi e tenta di riabilitarlo automaticamente"""
    def __init__(self, vps_ip: str, interval: float = AUTOTRADING_CHECK_INTERVAL):
        super().__init__(daemon=True)
        self.interval = interval
        self.vps_ip = vps_ip
        self._stop = threading.Event()

    def run(self):
        global autotrading_ok
        while not self._stop.is_set():
            try:
                # Controlla PROP
                prop_terminal_info = mt5_prop.terminal_info()
                prop_ok = prop_terminal_info and hasattr(prop_terminal_info, 'trade_allowed') and prop_terminal_info.trade_allowed

                # Controlla BROKER
                broker_terminal_info = mt5_broker.terminal_info()
                broker_ok = broker_terminal_info and hasattr(broker_terminal_info, 'trade_allowed') and broker_terminal_info.trade_allowed

                # Se uno dei due è disabilitato
                if not prop_ok or not broker_ok:
                    if autotrading_ok:  # Solo se era ok prima
                        autotrading_ok = False
                        accounts_disabled = []
                        accounts_to_enable = []

                        if not prop_ok:
                            accounts_disabled.append("PROP")
                            accounts_to_enable.append(("PROP", mt5_prop))
                        if not broker_ok:
                            accounts_disabled.append("BROKER")
                            accounts_to_enable.append(("BROKER", mt5_broker))

                        print(f"\n{'=' * 60}")
                        print(f"⚠ ATTENZIONE: AutoTrading DISABILITATO su {', '.join(accounts_disabled)}")
                        print(f"{'=' * 60}")

                        # Tenta di riabilitare automaticamente
                        print("→ Tentativo di riabilitazione automatica tramite API Windows...")

                        all_enabled = True
                        failed_accounts = []

                        for account_name, mt5_instance in accounts_to_enable:
                            # Ottieni account_id dalla istanza
                            account_info = mt5_instance.account_info()
                            if account_info:
                                account_id = account_info.login
                                success = enable_algo_trading_via_api(mt5_instance, account_name, account_id, max_attempts=2)
                                if not success:
                                    all_enabled = False
                                    failed_accounts.append(account_name)
                            else:
                                all_enabled = False
                                failed_accounts.append(account_name)

                        # Se tutti gli account sono stati abilitati con successo
                        if all_enabled:
                            autotrading_ok = True
                            print(f"\n✓ AutoTrading riabilitato automaticamente su tutti gli account!")
                            print(f"{'=' * 60}\n")
                        else:
                            # Se qualche account non è stato abilitato, invia email all'admin
                            print(f"\n✗ Impossibile abilitare AutoTrading automaticamente su: {', '.join(failed_accounts)}")
                            print("→ Invio email all'admin...")

                            subject = "URGENTE: AutoTrading disabilitato su MT5 - Intervento richiesto"
                            message = (
                                f"L'AutoTrading è stato disabilitato su IP VPS: {self.vps_ip}\n\n"
                                f"Account interessati: {', '.join(failed_accounts)}\n\n"
                                f"Il sistema ha tentato di riabilitarlo automaticamente ma ha fallito.\n"
                                f"Per favore, riabilita MANUALMENTE l'AutoTrading su MT5.\n\n"
                                f"Il trading è completamente bloccato fino al ripristino."
                            )
                            config.send_email_to_admin(None, subject, message)
                            print("✓ Email inviata all'admin")
                            print(f"{'=' * 60}\n")
                else:
                    if not autotrading_ok:  # Era disabilitato ma ora è ok
                        autotrading_ok = True
                        print(f"\n{'=' * 60}")
                        print("✓ AutoTrading RIABILITATO su tutti gli account")
                        print(f"{'=' * 60}\n")

            except Exception as e:
                print(f"⚠ Errore controllo autotrading: {e}")
                import traceback
                traceback.print_exc()

            time.sleep(self.interval)

    def stop(self):
        self._stop.set()


def get_trade_params(fase, size):
    """
    Restituisce SL, TP e lotti in base alla fase
    
    Args:
        fase: 1, 2, 3, o 4
        size: size dell'account (es. 100000 per 100k)
    
    Returns: dict con parametri
    """
    # Al momento solo per 100k
    if size != 100000:
        print(f"ATTENZIONE: Size {size} non implementata, uso parametri fase 1 di default")
        fase = 1
    
    params = {
        1: {
            'sl_pips': 1150,
            'tp_pips': 1250,
            'prop_lots': 2.0,
            'broker_lots': 0.14,
            'broker_enabled': True
        },
        2: {
            'sl_pips': 1150,
            'tp_pips': 625,
            'prop_lots': 2.0,
            'broker_lots': 0.3,
            'broker_enabled': True
        },
        3: {
            'sl_pips': None,  # Niente SL
            'tp_pips': None,  # Chiude a 50$ profit
            'prop_lots': 0.5,
            'broker_lots': 0,
            'broker_enabled': False,  # Solo prop
            'target_profit': 50.0
        },
        4: {
            'sl_pips': 1150,
            'tp_pips': 625,
            'prop_lots': 2.0,
            'broker_lots': 0.4,
            'broker_enabled': True
        }
    }
    
    return params.get(fase, params[1])


def find_mt5_window_by_account(account_id, debug=True):
    """
    Trova la finestra MT5 tramite il numero di account nel titolo

    Args:
        account_id: ID account MT5 (es. 12345678)
        debug: Se True, stampa tutte le finestre trovate (default True)

    Returns:
        Handle della finestra MT5 o None se non trovata
    """
    found_hwnd = None
    account_str = str(account_id)
    candidates = []  # Per debug

    def enum_callback(hwnd, lparam):
        nonlocal found_hwnd
        # Ottieni lunghezza titolo
        length = GetWindowTextLengthW(hwnd)
        if length == 0:
            return True  # Continua enumerazione

        # Ottieni titolo finestra
        buffer = ctypes.create_unicode_buffer(length + 1)
        GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value

        # Cerca finestre che contengono l'account ID
        if account_str in title:
            candidates.append((hwnd, title))

            # Formati MT5 possibili:
            # 1. "12345678 - MetaQuotes-Demo: Conto Demo..." (formato standard)
            # 2. "MetaTrader 5 - 12345678@Broker-Demo" (formato alternativo)
            # 3. "12345678@Broker-Demo" (formato corto)

            # Priorità 1: Inizia con account ID seguito da " - " (formato più comune)
            if title.startswith(account_str + " - "):
                if not found_hwnd:
                    found_hwnd = hwnd

            # Priorità 2: Contiene @ (formato con broker)
            elif "@" in title and account_str in title:
                if not found_hwnd:
                    found_hwnd = hwnd

            # Priorità 3: Contiene "MetaTrader" o "MetaQuotes"
            elif ("MetaTrader" in title or "MetaQuotes" in title) and account_str in title:
                if not found_hwnd:
                    found_hwnd = hwnd

        return True  # Continua enumerazione

    # Enumera tutte le finestre
    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    EnumWindows(EnumWindowsProc(enum_callback), 0)

    # Debug: stampa tutte le finestre candidate
    if debug and candidates:
        print(f"  [DEBUG] Finestre trovate per account {account_id}:")
        for hwnd, title in candidates:
            selected = " <-- SELEZIONATA" if hwnd == found_hwnd else ""
            print(f"    HWND={hwnd}: '{title}'{selected}")
    elif debug and not candidates:
        print(f"  [DEBUG] Nessuna finestra trovata con account ID {account_id}")

    return found_hwnd


def get_mt5_main_window_handle(mt5_instance, account_id):
    """
    Ottiene l'handle della finestra principale di MT5

    Args:
        mt5_instance: Istanza MT5 (mt5_prop o mt5_broker)
        account_id: ID account per trovare la finestra giusta

    Returns:
        Handle della finestra principale MT5 o None se errore
    """
    try:
        # Metodo 1: Cerca finestra tramite account ID nel titolo
        hwnd = find_mt5_window_by_account(account_id)
        if hwnd:
            return hwnd

        # Metodo 2: Prova con chart_get_integer se disponibile
        try:
            if hasattr(mt5_instance, 'chart_get_integer') and hasattr(mt5_instance, 'CHART_WINDOW_HANDLE'):
                chart_handle = mt5_instance.chart_get_integer(0, mt5_instance.CHART_WINDOW_HANDLE)
                if chart_handle != 0:
                    main_window = GetAncestor(chart_handle, GA_ROOT)
                    if main_window != 0:
                        return main_window
        except:
            pass

        return None

    except Exception as e:
        print(f"  ⚠ Errore ottenimento handle finestra: {e}")
        return None


def enable_algo_trading_via_api(mt5_instance, account_name, account_id, max_attempts=2):
    """
    Abilita Algo Trading usando Windows API (PostMessage)

    Args:
        mt5_instance: Istanza MT5 (mt5_prop o mt5_broker)
        account_name: Nome account (per logging)
        account_id: ID account per trovare la finestra
        max_attempts: Numero massimo di tentativi

    Returns:
        bool: True se abilitato con successo, False altrimenti
    """
    try:
        # Verifica stato attuale
        terminal_info = mt5_instance.terminal_info()
        if not terminal_info:
            print(f"  ✗ Impossibile ottenere terminal_info per {account_name}")
            return False

        current_state = terminal_info.trade_allowed

        if current_state:
            print(f"  ✓ AlgoTrading già abilitato su {account_name}")
            return True

        print(f"  → AlgoTrading disabilitato su {account_name}, tento di abilitarlo...")

        # Ottieni handle finestra principale
        main_window = get_mt5_main_window_handle(mt5_instance, account_id)

        if not main_window:
            print(f"  ✗ Impossibile ottenere handle finestra per {account_name} (Account ID: {account_id})")
            return False

        # Tenta di abilitare con retry
        for attempt in range(1, max_attempts + 1):
            print(f"  → Tentativo {attempt}/{max_attempts} di abilitazione...")

            # Invia comando toggle Algo Trading
            result = PostMessageW(main_window, WM_COMMAND, MT5_WMCMD_EXPERTS, 0)

            if not result:
                print(f"  ⚠ PostMessageW ha restituito False (tentativo {attempt})")

            # Attendi elaborazione comando
            time.sleep(1.5)

            # Verifica nuovo stato
            terminal_info = mt5_instance.terminal_info()
            if terminal_info and terminal_info.trade_allowed:
                print(f"  ✓ AlgoTrading abilitato con successo su {account_name}!")
                return True

            print(f"  ✗ Tentativo {attempt} fallito, stato rimane disabilitato")

            # Attendi prima del prossimo tentativo
            if attempt < max_attempts:
                time.sleep(1)

        print(f"  ✗ Impossibile abilitare AlgoTrading su {account_name} dopo {max_attempts} tentativi")
        return False

    except Exception as e:
        print(f"  ✗ Errore abilitazione AlgoTrading {account_name}: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_and_enable_autotrading(mt5_instance, mt5_path, account_name, account_id):
    """
    Controlla se AlgoTrading è abilitato e lo abilita se necessario tramite Windows API.

    Args:
        mt5_instance: Istanza MT5
        mt5_path: Path al terminal64.exe (non usato, mantenuto per compatibilità)
        account_name: Nome account (PROP/BROKER)
        account_id: ID account per trovare la finestra

    Returns:
        True: già abilitato
        False: abilitato con successo via API
        None: impossibile abilitare (richiede intervento manuale)
    """
    try:
        # Controlla se autotrading è già abilitato
        terminal_info = mt5_instance.terminal_info()

        if terminal_info and hasattr(terminal_info, 'trade_allowed'):
            if terminal_info.trade_allowed:
                print(f"  ✓ AlgoTrading già abilitato su {account_name}")
                return True

        # Tenta di abilitare tramite Windows API
        success = enable_algo_trading_via_api(mt5_instance, account_name, account_id, max_attempts=2)

        if success:
            return False  # Abilitato ora (non era abilitato prima)
        else:
            return None  # Impossibile abilitare

    except Exception as e:
        print(f"  ✗ Errore controllo AlgoTrading {account_name}: {e}")
        return None


def login_accounts(creds):
    """
    Effettua il login su prop e broker

    Returns:
        True se successo
        False se credenziali errate (non solleva eccezione, solo ritorna False)

    Raises:
        Exception per altri errori tecnici
    """
    print("\n" + "=" * 60)
    print("LOGIN CONTI MT5")
    print("=" * 60)

    # Fix permessi WebView2 per PROP
    #prop_path = r'C:\Program Files\MT5_prop\terminal64.exe'
    #print("\n→ Fix permessi WebView2 per PROP...")
    #fix_mt5_webview_permissions(prop_path)

    # Fix permessi WebView2 per BROKER
    #broker_path = r'C:\Program Files\MT5_broker\terminal64.exe'
    #print("\n→ Fix permessi WebView2 per BROKER...")
    #fix_mt5_webview_permissions(broker_path)

    # Login PROP
    print(f"\nLogin PROP: {creds['prop']['account_id']} @ {creds['prop']['server']}")
    print("Inizializzazione in corso...")

    try:
        # NON usare portable=True per evitare errori permessi WebView2
        prop_ok = mt5_prop.initialize(
            path=r'C:\Program Files\MT5_prop\terminal64.exe',
            login=int(creds['prop']['account_id']),
            password=creds['prop']['password'],
            server=creds['prop']['server'],
            portable=False,
            timeout=30000  # 30 secondi timeout
        )
    except Exception as e:
        raise Exception(f"Errore durante initialize PROP: {e}")

    if not prop_ok:
        error = mt5_prop.last_error()
        # Errori comuni per credenziali errate:
        # -6 = Authorization failed
        # 2 = Common error
        # 10004 = Invalid account
        # 10013 = Invalid account credentials
        error_code = error[0] if error else None
        error_str = str(error).lower() if error else ""

        if error_code in [-6, 2, 10004, 10013] or 'authorization' in error_str or 'invalid account' in error_str:
            print(f"✗ CREDENZIALI PROP ERRATE: {error}")
            return False
        raise Exception(f"Impossibile effettuare login su PROP: {error}")

    # Verifica connessione PROP
    print("Verifica connessione PROP...")
    time.sleep(2)  # Attendi che la connessione si stabilizzi

    account_info_prop = mt5_prop.account_info()
    if account_info_prop is None:
        error = mt5_prop.last_error()
        mt5_prop.shutdown()
        raise Exception(f"PROP connesso ma account_info fallisce: {error}")

    print(f"✓ Login PROP completato - Account: {account_info_prop.login}, Balance: {account_info_prop.balance}")

    # Login BROKER
    print(f"\nLogin BROKER: {creds['broker']['account_id']} @ {creds['broker']['server']}")
    print("Inizializzazione in corso...")

    try:
        # NON usare portable=True per evitare errori permessi WebView2
        broker_ok = mt5_broker.initialize(
            path=r'C:\Program Files\MT5_broker\terminal64.exe',
            login=int(creds['broker']['account_id']),
            password=creds['broker']['password'],
            server=creds['broker']['server'],
            portable=False,
            timeout=30000  # 30 secondi timeout
        )
    except Exception as e:
        mt5_prop.shutdown()
        raise Exception(f"Errore durante initialize BROKER: {e}")

    if not broker_ok:
        error = mt5_broker.last_error()
        # Errori comuni per credenziali errate:
        # -6 = Authorization failed
        # 2 = Common error
        # 10004 = Invalid account
        # 10013 = Invalid account credentials
        error_code = error[0] if error else None
        error_str = str(error).lower() if error else ""

        if error_code in [-6, 2, 10004, 10013] or 'authorization' in error_str or 'invalid account' in error_str:
            print(f"✗ CREDENZIALI BROKER ERRATE: {error}")
            mt5_prop.shutdown()
            return False
        mt5_prop.shutdown()
        raise Exception(f"Impossibile effettuare login su BROKER: {error}")

    # Verifica connessione BROKER
    print("Verifica connessione BROKER...")
    time.sleep(2)  # Attendi che la connessione si stabilizzi

    account_info_broker = mt5_broker.account_info()
    if account_info_broker is None:
        error = mt5_broker.last_error()
        mt5_prop.shutdown()
        mt5_broker.shutdown()
        raise Exception(f"BROKER connesso ma account_info fallisce: {error}")

    print(f"✓ Login BROKER completato - Account: {account_info_broker.login}, Balance: {account_info_broker.balance}")

    # Controllo AlgoTrading con tentativo automatico di abilitazione
    print("\n→ Controllo AlgoTrading...")
    prop_at = check_and_enable_autotrading(
        mt5_prop,
        r'C:\Program Files\MT5_prop\terminal64.exe',
        "PROP",
        int(creds['prop']['account_id'])
    )
    broker_at = check_and_enable_autotrading(
        mt5_broker,
        r'C:\Program Files\MT5_broker\terminal64.exe',
        "BROKER",
        int(creds['broker']['account_id'])
    )

    # Se uno dei due non è riuscito ad abilitarsi (None = fallito), invia email e aspetta
    if prop_at is None or broker_at is None:
        print("\n" + "=" * 60)
        print("⚠ ATTENZIONE: Impossibile abilitare AutoTrading automaticamente")
        print("   Invio email all'admin...")
        print("=" * 60)

        # Prepara lista account problematici
        accounts_list = []
        if prop_at is None:
            accounts_list.append(f"PROP ({creds['prop']['account_id']})")
        if broker_at is None:
            accounts_list.append(f"BROKER ({creds['broker']['account_id']})")

        # Invia email all'admin
        subject = "URGENTE: AutoTrading non abilitato su MT5 - Intervento richiesto"
        message = (
            f"L'AutoTrading non è abilitato sui seguenti account:\n"
            f"{', '.join(accounts_list)}\n\n"
            f"Il sistema ha tentato di abilitarlo automaticamente ma ha fallito dopo 2 tentativi.\n"
            f"Per favore, abilita MANUALMENTE l'AutoTrading su MT5 per questi account.\n\n"
            f"Il sistema resterà in attesa finché l'AutoTrading non sarà abilitato."
        )
        config.send_email_to_admin(None, subject, message)

        print("\n✓ Email inviata all'admin")
        print("\n→ In attesa che l'admin abiliti l'AutoTrading...")
        print("   (Controllo ogni 30 secondi...)\n")

        # Loop di attesa finché l'autotrading non è abilitato
        while True:
            time.sleep(30)

            # Ricontrolla lo stato e riprova ad abilitare
            prop_check = check_and_enable_autotrading(
                mt5_prop,
                r'C:\Program Files\MT5_prop\terminal64.exe',
                "PROP",
                int(creds['prop']['account_id'])
            )
            broker_check = check_and_enable_autotrading(
                mt5_broker,
                r'C:\Program Files\MT5_broker\terminal64.exe',
                "BROKER",
                int(creds['broker']['account_id'])
            )

            # Se entrambi sono OK (True = già abilitato, False = appena abilitato), esci dal loop
            if prop_check is not None and broker_check is not None:
                print("\n✓ AutoTrading abilitato su entrambi gli account!")
                break
            else:
                accounts_waiting = []
                if prop_check is None:
                    accounts_waiting.append("PROP")
                if broker_check is None:
                    accounts_waiting.append("BROKER")
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Ancora in attesa per: {', '.join(accounts_waiting)}")

        print("=" * 60)

    print("\n" + "=" * 60)

    return True


def calculate_delay(account_prop_id):
    """
    Calcola un ritardo randomico basato sull'ID
    ID più basso = apre prima

    Args:
        account_prop_id: ID dalla tabella account_prop

    Returns: secondi di ritardo (0-5)
    """
    # Usa l'ID come seed per consistenza
    random.seed(account_prop_id)
    # Ridotto da 0-60s a 0-5s per apertura più rapida
    delay = random.uniform(0, 10)
    random.seed()  # Reset seed

    return delay


def find_symbol_on_mt5(mt5_instance, symbol, account_name):
    """
    Cerca un simbolo su MT5, provando anche varianti comuni del nome.
    Returns: nome del simbolo trovato o None
    """
    # Prova il nome originale
    if mt5_instance.symbol_select(symbol, True):
        print("Simbolo ", symbol, " trovato")
        return symbol

    print(f"  ℹ {account_name}: Simbolo '{symbol}' non trovato, provo varianti...")

    # Prova varianti comuni
    alternate_symbols = [
        f"{symbol}.i",
        f"{symbol}m",
        f"{symbol}.raw",
        f"{symbol}#",
        f"{symbol}.a",
        f"{symbol}.c"
    ]

    for alt_symbol in alternate_symbols:
        print(f"    → {alt_symbol}...", end=" ")
        if mt5_instance.symbol_select(alt_symbol, True):
            print("✓")
            return alt_symbol
        print("✗")

    # Ultimo tentativo: cerca tutti i simboli che iniziano con lo stesso prefisso
    all_symbols = mt5_instance.symbols_get()
    if all_symbols:
        # Estrai solo il prefisso base (es. XAUUSD da XAUUSD.i)
        base = symbol.split('.')[0].split('#')[0].upper()
        matches = [s.name for s in all_symbols if base in s.name.upper()]

        if matches:
            print(f"  ℹ {account_name}: Simboli simili trovati: {matches[:5]}")
            # Prova il primo match
            if mt5_instance.symbol_select(matches[0], True):
                print(f"  ✓ Uso: {matches[0]}")
                return matches[0]

    print(f"  ✗ {account_name}: Simbolo non trovato")
    return None


def normalize_price(price, symbol_info):
    """Normalizza il prezzo in base ai digits del simbolo"""
    if price == 0:
        return 0
    digits = symbol_info.digits
    return round(price, digits)


def check_and_fix_stops(price, sl, tp, symbol_info, order_type_name):
    """
    Verifica e corregge SL/TP in base alle regole del broker.
    Retcode 10016 = TRADE_RETCODE_INVALID_STOPS
    """
    # Ottieni distanza minima stops (in points)
    stops_level = symbol_info.trade_stops_level
    point = symbol_info.point

    # Se stops_level == 0, il broker non ha restrizioni
    if stops_level == 0:
        stops_level = 10  # Usa un minimo di sicurezza

    min_distance = stops_level * point

    # Normalizza prezzi
    sl = normalize_price(sl, symbol_info)
    tp = normalize_price(tp, symbol_info)

    if sl != 0:
        # Verifica distanza SL
        if order_type_name == "BUY":
            if price - sl < min_distance:
                sl = normalize_price(price - min_distance, symbol_info)
                print(f"  ⚠ SL corretto per rispettare stops_level: {sl}")
        else:  # SELL
            if sl - price < min_distance:
                sl = normalize_price(price + min_distance, symbol_info)
                print(f"  ⚠ SL corretto per rispettare stops_level: {sl}")

    if tp != 0:
        # Verifica distanza TP
        if order_type_name == "BUY":
            if tp - price < min_distance:
                tp = normalize_price(price + min_distance, symbol_info)
                print(f"  ⚠ TP corretto per rispettare stops_level: {tp}")
        else:  # SELL
            if price - tp < min_distance:
                tp = normalize_price(price - min_distance, symbol_info)
                print(f"  ⚠ TP corretto per rispettare stops_level: {tp}")

    return sl, tp


def open_order_prop(symbol, direction, params):
    """
    Apre ordine su PROP
    """
    try:
        symbol_info = mt5_prop.symbol_info(symbol)
        if not symbol_info:
            print(f"✗ Simbolo {symbol} non disponibile su PROP")
            return None
        
        if not mt5_prop.symbol_select(symbol, True):
            print(f"✗ Impossibile selezionare {symbol} su PROP")
            return None
        
        tick = mt5_prop.symbol_info_tick(symbol)
        if not tick:
            print(f"✗ Nessun tick disponibile per {symbol}")
            return None
        
        # Calcola prezzi
        if direction == "buy":
            price = tick.ask
            order_type = mt5_prop.ORDER_TYPE_BUY
            order_type_name = "BUY"
            if params['sl_pips']:
                sl = price - params['sl_pips'] * symbol_info.point
            else:
                sl = 0
            if params['tp_pips']:
                tp = price + params['tp_pips'] * symbol_info.point
            else:
                tp = 0
        else:
            price = tick.bid
            order_type = mt5_prop.ORDER_TYPE_SELL
            order_type_name = "SELL"
            if params['sl_pips']:
                sl = price + params['sl_pips'] * symbol_info.point
            else:
                sl = 0
            if params['tp_pips']:
                tp = price - params['tp_pips'] * symbol_info.point
            else:
                tp = 0

        # Normalizza prezzo
        price = normalize_price(price, symbol_info)

        # Fix SL/TP per evitare errore 10016
        sl, tp = check_and_fix_stops(price, sl, tp, symbol_info, order_type_name)

        # DEBUG: Mostra tutti i dettagli
        print(f"\n  📊 PROP {order_type_name} {symbol}:")
        print(f"     Prezzo apertura: {price}")
        print(f"     Stop Loss: {sl if sl != 0 else 'Nessuno'} (SL pips: {params['sl_pips']})")
        print(f"     Take Profit: {tp if tp != 0 else 'Nessuno'} (TP pips: {params['tp_pips']})")
        print(f"     Volume: {params['prop_lots']} lotti")
        print(f"     Spread: {symbol_info.spread} points")
        print(f"     Stops Level: {symbol_info.trade_stops_level} points")
        print(f"     Point: {symbol_info.point}, Digits: {symbol_info.digits}")

        request = {
            "action": mt5_prop.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(params['prop_lots']),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234000,
            "comment": "slave_prop",
            "type_filling": mt5_prop.ORDER_FILLING_IOC
        }

        result = mt5_prop.order_send(request)

        if result is None or result.retcode != mt5_prop.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            comment = result.comment if result else "None"
            print(f"✗ Errore apertura PROP: retcode={retcode}, comment={comment}")
            if result:
                print(f"   Dettagli richiesta: {result.request}")
            return None

        print(f"✓ Ordine PROP aperto: ticket={result.order}, {direction.upper()}, {params['prop_lots']} lotti")
        return result.order
    
    except Exception as e:
        print(f"✗ Eccezione apertura PROP: {e}")
        return None


def open_order_broker(symbol, direction, params):
    """
    Apre ordine su BROKER (OPPOSTO al master)
    """
    if not params['broker_enabled']:
        return None  # Fase 3: non apre su broker

    try:
        # Cerca il simbolo (con varianti se necessario)
        symbol = find_symbol_on_mt5(mt5_broker, symbol, "BROKER")
        if not symbol:
            return None

        # Attendi un momento per permettere a MT5 di aggiornare i dati
        time.sleep(0.3)

        # Ottieni info simbolo con retry
        symbol_info = None
        for attempt in range(3):
            symbol_info = mt5_broker.symbol_info(symbol)
            if symbol_info and symbol_info.point > 0:
                break
            print(f"  ⚠ Tentativo {attempt + 1}/3: symbol_info non valido, riprovo...")
            time.sleep(0.3)

        if not symbol_info:
            print(f"✗ Simbolo {symbol} non disponibile su BROKER (symbol_info None)")
            print(f"   Ultimo errore MT5: {mt5_broker.last_error()}")
            return None

        # Verifica che symbol_info contenga dati validi
        if symbol_info.point == 0 or symbol_info.digits == 0:
            print(f"✗ Symbol info invalido per {symbol}: point={symbol_info.point}, digits={symbol_info.digits}")
            return None

        # Ottieni tick con retry
        tick = None
        for attempt in range(3):
            tick = mt5_broker.symbol_info_tick(symbol)
            if tick and (tick.bid > 0 or tick.ask > 0):
                break
            print(f"  ⚠ Tentativo {attempt + 1}/3: tick non valido, riprovo...")
            time.sleep(0.3)

        if not tick:
            print(f"✗ Nessun tick disponibile per {symbol}")
            print(f"   Ultimo errore MT5: {mt5_broker.last_error()}")
            return None

        # Verifica che tick contenga prezzi validi
        if tick.bid == 0 and tick.ask == 0:
            print(f"✗ Tick invalido per {symbol}: bid={tick.bid}, ask={tick.ask}")
            return None
        
        # INVERTI LA DIREZIONE
        if direction == "buy":
            # Master fa BUY -> Broker fa SELL
            price = tick.bid
            order_type = mt5_broker.ORDER_TYPE_SELL
            order_type_name = "SELL"
            if params['sl_pips']:
                sl = price + params['sl_pips'] * symbol_info.point
            else:
                sl = 0
            if params['tp_pips']:
                tp = price - params['tp_pips'] * symbol_info.point
            else:
                tp = 0
        else:
            # Master fa SELL -> Broker fa BUY
            price = tick.ask
            order_type = mt5_broker.ORDER_TYPE_BUY
            order_type_name = "BUY"
            if params['sl_pips']:
                sl = price - params['sl_pips'] * symbol_info.point
            else:
                sl = 0
            if params['tp_pips']:
                tp = price + params['tp_pips'] * symbol_info.point
            else:
                tp = 0

        # Normalizza prezzo
        price = normalize_price(price, symbol_info)

        # Fix SL/TP per evitare errore 10016
        sl, tp = check_and_fix_stops(price, sl, tp, symbol_info, order_type_name)

        # DEBUG: Mostra tutti i dettagli
        print(f"\n  📊 BROKER {order_type_name} {symbol} (opposto al master):")
        print(f"     Bid: {tick.bid}, Ask: {tick.ask}")
        print(f"     Prezzo apertura: {price}")
        print(f"     Stop Loss: {sl if sl != 0 else 'Nessuno'} (SL pips: {params['sl_pips']})")
        print(f"     Take Profit: {tp if tp != 0 else 'Nessuno'} (TP pips: {params['tp_pips']})")
        print(f"     Volume: {params['broker_lots']} lotti")
        print(f"     Spread: {symbol_info.spread} points")
        print(f"     Stops Level: {symbol_info.trade_stops_level} points")
        print(f"     Point: {symbol_info.point}, Digits: {symbol_info.digits}")

        request = {
            "action": mt5_broker.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(params['broker_lots']),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": 234001,
            "comment": "slave_broker",
            "type_filling": mt5_broker.ORDER_FILLING_IOC
        }

        result = mt5_broker.order_send(request)

        if result is None or result.retcode != mt5_broker.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            comment = result.comment if result else "None"
            print(f"✗ Errore apertura BROKER: retcode={retcode}, comment={comment}")
            if result:
                print(f"   Dettagli richiesta: {result.request}")
            return None

        opposite = "SELL" if direction == "buy" else "BUY"
        print(f"✓ Ordine BROKER aperto: ticket={result.order}, {opposite}, {params['broker_lots']} lotti")
        return result.order
    
    except Exception as e:
        print(f"✗ Eccezione apertura BROKER: {e}")
        return None


def close_position(position, mt5_instance, account_name):
    """
    Chiude una posizione specifica
    """
    try:
        symbol = position.symbol
        ticket = position.ticket
        volume = position.volume
        direction = position.type
        
        tick = mt5_instance.symbol_info_tick(symbol)
        if not tick:
            print(f"✗ Tick non disponibile per chiusura {symbol}")
            return False
        
        price = tick.bid if direction == 0 else tick.ask
        order_type = mt5_instance.ORDER_TYPE_SELL if direction == 0 else mt5_instance.ORDER_TYPE_BUY
        
        request = {
            "action": mt5_instance.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 234000 if account_name == "PROP" else 234001,
            "comment": f"close_{account_name.lower()}",
            "type_filling": mt5_instance.ORDER_FILLING_IOC
        }
        
        result = mt5_instance.order_send(request)
        
        if result is None or result.retcode != mt5_instance.TRADE_RETCODE_DONE:
            retcode = result.retcode if result else "None"
            print(f"✗ Errore chiusura {account_name} ticket={ticket}: retcode={retcode}")
            return False
        
        print(f"✓ Posizione {account_name} chiusa: ticket={ticket}")
        return True
    
    except Exception as e:
        print(f"✗ Eccezione chiusura {account_name}: {e}")
        return False


def close_all_positions(mt5_instance, account_name):
    """
    Chiude tutte le posizioni aperte su un'istanza MT5
    """
    try:
        positions = mt5_instance.positions_get()
        
        if not positions:
            return
        
        print(f"\nChiusura di {len(positions)} posizioni su {account_name}...")
        
        for pos in positions:
            close_position(pos, mt5_instance, account_name)
    
    except Exception as e:
        print(f"✗ Errore chiusura tutte le posizioni {account_name}: {e}")


def check_phase3_profit():
    """
    Controlla il profitto TOTALE dell'account PROP dall'inizio della fase 3.
    Chiude TUTTE le posizioni se il profitto >= $50.
    Solo per fase 3.
    """
    global phase3_starting_balance

    try:
        # Ottieni balance attuale
        account_info = mt5_prop.account_info()
        if not account_info:
            print("⚠ Impossibile ottenere account_info per fase 3")
            return False

        # Se non abbiamo ancora salvato il balance iniziale, salvalo ora
        if phase3_starting_balance is None:
            phase3_starting_balance = account_info.balance
            print(f"\n📊 Fase 3: Balance iniziale salvato: ${phase3_starting_balance:.2f}")
            return False

        # Calcola profitto totale
        current_balance = account_info.balance
        total_profit = current_balance - phase3_starting_balance

        # DEBUG: Mostra stato profitto ogni check
        positions = mt5_prop.positions_get()
        num_positions = len(positions) if positions else 0

        print(f"[Fase 3] Balance: ${current_balance:.2f} | Profitto: ${total_profit:.2f} | Posizioni: {num_positions}")

        # Se raggiunto target, chiudi tutto
        if total_profit >= 50.0:
            print(f"\n" + "=" * 60)
            print(f"✓ FASE 3: TARGET RAGGIUNTO!")
            print(f"=" * 60)
            print(f"  Balance iniziale: ${phase3_starting_balance:.2f}")
            print(f"  Balance corrente: ${current_balance:.2f}")
            print(f"  Profitto totale: ${total_profit:.2f}")
            print(f"  Chiusura di TUTTE le posizioni...")

            # Chiudi tutte le posizioni su PROP
            close_all_positions(mt5_prop, "PROP")

            # Chiudi anche il broker (se ci sono posizioni)
            close_all_positions(mt5_broker, "BROKER")

            # Reset balance iniziale per il prossimo ciclo
            phase3_starting_balance = None

            print(f"=" * 60)
            return True  # Segnala che la fase è finita

        return False

    except Exception as e:
        print(f"✗ Errore check fase 3: {e}")
        import traceback
        traceback.print_exc()
        return False


def monitor_phase_conditions(fase):
    """
    Monitora le condizioni di fine fase e notifica l'utente.
    Ogni fase inizia con 100k di balance.

    FASE 1: Finisce quando PROP >= 110k (passata) o <= 90k (bruciata)
    FASE 2: Finisce quando PROP >= 105k (passata) o <= 90k (bruciata)
    FASE 3: Finisce quando PROP >= 100050 (passata) [gestita da check_phase3_profit]
    FASE 4: Finisce quando PROP >= 105k (passata) o <= 90k (bruciata PROP)
            o BROKER <= 0 (bruciato BROKER)

    Returns:
        None se tutto ok
        dict con status e messaggio se fase finita
    """
    global user_id

    try:
        # Ottieni balance PROP
        account_info_prop = mt5_prop.account_info()
        if not account_info_prop:
            return None

        prop_balance = account_info_prop.balance

        # Ottieni balance BROKER (solo per fase 4)
        broker_balance = None
        if fase == 4:
            account_info_broker = mt5_broker.account_info()
            if account_info_broker:
                broker_balance = account_info_broker.balance

        # FASE 1: +10% o -10%
        if fase == 1:
            if prop_balance >= 110000:
                return {
                    'status': 'passed',
                    'phase': 1,
                    'balance': prop_balance,
                    'message': f'Congratulazioni! Hai superato la Fase 1 con un balance di ${prop_balance:,.2f}. '
                               f'Ora puoi passare alla Fase 2. Aggiorna le credenziali nel sito e riavvia il trading.'
                }
            elif prop_balance <= 90000:
                return {
                    'status': 'failed',
                    'phase': 1,
                    'balance': prop_balance,
                    'message': f'La Fase 1 è fallita. Il conto PROP è stato bruciato con balance ${prop_balance:,.2f}. '
                               f'Ricarica il conto e riprova.'
                }

        # FASE 2: +5% o -10%
        elif fase == 2:
            if prop_balance >= 105000:
                return {
                    'status': 'passed',
                    'phase': 2,
                    'balance': prop_balance,
                    'message': f'Congratulazioni! Hai superato la Fase 2 con un balance di ${prop_balance:,.2f}. '
                               f'Ora puoi passare alla Fase 3. Aggiorna le credenziali nel sito e riavvia il trading.'
                }
            elif prop_balance <= 90000:
                return {
                    'status': 'failed',
                    'phase': 2,
                    'balance': prop_balance,
                    'message': f'La Fase 2 è fallita. Il conto PROP è stato bruciato con balance ${prop_balance:,.2f}. '
                               f'Ricarica il conto e riprova.'
                }

        # FASE 3: +50$ (gestito da check_phase3_profit, qui solo per completezza)
        elif fase == 3:
            if prop_balance >= 100050:
                return {
                    'status': 'passed',
                    'phase': 3,
                    'balance': prop_balance,
                    'message': f'Congratulazioni! Hai superato la Fase 3 con un balance di ${prop_balance:,.2f}. '
                               f'Ora puoi passare alla Fase 4. Aggiorna le credenziali nel sito e riavvia il trading.'
                }

        # FASE 4: +5% PROP o -10% PROP o BROKER bruciato
        elif fase == 4:
            if prop_balance >= 105000:
                return {
                    'status': 'passed',
                    'phase': 4,
                    'balance': prop_balance,
                    'message': f'Congratulazioni! Hai completato la Fase 4 (FINALE) con un balance PROP di ${prop_balance:,.2f}. '
                               f'Hai completato con successo tutto il percorso!'
                }
            elif prop_balance <= 90000:
                return {
                    'status': 'failed',
                    'phase': 4,
                    'balance': prop_balance,
                    'message': f'La Fase 4 è fallita. Il conto PROP è stato bruciato con balance ${prop_balance:,.2f}. '
                               f'Ricarica il conto e riprova.'
                }
            elif broker_balance is not None and broker_balance <= 0:
                return {
                    'status': 'failed',
                    'phase': 4,
                    'balance': broker_balance,
                    'message': f'La Fase 4 è fallita. Il conto BROKER è stato bruciato con balance ${broker_balance:,.2f}. '
                               f'Ricarica il conto broker e riprova.'
                }

        return None  # Fase ancora in corso

    except Exception as e:
        print(f"✗ Errore monitoraggio fase: {e}")
        import traceback
        traceback.print_exc()
        return None


def handle_phase_end(phase_result, netwatcher):
    """
    Gestisce la fine di una fase: chiude posizioni, ferma trading, invia email.

    Args:
        phase_result: dict con status, phase, balance, message
        netwatcher: istanza NetWatcher per controllare connessione
    """
    global user_id, is_running

    try:
        print("\n" + "=" * 80)
        print(f"🚨 FINE FASE {phase_result['phase']} - {phase_result['status'].upper()}")
        print("=" * 80)
        print(phase_result['message'])
        print("=" * 80)

        # Chiudi tutte le posizioni
        print("\n→ Chiusura di tutte le posizioni...")
        close_all_positions(mt5_prop, "PROP")
        close_all_positions(mt5_broker, "BROKER")
        print("✓ Posizioni chiuse")

        # Ferma il trading
        print(f"\n→ Fermando il trading per user {user_id}...")
        config.stop_trading(user_id, netwatcher)
        is_running = False

        # Invia email di notifica
        subject = f"Fase {phase_result['phase']} - {phase_result['status'].upper()}"
        config.send_email_to_user(user_id, subject, phase_result['message'], netwatcher)

        print("\n✓ Notifica inviata all'utente")
        print("=" * 80)

    except Exception as e:
        print(f"✗ Errore gestione fine fase: {e}")
        import traceback
        traceback.print_exc()


def monitor_positions_sync():
    """
    Thread che monitora le posizioni e chiude in modo sincronizzato
    Se una posizione viene chiusa su un conto, chiude anche sull'altro
    """
    global is_running, prop_positions, broker_positions
    
    print("\n✓ Thread sincronizzazione avviato")
    
    while is_running:
        try:
            # Snapshot posizioni attuali
            prop_pos = mt5_prop.positions_get()
            broker_pos = mt5_broker.positions_get()
            
            current_prop = {p.ticket for p in prop_pos} if prop_pos else set()
            current_broker = {p.ticket for p in broker_pos} if broker_pos else set()
            
            # Trova posizioni chiuse su PROP
            closed_prop = set(prop_positions.keys()) - current_prop
            if closed_prop:
                print(f"\n⚠ Posizioni chiuse su PROP: {closed_prop}")
                print("→ Chiudo tutte le posizioni su BROKER...")
                close_all_positions(mt5_broker, "BROKER")
            
            # Trova posizioni chiuse su BROKER
            closed_broker = set(broker_positions.keys()) - current_broker
            if closed_broker:
                print(f"\n⚠ Posizioni chiuse su BROKER: {closed_broker}")
                print("→ Chiudo tutte le posizioni su PROP...")
                close_all_positions(mt5_prop, "PROP")
            
            # Aggiorna i dizionari
            prop_positions = {p.ticket: p for p in prop_pos} if prop_pos else {}
            broker_positions = {p.ticket: p for p in broker_pos} if broker_pos else {}
        
        except Exception as e:
            print(f"✗ Errore thread sincronizzazione: {e}")
        
        time.sleep(0.5)


def open_orders_for_signal(order, params, creds):
    """
    Apre ordini su entrambi i conti con gestione errori
    Se uno fallisce, chiude l'altro
    """
    symbol = order['symbol']
    direction = order['type']  # 'buy' o 'sell'
    
    print(f"\n{'=' * 60}")
    print(f"NUOVO ORDINE: {direction.upper()} {symbol}")
    print(f"{'=' * 60}")
    
    # Ritardo randomico basato su ID
    delay = calculate_delay(creds['prop']['id'])
    print(f"⏱ Attendo {delay:.1f}s prima di aprire...")
    time.sleep(delay)
    
    # Apri su PROP
    print("\n→ Apertura su PROP...")
    prop_ticket = open_order_prop(symbol, direction, params)
    
    if not prop_ticket:
        print("✗ PROP fallito, skip ordine")
        return
    
    # Apri su BROKER (solo se abilitato)
    broker_ticket = None
    if params['broker_enabled']:
        print("\n→ Apertura su BROKER...")
        broker_ticket = open_order_broker(symbol, direction, params)
        
        if not broker_ticket:
            print("✗ BROKER fallito, chiudo PROP...")
            # Chiudi la posizione appena aperta su PROP
            positions = mt5_prop.positions_get(ticket=prop_ticket)
            if positions:
                close_position(positions[0], mt5_prop, "PROP")
            return
    
    print(f"\n✓ Ordini aperti con successo")
    print(f"  PROP ticket: {prop_ticket}")
    if broker_ticket:
        print(f"  BROKER ticket: {broker_ticket}")


def listen_for_orders(creds, login_timestamp, netwatcher):
    """
    Loop principale: ascolta nuovi ordini e li copia
    """
    global is_running, user_id

    print("\n" + "=" * 60)
    print(f"INIZIO COPY TRADING - {login_timestamp}")
    print("=" * 60)
    print(f"Fase: {creds['prop']['fase']}")
    print(f"Ignoro ordini precedenti al login")
    print(f"User ID: {user_id}")
    print("\nIn ascolto di nuovi ordini...")

    last_check = login_timestamp
    params = get_trade_params(creds['prop']['fase'], creds['prop']['size'])
    fase = creds['prop']['fase']

    # Contatore per logging periodico e controllo fasi
    loop_count = 0
    phase_check_interval = 100  # Controlla ogni 10 secondi (100 * 0.1s)

    while is_running:
        try:
            loop_count += 1

            # BLOCCO COMPLETO se autotrading è disabilitato
            if not autotrading_ok:
                # Log periodico ogni 50 iterazioni (~5 secondi) anche quando bloccato
                if loop_count % 50 == 0:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ Trading BLOCCATO: AutoTrading disabilitato su MT5. In attesa riabilitazione...")
                time.sleep(0.1)
                continue

            # Log periodico ogni 50 iterazioni (~5 secondi)
            if loop_count % 50 == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop attivo, in ascolto...")

            # Controlla se started_trading è ancora TRUE (solo se online)
            started = config.check_started_trading(user_id, netwatcher)
            if started is False:  # None = errore rete (ignora), False = disabilitato
                print("\n⚠ Trading disabilitato dall'utente. Stop.")
                break

            # Recupera nuovi ordini (solo se online)
            new_orders = config.poll_new_orders(last_check, netwatcher)

            if new_orders:
                print(f"\n✓ Ricevuti {len(new_orders)} nuovi ordini")

            for order in new_orders:
                open_orders_for_signal(order, params, creds)
                # Aggiorna timestamp
                last_check = order['ts']

            # Controlla condizioni fine fase ogni 10 secondi
            if loop_count % phase_check_interval == 0:
                # Per fase 3, usa la funzione dedicata
                if fase == 3:
                    phase3_ended = check_phase3_profit()
                    if phase3_ended:
                        phase_result = {
                            'status': 'passed',
                            'phase': 3,
                            'balance': 100050,
                            'message': 'Congratulazioni! Hai superato la Fase 3 raggiungendo il target di +$50. '
                                       'Ora puoi passare alla Fase 4. Aggiorna le credenziali nel sito e riavvia il trading.'
                        }
                        handle_phase_end(phase_result, netwatcher)
                        break
                else:
                    # Per altre fasi, usa monitor_phase_conditions
                    phase_result = monitor_phase_conditions(fase)
                    if phase_result:
                        handle_phase_end(phase_result, netwatcher)
                        break

        except KeyboardInterrupt:
            print("\n⚠ Interruzione richiesta (Ctrl+C)")
            break

        except Exception as e:
            print(f"\n✗ Errore nel loop principale: {e}")
            import traceback
            traceback.print_exc()

        time.sleep(0.1)  # Polling ogni 100ms


def main():
    """
    Main entry point del programma
    """
    global is_running, user_id, current_creds

    print("\n" + "=" * 80)
    print(" " * 20 + "MT5 SLAVE COPYTRADER")
    print("=" * 80)

    # Inizializza watcher rete
    netwatcher = NetWatcher()
    netwatcher.start()
    print("✓ NetWatcher avviato (monitoraggio connessione internet)")

    # Variabile per thread autotrading (verrà creato dopo login)
    autotrading_watcher = None

    try:
        # Step 0: Setup VPS con istanze (solo una volta)
        main_setup()

        # Step 1: Identifica VPS e utente (solo una volta)
        print("\n[Step 1/7] Identificazione VPS e utente...")
        vps_ip = config.get_vps_ip()
        user = config.get_user_by_vps_ip(vps_ip, netwatcher)

        # Step 2: Se nessun utente, attendi assegnazione (solo una volta)
        if not user:
            print("\n[Step 2/7] Attesa assegnazione VPS...")
            user = config.wait_for_vps_assignment(netwatcher=netwatcher)
        else:
            print("\n[Step 2/7] VPS già assegnata, skip attesa")

        user_id = user['id']
        print(f"✓ User ID: {user_id}")

        # Loop infinito per gestire stop/riavvio trading
        while True:
            try:
                # Step 3: Attendi started_trading = TRUE
                print("\n[Step 3/7] Attesa avvio trading dall'utente...")
                current_creds = config.wait_for_trading_start(user_id, netwatcher=netwatcher)
                print("✓ Credenziali caricate")

                # Reset flag per il nuovo ciclo
                is_running = True

                # Step 4: Login su entrambi i conti
                print("\n[Step 4/7] Login su conti MT5...")
                login_result = login_accounts(current_creds)

                # Se credenziali errate, invia email e riprova
                if login_result == False:
                    print("\n" + "=" * 80)
                    print("⚠ CREDENZIALI ERRATE")
                    print("=" * 80)
                    print("Le credenziali PROP e/o BROKER non sono corrette.")
                    print("Invio notifica email all'utente...")

                    # Ferma il trading
                    config.stop_trading(user_id, netwatcher)

                    # Invia email di notifica
                    subject = "Errore: Credenziali MT5 non corrette"
                    message = ("Le credenziali inserite per il conto PROP e/o BROKER non sono corrette. "
                               "Per favore verifica i dati inseriti nel sito e riprova. "
                               "Il trading è stato automaticamente fermato.")
                    config.send_email_to_user(user_id, subject, message, netwatcher)

                    print("✓ Notifica inviata")
                    print("\nIn attesa che l'utente inserisca credenziali corrette...")
                    print("=" * 80)

                    # Torna all'inizio del loop per aspettare nuove credenziali
                    continue

                login_timestamp = datetime.now(timezone.utc).isoformat()
                print(f"✓ Login completato - Timestamp: {login_timestamp}")

                # Step 5: Avvia thread monitoraggio autotrading
                print("\n[Step 5/7] Avvio thread monitoraggio AutoTrading...")
                autotrading_watcher = AutoTradingWatcher(vps_ip)
                autotrading_watcher.start()
                time.sleep(1)  # Attendi avvio thread
                print("✓ Thread AutoTrading avviato (controllo ogni 10s)")

                # Step 6: Avvia thread di monitoraggio sincronizzazione
                print("\n[Step 6/7] Avvio thread sincronizzazione posizioni...")
                sync_thread = threading.Thread(target=monitor_positions_sync, daemon=True)
                sync_thread.start()
                time.sleep(1)  # Attendi avvio thread
                print("✓ Thread sincronizzazione avviato")

                # Step 7: Ascolta e copia ordini
                print("\n[Step 7/7] Avvio ascolto ordini...")
                listen_for_orders(current_creds, login_timestamp, netwatcher)

                # Se arriviamo qui, l'utente ha fermato il trading
                print("\n" + "=" * 80)
                print("TRADING FERMATO DALL'UTENTE")
                print("=" * 80)

                # Ferma i thread
                is_running = False
                if autotrading_watcher:
                    autotrading_watcher.stop()
                time.sleep(1)  # Attendi che i thread si fermino

                # Chiudi tutte le posizioni aperte
                print("\n→ Chiusura di tutte le posizioni aperte...")
                close_all_positions(mt5_prop, "PROP")
                close_all_positions(mt5_broker, "BROKER")
                print("✓ Tutte le posizioni chiuse")

                # Chiudi le connessioni MT5
                print("\n→ Chiusura connessioni MT5...")
                try:
                    mt5_prop.shutdown()
                    mt5_broker.shutdown()
                    print("✓ Connessioni MT5 chiuse")
                except:
                    pass

                print("\n" + "=" * 80)
                print("In attesa di nuove credenziali e riavvio trading...")
                print("=" * 80)

                # Torna all'inizio del loop per aspettare il prossimo avvio

            except KeyboardInterrupt:
                # Ctrl+C esce dal loop
                raise

            except Exception as e:
                print(f"\n✗ ERRORE nel ciclo trading: {e}")
                import traceback
                traceback.print_exc()

                # In caso di errore, chiudi tutto e riprova
                is_running = False
                if autotrading_watcher:
                    autotrading_watcher.stop()
                try:
                    close_all_positions(mt5_prop, "PROP")
                    close_all_positions(mt5_broker, "BROKER")
                    mt5_prop.shutdown()
                    mt5_broker.shutdown()
                except:
                    pass

                print("\n⚠ Attendo 10 secondi prima di riprovare...")
                time.sleep(10)

    except KeyboardInterrupt:
        print("\n\n⚠ Arresto richiesto (Ctrl+C)")

    except Exception as e:
        print(f"\n\n✗ ERRORE CRITICO: {e}")
        import traceback
        traceback.print_exc()

    finally:
        is_running = False
        # Ferma i thread
        if autotrading_watcher:
            autotrading_watcher.stop()
        netwatcher.stop()

        print("\n" + "=" * 80)
        print("Chiusura finale conti MT5...")
        try:
            mt5_prop.shutdown()
            mt5_broker.shutdown()
            print("✓ Conti chiusi")
        except:
            pass
        print("=" * 80)
        print("PROGRAMMA TERMINATO")
        print("=" * 80)


if __name__ == "__main__":
    main()
    input("\nPremi INVIO per chiudere...")