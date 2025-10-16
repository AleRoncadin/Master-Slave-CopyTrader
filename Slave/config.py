"""
MT5 Slave CopyTrader - Configurazione e Database
Gestisce connessione DB, credenziali, encryption
"""

import os
import base64
import socket
import requests
import time
from datetime import datetime, timezone
from supabase import create_client
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

# Carica variabili d'ambiente
load_dotenv()

# Configurazione Supabase
SUPABASE_URL = "https://tevsvjwhgsfzppzrjdwp.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InRldnN2andoZ3NmenBwenJqZHdwIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc1OTgyMzA3OSwiZXhwIjoyMDc1Mzk5MDc5fQ.JuxRkDq-nyvzELiVcbiVLg_lu27z5tTRmz_3sZev8wY"

# Chiavi di decryption (aggiungi altre versioni se necessario)
ENCRYPTION_KEYS = {
    "1": base64.b64decode("XKru4Pxkfq/02mOXTKAvBRR/WQUnuQ4lmoS1PtEA6cY="),
}

# Client Supabase globale
sb = None

def init_supabase():
    """Inizializza il client Supabase"""
    global sb
    if sb is None:
        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    return sb


def get_vps_ip():
    """
    Ottiene l'IP pubblico della VPS
    Usa un servizio esterno per ottenere l'IP pubblico reale
    """
    try:
        # Metodo 1: servizio ipify
        response = requests.get('https://api.ipify.org?format=json', timeout=5)
        ip = response.json()['ip']
        print(f"IP pubblico rilevato: {ip}")
        return ip
    except Exception as e:
        print(f"Errore recupero IP pubblico: {e}")
        
        # Metodo 2: fallback con socket (potrebbe dare IP locale)
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            print(f"IP locale rilevato: {ip}")
            return ip
        except:
            print("Impossibile determinare l'IP")
            return None


def get_user_by_vps_ip(vps_ip):
    """
    Trova l'utente associato alla VPS tramite IP
    
    Returns: dict con user_id e started_trading, o None
    """
    if not vps_ip:
        return None
    
    init_supabase()
    
    try:
        # Step 1: Trova la VPS tramite IP
        vps_result = sb.table("vps").select("id").eq("IP", vps_ip).execute()
        
        if not vps_result.data or len(vps_result.data) == 0:
            print(f"Nessuna VPS trovata con IP {vps_ip}")
            return None
        
        vps_id = vps_result.data[0]['id']
        print(f"VPS trovata: ID={vps_id}")
        
        # Step 2: Trova l'utente con quella vps_id
        user_result = sb.table("profiles").select("id, started_trading").eq("vps_id", vps_id).execute()
        
        if not user_result.data or len(user_result.data) == 0:
            print(f"Nessun utente assegnato alla VPS {vps_id}")
            return None
        
        user = user_result.data[0]
        print(f"Utente trovato: ID={user['id']}, started_trading={user['started_trading']}")
        return user
    
    except Exception as e:
        print(f"Errore durante la ricerca dell'utente: {e}")
        return None


def wait_for_vps_assignment(poll_interval=10):
    """
    Attende fino a quando la VPS non viene assegnata a un utente
    """
    print("\n" + "=" * 60)
    print("In attesa di assegnazione VPS...")
    print("=" * 60)
    
    while True:
        vps_ip = get_vps_ip()
        user = get_user_by_vps_ip(vps_ip)
        
        if user:
            print(f"\n✓ VPS assegnata all'utente: {user['id']}")
            return user
        
        print(f"Nessun utente assegnato (IP: {vps_ip}). Riprovo tra {poll_interval}s...")
        time.sleep(poll_interval)


def decrypt_password(token_b64, version):
    """
    Decripta la password dal database usando AES-GCM
    """
    try:
        raw = base64.b64decode(token_b64)
        if len(raw) < 12 + 16:
            raise ValueError("Ciphertext invalido (troppo corto)")
        
        iv = raw[:12]
        tag = raw[-16:]
        ct = raw[12:-16]
        
        key = ENCRYPTION_KEYS.get(str(version))
        if not key:
            raise RuntimeError(f"Chiave per versione {version} non trovata")
        
        aesgcm = AESGCM(key)
        pt = aesgcm.decrypt(iv, ct + tag, associated_data=None)
        return pt.decode("utf-8")
    
    except Exception as e:
        raise Exception(f"Errore decryption: {e}")


def load_user_accounts(user_id):
    """
    Carica le credenziali prop e broker dell'utente dal database
    
    Returns: dict con credenziali decriptate
    """
    init_supabase()
    
    try:
        # Account Broker
        broker_result = sb.table("account_broker").select(
            "account_id, server, password_enc, key_version"
        ).eq("user_id", user_id).single().execute()
        
        # Account Prop
        prop_result = sb.table("account_prop").select(
            "id, account_id, server, size, password_enc, key_version, fase"
        ).eq("user_id", user_id).single().execute()
        
        if not broker_result.data:
            raise Exception("Credenziali broker non trovate nel database")
        
        if not prop_result.data:
            raise Exception("Credenziali prop non trovate nel database")
        
        # Decripta password
        broker_pw = decrypt_password(
            broker_result.data['password_enc'],
            broker_result.data['key_version']
        )
        
        prop_pw = decrypt_password(
            prop_result.data['password_enc'],
            prop_result.data['key_version']
        )
        
        creds = {
            'broker': {
                'account_id': broker_result.data['account_id'],
                'server': broker_result.data['server'],
                'password': broker_pw
            },
            'prop': {
                'id': prop_result.data['id'],  # Importante per l'ordine di apertura
                'account_id': prop_result.data['account_id'],
                'server': prop_result.data['server'],
                'size': float(prop_result.data['size']),
                'password': prop_pw,
                'fase': prop_result.data.get('fase', 1)
            }
        }
        
        print("\n✓ Credenziali caricate con successo")
        print(f"  Broker: {creds['broker']['account_id']} @ {creds['broker']['server']}")
        print(f"  Prop: {creds['prop']['account_id']} @ {creds['prop']['server']} (Fase {creds['prop']['fase']})")
        
        return creds
    
    except Exception as e:
        raise Exception(f"Errore caricamento credenziali: {e}")


def check_started_trading(user_id):
    """
    Verifica se l'utente ha abilitato il trading
    """
    init_supabase()
    
    try:
        result = sb.table("profiles").select("started_trading").eq("id", user_id).single().execute()
        
        if result.data:
            return result.data['started_trading']
        return False
    
    except Exception as e:
        print(f"Errore verifica started_trading: {e}")
        return False


def wait_for_trading_start(user_id, poll_interval=5):
    """
    Attende che started_trading diventi TRUE
    Quando diventa TRUE, ricarica le credenziali (potrebbero essere cambiate)
    """
    print("\n" + "=" * 60)
    print("In attesa che l'utente abiliti il trading nel sito...")
    print("=" * 60)
    print("(L'utente può modificare le credenziali finché il trading è disabilitato)")
    
    while True:
        if check_started_trading(user_id):
            print("\n✓ Trading abilitato!")
            # Ricarica le credenziali (potrebbero essere state modificate)
            return load_user_accounts(user_id)
        
        print(f"Trading non ancora abilitato nel sito. Riprovo tra {poll_interval}s...")
        time.sleep(poll_interval)


def poll_new_orders(last_ts):
    """
    Recupera nuovi ordini dal database dopo last_ts
    
    Returns: lista di ordini
    """
    init_supabase()
    
    try:
        result = sb.table("orders").select("*").gt("ts", last_ts).order("ts").execute()
        
        if result.data:
            return result.data
        return []
    
    except Exception as e:
        print(f"Errore polling ordini: {e}")
        return []


if __name__ == "__main__":
    # Test della configurazione
    print("=== TEST CONFIGURAZIONE ===\n")
    
    # Test IP
    ip = get_vps_ip()
    print(f"IP: {ip}\n")
    
    # Test connessione DB
    try:
        init_supabase()
        print("✓ Connessione Supabase OK\n")
    except Exception as e:
        print(f"✗ Errore connessione Supabase: {e}\n")
    
    # Test ricerca utente
    user = get_user_by_vps_ip(ip)
    if user:
        print(f"✓ Utente trovato: {user['id']}")
        
        # Test caricamento credenziali
        try:
            creds = load_user_accounts(user['id'])
            print("✓ Credenziali caricate\n")
        except Exception as e:
            print(f"✗ Errore caricamento credenziali: {e}\n")
    else:
        print("✗ Nessun utente associato a questa VPS\n")
    
    input("Premi INVIO per chiudere...")