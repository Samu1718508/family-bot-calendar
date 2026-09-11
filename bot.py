import anthropic
import requests
import datetime
import json
import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build

# ============================================
# SETUP
# ============================================
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
TELEGRAM_TOKEN_1 = os.environ.get("TELEGRAM_TOKEN_2")
TELEGRAM_TOKEN_2 = os.environ.get("TELEGRAM_TOKEN_3")
GOOGLE_CREDENTIALS_JSON = os.environ.get("GOOGLE_CREDENTIALS_JSON")
ID_CALENDARIO = os.environ.get("ID_CALENDARIO")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

credenziali_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
SCOPES = ['https://www.googleapis.com/auth/calendar']
credenziali = service_account.Credentials.from_service_account_info(credenziali_dict, scopes=SCOPES)
servizio_calendar = build('calendar', 'v3', credentials=credenziali)

# File dati locali (su Railway il filesystem e' effimero, ma vanno bene per dati che si rigenerano)
CARTELLA_DATI = "/app/dati/"
if not os.path.exists(CARTELLA_DATI):
    os.makedirs(CARTELLA_DATI)

FILE_CONFIG_RIFIUTI = CARTELLA_DATI + "config_rifiuti.json"
if not os.path.exists(FILE_CONFIG_RIFIUTI):
    with open(FILE_CONFIG_RIFIUTI, "w") as f:
        json.dump({"ora_alert": "21:00", "ultimo_alert_inviato": None}, f)

FILE_CHAT_IDS = CARTELLA_DATI + "chat_ids.json"
if not os.path.exists(FILE_CHAT_IDS):
    with open(FILE_CHAT_IDS, "w") as f:
        json.dump({}, f)

FILE_RICORRENTI = CARTELLA_DATI + "ricorrenti_familiari.json"
if not os.path.exists(FILE_RICORRENTI):
    with open(FILE_RICORRENTI, "w") as f:
        json.dump([], f)

FILE_EVENTI = CARTELLA_DATI + "eventi_singoli.json"
if not os.path.exists(FILE_EVENTI):
    with open(FILE_EVENTI, "w") as f:
        json.dump([], f)

PASSWORD_FAMIGLIA = "Pixel2026"

print("Setup completato!")

# ============================================
# TOOLS TEMPO E METEO
# ============================================
def che_ore_sono():
    adesso = datetime.datetime.now()
    ora_italiana = adesso + datetime.timedelta(hours=2)
    return "Sono le " + str(ora_italiana.hour) + ":" + str(ora_italiana.minute)

def data_oggi():
    adesso = datetime.datetime.now()
    return str(adesso.year) + "-" + str(adesso.month).zfill(2) + "-" + str(adesso.day).zfill(2)

def giorno_settimana_di(data_str):
    data = datetime.datetime.strptime(data_str, "%Y-%m-%d")
    giorni = ["lunedi", "martedi", "mercoledi", "giovedi", "venerdi", "sabato", "domenica"]
    return giorni[data.weekday()]

def prossima_data_con_giorno_mese(giorno_mese, mese, giorno_settimana_richiesto=None):
    adesso = datetime.datetime.now() + datetime.timedelta(hours=2)
    anno = adesso.year
    data_provata = datetime.datetime(anno, mese, giorno_mese)
    if data_provata < adesso:
        anno += 1
        data_provata = datetime.datetime(anno, mese, giorno_mese)
    return data_provata.strftime("%Y-%m-%d")

def controlla_meteo(citta):
    url_coordinate = "https://geocoding-api.open-meteo.com/v1/search?name=" + citta + "&count=1&language=it"
    risposta_coord = requests.get(url_coordinate)
    dati_coord = risposta_coord.json()
    lat = dati_coord["results"][0]["latitude"]
    lon = dati_coord["results"][0]["longitude"]
    url_meteo = "https://api.open-meteo.com/v1/forecast?latitude=" + str(lat) + "&longitude=" + str(lon) + "&current=temperature_2m,precipitation&timezone=Europe/Rome"
    risposta_meteo = requests.get(url_meteo)
    dati_meteo = risposta_meteo.json()
    temperatura = dati_meteo["current"]["temperature_2m"]
    pioggia = dati_meteo["current"]["precipitation"]
    return "A " + citta + ": " + str(temperatura) + "C, Pioggia: " + str(pioggia) + "mm"

# ============================================
# TOOLS RIFIUTI (su JSON, come deciso)
# ============================================
def imposta_alert_rifiuti(ora):
    with open(FILE_CONFIG_RIFIUTI, "r") as f:
        config = json.load(f)
    config["ora_alert"] = ora
    with open(FILE_CONFIG_RIFIUTI, "w") as f:
        json.dump(config, f, indent=2)
    return "Orario alert rifiuti impostato alle " + ora

def mostra_config_rifiuti():
    with open(FILE_CONFIG_RIFIUTI, "r") as f:
        config = json.load(f)
    return "L'alert rifiuti e impostato alle " + config["ora_alert"]

def cosa_buttare(giorni_da_oggi=0):
    adesso = datetime.datetime.now() + datetime.timedelta(hours=2) + datetime.timedelta(days=giorni_da_oggi)
    giorno_settimana = adesso.weekday()
    numero_settimana = adesso.isocalendar()[1]
    settimana_pari = (numero_settimana % 2 == 0)
    rifiuti = []
    if giorno_settimana == 1:
        rifiuti.append("carta")
        rifiuti.append("indifferenziata")
    if giorno_settimana == 0 and settimana_pari:
        rifiuti.append("vetro")
    if giorno_settimana == 3:
        rifiuti.append("plastica")
        rifiuti.append("umido")
    if giorno_settimana == 6:
        rifiuti.append("umido")
    return rifiuti

def cosa_buttare_oggi():
    return cosa_buttare(0)

# ============================================
# GESTIONE CHAT_ID E PASSWORD
# ============================================
def registra_chat_id(nome, chat_id, nome_bot):
    with open(FILE_CHAT_IDS, "r") as f:
        chat_ids = json.load(f)
    chat_ids[nome] = {"chat_id": chat_id, "bot": nome_bot}
    with open(FILE_CHAT_IDS, "w") as f:
        json.dump(chat_ids, f, indent=2)

def get_tutti_con_bot():
    with open(FILE_CHAT_IDS, "r") as f:
        chat_ids = json.load(f)
    return chat_ids

def chat_id_autorizzato(chat_id):
    with open(FILE_CHAT_IDS, "r") as f:
        chat_ids = json.load(f)
    for persona, info in chat_ids.items():
        if info["chat_id"] == chat_id:
            return True
    return False

def prova_registrazione(messaggio, chat_id, nome_persona):
    if messaggio.strip() == PASSWORD_FAMIGLIA:
        registra_chat_id(nome_persona, chat_id, nome_persona)
        return True
    return False

# ============================================
# CONTROLLO SOVRAPPOSIZIONI (legge da Google Calendar, con buffer 30 min)
# ============================================
def orari_si_sovrappongono(ora1_inizio, ora1_fine, ora2_inizio, ora2_fine, buffer_minuti=30):
    def a_minuti(ora_str):
        h, m = ora_str.split(":")
        return int(h) * 60 + int(m)

    i1 = a_minuti(ora1_inizio) - buffer_minuti
    f1 = a_minuti(ora1_fine) + buffer_minuti
    i2 = a_minuti(ora2_inizio) - buffer_minuti
    f2 = a_minuti(ora2_fine) + buffer_minuti

    return i1 < f2 and i2 < f1

def controlla_sovrapposizione(persona, data, ora):
    ora_inizio_nuovo = ora
    ora_fine_obj = datetime.datetime.strptime(ora, "%H:%M") + datetime.timedelta(hours=1)
    ora_fine_nuovo = ora_fine_obj.strftime("%H:%M")

    conflitti = []

    try:
        # Controlla eventi singoli su Calendar
        eventi_result = servizio_calendar.events().list(
            calendarId=ID_CALENDARIO,
            timeMin=data + "T00:00:00Z",
            timeMax=data + "T23:59:59Z",
            singleEvents=True
        ).execute()
        for e in eventi_result.get('items', []):
            if 'dateTime' in e.get('start', {}):
                e_ora_inizio = e['start']['dateTime'][11:16]
                e_ora_fine = e['end']['dateTime'][11:16]
                if orari_si_sovrappongono(ora_inizio_nuovo, ora_fine_nuovo, e_ora_inizio, e_ora_fine):
                    conflitti.append(e['summary'] + " (evento)")

        # Controlla ricorrenti su Calendar
        giorno_richiesto = giorno_settimana_di(data)
        eventi_ricorrenti = servizio_calendar.events().list(
            calendarId=ID_CALENDARIO,
            maxResults=50,
            singleEvents=False
        ).execute()
        giorni_rrule_inv = {"MO": "lunedi", "TU": "martedi", "WE": "mercoledi", "TH": "giovedi", "FR": "venerdi", "SA": "sabato", "SU": "domenica"}
        for e in eventi_ricorrenti.get('items', []):
            if 'recurrence' in e and 'dateTime' in e.get('start', {}):
                e_ora_inizio = e['start']['dateTime'][11:16]
                e_ora_fine = e['end']['dateTime'][11:16]
                regola = e['recurrence'][0]
                for codice, nome_giorno in giorni_rrule_inv.items():
                    if codice in regola and nome_giorno == giorno_richiesto:
                        if orari_si_sovrappongono(ora_inizio_nuovo, ora_fine_nuovo, e_ora_inizio, e_ora_fine):
                            conflitti.append(e['summary'] + " (ricorrente)")
    except Exception as e:
        print("Errore controllo sovrapposizione:", str(e))

    if len(conflitti) > 0:
        return "ATTENZIONE: il " + data + " alle " + ora + " ci sono gia questi impegni in famiglia (considerando 30 min di margine): " + " | ".join(conflitti) + ". Vuoi salvare comunque?"
    return None

def imposta_alert_personalizzato(nome_evento, minuti_prima, data=None):
    if data:
        eventi_result = servizio_calendar.events().list(
            calendarId=ID_CALENDARIO, timeMin=data + "T00:00:00Z",
            timeMax=data + "T23:59:59Z", singleEvents=True
        ).execute()
        eventi = eventi_result.get('items', [])
        for e in eventi:
            if nome_evento.lower() in e['summary'].lower():
                e['reminders'] = {
                    'useDefault': False,
                    'overrides': [{'method': 'popup', 'minutes': minuti_prima}]
                }
                servizio_calendar.events().update(calendarId=ID_CALENDARIO, eventId=e['id'], body=e).execute()
                return "Alert impostato: " + str(minuti_prima) + " minuti prima per " + e['summary']
        return "Evento non trovato: " + nome_evento + " del " + data
    else:
        eventi_result = servizio_calendar.events().list(
            calendarId=ID_CALENDARIO, maxResults=50, singleEvents=False
        ).execute()
        eventi = eventi_result.get('items', [])
        for e in eventi:
            if 'recurrence' in e and nome_evento.lower() in e['summary'].lower():
                e['reminders'] = {
                    'useDefault': False,
                    'overrides': [{'method': 'popup', 'minutes': minuti_prima}]
                }
                servizio_calendar.events().update(calendarId=ID_CALENDARIO, eventId=e['id'], body=e).execute()
                return "Alert impostato: " + str(minuti_prima) + " minuti prima per " + e['summary'] + " (ricorrente, si applica a tutte le occorrenze future)"
        return "Ricorrente non trovato: " + nome_evento

# ============================================
# TOOLS EVENTI SINGOLI (Google Calendar)
# ============================================
COLORI_PERSONE = {
    "cecilia": "4",   # Flamingo (rosa)
    "chicco": "5",    # Banana (giallo)
    "samuele": "7",   # Peacock (azzurro/turchese)
    "anna": "3"       # Grape (viola)
}

def colore_di(persona):
    return COLORI_PERSONE.get(persona.lower())

def salva_evento_singolo_calendar(persona, evento, data, ora, forza=False):
    if not forza:
        conflitto = controlla_sovrapposizione(persona, data, ora)
        if conflitto:
            return conflitto
    datetime_inizio_obj = datetime.datetime.strptime(data + " " + ora, "%Y-%m-%d %H:%M")
    datetime_fine_obj = datetime_inizio_obj + datetime.timedelta(hours=1)
    datetime_inizio = datetime_inizio_obj.strftime("%Y-%m-%dT%H:%M:00")
    datetime_fine = datetime_fine_obj.strftime("%Y-%m-%dT%H:%M:00")
    evento_calendar = {
        'summary': persona + ": " + evento,
        'description': 'Evento per ' + persona,
        'start': {'dateTime': datetime_inizio, 'timeZone': 'Europe/Rome'},
        'end': {'dateTime': datetime_fine, 'timeZone': 'Europe/Rome'},
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 24 * 60},
                {'method': 'popup', 'minutes': 60},
            ],
        },
    }
    colore = colore_di(persona)
    if colore:
        evento_calendar['colorId'] = colore
    servizio_calendar.events().insert(calendarId=ID_CALENDARIO, body=evento_calendar).execute()
    return persona + ": " + evento + " il " + data + " alle " + ora + " salvato su Calendar!"

def mostra_eventi_calendar():
    adesso = datetime.datetime.now() + datetime.timedelta(hours=2)
    tempo_min = adesso.isoformat() + 'Z'
    tempo_max = (adesso + datetime.timedelta(days=14)).isoformat() + 'Z'

    eventi_result = servizio_calendar.events().list(
        calendarId=ID_CALENDARIO, timeMin=tempo_min, timeMax=tempo_max,
        maxResults=20, singleEvents=True, orderBy='startTime'
    ).execute()

    eventi = eventi_result.get('items', [])
    eventi_singoli = [e for e in eventi if 'recurringEventId' not in e]

    if not eventi_singoli:
        return "Nessun evento singolo nei prossimi 14 giorni."

    testo = "Prossimi eventi (prossimi 14 giorni):\n"
    for e in eventi_singoli:
        inizio = e['start'].get('dateTime', e['start'].get('date'))
        testo += "- " + e['summary'] + " il " + inizio[:16].replace("T", " alle ") + "\n"
    return testo

def cancella_evento_calendar(nome_evento, data):
    eventi_result = servizio_calendar.events().list(
        calendarId=ID_CALENDARIO, timeMin=data + "T00:00:00Z",
        timeMax=data + "T23:59:59Z", singleEvents=True
    ).execute()
    eventi = eventi_result.get('items', [])
    for e in eventi:
        if nome_evento.lower() in e['summary'].lower():
            servizio_calendar.events().delete(calendarId=ID_CALENDARIO, eventId=e['id']).execute()
            return "Evento cancellato: " + e['summary']
    return "Evento non trovato: " + nome_evento + " del " + data

def modifica_evento_calendar(evento, data_vecchia, nuova_data=None, nuova_ora=None, nuovo_evento=None):
    eventi_result = servizio_calendar.events().list(
        calendarId=ID_CALENDARIO, timeMin=data_vecchia + "T00:00:00Z",
        timeMax=data_vecchia + "T23:59:59Z", singleEvents=True
    ).execute()
    eventi = eventi_result.get('items', [])
    for e in eventi:
        if evento.lower() in e['summary'].lower():
            if nuovo_evento:
                parti = e['summary'].split(": ", 1)
                persona = parti[0] if len(parti) > 1 else ""
                e['summary'] = persona + ": " + nuovo_evento if persona else nuovo_evento
            if nuova_data or nuova_ora:
                data_attuale = e['start']['dateTime'][:10]
                ora_attuale = e['start']['dateTime'][11:16]
                data_finale = nuova_data if nuova_data else data_attuale
                ora_finale = nuova_ora if nuova_ora else ora_attuale
                datetime_inizio_obj = datetime.datetime.strptime(data_finale + " " + ora_finale, "%Y-%m-%d %H:%M")
                datetime_fine_obj = datetime_inizio_obj + datetime.timedelta(hours=1)
                e['start']['dateTime'] = datetime_inizio_obj.strftime("%Y-%m-%dT%H:%M:00")
                e['end']['dateTime'] = datetime_fine_obj.strftime("%Y-%m-%dT%H:%M:00")
            servizio_calendar.events().update(calendarId=ID_CALENDARIO, eventId=e['id'], body=e).execute()
            return "Evento modificato con successo su Calendar!"
    return "Evento non trovato: " + evento + " del " + data_vecchia

# ============================================
# TOOLS RICORRENTI FAMILIARI (Google Calendar)
# ============================================
def salva_ricorrente_calendar(persona, attivita, giorno, ora, data_inizio=None, data_fine=None):
    giorni_rrule = {
        "lunedi": "MO", "martedi": "TU", "mercoledi": "WE",
        "giovedi": "TH", "venerdi": "FR", "sabato": "SA", "domenica": "SU"
    }
    giorno_codice = giorni_rrule.get(giorno.lower())
    if not giorno_codice:
        return "Giorno non valido: " + giorno
    giorni_settimana_num = {"lunedi": 0, "martedi": 1, "mercoledi": 2, "giovedi": 3, "venerdi": 4, "sabato": 5, "domenica": 6}
    giorno_target = giorni_settimana_num[giorno.lower()]
    if data_inizio:
        riferimento = datetime.datetime.strptime(data_inizio, "%Y-%m-%d")
    else:
        riferimento = datetime.datetime.now() + datetime.timedelta(hours=2)
    giorni_da_aggiungere = (giorno_target - riferimento.weekday()) % 7
    prima_data = riferimento + datetime.timedelta(days=giorni_da_aggiungere)
    ora_inizio_completo = datetime.datetime.strptime(prima_data.strftime("%Y-%m-%d") + " " + ora, "%Y-%m-%d %H:%M")
    ora_fine_completo = ora_inizio_completo + datetime.timedelta(hours=1)
    datetime_inizio = ora_inizio_completo.strftime("%Y-%m-%dT%H:%M:00")
    datetime_fine = ora_fine_completo.strftime("%Y-%m-%dT%H:%M:00")
    if not data_fine:
        data_fine_obj = riferimento + datetime.timedelta(days=300)
        data_fine = data_fine_obj.strftime("%Y-%m-%d")
    data_fine_obj = datetime.datetime.strptime(data_fine, "%Y-%m-%d")
    regola = 'RRULE:FREQ=WEEKLY;BYDAY=' + giorno_codice + ';UNTIL=' + data_fine_obj.strftime("%Y%m%d") + 'T235959Z'
    evento_calendar = {
        'summary': persona + ": " + attivita,
        'description': 'Attivita ricorrente di ' + persona,
        'start': {'dateTime': datetime_inizio, 'timeZone': 'Europe/Rome'},
        'end': {'dateTime': datetime_fine, 'timeZone': 'Europe/Rome'},
        'recurrence': [regola],
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'popup', 'minutes': 12 * 60},
                {'method': 'popup', 'minutes': 60},
            ],
        },
    }
    colore = colore_di(persona)
    if colore:
        evento_calendar['colorId'] = colore
    servizio_calendar.events().insert(calendarId=ID_CALENDARIO, body=evento_calendar).execute()
    return persona + ": " + attivita + " ogni " + giorno + " alle " + ora + " dal " + prima_data.strftime("%Y-%m-%d") + " al " + data_fine + " salvato su Calendar!"

def mostra_ricorrenti_calendar():
    eventi_result = servizio_calendar.events().list(
        calendarId=ID_CALENDARIO, maxResults=50, singleEvents=False
    ).execute()
    eventi = eventi_result.get('items', [])
    ricorrenti = [e for e in eventi if 'recurrence' in e]
    if not ricorrenti:
        return "Nessun appuntamento ricorrente al momento."
    giorni_rrule_inv = {"MO": "lunedi", "TU": "martedi", "WE": "mercoledi", "TH": "giovedi", "FR": "venerdi", "SA": "sabato", "SU": "domenica"}
    testo = "Appuntamenti ricorrenti:\n"
    for e in ricorrenti:
        inizio = e['start'].get('dateTime', '')
        ora = inizio[11:16] if inizio else ""
        giorno_testo = ""
        if e.get('recurrence'):
            regola = e['recurrence'][0]
            for codice, nome_giorno in giorni_rrule_inv.items():
                if "BYDAY=" + codice in regola:
                    giorno_testo = " ogni " + nome_giorno
                    break
        testo += "- " + e['summary'] + giorno_testo + " alle " + ora + "\n"
    return testo

def cancella_ricorrente_calendar(persona, attivita):
    eventi_result = servizio_calendar.events().list(
        calendarId=ID_CALENDARIO, q=persona + ": " + attivita,
        maxResults=10, singleEvents=False
    ).execute()
    eventi = eventi_result.get('items', [])
    for e in eventi:
        if persona.lower() in e['summary'].lower() and attivita.lower() in e['summary'].lower():
            servizio_calendar.events().delete(calendarId=ID_CALENDARIO, eventId=e['id']).execute()
            return "Ricorrente cancellato: " + e['summary']
    return "Ricorrente non trovato: " + persona + " - " + attivita

# ============================================
# RIEPILOGO COMPLETO
# ============================================
def mostra_tutto():
    testo = "RIEPILOGO COMPLETO\n\n"
    testo += "RIFIUTI OGGI:\n"
    rifiuti = cosa_buttare_oggi()
    if len(rifiuti) == 0:
        testo += "Niente da buttare oggi\n"
    else:
        testo += ", ".join(rifiuti) + "\n"
    testo += "\nRICORRENTI:\n"
    testo += mostra_ricorrenti_calendar()
    testo += "\nPROSSIMI EVENTI:\n"
    testo += mostra_eventi_calendar()
    return testo

# ============================================
# LISTA TOOLS
# ============================================
tools = [
    {"name": "che_ore_sono", "description": "Usa questo tool quando l utente chiede che ore sono", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "data_oggi", "description": "Usa questo tool quando hai bisogno della data di oggi o quando l utente dice domani, dopodomani, tra X giorni", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "controlla_meteo", "description": "Usa questo tool per qualsiasi domanda sul meteo", "input_schema": {"type": "object", "properties": {"citta": {"type": "string", "description": "Il nome della citta"}}, "required": ["citta"]}},
    {"name": "cosa_buttare", "description": "Usa questo tool quando l utente chiede cosa buttare, oggi o in un altro giorno (domani, dopodomani)", "input_schema": {"type": "object", "properties": {"giorni_da_oggi": {"type": "integer", "description": "0 per oggi, 1 per domani, 2 per dopodomani, ecc"}}, "required": ["giorni_da_oggi"]}},
    {"name": "imposta_alert_rifiuti", "description": "Usa questo tool quando l utente vuole cambiare l orario dell alert rifiuti", "input_schema": {"type": "object", "properties": {"ora": {"type": "string", "description": "Ora in formato HH:MM"}}, "required": ["ora"]}},
    {"name": "mostra_config_rifiuti", "description": "Usa questo tool quando l utente chiede a che ora e impostato l alert rifiuti", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "salva_ricorrente_familiare", "description": "Usa questo tool quando l utente vuole salvare un appuntamento ricorrente settimanale per un familiare (es. corso di nuoto ogni martedi). Se l utente specifica un periodo (es. da settembre a maggio), usa data_inizio e data_fine", "input_schema": {"type": "object", "properties": {"persona": {"type": "string", "description": "Nome della persona: Samuele, Anna, Cecilia o Chicco"}, "attivita": {"type": "string", "description": "Nome dell attivita"}, "giorno": {"type": "string", "description": "Giorno della settimana in minuscolo: lunedi, martedi, ecc"}, "ora": {"type": "string", "description": "Ora in formato HH:MM"}, "data_inizio": {"type": "string", "description": "Data di inizio opzionale in formato YYYY-MM-DD"}, "data_fine": {"type": "string", "description": "Data di fine opzionale in formato YYYY-MM-DD"}}, "required": ["persona", "attivita", "giorno", "ora"]}},
    {"name": "mostra_ricorrenti_familiari", "description": "Usa questo tool quando l utente vuole vedere tutti gli appuntamenti ricorrenti familiari", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cancella_ricorrente_familiare", "description": "Usa questo tool quando l utente vuole cancellare un appuntamento ricorrente familiare", "input_schema": {"type": "object", "properties": {"persona": {"type": "string"}, "attivita": {"type": "string"}}, "required": ["persona", "attivita"]}},
    {"name": "salva_evento_singolo", "description": "Usa questo tool quando l utente vuole salvare un evento specifico. Se il tool risponde con un AVVISO di sovrapposizione, chiedi conferma all utente prima di richiamarlo di nuovo con forza=true", "input_schema": {"type": "object", "properties": {"persona": {"type": "string", "description": "Nome della persona coinvolta"}, "evento": {"type": "string", "description": "Descrizione dell evento"}, "data": {"type": "string", "description": "Data in formato YYYY-MM-DD"}, "ora": {"type": "string", "description": "Ora in formato HH:MM"}, "forza": {"type": "boolean", "description": "Metti true solo se l utente ha gia confermato di voler salvare nonostante il conflitto"}}, "required": ["persona", "evento", "data", "ora"]}},
    {"name": "mostra_eventi_singoli", "description": "Usa questo tool quando l utente vuole vedere la lista dei prossimi eventi singoli/occasionali (non ricorrenti) nei prossimi 14 giorni. Per gli appuntamenti fissi/ricorrenti usa invece mostra_ricorrenti_familiari", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cancella_evento_singolo", "description": "Usa questo tool quando l utente vuole cancellare un evento specifico", "input_schema": {"type": "object", "properties": {"evento": {"type": "string"}, "data": {"type": "string"}}, "required": ["evento", "data"]}},
    {"name": "mostra_tutto", "description": "Usa questo tool quando l utente chiede un riepilogo generale di tutto: rifiuti, appuntamenti ricorrenti ed eventi", "input_schema": {"type": "object", "properties": {}, "required": []}},
    {"name": "giorno_settimana_di", "description": "Usa questo tool per scoprire che giorno della settimana cade una data specifica. Usalo SEMPRE prima di salvare un evento per verificare che il giorno della settimana indicato dall utente sia corretto", "input_schema": {"type": "object", "properties": {"data": {"type": "string", "description": "Data in formato YYYY-MM-DD"}}, "required": ["data"]}},
    {"name": "prossima_data_con_giorno_mese", "description": "Usa questo tool per calcolare la data esatta futura (YYYY-MM-DD) di un giorno e mese specifico, quando l utente non specifica l anno", "input_schema": {"type": "object", "properties": {"giorno_mese": {"type": "integer", "description": "Il numero del giorno del mese, es 15"}, "mese": {"type": "integer", "description": "Il numero del mese, es 11 per novembre"}}, "required": ["giorno_mese", "mese"]}},
    {"name": "modifica_evento_singolo", "description": "Usa questo tool quando l utente vuole correggere/modificare un evento gia esistente (data, ora, nome evento), SENZA cancellare e ricreare. Cambia SOLO i campi specificati dall utente, lascia intatti gli altri", "input_schema": {"type": "object", "properties": {"evento": {"type": "string", "description": "Nome evento attuale, per trovarlo"}, "data_vecchia": {"type": "string", "description": "Data attuale dell evento YYYY-MM-DD, per trovarlo"}, "nuova_data": {"type": "string", "description": "Nuova data, opzionale"}, "nuova_ora": {"type": "string", "description": "Nuova ora, opzionale"}, "nuovo_evento": {"type": "string", "description": "Nuovo nome evento, opzionale"}}, "required": ["evento", "data_vecchia"]}},
    {"name": "imposta_alert_personalizzato", "description": "Usa questo tool quando l utente vuole impostare un alert/notifica personalizzato per un evento specifico (singolo o ricorrente), specificando quanti minuti prima vuole essere avvisato. Se l utente dice ore, converti in minuti (es. 2 ore = 120 minuti)", "input_schema": {"type": "object", "properties": {"nome_evento": {"type": "string", "description": "Nome o parte del nome dell evento"}, "minuti_prima": {"type": "integer", "description": "Quanti minuti prima dell evento mandare la notifica"}, "data": {"type": "string", "description": "Data dell evento in formato YYYY-MM-DD, SOLO se e un evento singolo. Omettere per i ricorrenti"}}, "required": ["nome_evento", "minuti_prima"]}}
]

# ============================================
# AGENTE PRINCIPALE
# ============================================
conversazione = []
ultimo_messaggio_timestamp = None
TIMEOUT_MINUTI = 10

def agente(messaggio, nome_utente="Utente"):
    global ultimo_messaggio_timestamp
    adesso = datetime.datetime.now()
    if ultimo_messaggio_timestamp is not None:
        minuti_passati = (adesso - ultimo_messaggio_timestamp).total_seconds() / 60
        if minuti_passati > TIMEOUT_MINUTI:
            conversazione.clear()
    ultimo_messaggio_timestamp = adesso

    conversazione.append({"role": "user", "content": messaggio})
    testo = ""

    for _ in range(5):
        risposta = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system="Sei l assistente della famiglia. Aiuti a gestire rifiuti, appuntamenti ricorrenti ed eventi singoli per Samuele, Anna, Cecilia e Chicco. L utente che ti sta scrivendo in questo momento si chiama " + nome_utente + ". Se l utente usa parole come io, mio, mia, per me, senza specificare esplicitamente un altro nome, significa che si riferisce SE STESSO cioe " + nome_utente + ", quindi usa " + nome_utente + " come persona nei tool. Rispondi sempre in italiano, in modo chiaro e sintetico. Usa SEMPRE i tools disponibili quando servono. IMPORTANTE: quando l utente ti dice una data senza specificare l anno, usa SEMPRE PRIMA il tool data_oggi per sapere l anno corrente, poi calcola la data corretta nel futuro. Non usare mai anni dal tuo addestramento, usa sempre l anno reale attuale. Quando l utente specifica un giorno della settimana insieme a una data, usa SEMPRE il tool giorno_settimana_di per verificare che il giorno indicato sia corretto. Se non corrisponde, AVVISA l utente dell errore e chiedi conferma su quale sia la data giusta, non salvare automaticamente. Quando l utente vuole correggere un dettaglio di un evento esistente, usa SEMPRE modifica_evento_singolo invece di cancellare e ricreare. Se salva_evento_singolo ritorna un messaggio di AVVISO/conflitto, chiedi sempre conferma all utente prima di richiamare di nuovo il tool con forza=true. Non dire mai che non puoi fare qualcosa se hai un tool per farlo.",
            tools=tools,
            messages=conversazione
        )

        if risposta.stop_reason == "tool_use":
            tool_results = []
            for block in risposta.content:
                if block.type == "tool_use":
                    nome_tool = block.name
                    inp = block.input

                    if nome_tool == "che_ore_sono":
                        risultato = che_ore_sono()
                    elif nome_tool == "data_oggi":
                        risultato = data_oggi()
                    elif nome_tool == "giorno_settimana_di":
                        risultato = giorno_settimana_di(inp["data"])
                    elif nome_tool == "prossima_data_con_giorno_mese":
                        risultato = prossima_data_con_giorno_mese(inp["giorno_mese"], inp["mese"])
                    elif nome_tool == "controlla_meteo":
                        risultato = controlla_meteo(inp["citta"])
                    elif nome_tool == "cosa_buttare":
                        rifiuti = cosa_buttare(inp.get("giorni_da_oggi", 0))
                        risultato = "Niente da buttare" if len(rifiuti) == 0 else ", ".join(rifiuti)
                    elif nome_tool == "imposta_alert_rifiuti":
                        risultato = imposta_alert_rifiuti(inp["ora"])
                    elif nome_tool == "mostra_config_rifiuti":
                        risultato = mostra_config_rifiuti()
                    elif nome_tool == "salva_evento_singolo":
                        risultato = salva_evento_singolo_calendar(
                            inp["persona"], inp["evento"], inp["data"], inp["ora"],
                            forza=inp.get("forza", False)
                        )
                    elif nome_tool == "mostra_eventi_singoli":
                        risultato = mostra_eventi_calendar()
                    elif nome_tool == "cancella_evento_singolo":
                        risultato = cancella_evento_calendar(inp["evento"], inp["data"])
                    elif nome_tool == "modifica_evento_singolo":
                        risultato = modifica_evento_calendar(
                            inp["evento"], inp["data_vecchia"],
                            inp.get("nuova_data"), inp.get("nuova_ora"),
                            inp.get("nuovo_evento")
                        )
                    elif nome_tool == "imposta_alert_personalizzato":
                        risultato = imposta_alert_personalizzato(
                            inp["nome_evento"], inp["minuti_prima"], inp.get("data")
                        )
                    elif nome_tool == "salva_ricorrente_familiare":
                        risultato = salva_ricorrente_calendar(
                            inp["persona"], inp["attivita"], inp["giorno"], inp["ora"],
                            inp.get("data_inizio"), inp.get("data_fine")
                        )
                    elif nome_tool == "mostra_ricorrenti_familiari":
                        risultato = mostra_ricorrenti_calendar()
                    elif nome_tool == "cancella_ricorrente_familiare":
                        risultato = cancella_ricorrente_calendar(inp["persona"], inp["attivita"])
                    elif nome_tool == "mostra_tutto":
                        risultato = mostra_tutto()
                    else:
                        risultato = "Tool non trovato"

                    print("Tool chiamato:", nome_tool, "- Risultato:", risultato)
                    tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": risultato})

            conversazione.append({"role": "assistant", "content": risposta.content})
            conversazione.append({"role": "user", "content": tool_results})
        else:
            testo = risposta.content[0].text
            break

    conversazione.append({"role": "assistant", "content": testo})
    return testo

# ============================================
# ALERT AUTOMATICO RIFIUTI
# ============================================
bot_instances = {}  # mappa nome persona -> oggetto bot corrispondente

async def controlla_alert_rifiuti():
    while True:
        try:
            with open(FILE_CONFIG_RIFIUTI, "r") as f:
                config = json.load(f)

            adesso = datetime.datetime.now() + datetime.timedelta(hours=2)
            ora_attuale = adesso.strftime("%H:%M")
            oggi_str = adesso.strftime("%Y-%m-%d")

            ora_target = config["ora_alert"]
            ultimo_inviato = config.get("ultimo_alert_inviato")

            if ora_attuale >= ora_target and ultimo_inviato != oggi_str:
                rifiuti = cosa_buttare_oggi()
                if len(rifiuti) > 0:
                    testo_rifiuti = ", ".join(rifiuti)
                    messaggio = "Promemoria rifiuti: oggi si butta " + testo_rifiuti + "!"

                    chat_ids_info = get_tutti_con_bot()
                    for persona, info in chat_ids_info.items():
                        try:
                            bot_da_usare = bot_instances.get(info["bot"])
                            if bot_da_usare:
                                await bot_da_usare.send_message(chat_id=info["chat_id"], text=messaggio)
                        except Exception as e:
                            print("Errore invio alert rifiuti a", persona, ":", str(e))

                config["ultimo_alert_inviato"] = oggi_str
                with open(FILE_CONFIG_RIFIUTI, "w") as f:
                    json.dump(config, f, indent=2)

        except Exception as e:
            print("Errore controllo alert rifiuti:", str(e))

        await asyncio.sleep(60)

# ============================================
# DUE BOT TELEGRAM CON NOTIFICA INCROCIATA
# ============================================
async def notifica_tutti(testo, nome_mittente):
    chat_ids_info = get_tutti_con_bot()
    for persona, info in chat_ids_info.items():
        try:
            bot_da_usare = bot_instances.get(info["bot"])
            if bot_da_usare:
                await bot_da_usare.send_message(chat_id=info["chat_id"], text=testo)
        except Exception as e:
            print("Errore invio a", persona, ":", str(e))

async def gestisci_messaggio(update: Update, context: ContextTypes.DEFAULT_TYPE, nome_bot):
    chat_id = update.effective_chat.id
    messaggio = update.message.text

    if not chat_id_autorizzato(chat_id):
        if prova_registrazione(messaggio, chat_id, nome_bot):
            await update.message.reply_text("Accesso autorizzato! Benvenuto nel Family Bot")
        else:
            await update.message.reply_text("Accesso negato. Scrivi la parola segreta per accedere.")
        return

    risposta_agente = agente(messaggio, nome_bot)

    parole_chiave_salvataggio = ["salvato", "modificato", "cancellato"]
    if any(parola in risposta_agente.lower() for parola in parole_chiave_salvataggio):
        messaggio_notifica = nome_bot + " ha fissato/modificato un appuntamento:\n" + risposta_agente
        await notifica_tutti(messaggio_notifica, nome_bot)
    else:
        await update.message.reply_text(risposta_agente)

async def rispondi_bot1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await gestisci_messaggio(update, context, "Anna")

async def rispondi_bot2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await gestisci_messaggio(update, context, "Samuele")

async def main():
    app1 = ApplicationBuilder().token(TELEGRAM_TOKEN_1).build()
    app1.add_handler(MessageHandler(filters.TEXT, rispondi_bot1))

    app2 = ApplicationBuilder().token(TELEGRAM_TOKEN_2).build()
    app2.add_handler(MessageHandler(filters.TEXT, rispondi_bot2))

    await app1.initialize()
    await app1.start()
    await app1.updater.start_polling(drop_pending_updates=True)

    await app2.initialize()
    await app2.start()
    await app2.updater.start_polling(drop_pending_updates=True)

    bot_instances["Anna"] = app1.bot
    bot_instances["Samuele"] = app2.bot

    print("Entrambi i bot avviati!")

    asyncio.ensure_future(controlla_alert_rifiuti())

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
