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


def get_user_by_vps_ip(vps_ip, netwatcher=None):
    """
    Trova l'utente associato alla VPS tramite IP

    Returns: dict con user_id e started_trading, o None
    """
    if not vps_ip:
        return None

    # Se netwatcher fornito e offline, salta
    if netwatcher and not netwatcher.online:
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


def wait_for_vps_assignment(poll_interval=10, netwatcher=None):
    """
    Attende fino a quando la VPS non viene assegnata a un utente
    """
    print("\n" + "=" * 60)
    print("In attesa di assegnazione VPS...")
    print("=" * 60)

    while True:
        # Se netwatcher fornito e offline, attendi senza fare query
        if netwatcher and not netwatcher.online:
            print(f"Internet offline. Riprovo tra {poll_interval}s...")
            time.sleep(poll_interval)
            continue

        vps_ip = get_vps_ip()
        user = get_user_by_vps_ip(vps_ip, netwatcher)

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


def load_user_accounts(user_id, netwatcher=None):
    """
    Carica le credenziali prop e broker dell'utente dal database

    Returns: dict con credenziali decriptate
    """
    # Se netwatcher fornito e offline, fallisce
    if netwatcher and not netwatcher.online:
        raise Exception("Internet offline: impossibile caricare credenziali")

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


def check_started_trading(user_id, netwatcher=None):
    """
    Verifica se l'utente ha abilitato il trading
    """
    # Se netwatcher fornito e offline, ritorna None per indicare errore rete
    if netwatcher and not netwatcher.online:
        return None

    init_supabase()

    try:
        result = sb.table("profiles").select("started_trading").eq("id", user_id).single().execute()

        if result.data:
            return result.data['started_trading']
        return False

    except Exception as e:
        print(f"Errore verifica started_trading: {e}")
        return None


def wait_for_trading_start(user_id, poll_interval=5, netwatcher=None):
    """
    Attende che started_trading diventi TRUE
    Quando diventa TRUE, ricarica le credenziali (potrebbero essere cambiate)
    """
    print("\n" + "=" * 60)
    print("In attesa che l'utente abiliti il trading nel sito...")
    print("=" * 60)
    print("(L'utente può modificare le credenziali finché il trading è disabilitato)")

    while True:
        # Se netwatcher fornito e offline, attendi senza fare query
        if netwatcher and not netwatcher.online:
            print(f"Internet offline. Riprovo tra {poll_interval}s...")
            time.sleep(poll_interval)
            continue

        started = check_started_trading(user_id, netwatcher)
        if started is None:
            # Errore rete, riprova
            print(f"Errore connessione database. Riprovo tra {poll_interval}s...")
            time.sleep(poll_interval)
            continue

        if started:
            print("\n✓ Trading abilitato!")
            # Ricarica le credenziali (potrebbero essere state modificate)
            try:
                return load_user_accounts(user_id, netwatcher)
            except Exception as e:
                print(f"Errore caricamento credenziali: {e}")
                print(f"Riprovo tra {poll_interval}s...")
                time.sleep(poll_interval)
                continue

        print(f"Trading non ancora abilitato nel sito. Riprovo tra {poll_interval}s...")
        time.sleep(poll_interval)


def poll_new_orders(last_ts, netwatcher=None):
    """
    Recupera nuovi ordini dal database dopo last_ts

    Returns: lista di ordini
    """
    # Se netwatcher fornito e offline, ritorna lista vuota senza errore
    if netwatcher and not netwatcher.online:
        return []

    init_supabase()

    try:
        result = sb.table("orders").select("*").gt("ts", last_ts).order("ts").execute()

        if result.data:
            return result.data
        return []

    except Exception as e:
        print(f"Errore polling ordini: {e}")
        return []


def stop_trading(user_id, netwatcher=None):
    """
    Ferma il trading per un utente (imposta started_trading = FALSE)
    IMPORTANTE: Usa service_role key per bypassare RLS
    """
    # Se netwatcher fornito e offline, salta (non critico)
    if netwatcher and not netwatcher.online:
        print(f"⚠ Internet offline: impossibile aggiornare stato trading nel database")
        return False

    init_supabase()

    try:
        # Update con service_role bypassa RLS
        result = sb.table("profiles").update({
            "started_trading": False
        }).eq("id", user_id).execute()

        print(f"✓ Trading fermato per user {user_id}")
        return True

    except Exception as e:
        print(f"✗ Errore fermando trading: {e}")
        return False


def send_email_to_user(user_id, subject, message, netwatcher=None):
    """
    Invia una email di notifica all'utente usando Supabase Auth.
    NOTA: Questo usa il sistema email di Supabase Auth che andrà sostituito
    con email personalizzata con dominio custom.

    Args:
        user_id: UUID dell'utente
        subject: Oggetto email
        message: Corpo del messaggio
    """
    # Se netwatcher fornito e offline, stampa solo il messaggio
    if netwatcher and not netwatcher.online:
        print(f"\n{'=' * 80}")
        print(f"📧 EMAIL ALL'UTENTE (OFFLINE - non inviata)")
        print(f"{'=' * 80}")
        print(f"Oggetto: {subject}")
        print(f"Messaggio:\n{message}")
        print(f"{'=' * 80}\n")
        return True

    init_supabase()

    try:
        # Ottieni l'email dell'utente dalla tabella auth.users
        # NOTA: Con service_role possiamo accedere a auth.users
        result = sb.auth.admin.get_user_by_id(user_id)

        if not result or not result.user:
            print(f"⚠ Utente {user_id} non trovato in auth")
            print(f"📧 NOTIFICA (simulata):")
            print(f"   Oggetto: {subject}")
            print(f"   Messaggio: {message}")
            return True  # Non fallire se non troviamo l'utente

        user_email = result.user.email

        print(f"\n{'=' * 80}")
        print(f"📧 EMAIL ALL'UTENTE")
        print(f"{'=' * 80}")
        print(f"Destinatario: {user_email}")
        print(f"Oggetto: {subject}")
        print(f"Messaggio:\n{message}")
        print(f"{'=' * 80}\n")

        # TODO: invio email VERO tramite:
        # - SendGrid (https://sendgrid.com/)
        # - Mailgun (https://www.mailgun.com/)
        # - SMTP con dominio custom
        # - Supabase Edge Function con provider email

        # Per ora, la notifica è stata stampata nel log
        return True

    except Exception as e:
        print(f"✗ Errore invio email: {e}")
        import traceback
        traceback.print_exc()
        # Non fallire, almeno abbiamo stampato il messaggio
        return True
    
def send_email_to_admin(user_id, subject, message, netwatcher=None):
    """
    Invia una email in caso di errori all'admin
    """
    # Se netwatcher fornito e offline, stampa solo il messaggio
    if netwatcher and not netwatcher.online:
        print(f"\n{'=' * 80}")
        print(f"📧 EMAIL ALL'ADMIN (OFFLINE - non inviata)")
        print(f"{'=' * 80}")
        print(f"Oggetto: {subject}")
        print(f"Messaggio:\n{message}")
        print(f"{'=' * 80}\n")
        return True

    init_supabase()

    try:
        admin_email = "af.traders.business@gmail.com"

        print(f"\n{'=' * 80}")
        print(f"📧 EMAIL ALL'ADMIN")
        print(f"{'=' * 80}")
        print(f"Destinatario: {admin_email}")
        print(f"Oggetto: {subject}")
        print(f"Messaggio:\n{message}")
        print(f"{'=' * 80}\n")

        # TODO: invio email VERO tramite:
        # - SendGrid (https://sendgrid.com/)
        # - Mailgun (https://www.mailgun.com/)
        # - SMTP con dominio custom
        # - Supabase Edge Function con provider email

        return True

    except Exception as e:
        print(f"✗ Errore invio email: {e}")
        import traceback
        traceback.print_exc()
        # Non fallire, almeno abbiamo stampato il messaggio
        return True


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