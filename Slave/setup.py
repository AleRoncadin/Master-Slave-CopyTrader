"""
MT5 Slave CopyTrader - Setup
Installa e configura le due istanze MT5 (prop e broker)
"""

import os
import shutil
import subprocess
import urllib.request
import sys
import time

# Percorsi
PATHS = {
    'base': r'C:\Program Files\MetaTrader 5',
    'prop': r'C:\Program Files\MT5_prop',
    'broker': r'C:\Program Files\MT5_broker'
}

MT5_DOWNLOAD_URL = "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"


def check_admin():
    """Verifica se lo script ha privilegi amministrativi"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def check_mt5_instances():
    """
    Controlla se esistono terminal64.exe nelle due cartelle
    Returns: dict con status di prop e broker
    """
    return {
        'prop': os.path.exists(os.path.join(PATHS['prop'], 'terminal64.exe')),
        'broker': os.path.exists(os.path.join(PATHS['broker'], 'terminal64.exe'))
    }


def download_mt5(dest_path="C:\\Program Files\\mt5setup.exe"):
    """Scarica l'installer di MT5"""
    print("Download MT5 in corso...")

    # Metodo 1: Usa requests (più robusto)
    try:
        import requests
        import ssl

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        print("Tentativo download con requests...")
        response = requests.get(MT5_DOWNLOAD_URL, headers=headers, timeout=60, verify=True)
        response.raise_for_status()

        with open(dest_path, 'wb') as f:
            f.write(response.content)

        print(f"Download completato: {dest_path}")
        return dest_path

    except Exception as e:
        print(f"Metodo 1 fallito: {e}")

        # Metodo 2: Fallback con urllib (con headers)
        try:
            print("Tentativo download con urllib...")
            import ssl

            # Crea un context SSL che accetta certificati
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                MT5_DOWNLOAD_URL,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )

            with urllib.request.urlopen(req, timeout=60, context=ssl_context) as response:
                with open(dest_path, 'wb') as out_file:
                    out_file.write(response.read())

            print(f"Download completato: {dest_path}")
            return dest_path

        except Exception as e2:
            print(f"Metodo 2 fallito: {e2}")
            print(f"\nErrore durante il download da entrambi i metodi.")
            print("Puoi scaricare manualmente MT5 da:")
            print(MT5_DOWNLOAD_URL)
            return None


def install_mt5_silent(installer_path, install_dir):
    """
    Installa MT5 in modalità silenziosa
    """
    print(f"Installazione MT5 in {install_dir}...")
    
    # Crea la directory se non esiste
    os.makedirs(install_dir, exist_ok=True)
    
    cmd = [
        installer_path,
        '/auto',
        f'/path={install_dir}'
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        
        if result.returncode == 0 or os.path.exists(os.path.join(install_dir, 'terminal64.exe')):
            print(f"MT5 installato con successo in {install_dir}")
            return True
        else:
            print(f"Errore installazione. Return code: {result.returncode}")
            if result.stderr:
                print(f"Stderr: {result.stderr.decode()}")
            return False
    except subprocess.TimeoutExpired:
        print("Timeout durante l'installazione")
        return False
    except Exception as e:
        print(f"Errore durante l'installazione: {e}")
        return False


def create_mt5_instances():
    """
    Crea le istanze MT5_prop e MT5_broker
    """
    # Verifica che esista la base
    if not os.path.exists(PATHS['base']):
        print("MetaTrader5 base non trovato. Installazione in corso...")
        installer = download_mt5()
        if not installer:
            raise Exception("Impossibile scaricare MT5")
        
        if not install_mt5_silent(installer, PATHS['base']):
            raise Exception("Impossibile installare MT5")
        
        # Rimuovi installer
        try:
            os.remove(installer)
        except:
            pass
        
        # Attendi che l'installazione si completi
        print("Attendo completamento installazione...")
        time.sleep(10)
    
    # Crea le due istanze
    for name in ['prop', 'broker']:
        dest = PATHS[name]
        if not os.path.exists(dest):
            print(f"Creazione istanza {name}...")
            try:
                shutil.copytree(PATHS['base'], dest)
                print(f"Istanza {name} creata con successo")
            except Exception as e:
                print(f"Errore durante la copia per {name}: {e}")
                raise
        else:
            print(f"Istanza {name} già esistente")


def enable_autotrading(mt5_path):
    """
    Abilita AutoTrading modificando common.ini.
    IMPORTANTE: AutoTrading DEVE essere sulla PRIMA riga (fuori da qualsiasi sezione).

    Formato corretto common.ini:
    AutoTrading=true
    [Common]
    Login=...
    Server=...
    """
    enabled_count = 0

    # Location 1: Config nella cartella di installazione
    config_dir_install = os.path.join(mt5_path, 'config')
    config_file_install = os.path.join(config_dir_install, 'common.ini')

    try:
        os.makedirs(config_dir_install, exist_ok=True)

        # Leggi il file esistente
        config_content = []
        if os.path.exists(config_file_install):
            try:
                with open(config_file_install, 'r', encoding='utf-8') as f:
                    config_content = f.readlines()
            except:
                try:
                    with open(config_file_install, 'r', encoding='utf-16') as f:
                        config_content = f.readlines()
                except:
                    config_content = []

        # Rimuovi AutoTrading se esiste in qualsiasi posizione
        config_content = [line for line in config_content if not line.strip().startswith('AutoTrading')]

        # IMPORTANTE: Aggiungi AutoTrading=true come PRIMA riga
        config_content.insert(0, 'AutoTrading=true\n')

        # Scrivi il file
        with open(config_file_install, 'w', encoding='utf-8') as f:
            f.writelines(config_content)

        print(f"✓ AutoTrading abilitato in: {config_file_install}")
        enabled_count += 1

    except Exception as e:
        print(f"⚠ Errore config installazione: {e}")

    # Location 2: Config in AppData (dove MT5 salva veramente i dati)
    try:
        appdata = os.environ.get('APPDATA')
        if appdata:
            terminal_path = os.path.join(appdata, 'MetaQuotes', 'Terminal')

            if os.path.exists(terminal_path):
                # Cerca tutte le cartelle terminal (hash)
                for folder in os.listdir(terminal_path):
                    folder_path = os.path.join(terminal_path, folder)
                    if os.path.isdir(folder_path) and len(folder) == 32:  # Hash MD5 di 32 caratteri
                        config_dir_appdata = os.path.join(folder_path, 'config')
                        config_file_appdata = os.path.join(config_dir_appdata, 'common.ini')

                        try:
                            os.makedirs(config_dir_appdata, exist_ok=True)

                            # Leggi il file esistente
                            config_content = []
                            if os.path.exists(config_file_appdata):
                                try:
                                    with open(config_file_appdata, 'r', encoding='utf-8') as f:
                                        config_content = f.readlines()
                                except:
                                    try:
                                        with open(config_file_appdata, 'r', encoding='utf-16') as f:
                                            config_content = f.readlines()
                                    except:
                                        config_content = []

                            # Rimuovi AutoTrading se esiste
                            config_content = [line for line in config_content if not line.strip().startswith('AutoTrading')]

                            # Aggiungi AutoTrading=true come PRIMA riga
                            config_content.insert(0, 'AutoTrading=true\n')

                            # Scrivi
                            with open(config_file_appdata, 'w', encoding='utf-8') as f:
                                f.writelines(config_content)

                            print(f"✓ AutoTrading abilitato in AppData: {folder[:8]}...")
                            enabled_count += 1

                        except Exception as e:
                            pass  # Ignora errori su singoli folder

    except Exception as e:
        print(f"⚠ Errore config AppData: {e}")

    if enabled_count > 0:
        print(f"\n✓ AutoTrading abilitato in {enabled_count} location")
    else:
        print(f"\n⚠ ATTENZIONE: Non è stato possibile abilitare AutoTrading automaticamente")
        print("   Dovrai abilitarlo manualmente in MT5: Tools -> Options -> Expert Advisors -> Allow Algo Trading")


def verify_installation():
    """Verifica che l'installazione sia corretta"""
    print("\n=== VERIFICA INSTALLAZIONE ===")
    
    status = check_mt5_instances()
    
    if status['prop']:
        print("✓ MT5_prop installato correttamente")
    else:
        print("✗ MT5_prop NON trovato")
    
    if status['broker']:
        print("✓ MT5_broker installato correttamente")
    else:
        print("✗ MT5_broker NON trovato")
    
    return status['prop'] and status['broker']


def enable_autotrading_only():
    """
    Abilita solo AutoTrading senza reinstallare
    """
    print("=" * 60)
    print("ABILITA ALGOTRADING SU ISTANZE ESISTENTI")
    print("=" * 60)
    print()

    status = check_mt5_instances()

    if not status['prop'] and not status['broker']:
        print("✗ Nessuna istanza MT5 trovata. Esegui prima l'installazione completa.")
        return

    print("Abilitazione AutoTrading...")
    if status['prop']:
        print("\n→ PROP:")
        enable_autotrading(PATHS['prop'])

    if status['broker']:
        print("\n→ BROKER:")
        enable_autotrading(PATHS['broker'])

    print("\n" + "=" * 60)
    print("✓ OPERAZIONE COMPLETATA")
    print("=" * 60)


def main_setup():
    """
    Esegue tutto il setup
    """
    print("=" * 60)
    print("MT5 SLAVE COPYTRADER - SETUP")
    print("=" * 60)
    print()

    # Verifica privilegi admin
    if not check_admin():
        print("ATTENZIONE: Questo script richiede privilegi di amministratore")
        print("Esegui come amministratore per installare in Program Files")
        print()
        response = input("Continuare comunque? (s/n): ").lower()
        if response != 's':
            print("Setup annullato")
            return

    # Check istanze
    print("Controllo istanze esistenti...")
    status = check_mt5_instances()

    if status['prop'] and status['broker']:
        print("✓ Entrambe le istanze MT5 sono già installate")
        return
    
    try:
        # Crea istanze mancanti
        print("\nCreazione istanze MT5...")
        create_mt5_instances()
        
        # Abilita AutoTrading
        print("\nAbilitazione AutoTrading...")
        for name in ['prop', 'broker']:
            enable_autotrading(PATHS[name])
        
        # Verifica
        if verify_installation():
            print("\n" + "=" * 60)
            print("✓ SETUP COMPLETATO CON SUCCESSO")
            print("=" * 60)
            print("\nPuoi ora eseguire il programma copier.py")
        else:
            print("\n" + "=" * 60)
            print("✗ SETUP INCOMPLETO")
            print("=" * 60)
            print("Verifica i messaggi di errore sopra")
    
    except Exception as e:
        print("\n" + "=" * 60)
        print("✗ ERRORE DURANTE IL SETUP")
        print("=" * 60)
        print(f"Errore: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main_setup()
    input("\nPremi INVIO per chiudere...")