"""
Sei Unica Parrucchieri - Salon Manager
Versione commentata per imparare Python!
Vedi IMPARA_PYTHON.md per la guida completa.
"""

# === IMPORTAZIONI ===
import os, sys, json, sqlite3, shutil, csv, io, hashlib, secrets, re
from datetime import datetime, date, timedelta
from functools import wraps

try:
    from flask import Flask, render_template, request, jsonify, send_file, redirect
except ImportError:
    print("Installazione Flask in corso...")
    os.system(f"{sys.executable} -m pip install flask")
    from flask import Flask, render_template, request, jsonify, send_file, redirect

try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# === CONFIGURAZIONE ===
APP_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR     = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", APP_DIR)
DB_PATH      = os.path.join(DATA_DIR, "salone.db")
BACKUP_DIR   = os.path.join(DATA_DIR, "backups")
STATIC_DIR   = os.path.join(APP_DIR, "static")
TEMPLATE_DIR = os.path.join(APP_DIR, "templates")

app = Flask(__name__, static_folder=STATIC_DIR, template_folder=TEMPLATE_DIR)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

# === SICUREZZA ===

# PIN di accesso (opzionale): impostare qui un PIN numerico per proteggere l'app.
# Se vuoto o None, nessuna autenticazione richiesta.
PIN_ACCESSO = os.environ.get("SEI_UNICA_PIN", "")  # es. "1234"

# Token di sessione generato all'avvio (cambia ogni restart)
SESSION_TOKEN = secrets.token_hex(16) if PIN_ACCESSO else None

def richiedi_pin(f):
    """Decoratore: protegge un endpoint con il PIN (se configurato)"""
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not PIN_ACCESSO:
            return f(*args, **kwargs)
        token = request.headers.get("X-Auth-Token") or request.args.get("token")
        if token != SESSION_TOKEN:
            return jsonify({"ok": False, "error": "Non autorizzato. PIN richiesto."}), 401
        return f(*args, **kwargs)
    return wrapper

# --- Validazione input ---

def valida_stringa(valore, nome_campo, max_len=500, obbligatorio=False):
    """Valida una stringa: tipo, lunghezza, sanitizzazione base"""
    if valore is None:
        if obbligatorio:
            raise ValueError(f"Il campo '{nome_campo}' è obbligatorio")
        return ""
    if not isinstance(valore, str):
        raise ValueError(f"Il campo '{nome_campo}' deve essere testo")
    v = valore.strip()
    if obbligatorio and not v:
        raise ValueError(f"Il campo '{nome_campo}' non può essere vuoto")
    if len(v) > max_len:
        raise ValueError(f"Il campo '{nome_campo}' è troppo lungo (max {max_len} caratteri)")
    return v

def valida_numero(valore, nome_campo, minimo=0, massimo=999999):
    """Valida un valore numerico"""
    if valore is None:
        raise ValueError(f"Il campo '{nome_campo}' è obbligatorio")
    try:
        n = float(valore)
    except (TypeError, ValueError):
        raise ValueError(f"Il campo '{nome_campo}' deve essere un numero")
    if n < minimo or n > massimo:
        raise ValueError(f"Il campo '{nome_campo}' deve essere tra {minimo} e {massimo}")
    return n

def valida_data(valore, nome_campo):
    """Valida formato data YYYY-MM-DD"""
    v = valida_stringa(valore, nome_campo, 10, True)
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
        raise ValueError(f"Il campo '{nome_campo}' deve essere in formato AAAA-MM-GG")
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"Data non valida per '{nome_campo}'")
    return v

def valida_ora(valore, nome_campo):
    """Valida formato ora HH:MM"""
    v = valida_stringa(valore, nome_campo, 5, True)
    if not re.match(r'^\d{2}:\d{2}$', v):
        raise ValueError(f"Il campo '{nome_campo}' deve essere in formato HH:MM")
    return v

def errore_validazione(e):
    """Restituisce una risposta JSON per errori di validazione"""
    return jsonify({"ok": False, "error": str(e)}), 400

@app.errorhandler(413)
def file_troppo_grande(e):
    return jsonify({"ok": False, "error": "File troppo grande (max 16 MB)"}), 413

@app.errorhandler(500)
def errore_server(e):
    return jsonify({"ok": False, "error": "Errore interno del server"}), 500

# === DATABASE ===

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db

def init_db():
    """Crea le tabelle se non esistono (eseguita all'avvio)"""
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS clienti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            cognome TEXT NOT NULL,
            soprannome TEXT DEFAULT '',
            telefono TEXT DEFAULT '',
            email TEXT DEFAULT '',
            note TEXT DEFAULT '',
            foto TEXT DEFAULT NULL,
            metodo_pref TEXT DEFAULT '',
            creato TEXT DEFAULT (date('now')),
            attivo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS servizi (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            prezzo REAL NOT NULL,
            durata INTEGER NOT NULL,
            emoji TEXT DEFAULT '✂️',
            attivo INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS appuntamenti (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cliente_id INTEGER NOT NULL,
            data TEXT NOT NULL,
            ora TEXT NOT NULL,
            note TEXT DEFAULT '',
            completato INTEGER DEFAULT 0,
            metodo_pagamento TEXT DEFAULT NULL,
            creato TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (cliente_id) REFERENCES clienti(id)
        );
        CREATE TABLE IF NOT EXISTS appuntamento_servizi (
            appuntamento_id INTEGER NOT NULL,
            servizio_id INTEGER NOT NULL,
            prezzo_applicato REAL,
            PRIMARY KEY (appuntamento_id, servizio_id),
            FOREIGN KEY (appuntamento_id) REFERENCES appuntamenti(id) ON DELETE CASCADE,
            FOREIGN KEY (servizio_id) REFERENCES servizi(id)
        );
        CREATE TABLE IF NOT EXISTS magazzino (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emoji TEXT DEFAULT '📦',
            nome TEXT NOT NULL,
            qty INTEGER DEFAULT 0,
            costo REAL DEFAULT 0.0,
            fornitore TEXT DEFAULT '',
            note TEXT DEFAULT '',
            creato TEXT DEFAULT (date('now'))
        );
        CREATE TABLE IF NOT EXISTS impostazioni (
            chiave TEXT PRIMARY KEY,
            valore TEXT
        );
    """)

    # Migrazione colonne per DB esistenti
    for col, defval in [
        ("metodo_pagamento", "TEXT DEFAULT NULL"),
        ("metodo_pref",      "TEXT DEFAULT ''"),
    ]:
        try:
            db.execute(f"ALTER TABLE appuntamenti ADD COLUMN {col} {defval}")
            db.commit()
        except:
            pass

    # Servizi predefiniti (solo se vuota)
    if db.execute("SELECT COUNT(*) FROM servizi").fetchone()[0] == 0:
        servizi_default = [
            ("Taglio donna", 25, 45, "✂️"), ("Taglio uomo", 18, 30, "✂️"),
            ("Piega", 20, 40, "💇‍♀️"), ("Colore radici", 35, 60, "🎨"),
            ("Colore completo", 55, 90, "🎨"), ("Meches / Balayage", 70, 120, "✨"),
            ("Trattamento cheratina", 80, 90, "💎"), ("Permanente", 50, 120, "🌀"),
            ("Shampoo + Piega", 15, 30, "🧴"), ("Taglio bambino", 12, 20, "👶"),
            ("Acconciatura sposa", 120, 180, "👰"), ("Extension", 150, 180, "💫"),
        ]
        db.executemany("INSERT INTO servizi (nome, prezzo, durata, emoji) VALUES (?,?,?,?)", servizi_default)

    # Magazzino predefinito (solo se vuoto)
    if db.execute("SELECT COUNT(*) FROM magazzino").fetchone()[0] == 0:
        mag_default = [
            ("🟤", "Aquarely Castano",       8,  5.20, "Itely",     "Nuance 4N, 4CL, 4CH"),
            ("⚫", "Aquarely Nero",          5,  5.20, "Itely",     "Nuance 1N"),
            ("🟡", "Aquarely Biondo",        6,  5.20, "Itely",     "Nuance 8N, 9N, 10N"),
            ("🔴", "Aquarely Rosso",         4,  5.20, "Itely",     "Nuance 6RF, 7RF"),
            ("💧", "Acqua Ossigenata",       12, 2.80, "Fanola",    "Vol. 10, 20, 30, 40"),
            ("🧴", "Shampoo Professionale",  3,  12.00,"Kerastase", "Uso quotidiano"),
            ("🫙", "Maschera Ristrutturante",2,  18.00,"Schwarzkopf","Trattamento intensivo"),
        ]
        db.executemany(
            "INSERT INTO magazzino (emoji,nome,qty,costo,fornitore,note) VALUES (?,?,?,?,?,?)",
            mag_default
        )

    db.commit()
    db.close()


def backup_db():
    if not os.path.exists(DB_PATH):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(BACKUP_DIR, f"salone_backup_{ts}.db")
    shutil.copy2(DB_PATH, dst)
    backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.endswith('.db')])
    while len(backups) > 20:
        os.remove(os.path.join(BACKUP_DIR, backups.pop(0)))
    return dst


# === PAGINE ===

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/mobile")
def mobile():
    return render_template("mobile.html")


# === API AUTH & HEALTH ===

@app.route("/api/login", methods=["POST"])
def api_login():
    """Autenticazione con PIN — restituisce un token di sessione"""
    if not PIN_ACCESSO:
        return jsonify({"ok": True, "token": None, "protetto": False})
    data = request.json or {}
    pin = str(data.get("pin", "")).strip()
    if pin == PIN_ACCESSO:
        return jsonify({"ok": True, "token": SESSION_TOKEN, "protetto": True})
    return jsonify({"ok": False, "error": "PIN errato"}), 401

@app.route("/api/health")
def api_health():
    """Stato del server — utile per verificare se il backend è raggiungibile"""
    protetto = bool(PIN_ACCESSO)
    try:
        db = get_db()
        n = db.execute("SELECT COUNT(*) FROM clienti").fetchone()[0]
        db.close()
        return jsonify({"ok": True, "protetto": protetto, "db": True, "clienti": n,
                        "versione": "5.0", "aggiornato": date.today().isoformat()})
    except Exception:
        return jsonify({"ok": True, "protetto": protetto, "db": False})


# === API CLIENTI ===

@app.route("/api/clienti", methods=["GET"])
@richiedi_pin
def api_clienti():
    db = get_db()
    clienti = db.execute("SELECT * FROM clienti WHERE attivo=1 ORDER BY nome, cognome").fetchall()
    db.close()
    return jsonify([dict(c) for c in clienti])

@app.route("/api/clienti", methods=["POST"])
@richiedi_pin
def api_clienti_create():
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        nome = valida_stringa(data.get("nome"), "nome", 100, True)
        cognome = valida_stringa(data.get("cognome"), "cognome", 100, True)
        soprannome = valida_stringa(data.get("soprannome"), "soprannome", 100)
        telefono = valida_stringa(data.get("telefono"), "telefono", 30)
        email = valida_stringa(data.get("email"), "email", 200)
        note = valida_stringa(data.get("note"), "note", 2000)
        foto = data.get("foto")  # base64, validazione lunghezza gestita da MAX_CONTENT_LENGTH
        metodo_pref = valida_stringa(data.get("metodo_pref"), "metodo_pref", 50)
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    cur = db.execute(
        "INSERT INTO clienti (nome,cognome,soprannome,telefono,email,note,foto,metodo_pref) VALUES (?,?,?,?,?,?,?,?)",
        (nome, cognome, soprannome, telefono, email, note, foto, metodo_pref))
    db.commit()
    cliente = db.execute("SELECT * FROM clienti WHERE id=?", (cur.lastrowid,)).fetchone()
    db.close()
    return jsonify(dict(cliente))

@app.route("/api/clienti/<int:cid>", methods=["PUT"])
@richiedi_pin
def api_clienti_update(cid):
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        nome = valida_stringa(data.get("nome"), "nome", 100, True)
        cognome = valida_stringa(data.get("cognome"), "cognome", 100, True)
        soprannome = valida_stringa(data.get("soprannome"), "soprannome", 100)
        telefono = valida_stringa(data.get("telefono"), "telefono", 30)
        email = valida_stringa(data.get("email"), "email", 200)
        note = valida_stringa(data.get("note"), "note", 2000)
        foto = data.get("foto")
        metodo_pref = valida_stringa(data.get("metodo_pref"), "metodo_pref", 50)
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    db.execute(
        "UPDATE clienti SET nome=?,cognome=?,soprannome=?,telefono=?,email=?,note=?,foto=?,metodo_pref=? WHERE id=?",
        (nome, cognome, soprannome, telefono, email, note, foto, metodo_pref, cid))
    db.commit()
    cliente = db.execute("SELECT * FROM clienti WHERE id=?", (cid,)).fetchone()
    db.close()
    return jsonify(dict(cliente))

@app.route("/api/clienti/<int:cid>", methods=["DELETE"])
@richiedi_pin
def api_clienti_delete(cid):
    db = get_db()
    db.execute("UPDATE clienti SET attivo=0 WHERE id=?", (cid,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# === API SERVIZI ===

@app.route("/api/servizi", methods=["GET"])
@richiedi_pin
def api_servizi():
    db = get_db()
    servizi = db.execute("SELECT * FROM servizi WHERE attivo=1 ORDER BY nome").fetchall()
    db.close()
    return jsonify([dict(s) for s in servizi])

@app.route("/api/servizi", methods=["POST"])
@richiedi_pin
def api_servizi_create():
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        nome = valida_stringa(data.get("nome"), "nome", 100, True)
        prezzo = valida_numero(data.get("prezzo"), "prezzo", 0, 9999)
        durata = int(valida_numero(data.get("durata"), "durata", 1, 600))
        emoji = valida_stringa(data.get("emoji", "✂️"), "emoji", 10)
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    cur = db.execute("INSERT INTO servizi (nome,prezzo,durata,emoji) VALUES (?,?,?,?)",
        (nome, prezzo, durata, emoji))
    db.commit()
    s = db.execute("SELECT * FROM servizi WHERE id=?", (cur.lastrowid,)).fetchone()
    db.close()
    return jsonify(dict(s))

@app.route("/api/servizi/<int:sid>", methods=["PUT"])
@richiedi_pin
def api_servizi_update(sid):
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        nome = valida_stringa(data.get("nome"), "nome", 100, True)
        prezzo = valida_numero(data.get("prezzo"), "prezzo", 0, 9999)
        durata = int(valida_numero(data.get("durata"), "durata", 1, 600))
        emoji = valida_stringa(data.get("emoji", "✂️"), "emoji", 10)
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    db.execute("UPDATE servizi SET nome=?,prezzo=?,durata=?,emoji=? WHERE id=?",
        (nome, prezzo, durata, emoji, sid))
    db.commit()
    s = db.execute("SELECT * FROM servizi WHERE id=?", (sid,)).fetchone()
    db.close()
    return jsonify(dict(s))


# === API APPUNTAMENTI ===

@app.route("/api/appuntamenti", methods=["GET"])
@richiedi_pin
def api_appuntamenti():
    db = get_db()
    appts = db.execute("""
        SELECT a.*, c.nome as cliente_nome, c.cognome as cliente_cognome,
               c.soprannome as cliente_soprannome, c.telefono as cliente_telefono,
               c.foto as cliente_foto
        FROM appuntamenti a
        JOIN clienti c ON a.cliente_id = c.id
        ORDER BY a.data, a.ora
    """).fetchall()
    result = []
    for a in appts:
        d = dict(a)
        svcs = db.execute("""
            SELECT s.id, s.nome, s.emoji, s.durata, COALESCE(aps.prezzo_applicato, s.prezzo) as prezzo
            FROM appuntamento_servizi aps
            JOIN servizi s ON aps.servizio_id = s.id
            WHERE aps.appuntamento_id = ?
        """, (a["id"],)).fetchall()
        d["servizi"] = [dict(s) for s in svcs]
        result.append(d)
    db.close()
    return jsonify(result)

@app.route("/api/appuntamenti", methods=["POST"])
@richiedi_pin
def api_appuntamenti_create():
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        cliente_id = int(valida_numero(data.get("cliente_id"), "cliente_id", 1))
        data_app = valida_data(data.get("data"), "data")
        ora = valida_ora(data.get("ora"), "ora")
        note = valida_stringa(data.get("note"), "note", 2000)
        servizi_ids = data.get("servizi", [])
        if not isinstance(servizi_ids, list) or len(servizi_ids) == 0:
            raise ValueError("Selezionare almeno un servizio")
        for sid in servizi_ids:
            if not isinstance(sid, int) or sid < 1:
                raise ValueError("ID servizio non valido")
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    # Verifica che il cliente esista
    cl = db.execute("SELECT id FROM clienti WHERE id=? AND attivo=1", (cliente_id,)).fetchone()
    if not cl:
        db.close()
        return errore_validazione(ValueError("Cliente non trovato"))
    cur = db.execute("INSERT INTO appuntamenti (cliente_id,data,ora,note) VALUES (?,?,?,?)",
        (cliente_id, data_app, ora, note))
    appt_id = cur.lastrowid
    for sid in data.get("servizi", []):
        srv = db.execute("SELECT prezzo FROM servizi WHERE id=?", (sid,)).fetchone()
        db.execute("INSERT INTO appuntamento_servizi (appuntamento_id,servizio_id,prezzo_applicato) VALUES (?,?,?)",
            (appt_id, sid, srv["prezzo"] if srv else 0))
    db.commit()
    db.close()
    return jsonify({"id": appt_id})

@app.route("/api/appuntamenti/<int:aid>/toggle", methods=["POST"])
@richiedi_pin
def api_appuntamenti_toggle(aid):
    data = request.json or {}
    db = get_db()
    a = db.execute("SELECT completato FROM appuntamenti WHERE id=?", (aid,)).fetchone()
    if not a:
        db.close()
        return errore_validazione(ValueError("Appuntamento non trovato"))
    new_state = not bool(a["completato"])
    if new_state:
        metodo = valida_stringa(data.get("metodo_pagamento", "contanti"), "metodo_pagamento", 50)
        db.execute("UPDATE appuntamenti SET completato=1, metodo_pagamento=? WHERE id=?", (metodo, aid))
    else:
        db.execute("UPDATE appuntamenti SET completato=0, metodo_pagamento=NULL WHERE id=?", (aid,))
    db.commit()
    a = db.execute("SELECT completato, metodo_pagamento FROM appuntamenti WHERE id=?", (aid,)).fetchone()
    db.close()
    return jsonify({"completato": a["completato"], "metodo_pagamento": a["metodo_pagamento"]})

@app.route("/api/appuntamenti/<int:aid>", methods=["DELETE"])
@richiedi_pin
def api_appuntamenti_delete(aid):
    db = get_db()
    db.execute("DELETE FROM appuntamento_servizi WHERE appuntamento_id=?", (aid,))
    db.execute("DELETE FROM appuntamenti WHERE id=?", (aid,))
    db.commit()
    db.close()
    return jsonify({"ok": True})


# === API MAGAZZINO ===

@app.route("/api/magazzino", methods=["GET"])
@richiedi_pin
def api_magazzino():
    db = get_db()
    items = db.execute("SELECT * FROM magazzino ORDER BY nome").fetchall()
    db.close()
    return jsonify([dict(i) for i in items])

@app.route("/api/magazzino", methods=["POST"])
@richiedi_pin
def api_magazzino_create():
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        emoji = valida_stringa(data.get("emoji", "📦"), "emoji", 10)
        nome = valida_stringa(data.get("nome"), "nome", 200, True)
        qty = int(valida_numero(data.get("qty", 0), "quantità", 0, 99999))
        costo = valida_numero(data.get("costo", 0), "costo", 0, 99999)
        fornitore = valida_stringa(data.get("fornitore"), "fornitore", 200)
        note = valida_stringa(data.get("note"), "note", 2000)
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    cur = db.execute(
        "INSERT INTO magazzino (emoji,nome,qty,costo,fornitore,note) VALUES (?,?,?,?,?,?)",
        (emoji, nome, qty, costo, fornitore, note)
    )
    db.commit()
    item = db.execute("SELECT * FROM magazzino WHERE id=?", (cur.lastrowid,)).fetchone()
    db.close()
    return jsonify(dict(item))

@app.route("/api/magazzino/<int:mid>", methods=["PUT"])
@richiedi_pin
def api_magazzino_update(mid):
    data = request.json
    if not data:
        return errore_validazione(ValueError("Dati mancanti"))
    try:
        emoji = valida_stringa(data.get("emoji", "📦"), "emoji", 10)
        nome = valida_stringa(data.get("nome"), "nome", 200, True)
        qty = int(valida_numero(data.get("qty", 0), "quantità", 0, 99999))
        costo = valida_numero(data.get("costo", 0), "costo", 0, 99999)
        fornitore = valida_stringa(data.get("fornitore"), "fornitore", 200)
        note = valida_stringa(data.get("note"), "note", 2000)
    except ValueError as e:
        return errore_validazione(e)
    db = get_db()
    db.execute(
        "UPDATE magazzino SET emoji=?,nome=?,qty=?,costo=?,fornitore=?,note=? WHERE id=?",
        (emoji, nome, qty, costo, fornitore, note, mid))
    db.commit()
    item = db.execute("SELECT * FROM magazzino WHERE id=?", (mid,)).fetchone()
    db.close()
    return jsonify(dict(item))

@app.route("/api/magazzino/<int:mid>", methods=["DELETE"])
@richiedi_pin
def api_magazzino_delete(mid):
    db = get_db()
    db.execute("DELETE FROM magazzino WHERE id=?", (mid,))
    db.commit()
    db.close()
    return jsonify({"ok": True})

@app.route("/api/magazzino/<int:mid>/qty", methods=["POST"])
@richiedi_pin
def api_magazzino_qty(mid):
    """Incrementa o decrementa la quantità di un prodotto (+1 / -1)"""
    data = request.json or {}
    delta = int(data.get("delta", 1))   # +1 carico, -1 scarico
    db = get_db()
    item = db.execute("SELECT qty FROM magazzino WHERE id=?", (mid,)).fetchone()
    if not item:
        db.close()
        return jsonify({"ok": False, "error": "Prodotto non trovato"}), 404
    new_qty = max(0, item["qty"] + delta)
    db.execute("UPDATE magazzino SET qty=? WHERE id=?", (new_qty, mid))
    db.commit()
    item = db.execute("SELECT * FROM magazzino WHERE id=?", (mid,)).fetchone()
    db.close()
    return jsonify(dict(item))


# === API STATISTICHE ===

@app.route("/api/statistiche", methods=["GET"])
@richiedi_pin
def api_statistiche():
    db = get_db()
    oggi = date.today().isoformat()
    inizio_mese = date.today().replace(day=1).isoformat()
    inizio_settimana = (date.today() - timedelta(days=date.today().weekday())).isoformat()

    inc_oggi = db.execute("""
        SELECT COALESCE(SUM(aps.prezzo_applicato),0) as totale
        FROM appuntamenti a JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        WHERE a.data=? AND a.completato=1""", (oggi,)).fetchone()["totale"]

    inc_sett = db.execute("""
        SELECT COALESCE(SUM(aps.prezzo_applicato),0) as totale
        FROM appuntamenti a JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        WHERE a.data>=? AND a.completato=1""", (inizio_settimana,)).fetchone()["totale"]

    inc_mese = db.execute("""
        SELECT COALESCE(SUM(aps.prezzo_applicato),0) as totale
        FROM appuntamenti a JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        WHERE a.data>=? AND a.completato=1""", (inizio_mese,)).fetchone()["totale"]

    n_clienti = db.execute("SELECT COUNT(*) as n FROM clienti WHERE attivo=1").fetchone()["n"]
    n_oggi = db.execute("SELECT COUNT(*) as n FROM appuntamenti WHERE data=?", (oggi,)).fetchone()["n"]

    top_servizi = db.execute("""
        SELECT s.nome, s.emoji, COUNT(*) as conteggio, SUM(aps.prezzo_applicato) as incasso
        FROM appuntamento_servizi aps JOIN servizi s ON aps.servizio_id=s.id
        JOIN appuntamenti a ON aps.appuntamento_id=a.id
        WHERE a.data>=? AND a.completato=1
        GROUP BY s.id ORDER BY conteggio DESC LIMIT 5""", (inizio_mese,)).fetchall()

    top_clienti = db.execute("""
        SELECT c.nome, c.cognome, c.soprannome, COUNT(*) as visite, SUM(aps.prezzo_applicato) as speso
        FROM appuntamenti a JOIN clienti c ON a.cliente_id=c.id
        JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        WHERE a.data>=? AND a.completato=1
        GROUP BY c.id ORDER BY visite DESC LIMIT 5""", (inizio_mese,)).fetchall()

    giorni = []
    for i in range(13, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        inc = db.execute("""
            SELECT COALESCE(SUM(aps.prezzo_applicato),0) as totale
            FROM appuntamenti a JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
            WHERE a.data=? AND a.completato=1""", (d,)).fetchone()["totale"]
        giorni.append({"data": d, "incasso": inc})

    pagamenti = db.execute("""
        SELECT COALESCE(metodo_pagamento,'contanti') as metodo,
               COUNT(DISTINCT a.id) as n_app, SUM(aps.prezzo_applicato) as totale
        FROM appuntamenti a JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        WHERE a.data>=? AND a.completato=1
        GROUP BY metodo ORDER BY totale DESC""", (inizio_mese,)).fetchall()

    # Riepilogo magazzino (scorte basse)
    mag_items = db.execute("SELECT * FROM magazzino ORDER BY qty ASC").fetchall()
    mag_totale = sum(i["qty"] * i["costo"] for i in mag_items)
    mag_esauriti = [dict(i) for i in mag_items if i["qty"] == 0]
    mag_bassi   = [dict(i) for i in mag_items if 0 < i["qty"] <= 2]

    db.close()
    return jsonify({
        "incasso_oggi": inc_oggi, "incasso_settimana": inc_sett, "incasso_mese": inc_mese,
        "n_clienti": n_clienti, "n_appuntamenti_oggi": n_oggi,
        "top_servizi": [dict(s) for s in top_servizi],
        "top_clienti": [dict(c) for c in top_clienti],
        "incassi_giornalieri": giorni,
        "pagamenti_mese": [dict(p) for p in pagamenti],
        "magazzino_valore": mag_totale,
        "magazzino_esauriti": mag_esauriti,
        "magazzino_bassi": mag_bassi,
    })


# === API EXPORT CSV ===

@app.route("/api/export/clienti")
@richiedi_pin
def export_clienti():
    db = get_db()
    clienti = db.execute(
        "SELECT nome,cognome,soprannome,telefono,email,note,creato FROM clienti WHERE attivo=1 ORDER BY cognome,nome"
    ).fetchall()
    db.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(["Nome","Cognome","Soprannome","Telefono","Email","Note","Data registrazione"])
    for c in clienti:
        w.writerow([c["nome"],c["cognome"],c["soprannome"],c["telefono"],c["email"],c["note"],c["creato"]])
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode('utf-8-sig')),
        mimetype='text/csv', as_attachment=True, download_name=f'clienti_{date.today().isoformat()}.csv')

@app.route("/api/export/appuntamenti")
@richiedi_pin
def export_appuntamenti():
    mese = request.args.get("mese", date.today().strftime("%Y-%m"))
    db = get_db()
    appts = db.execute("""
        SELECT a.data, a.ora, c.nome, c.cognome, c.soprannome,
               GROUP_CONCAT(s.nome, ', ') as servizi, SUM(aps.prezzo_applicato) as totale,
               a.metodo_pagamento, a.note,
               CASE WHEN a.completato THEN 'Si' ELSE 'No' END as completato
        FROM appuntamenti a JOIN clienti c ON a.cliente_id=c.id
        JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        JOIN servizi s ON aps.servizio_id=s.id
        WHERE a.data LIKE ? GROUP BY a.id ORDER BY a.data, a.ora""", (mese+"%",)).fetchall()
    db.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(["Data","Ora","Nome","Cognome","Soprannome","Servizi","Totale","Pagamento","Note","Completato"])
    for a in appts:
        w.writerow([a["data"],a["ora"],a["nome"],a["cognome"],a["soprannome"],
                    a["servizi"],a["totale"],a["metodo_pagamento"]or"",a["note"],a["completato"]])
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode('utf-8-sig')),
        mimetype='text/csv', as_attachment=True, download_name=f'appuntamenti_{mese}.csv')

@app.route("/api/export/incassi")
@richiedi_pin
def export_incassi():
    mese = request.args.get("mese", date.today().strftime("%Y-%m"))
    db = get_db()
    giorni = db.execute("""
        SELECT a.data, COUNT(DISTINCT a.id) as n_app, SUM(aps.prezzo_applicato) as incasso
        FROM appuntamenti a JOIN appuntamento_servizi aps ON a.id=aps.appuntamento_id
        WHERE a.data LIKE ? AND a.completato=1
        GROUP BY a.data ORDER BY a.data""", (mese+"%",)).fetchall()
    db.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(["Data","N. Appuntamenti","Incasso"])
    totale = 0
    for g in giorni:
        w.writerow([g["data"], g["n_app"], f"{g['incasso']:.2f}"])
        totale += g["incasso"]
    w.writerow([])
    w.writerow(["TOTALE MESE","",f"{totale:.2f}"])
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode('utf-8-sig')),
        mimetype='text/csv', as_attachment=True, download_name=f'incassi_{mese}.csv')

@app.route("/api/export/magazzino")
@richiedi_pin
def export_magazzino():
    """CSV inventario magazzino — perfetto per fine anno"""
    db = get_db()
    items = db.execute("SELECT * FROM magazzino ORDER BY nome").fetchall()
    db.close()
    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(["Prodotto","Fornitore","Note/Nuance","Quantità (flaconi)","Costo unitario (€)","Valore totale (€)","Stato"])
    totale_val = 0
    totale_qty = 0
    for i in items:
        val = i["qty"] * i["costo"]
        stato = "Esaurito" if i["qty"]==0 else ("Quasi finito" if i["qty"]<=2 else "OK")
        w.writerow([i["nome"], i["fornitore"], i["note"], i["qty"],
                    f"{i['costo']:.2f}", f"{val:.2f}", stato])
        totale_val += val
        totale_qty += i["qty"]
    w.writerow([])
    w.writerow(["TOTALE","","",totale_qty,"",f"{totale_val:.2f}",""])
    w.writerow([])
    w.writerow([f"Inventario generato il {date.today().strftime('%d/%m/%Y')}"])
    out.seek(0)
    return send_file(io.BytesIO(out.getvalue().encode('utf-8-sig')),
        mimetype='text/csv', as_attachment=True,
        download_name=f'magazzino_inventario_{date.today().isoformat()}.csv')


# === API BACKUP ===

@app.route("/api/backup", methods=["POST"])
@richiedi_pin
def api_backup():
    path = backup_db()
    if path:
        return jsonify({"ok": True, "file": os.path.basename(path)})
    return jsonify({"ok": False, "error": "Nessun database trovato"})


# === API WHATSAPP ===

@app.route("/api/whatsapp/<int:aid>")
@richiedi_pin
def api_whatsapp(aid):
    db = get_db()
    a = db.execute("""
        SELECT a.*, c.nome, c.cognome, c.telefono
        FROM appuntamenti a JOIN clienti c ON a.cliente_id=c.id WHERE a.id=?""", (aid,)).fetchone()
    servizi = db.execute("""
        SELECT s.nome FROM appuntamento_servizi aps
        JOIN servizi s ON aps.servizio_id=s.id WHERE aps.appuntamento_id=?""", (aid,)).fetchall()
    db.close()
    if not a:
        return jsonify({"error": "Appuntamento non trovato"})

    srv_names = ", ".join(s["nome"] for s in servizi)
    try:
        data_fmt = datetime.strptime(a["data"], "%Y-%m-%d").strftime("%d/%m/%Y")
    except:
        data_fmt = a["data"]

    msg = (f"Ciao {a['nome']}! 😊\n"
           f"Ti ricordiamo il tuo appuntamento da Sei Unica Parrucchieri:\n"
           f"📅 {data_fmt} alle ore {a['ora']}\n"
           f"💇‍♀️ {srv_names}\n\nTi aspettiamo! ✂️")

    phone = (a["telefono"] or "").replace(" ","").replace("-","").replace("+","")
    if phone and not phone.startswith("39"):
        phone = "39" + phone
    wa_url = f"https://wa.me/{phone}?text={msg}" if phone else None
    return jsonify({"messaggio": msg, "whatsapp_url": wa_url, "telefono": a["telefono"]})


# === PWA: Manifest & Service Worker ===

@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "Sei Unica Parrucchieri",
        "short_name": "Sei Unica",
        "start_url": "/mobile",
        "display": "standalone",
        "background_color": "#FBF5F9",
        "theme_color": "#C4568C",
        "icons": [{
            "src": "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 192 192'%3E%3Crect width='192' height='192' rx='32' fill='%23C4568C'/%3E%3Ctext x='96' y='120' font-size='96' text-anchor='middle'%3E%E2%9C%82%EF%B8%8F%3C/text%3E%3C/svg%3E",
            "sizes": "192x192",
            "type": "image/svg+xml"
        }]
    }
    return app.response_class(json.dumps(manifest), mimetype='application/json')

@app.route("/sw.js")
def pwa_service_worker():
    sw_code = """
const CACHE_NAME = 'sei-unica-v5';
const OFFLINE_URLS = ['/mobile', '/manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(OFFLINE_URLS)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(
    ks.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
  )));
  self.clients.claim();
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request).then(r => {
      const rc = r.clone();
      if (r.ok) caches.open(CACHE_NAME).then(c => c.put(e.request, rc));
      return r;
    }).catch(() => caches.match(e.request))
  );
});
"""
    return app.response_class(sw_code.strip(), mimetype='application/javascript',
                              headers={'Service-Worker-Allowed': '/'})


# === AVVIO ===

with app.app_context():
    init_db()

if __name__ == "__main__":
    import webbrowser
    from threading import Timer
    PORT = int(os.environ.get("PORT", 5555))
    print("=" * 50)
    print("  ✂️  SEI UNICA — Salon Manager v5 Cloud")
    print("=" * 50)
    Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
