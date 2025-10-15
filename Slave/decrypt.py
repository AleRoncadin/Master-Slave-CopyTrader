import os
import base64
from dotenv import load_dotenv
from supabase import create_client
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

load_dotenv()  # carica .env nella stessa cartella

# Config
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Imposta SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY nel file .env")

# Mappa chiavi per versione (aggiungi altre versioni se serve)
KEYS = {
    "1": base64.b64decode(os.environ["APP_ENC_KEY_V1"]),
    # "2": base64.b64decode(os.environ.get("APP_ENC_KEY_V2",""))  # se aggiungi
}

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

def decrypt_token_b64(token_b64: str, version: int) -> str:
    raw = base64.b64decode(token_b64)
    if len(raw) < 12 + 16:
        raise ValueError("Ciphertext invalido")
    iv = raw[:12]
    tag = raw[-16:]
    ct = raw[12:-16]
    key = KEYS.get(str(version))
    if not key:
        raise RuntimeError(f"Chiave per versione {version} non trovata nelle env")
    aesgcm = AESGCM(key)
    pt = aesgcm.decrypt(iv, ct + tag, associated_data=None)
    return pt.decode("utf-8")

def get_user_id_by_email(email: str) -> str:
    # usa la tabella profiles per risolvere l'UUID
    r = sb.table("profiles").select("id").eq("email", email).single().execute()
    if not r.data:
        raise RuntimeError(f"Profilo non trovato per {email}")
    return r.data["id"]

def load_accounts_by_user_id(user_id: str):
    b_r = sb.table("account_broker").select(
        "account_id, server, size, password_enc, key_version"
    ).eq("user_id", user_id).single().execute()

    p_r = sb.table("account_prop").select(
        "account_id, server, size, password_enc, key_version"
    ).eq("user_id", user_id).single().execute()

    if not b_r.data:
        raise RuntimeError(f"Nessun record trovato in account_broker per {user_id}")
    if not p_r.data:
        raise RuntimeError(f"Nessun record trovato in account_prop per {user_id}")

    broker_pw = decrypt_token_b64(b_r.data["password_enc"], int(b_r.data["key_version"]))
    prop_pw   = decrypt_token_b64(p_r.data["password_enc"], int(p_r.data["key_version"]))

    return {
        "broker": {
            "id": b_r.data["account_id"],
            "server": b_r.data["server"],
            "size": b_r.data["size"],
            "password": broker_pw,
        },
        "prop": {
            "id": p_r.data["account_id"],
            "server": p_r.data["server"],
            "size": p_r.data["size"],
            "password": prop_pw,
        },
    }


if __name__ == "__main__":
    # modifica qui l'email dell'utente che vuoi testare
    email = input("Email utente da testare: ").strip()

    try:
        user_id = get_user_id_by_email(email)
        creds = load_accounts_by_user_id(user_id)
    except Exception as e:
        print("Errore:", e)
        raise SystemExit(1)

    print("=== RISULTATO TEST ===")
    print(f"User ID: {user_id}")
    print("Broker ID:", creds["broker"]["id"])
    print("Broker Server:", creds["broker"]["server"])
    print("Broker Size:", creds["broker"]["size"])
    print("Broker password (plaintext):", creds["broker"]["password"])
    print("---")
    print("Prop ID:", creds["prop"]["id"])
    print("Prop Server:", creds["prop"]["server"])
    print("Prop Size:", creds["prop"]["size"])
    print("Prop password (plaintext):", creds["prop"]["password"])
    print("======================")
