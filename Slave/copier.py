"""
MT5 Slave CopyTrader - Copy Trading Engine
Copia le operazioni dal master ai conti prop e broker
"""

import sys
import os
import time
import random
import threading
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

# Variabili globali
user_id = None
current_creds = None
prop_positions = {}  # {ticket: position_data}
broker_positions = {}  # {ticket: position_data}
is_running = True
phase3_starting_balance = None  # Balance iniziale per fase 3


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
            'sl_pips': 1250,
            'tp_pips': 1250,
            'prop_lots': 2.0,
            'broker_lots': 0.14,
            'broker_enabled': True
        },
        2: {
            'sl_pips': 1250,
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
            'sl_pips': 1250,
            'tp_pips': 625,
            'prop_lots': 2.0,
            'broker_lots': 0.4,
            'broker_enabled': True
        }
    }
    
    return params.get(fase, params[1])


def check_and_enable_autotrading(mt5_instance, mt5_path, account_name):
    """
    Controlla se AlgoTrading è abilitato e lo abilita se necessario.

    IMPORTANTE: MT5 richiede che AutoTrading sia abilitato per aprire ordini via API.
    """
    try:
        # Controlla se autotrading è già abilitato tramite terminal_info
        terminal_info = mt5_instance.terminal_info()

        if terminal_info and hasattr(terminal_info, 'trade_allowed'):
            if terminal_info.trade_allowed:
                print(f"  ✓ AlgoTrading già abilitato su {account_name}")
                return True
            else:
                print(f"  ⚠ AlgoTrading NON abilitato su {account_name}, tento di abilitarlo...")

        # Modifica common.ini per abilitare AutoTrading
        mt5_dir = os.path.dirname(mt5_path)
        config_dir = os.path.join(mt5_dir, 'config')
        config_file = os.path.join(config_dir, 'common.ini')

        if not os.path.exists(config_file):
            print(f"  ⚠ File {config_file} non trovato, lo creo...")
            os.makedirs(config_dir, exist_ok=True)
            with open(config_file, 'w', encoding='utf-8') as f:
                f.write('AutoTrading=true\n')
            print(f"  ✓ AutoTrading abilitato in {account_name}")
            print(f"  ℹ IMPORTANTE: Chiudi e riavvia copier.py per applicare le modifiche")
            return False

        # Leggi il file esistente
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_content = f.readlines()
        except:
            try:
                with open(config_file, 'r', encoding='utf-16') as f:
                    config_content = f.readlines()
            except:
                config_content = []

        # Rimuovi AutoTrading se esiste
        config_content = [line for line in config_content if not line.strip().startswith('AutoTrading')]

        # Aggiungi AutoTrading=true come PRIMA riga
        config_content.insert(0, 'AutoTrading=true\n')

        # Scrivi il file
        with open(config_file, 'w', encoding='utf-8') as f:
            f.writelines(config_content)

        print(f"  ✓ AutoTrading abilitato in {config_file}")
        print(f"  ℹ IMPORTANTE: Chiudi e riavvia copier.py per applicare le modifiche")

        return False  # Richiede riavvio

    except Exception as e:
        print(f"  ✗ Errore controllo AlgoTrading {account_name}: {e}")
        return None


def login_accounts(creds):
    """
    Effettua il login su prop e broker
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

    # Controllo AlgoTrading
    print("\n→ Controllo AlgoTrading...")
    prop_at = check_and_enable_autotrading(mt5_prop, r'C:\Program Files\MT5_prop\terminal64.exe', "PROP")
    broker_at = check_and_enable_autotrading(mt5_broker, r'C:\Program Files\MT5_broker\terminal64.exe', "BROKER")

    # Se uno dei due ha richiesto un riavvio, avverti l'utente
    if prop_at == False or broker_at == False:
        print("\n" + "=" * 60)
        print("⚠ ATTENZIONE: AutoTrading è stato abilitato ma")
        print("   DEVI RIAVVIARE copier.py per applicare le modifiche!")
        print("=" * 60)
        input("\nPremi INVIO per uscire e poi riavvia copier.py...")
        sys.exit(0)

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
    delay = random.uniform(0, 5)
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
            return

        # Se non abbiamo ancora salvato il balance iniziale, salvalo ora
        if phase3_starting_balance is None:
            phase3_starting_balance = account_info.balance
            print(f"\n📊 Fase 3: Balance iniziale salvato: ${phase3_starting_balance:.2f}")
            return

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

    except Exception as e:
        print(f"✗ Errore check fase 3: {e}")
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


def listen_for_orders(creds, login_timestamp):
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

    # Contatore per logging periodico
    loop_count = 0

    while is_running:
        try:
            loop_count += 1

            # Log periodico ogni 50 iterazioni (~5 secondi)
            if loop_count % 50 == 0:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Loop attivo, in ascolto...")

            # Controlla se started_trading è ancora TRUE
            if not config.check_started_trading(user_id):
                print("\n⚠ Trading disabilitato dall'utente. Stop.")
                break

            # Recupera nuovi ordini
            new_orders = config.poll_new_orders(last_check)

            if new_orders:
                print(f"\n✓ Ricevuti {len(new_orders)} nuovi ordini")

            for order in new_orders:
                open_orders_for_signal(order, params, creds)
                # Aggiorna timestamp
                last_check = order['ts']

            # Controlla fase 3 (chiusura a 50$ profit)
            if params.get('target_profit'):
                check_phase3_profit()

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

    try:
        # Step 0: Setup VPS con istanze (solo una volta)
        main_setup()

        # Step 1: Identifica VPS e utente (solo una volta)
        print("\n[Step 1/6] Identificazione VPS e utente...")
        vps_ip = config.get_vps_ip()
        user = config.get_user_by_vps_ip(vps_ip)

        # Step 2: Se nessun utente, attendi assegnazione (solo una volta)
        if not user:
            print("\n[Step 2/6] Attesa assegnazione VPS...")
            user = config.wait_for_vps_assignment()
        else:
            print("\n[Step 2/6] VPS già assegnata, skip attesa")

        user_id = user['id']
        print(f"✓ User ID: {user_id}")

        # Loop infinito per gestire stop/riavvio trading
        while True:
            try:
                # Step 3: Attendi started_trading = TRUE
                print("\n[Step 3/6] Attesa avvio trading dall'utente...")
                current_creds = config.wait_for_trading_start(user_id)
                print("✓ Credenziali caricate")

                # Reset flag per il nuovo ciclo
                is_running = True

                # Step 4: Login su entrambi i conti
                print("\n[Step 4/6] Login su conti MT5...")
                login_accounts(current_creds)
                login_timestamp = datetime.now(timezone.utc).isoformat()
                print(f"✓ Login completato - Timestamp: {login_timestamp}")

                # Step 5: Avvia thread di monitoraggio sincronizzazione
                print("\n[Step 5/6] Avvio thread sincronizzazione posizioni...")
                sync_thread = threading.Thread(target=monitor_positions_sync, daemon=True)
                sync_thread.start()
                time.sleep(1)  # Attendi avvio thread
                print("✓ Thread sincronizzazione avviato")

                # Step 6: Ascolta e copia ordini
                print("\n[Step 6/6] Avvio ascolto ordini...")
                listen_for_orders(current_creds, login_timestamp)

                # Se arriviamo qui, l'utente ha fermato il trading
                print("\n" + "=" * 80)
                print("TRADING FERMATO DALL'UTENTE")
                print("=" * 80)

                # Ferma il thread di sincronizzazione
                is_running = False
                time.sleep(1)  # Attendi che il thread si fermi

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