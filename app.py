import os, csv
from io import StringIO
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
db_path = os.path.join(BASE_DIR, "multiconsulta.db")
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'multiconsulta_secret_very_secure'

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# --- MODELOS ---
class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id            = db.Column(db.Integer, primary_key=True)
    username      = db.Column(db.String(50), unique=True, nullable=False)
    nombre        = db.Column(db.String(100), nullable=True)   # Nombre completo
    password_hash = db.Column(db.String(255), nullable=False)
    rol           = db.Column(db.String(20), nullable=False, default='Usuario')

    def to_dict(self):
        return {'id': self.id, 'username': self.username,
                'nombre': self.nombre or self.username, 'rol': self.rol}

class OpcionDesplegable(db.Model):
    __tablename__ = 'opciones_desplegables'
    id        = db.Column(db.Integer, primary_key=True)
    categoria = db.Column(db.String(50), nullable=False)
    valor     = db.Column(db.String(100), nullable=False)

    def to_dict(self):
        return {'id': self.id, 'categoria': self.categoria, 'valor': self.valor}

class Incidencia(db.Model):
    __tablename__ = 'incidencias'
    id               = db.Column(db.Integer, primary_key=True)
    numero_ticket    = db.Column(db.String(50), nullable=False)   # SIN unique — puede repetirse
    departamento     = db.Column(db.String(100), nullable=True)
    ciudad           = db.Column(db.String(100), nullable=True)
    site_name        = db.Column(db.String(150), nullable=True)
    fecha_ticket     = db.Column(db.DateTime, nullable=True)       # Fecha/hora que ingresa el agente
    fecha_captura    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)  # Automática del sistema
    descripcion      = db.Column(db.Text, nullable=False)
    tecnico_asignado = db.Column(db.String(100), nullable=True)
    contrata         = db.Column(db.String(100), nullable=True)
    gestor           = db.Column(db.String(100), nullable=True)   # Auto desde usuario
    sla              = db.Column(db.String(50), nullable=True)
    estado           = db.Column(db.String(50), nullable=False, default='Pendiente')
    evidencia        = db.Column(db.String(255), nullable=True)
    usuario_creador  = db.Column(db.String(50), nullable=True)

    def to_dict(self):
        return {
            'id': self.id,
            'numero_ticket': self.numero_ticket,
            'departamento': self.departamento,
            'ciudad': self.ciudad,
            'site_name': self.site_name,
            'fecha_ticket':  self.fecha_ticket.strftime('%Y-%m-%d %H:%M') if self.fecha_ticket else '-',
            'fecha_captura': self.fecha_captura.strftime('%Y-%m-%d %H:%M') if self.fecha_captura else '-',
            'descripcion': self.descripcion,
            'tecnico_asignado': self.tecnico_asignado,
            'contrata': self.contrata,
            'gestor': self.gestor,
            'sla': self.sla,
            'estado': self.estado,
            'evidencia': self.evidencia,
            'usuario_creador': self.usuario_creador
        }


# --- DECORADORES ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or session.get('user_rol') != 'Admin':
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function


# --- CONTEXTO GLOBAL ---
@app.context_processor
def inject_user():
    user = None
    if 'user_id' in session:
        user = Usuario.query.get(session['user_id'])
    return dict(current_user=user)


# --- RUTAS WEB ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = Usuario.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            session['user_rol'] = user.rol
            return redirect(url_for('index'))
        return render_template('login.html', error='Credenciales inválidas')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/admin/usuarios')
@admin_required
def admin_usuarios():
    return render_template('admin_usuarios.html')

@app.route('/admin/configuracion')
@admin_required
def admin_configuracion():
    return render_template('admin_config.html')


# --- API INCIDENCIAS ---
@app.route('/api/incidencias', methods=['GET', 'POST'])
@login_required
def api_incidencias():
    if request.method == 'POST':
        data = request.form

        # Guardar archivo si existe
        evidencia_url = None
        if 'evidencia_file' in request.files:
            file = request.files['evidencia_file']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                evidencia_url = url_for('static', filename=f'uploads/{filename}')

        if not evidencia_url and data.get('evidencia_text'):
            evidencia_url = data.get('evidencia_text')

        # Gestor y creador desde el usuario logueado
        user = Usuario.query.get(session['user_id'])
        creador = user.username if user else 'Desconocido'
        gestor_auto = (user.nombre or user.username) if user else 'Desconocido'

        # Parsear fecha_ticket ingresada por el agente
        fecha_ticket_str = data.get('fecha_ticket', '')
        fecha_ticket_val = None
        if fecha_ticket_str:
            try:
                fecha_ticket_val = datetime.strptime(fecha_ticket_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                pass

        nueva = Incidencia(
            numero_ticket    = data.get('numero_ticket'),
            departamento     = data.get('departamento'),
            ciudad           = data.get('ciudad'),
            site_name        = data.get('site_name'),
            fecha_ticket     = fecha_ticket_val,
            descripcion      = data.get('descripcion'),
            tecnico_asignado = data.get('tecnico_asignado'),
            contrata         = data.get('contrata'),
            gestor           = gestor_auto,
            sla              = data.get('sla'),
            estado           = data.get('estado', 'Pendiente'),
            evidencia        = evidencia_url,
            usuario_creador  = creador
        )
        db.session.add(nueva)
        try:
            db.session.commit()
            return jsonify({'success': True, 'incidencia': nueva.to_dict()}), 201
        except Exception as e:
            db.session.rollback()
            return jsonify({'error': str(e)}), 400

    incidencias = Incidencia.query.order_by(Incidencia.fecha_captura.desc()).all()
    return jsonify([i.to_dict() for i in incidencias])


# --- EXPORTAR INCIDENCIAS (CSV) ---
@app.route('/api/incidencias/export')
@login_required
def export_incidencias():
    incidencias = Incidencia.query.order_by(Incidencia.fecha_captura.desc()).all()
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow([
        'Ticket', 'Fecha Ticket (Agente)', 'Estado', 'Fecha Captura (Sistema)',
        'Gestor/Registrador', 'Departamento', 'Ciudad', 'Site Name', 'Descripcion',
        'Tecnico', 'Contrata', 'SLA'
    ])
    for i in incidencias:
        writer.writerow([
            i.numero_ticket,
            i.fecha_ticket.strftime('%Y-%m-%d %H:%M') if i.fecha_ticket else '',
            i.estado,
            i.fecha_captura.strftime('%Y-%m-%d %H:%M') if i.fecha_captura else '',
            i.gestor, i.departamento, i.ciudad or '', i.site_name or '',
            i.descripcion, i.tecnico_asignado, i.contrata, i.sla
        ])
    output = si.getvalue()
    return Response(
        output,
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': f'attachment; filename=incidencias_{datetime.now().strftime("%Y%m%d_%H%M")}.csv'}
    )

# --- BORRAR INCIDENCIA (solo Admin) ---
@app.route('/api/incidencias/<int:id>', methods=['DELETE'])
@admin_required
def api_incidencia_delete(id):
    inc = Incidencia.query.get_or_404(id)
    db.session.delete(inc)
    db.session.commit()
    return jsonify({'success': True})


# --- API USUARIOS ---
@app.route('/api/usuarios', methods=['GET', 'POST'])
@admin_required
def api_usuarios():
    if request.method == 'POST':
        data     = request.json
        username = data.get('username')
        password = data.get('password')
        nombre   = data.get('nombre', '')
        rol      = data.get('rol', 'Usuario')

        if Usuario.query.filter_by(username=username).first():
            return jsonify({'error': 'El usuario ya existe'}), 400

        nuevo = Usuario(
            username      = username,
            nombre        = nombre,
            password_hash = generate_password_hash(password),
            rol           = rol
        )
        db.session.add(nuevo)
        db.session.commit()
        return jsonify({'success': True, 'usuario': nuevo.to_dict()})

    users = Usuario.query.all()
    return jsonify([u.to_dict() for u in users])

@app.route('/api/usuarios/<int:id>', methods=['DELETE'])
@admin_required
def api_usuario_delete(id):
    if id == session['user_id']:
        return jsonify({'error': 'No te puedes borrar a ti mismo'}), 400
    user = Usuario.query.get_or_404(id)
    db.session.delete(user)
    db.session.commit()
    return jsonify({'success': True})


# --- API OPCIONES ---
@app.route('/api/opciones', methods=['GET', 'POST'])
def api_opciones():
    if request.method == 'POST':
        if session.get('user_rol') != 'Admin':
            return jsonify({'error': 'No autorizado'}), 403
        data  = request.json
        nueva = OpcionDesplegable(categoria=data.get('categoria'), valor=data.get('valor'))
        db.session.add(nueva)
        db.session.commit()
        return jsonify({'success': True, 'opcion': nueva.to_dict()})

    todas    = OpcionDesplegable.query.all()
    resultado = {'Departamento': [], 'Contrata': [], 'SLA': [], 'Estado': [], 'Site Name': []}
    for op in todas:
        if op.categoria in resultado:
            resultado[op.categoria].append(op.to_dict())
    return jsonify(resultado)

@app.route('/api/opciones/<int:id>', methods=['DELETE'])
@admin_required
def api_delete_opciones(id):
    op = OpcionDesplegable.query.get_or_404(id)
    db.session.delete(op)
    db.session.commit()
    return jsonify({'success': True})


# --- MIGRACIÓN Y SEED ---
def migrate_db():
    """Aplica migraciones incrementales detectando el estado real de la BD."""
    from sqlalchemy import text
    with db.engine.connect() as conn:

        # 1. Agregar columna 'nombre' a usuarios si no existe
        try:
            conn.execute(text("ALTER TABLE usuarios ADD COLUMN nombre TEXT"))
            conn.commit()
        except Exception:
            pass

        # 2. Leer estado actual de la tabla incidencias
        row = conn.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='incidencias'")
        ).fetchone()
        if not row or not row[0]:
            return  # tabla aún no creada, init la creará

        create_sql   = row[0].upper()
        col_info     = conn.execute(text("PRAGMA table_info(incidencias)")).fetchall()
        existing_cols = [r[1] for r in col_info]

        has_unique       = 'UNIQUE' in create_sql
        has_fecha_hora   = 'fecha_hora'    in existing_cols
        has_fecha_captura= 'fecha_captura' in existing_cols
        has_fecha_ticket = 'fecha_ticket'  in existing_cols

        # Si la tabla necesita recrearse (tiene UNIQUE o fecha_hora pero no fecha_captura)
        needs_recreate = has_unique or (has_fecha_hora and not has_fecha_captura)

        if needs_recreate:
            # Determinar columna fuente para fecha_captura
            src_fecha = 'fecha_hora' if has_fecha_hora else 'fecha_captura'

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS incidencias_new (
                    id               INTEGER PRIMARY KEY,
                    numero_ticket    VARCHAR(50)  NOT NULL,
                    departamento     VARCHAR(100),
                    ciudad           VARCHAR(100),
                    site_name        VARCHAR(150),
                    fecha_ticket     DATETIME,
                    fecha_captura    DATETIME,
                    descripcion      TEXT         NOT NULL,
                    tecnico_asignado VARCHAR(100),
                    contrata         VARCHAR(100),
                    gestor           VARCHAR(100),
                    sla              VARCHAR(50),
                    estado           VARCHAR(50)  NOT NULL,
                    evidencia        VARCHAR(255),
                    usuario_creador  VARCHAR(50)
                )
            """))
            conn.execute(text(f"""
                INSERT INTO incidencias_new
                    (id, numero_ticket, departamento, fecha_captura,
                     descripcion, tecnico_asignado, contrata, gestor,
                     sla, estado, evidencia, usuario_creador)
                SELECT id, numero_ticket, departamento, {src_fecha},
                       descripcion, tecnico_asignado, contrata, gestor,
                       sla, estado, evidencia, usuario_creador
                FROM incidencias
            """))
            conn.execute(text("DROP TABLE incidencias"))
            conn.execute(text("ALTER TABLE incidencias_new RENAME TO incidencias"))
            conn.commit()
        else:
            # Solo agregar columnas que falten
            for col, tipo in [
                ('fecha_ticket',  'DATETIME'),
                ('fecha_captura', 'DATETIME'),
                ('ciudad',        'VARCHAR(100)'),
                ('site_name',     'VARCHAR(150)'),
            ]:
                if col not in existing_cols:
                    try:
                        conn.execute(text(f"ALTER TABLE incidencias ADD COLUMN {col} {tipo}"))
                        conn.commit()
                    except Exception:
                        pass

def init_db():
    db.create_all()
    migrate_db()

    if not Usuario.query.filter_by(username='hvargas').first():
        admin = Usuario(
            username      = 'hvargas',
            nombre        = 'Hector Vargas',
            password_hash = generate_password_hash('123456'),
            rol           = 'Admin'
        )
        db.session.add(admin)

    if OpcionDesplegable.query.count() == 0:
        opciones_defecto = [
            ('Departamento','Amazonas'),('Departamento','Ancash'),('Departamento','Apurimac'),
            ('Departamento','Arequipa'),('Departamento','Ayacucho'),('Departamento','Cajamarca'),
            ('Departamento','Callao'),('Departamento','Cusco'),('Departamento','Huancavelica'),
            ('Departamento','Huanuco'),('Departamento','Ica'),('Departamento','Junin'),
            ('Departamento','La Libertad'),('Departamento','Lambayeque'),('Departamento','Lima'),
            ('Departamento','Loreto'),('Departamento','Madre de Dios'),('Departamento','Moquegua'),
            ('Departamento','Pasco'),('Departamento','Piura'),('Departamento','Puno'),
            ('Departamento','San Martin'),('Departamento','Tacna'),('Departamento','Tumbes'),
            ('Departamento','Ucayali'),
            ('Contrata','Jius'),('Contrata','Gesitel'),('Contrata','HBA Proyect'),
            ('Contrata','Satelecom'),('Contrata','Cobra'),('Contrata','Nastel'),
            ('SLA','8HRS'),('SLA','16HRS'),('SLA','48HRS'),
            ('Estado','Pendiente'),('Estado','Asignado'),('Estado','Parada de Reloj'),
            ('Estado','Cierre Operativo'),('Estado','Liquidado')
        ]
        for cat, val in opciones_defecto:
            db.session.add(OpcionDesplegable(categoria=cat, valor=val))

    if OpcionDesplegable.query.filter_by(categoria='Site Name').count() == 0:
        opciones_sitios = [
            ('Site Name','013295793_CP_Campamento_Yumpaq'),
            ('Site Name','0131601_JU_Huancayo_Centro'),
            ('Site Name','0131673_JU_Univ_Continental'),
            ('Site Name','013191749_HU_Zenovio_Rodriguez'),
            ('Site Name','013192129_HU_Castrovirreyna'),
            ('Site Name','0132501_MD_Ernesto_Rivero'),
            ('Site Name','0132533_MD_Puerto_Rosario'),
            ('Site Name','0132537_MD_Caserio_Sta_Rosa'),
            ('Site Name','0132538_MD_Mazuko'),
            ('Site Name','0133036_JU_San_Francisco_Asis'),
            ('Site Name','0133048_JU_Silla'),
            ('Site Name','0130802_IC_Nazca'),
            ('Site Name','0130809_IC_Villacuri_Tm'),
            ('Site Name','0130813_IC_La_Calera_Chincha'),
            ('Site Name','0130832_IC_Av_Municipalidad'),
            ('Site Name','0130842_IC_Los_Aquijes'),
            ('Site Name','0130844_IC_Rio_Seco'),
            ('Site Name','0130905_AQ_Villa_Dolores'),
            ('Site Name','0130915_AQ_Matarani'),
            ('Site Name','0130933_AQ_Vitor'),
            ('Site Name','0130937_AQ_Atiquipa'),
            ('Site Name','0130939_AQ_Jesus'),
            ('Site Name','0131115_MQ_Miramar_Ilo'),
            ('Site Name','0131173_MQ_Los_Angeles'),
            ('Site Name','0131193_MQ_Torata_Pueblo'),
            ('Site Name','0131201_TA_Tacna_Centro'),
            ('Site Name','0131304_CS_Ollantaytambo'),
            ('Site Name','0131305_CS_Pisac'),
            ('Site Name','0131306_CS_Aguas_Calientes'),
            ('Site Name','0131308_CS_Cerro_Sacro'),
            ('Site Name','0131312_CS_Tintaya'),
            ('Site Name','0131350_CS_Diamantes_Cusco'),
            ('Site Name','0131395_CS_Tren_Artesanias'),
            ('Site Name','0131402_PN_Juli'),
            ('Site Name','0131404_PN_Puno_Centro'),
            ('Site Name','0131422_PN_Uancv'),
            ('Site Name','0131496_PN_Puma_Uta'),
            ('Site Name','013221090_JU_Puerta_De_Oro'),
            ('Site Name','013225258_JU_Huamancaca'),
            ('Site Name','0132269_IC_Pueblo_San_J'),
            ('Site Name','0132346_AQ_Peaje_Miramar'),
            ('Site Name','0132541_MD_Centro_De_Piedras'),
            ('Site Name','0132809_PN_Don_Bosco_Puno'),
            ('Site Name','0132830_PN_Dorsal_Guayaca'),
            ('Site Name','0132897_PN_Repetidor_Vizcach'),
            ('Site Name','0132936_AP_Huallpahuasi'),
            ('Site Name','0133078_JU_Palcomayo_Pueblo'),
            ('Site Name','0133091_JU_Palca_Junin'),
            ('Site Name','0133607_HU_Huayllaraccra'),
            ('Site Name','0133711_AY_Colegio_Senor_Milag'),
            ('Site Name','0133755_AY_Nuevo_PPJJ_Acuchima'),
            ('Site Name','0133792_AP_Vilcabamba'),
            ('Site Name','0133810_AQ_Ovalo_Vidaurra'),
            ('Site Name','0133863_AQ_Arequipa'),
            ('Site Name','0133935_AQ_La_Tomilla'),
            ('Site Name','0134400_CP_Pumacancha'),
            ('Site Name','0136383_AQ_IB_Aerop_Arequipa'),
            ('Site Name','013214199_IC_SantaBarbara'),
            ('Site Name','013311687_PN_RittyKucho'),
            ('Site Name','013214203_IC_VientoYArena'),
            ('Site Name','013214205_IC_VictorMatta'),
            ('Site Name','013312420_PN_AvenidaCandelaria'),
            ('Site Name','013312780_PN_ManazoCiudad'),
            ('Site Name','0130815_IC_Chincha'),
            ('Site Name','0130820_IC_Cerro_Prieto'),
            ('Site Name','0130821_IC_Ayabaca'),
            ('Site Name','0130911_AQ_Tiabaya'),
            ('Site Name','0130913_AQ_Sachaca'),
            ('Site Name','0130916_AQ_Mollendo'),
            ('Site Name','0130917_AQ_Catas'),
            ('Site Name','0130938_AQ_Chala'),
            ('Site Name','0131104_MQ_Cerro_El_Hueco'),
            ('Site Name','0131158_MQ_Villa_Botiflaca'),
            ('Site Name','0131225_TA_Asoc_Los_Sauces'),
            ('Site Name','0131314_CS_Sol_de_Oro'),
            ('Site Name','0131317_CS_Combapata'),
            ('Site Name','0131319_CS_Quiquijana'),
            ('Site Name','0131346_CS_Mayuorco'),
            ('Site Name','013140406_AQ_Vizcardo_Camana'),
            ('Site Name','0131414_PN_Huisoroque'),
            ('Site Name','0131415_PN_Taraco'),
            ('Site Name','0131416_PN_Cabana'),
            ('Site Name','0131600_JU_Rio_Negro'),
            ('Site Name','0131606_JU_Chupaca'),
            ('Site Name','0131641_JU_Univ_Peruana_Andes'),
            ('Site Name','0131653_JU_Cementerio_General'),
            ('Site Name','0131661_AQ_Paulet_AQP'),
            ('Site Name','0131677_JU_Frutas_Y_Verduras'),
            ('Site Name','0132033_JU_Cachi_Cachi'),
            ('Site Name','013214166_IC_Jaguay'),
            ('Site Name','0132292_IC_Casuarinas_de_Ica'),
            ('Site Name','0132536_MD_Virgen_De_La_Candel'),
            ('Site Name','0132542_MD_Boca_Colorado'),
            ('Site Name','0132547_MD_San_Bernardo'),
            ('Site Name','0132612_PN_Huayruruni'),
            ('Site Name','0132817_PN_Boris_Suas'),
            ('Site Name','0132909_AP_28_De_Julio_Abancay'),
            ('Site Name','0132942_AP_Uripa'),
            ('Site Name','0132963_AY_Huancapi_Principal'),
            ('Site Name','0133243_IC_Bodega_COW'),
            ('Site Name','0133347_CS_Susucalle'),
            ('Site Name','0133534_LH_Tingo_Maria_Centro'),
            ('Site Name','0133580_JU_Asuncion'),
            ('Site Name','0133603_HU_Manchengo_Munoz'),
            ('Site Name','0133606_JU_Ahuac'),
            ('Site Name','0133609_HU_Chillcahuaycco'),
            ('Site Name','0133740_AY_Terminal_Terrestre'),
            ('Site Name','0133741_AY_Nueve_Diciembre'),
            ('Site Name','0133922_AQ_Aptasa'),
            ('Site Name','0133959_AQ_Las_Palmeras_Aqp'),
            ('Site Name','0133960_AQ_Santa_Rita'),
            ('Site Name','0134351_AY_Huanca_Solar'),
            ('Site Name','0134403_CP_Cablacancha'),
            ('Site Name','0134416_CP_Milpo'),
            ('Site Name','013214201_IC_LaSurena'),
            ('Site Name','013214208_IC_OvaloNazca'),
            ('Site Name','013330302_TA_Gregorio_Albarracin'),
            ('Site Name','013331017_TA_CriptaDeLosHeroes'),
            ('Site Name','013315126_PN_LagunaQoriwata'),
            ('Site Name','0130823_IC_Las_Dunas'),
            ('Site Name','0130902_AQ_Sol_de_Mayo'),
            ('Site Name','0130909_AQ_Los_Rosales'),
            ('Site Name','0131373_CS_Marriot_Cusco'),
            ('Site Name','0131377_CS_Cuatro_Torres'),
            ('Site Name','0131406_PN_Juliaca_Cerro'),
            ('Site Name','0131411_PN_Santa_Rosa_Puno'),
            ('Site Name','0131412_PN_Pucara'),
            ('Site Name','0131434_PN_Sol_Y_Luna_Juliaca'),
            ('Site Name','0131686_JU_Calle_Real'),
            ('Site Name','013182091_CS_Pomacanchi'),
            ('Site Name','0132118_HU_Tinquerccasa'),
            ('Site Name','013212533_IC_San_Clemente'),
            ('Site Name','0132239_IC_Baldelomar_1900'),
            ('Site Name','0132338_PN_Calapuja'),
            ('Site Name','0132368_AQ_Pastor_Ruiz'),
            ('Site Name','0132551_MD_Pto_Maldonado_Cip'),
            ('Site Name','0132611_PN_Huancane_Centro'),
            ('Site Name','0132725_CS_Pumapata'),
            ('Site Name','0132839_PN_Cerro_Moho'),
            ('Site Name','0133031_JU_Tzancuvatziari'),
            ('Site Name','0133037_JU_Alto_Peru'),
            ('Site Name','0133040_JU_Santa_Rosa_De_Sacco'),
            ('Site Name','0133043_JU_Curis'),
            ('Site Name','0133045_JU_Chongos_Bajo_Ciudad'),
            ('Site Name','0133070_JU_Huaricolca'),
            ('Site Name','0133190_JU_San_Luis_de_Shuaro'),
            ('Site Name','0133492_JU_Sapallanga'),
            ('Site Name','0133562_LH_Alomias'),
            ('Site Name','0133581_LH_Aparicio_Pomares'),
            ('Site Name','0133679_CS_Choquepata'),
            ('Site Name','0133742_AY_Arteaga'),
            ('Site Name','0133747_AY_Aahh_Pampa_Del_Arco'),
            ('Site Name','0133977_AQ_Olimpico'),
            ('Site Name','0133979_AQ_Zoomundo'),
            ('Site Name','0133981_AQ_Reserva_Arequipa'),
            ('Site Name','0134003_AQ_Sarcas'),
            ('Site Name','0134046_AQ_Portales_de_Chiguata'),
            ('Site Name','0134074_MQ_Alto_Ilo_R1'),
            ('Site Name','0134477_AQ_Francisco_Mostajo'),
            ('Site Name','0134522_MD_La_Pastora'),
            ('Site Name','0134675_IC_Calle_Ignacio'),
            ('Site Name','013311686_PN_JardinDelAltiplano'),
            ('Site Name','0130845_IC_Chincha_Baja'),
            ('Site Name','0130901_AQ_Arequipa_Centro'),
            ('Site Name','0130908_AQ_Yanahuara'),
            ('Site Name','0130920_AQ_San_Andres'),
            ('Site Name','0130940_AQ_UNSA'),
            ('Site Name','0131325_CS_Machupicchu'),
            ('Site Name','0131403_PN_Ilave'),
            ('Site Name','0131471_PN_Chifron'),
            ('Site Name','013151746_AY_Tintay'),
            ('Site Name','013182562_CS_Zurite'),
            ('Site Name','013193198_HU_Huaytara'),
            ('Site Name','013212710_IC_Chincha_Alta_R1'),
            ('Site Name','0132369_AQ_Piedras_Blancas'),
            ('Site Name','0132529_CS_Mantoclla'),
            ('Site Name','0132530_MD_Madre_De_Dios'),
            ('Site Name','0132544_MD_Sarayacu'),
            ('Site Name','0132729_CS_Pisac_Pueblo'),
            ('Site Name','0132755_CS_Huancarani'),
            ('Site Name','0132803_PN_Terminal_Puno'),
            ('Site Name','0132828_PN_Av_Puno_Ilav_19'),
            ('Site Name','0132834_PN_Ayaviri'),
            ('Site Name','0132866_PN_Dorsal_Sallahuanca'),
            ('Site Name','0132975_AP_Uripa_Pueblo'),
            ('Site Name','0133052_JU_Acobamba'),
            ('Site Name','0133053_JU_Yauman_Pata'),
            ('Site Name','0133612_HU_Pampas'),
            ('Site Name','0133613_HU_Acostambo'),
            ('Site Name','0133748_AY_Huanta_Centro'),
            ('Site Name','0133759_AY_Huamanguilla'),
            ('Site Name','0133911_AQ_Farfan_Ballon'),
            ('Site Name','0134033_AQ_Cerro_Quemado'),
            ('Site Name','0134357_AY_Lecclespata'),
            ('Site Name','0134407_CP_Agregador_Pajonal'),
            ('Site Name','0134501_LH_Baltodano_Cerro'),
            ('Site Name','0134665_AQ_Tirso_Borja'),
            ('Site Name','0134687_IC_Luis_Alvizuri'),
            ('Site Name','0134688_IC_Olivar_Sur'),
            ('Site Name','0134694_JU_Dos_Gardenias'),
            ('Site Name','0134699_JU_Real_Huancan'),
            ('Site Name','013214387_IC_FundoPortada'),
            ('Site Name','013211506_IC_Lupuna'),
            ('Site Name','013311635_PN_JuliacaLampa'),
            ('Site Name','013141806_AQ_MercadoMajes'),
            ('Site Name','013141812_AQ_AcequiaLasFlores'),
            ('Site Name','013141776_AQ_MiradorCayma'),
            ('Site Name','0134825_CS_IB_Marriot_Cusco'),
            ('Site Name','0130822_IC_Santiago'),
            ('Site Name','0130870_IC_Campo_Ferial_Ica'),
            ('Site Name','0130903_AQ_El_Palomar'),
            ('Site Name','0131101_MQ_Moquegua'),
            ('Site Name','0131107_MQ_Ceticos_Ilo'),
            ('Site Name','0131275_TA_Varela'),
            ('Site Name','0131301_CS_Wanchaq'),
            ('Site Name','0131302_CS_Cusco_Centro'),
            ('Site Name','0131303_CS_Urubamba'),
            ('Site Name','0131309_CS_Calca'),
            ('Site Name','0131334_CS_Manahuanunca'),
            ('Site Name','0131382_CS_Bajo_Mirador'),
            ('Site Name','0131405_PN_Llallahuani'),
            ('Site Name','0131443_PN_Feria_Juliaca'),
            ('Site Name','0131458_PN_Juliaca_Ballon'),
            ('Site Name','0131488_PN_Zarumilla_Juliaca'),
            ('Site Name','0131489_PN_Triunfo_Juliaca'),
            ('Site Name','0131497_PN_Laykakota'),
            ('Site Name','0131631_JU_Colegio_Siglo_Xxi'),
            ('Site Name','0131633_JU_Antunez'),
            ('Site Name','0131676_JU_Ah_Justicia_Paz'),
            ('Site Name','0131692_JU_San_Agustin_De_Caja'),
            ('Site Name','0131895_AY_Canaria_Taca'),
            ('Site Name','013212531_IC_Pisco'),
            ('Site Name','0132189_MD_Inapari'),
            ('Site Name','0132500_MD_El_Triunfo_Puerto'),
            ('Site Name','0132543_MD_Loyoboc'),
            ('Site Name','0132684_PN_Lunar_de_Oro'),
            ('Site Name','0132801_PN_Jayllihuaya'),
            ('Site Name','0132802_PN_Una'),
            ('Site Name','013282190_MQ_Omate'),
            ('Site Name','0132906_AP_Patibamba'),
            ('Site Name','0133083_JU_Matahuasi'),
            ('Site Name','0133086_JU_Unishcoto'),
            ('Site Name','0133185_AP_Chuquibambilla'),
            ('Site Name','0133560_LH_Panao_Molinos_Tamil'),
            ('Site Name','0133561_LH_Microcuenca_Tulumay'),
            ('Site Name','0133656_AQ_Chilina_Puente'),
            ('Site Name','0133951_AQ_Torrentera'),
            ('Site Name','0133967_AQ_Comandante_Canga'),
            ('Site Name','0133968_AQ_Belgrano'),
            ('Site Name','0133986_AQ_Poetas_Peruanos'),
            ('Site Name','0134006_AQ_Yauca'),
            ('Site Name','0134354_PN_Ayaviri_pueblo'),
            ('Site Name','0134393_IC_Fermin_Tanguis'),
            ('Site Name','0164056_LH_LE_Huanuco_AMT'),
            ('Site Name','0164057_AQ_LE_Arequipa_AMT'),
            ('Site Name','013141832_AQ_PorvenirApipa'),
            ('Site Name','013201024_LH_PuertoSungaro'),
            ('Site Name','013212799_IC_Fundos_Beta_Ica'),
            ('Site Name','013213708_IC_Corp_Agrolatina'),
            ('Site Name','013214385_IC_FundoEscondido'),
            ('Site Name','013141771_AQ_MajesModuloB'),
            ('Site Name','013214180_IC_PedroTipacti'),
            ('Site Name','013141790_AQ_BellavistaMollendo'),
            ('Site Name','0130811_IC_Ocucaje'),
            ('Site Name','0130818_IC_Paracas'),
            ('Site Name','0130824_IC_Pisco_Centro'),
            ('Site Name','0130906_AQ_Socabaya'),
            ('Site Name','0130907_AQ_Zamacola'),
            ('Site Name','0130918_AQ_Sihuas'),
            ('Site Name','0130921_AQ_Mariano_Melgar'),
            ('Site Name','0130923_AQ_Selva_Alegre_Alto'),
            ('Site Name','0131100_MQ_Pampa_Ingles'),
            ('Site Name','0131205_TA_Morro_de_Sama'),
            ('Site Name','0131207_TA_Cerro_Para'),
            ('Site Name','0131300_CS_Chiaraje'),
            ('Site Name','0131316_CS_Aranwa'),
            ('Site Name','0131324_CS_Instituto_Tupac_Amaru'),
            ('Site Name','0131357_CS_Cap_Red_Sur'),
            ('Site Name','0131407_PN_Cerro_Pampajjase'),
            ('Site Name','0131464_PN_Modesto'),
            ('Site Name','013151785_AY_Vilcas_Raymi'),
            ('Site Name','0131615_JU_Tarma'),
            ('Site Name','013182336_CS_Viva_Peru_Cusco'),
            ('Site Name','013182353_CS_Antonio_Lorena'),
            ('Site Name','013221102_JU_Huasahuasi_Pueblo'),
            ('Site Name','013224024_JU_Chamiseria'),
            ('Site Name','0132502_MD_Esquina_Tambopata'),
            ('Site Name','0132503_MD_Javier_Heraud'),
            ('Site Name','0132510_MD_Sauces_Puerto_Maldo'),
            ('Site Name','0132595_IC_Nuevo_ICA'),
            ('Site Name','0132655_PN_Pomata'),
            ('Site Name','0132728_CS_Jajayacta'),
            ('Site Name','0132806_PN_Ciudad_Paz'),
            ('Site Name','0132816_PN_Rivera_Del_Mar'),
            ('Site Name','0132831_PN_Sta_Lucia_Puno'),
            ('Site Name','0132910_AP_Santa_Rosa_Abancay'),
            ('Site Name','0132931_AP_Totoral'),
            ('Site Name','0133069_JU_Llocllapampa'),
            ('Site Name','0133082_JU_El_Tambo_R1'),
            ('Site Name','0133341_CS_Checacupe'),
            ('Site Name','0133415_CS_Sambaray'),
            ('Site Name','0133641_HU_Ccochaccasa'),
            ('Site Name','0133670_CS_Cusco_Montessori'),
            ('Site Name','0133749_JU_Rp_La_Oroya_BBU1'),
            ('Site Name','0133875_AQ_Joya_Arequipa'),
            ('Site Name','0133936_AQ_Mirasol_De_Cayma'),
            ('Site Name','0133954_AQ_Jorge_Polar'),
            ('Site Name','0133964_AQ_Ovalo_Sepulveda'),
            ('Site Name','0134009_AQ_Progreso_48'),
            ('Site Name','0130816_IC_Alto_Pisco'),
            ('Site Name','0130925_AQ_La_Libertad'),
            ('Site Name','0130970_AQ_Misti'),
            ('Site Name','0131142_MQ_San_Antonio_Moquegu'),
            ('Site Name','0131266_TA_Ovalo_Cuzco'),
            ('Site Name','0131307_CS_Cerro_Huaynacorco'),
            ('Site Name','0131339_CS_Precursores_Cusco'),
            ('Site Name','0131365_CS_Versalles'),
            ('Site Name','0131372_CS_Santa_Rosa_Cusco'),
            ('Site Name','0131401_PN_Desaguadero'),
            ('Site Name','013140240_AQ_BanosPampa_Castilla'),
            ('Site Name','013140273_AQ_Fuerza_Characata'),
            ('Site Name','0131408_PN_Cerro_Atojja'),
            ('Site Name','0131635_JU_Undac'),
            ('Site Name','0131694_JU_Nemesio'),
            ('Site Name','0131697_JU_Villa_Perene'),
            ('Site Name','013201589_LH_Huacaybamba'),
            ('Site Name','013202567_LH_Cueva_Pavas'),
            ('Site Name','013204033_LH_Milagros_Huanuco'),
            ('Site Name','013210717_IC_COW_Vina_Vieja'),
            ('Site Name','013221086_JU_Capital_Ecologica'),
            ('Site Name','0132511_MD_Interoceanica_Sur'),
            ('Site Name','0132513_MD_La_Torre_Valsai'),
            ('Site Name','0132514_MD_La_Joya_Puerto_Mald'),
            ('Site Name','0132777_CS_Santa_Teresa'),
            ('Site Name','0132912_AP_Abancay_Bastidas'),
            ('Site Name','013311504_PN_Megacentro_Juliaca'),
            ('Site Name','0133646_HU_Cow_Callqui'),
            ('Site Name','0133647_HU_Pampas_Bajo'),
            ('Site Name','0133671_CS_Pucyura'),
            ('Site Name','0133677_CS_Union_Anta'),
            ('Site Name','0133770_AY_Tambo'),
            ('Site Name','0133780_AY_Saurama'),
            ('Site Name','0133937_AQ_Los_Ciruelos'),
            ('Site Name','0133940_PN_Vitupata_R1'),
            ('Site Name','0134070_AQ_Aplao'),
            ('Site Name','0134606_JU_Condorcocha'),
            ('Site Name','0134732_CS_Ocongate'),
            ('Site Name','0134937_CS_IB_RP_Cusco'),
            ('Site Name','013214184_IC_AtleticoPisqueno'),
            ('Site Name','013214193_IC_PlazaAquijes'),
            ('Site Name','013210117_IC_Drokasa_Parlac'),
            ('Site Name','013180389_CS_MiradorQuillabamba'),
            ('Site Name','0130928_AQ_Mayta_Capac'),
            ('Site Name','0130934_AQ_Chiguata'),
            ('Site Name','0130992_AQ_Coliseo_Arequipa'),
            ('Site Name','0131106_MQ_Muelle_Meylan'),
            ('Site Name','0131257_TA_Av_El_Litoral'),
            ('Site Name','0131321_CS_Sicuani'),
            ('Site Name','013140087_AQ_Chuquibamba'),
            ('Site Name','013140251_AQ_Canon_Cotahuasi'),
            ('Site Name','0131409_PN_Santiago_Giraldo'),
            ('Site Name','0131420_PN_Ayar_Cachi'),
            ('Site Name','0131447_PN_Aeropuerto_Juliaca'),
            ('Site Name','0131449_PN_Collao_Norte'),
            ('Site Name','0131456_PN_Pacifico_Juliaca'),
            ('Site Name','0131620_JU_Sicaya'),
            ('Site Name','0131646_CS_Tambomachay'),
            ('Site Name','0131666_AQ_Santos_chocano'),
            ('Site Name','013182449_CS_INEI_Cusco'),
            ('Site Name','0132152_IC_Rio_Grande_Palpa'),
            ('Site Name','013220504_JU_Sicaya_Pueblo'),
            ('Site Name','013221748_JU_Esperanza_Pichanaki'),
            ('Site Name','013221757_JU_Virgen_Merced'),
            ('Site Name','0132370_AQ_Camilo_Joya'),
            ('Site Name','0132521_MD_Imperio_Puerto_Mald'),
            ('Site Name','0132523_MD_Padre_Aldamariz'),
            ('Site Name','0132526_MD_Plaza_Puerto_Maldon'),
            ('Site Name','0132531_MD_Jaime_Troncoso'),
            ('Site Name','0132747_CS_Cruz_De_Urubamba'),
            ('Site Name','0132787_CS_Plaza_Calca'),
            ('Site Name','0132898_PN_Pucara_Pueblo'),
            ('Site Name','0133517_LH_Iglesia_San_Cristob'),
            ('Site Name','0133695_CS_Cusco_Antonio'),
            ('Site Name','0133908_AQ_Apipe_Aqp'),
            ('Site Name','0134063_AQ_Corire'),
            ('Site Name','0134067_AQ_Pedregal_Sur'),
            ('Site Name','0134278_CS_Chinchaypujio'),
            ('Site Name','0134353_PN_Lucia_Centro'),
            ('Site Name','0134359_AY_Qollpahuaycco'),
            ('Site Name','0134473_AQ_Camposanto_Haiti'),
            ('Site Name','0134947_JU_IB_RP_Huancayo'),
            ('Site Name','0135386_AY_Yanahuillca'),
            ('Site Name','013210129_IC_Drokasa_Santa_Rita'),
            ('Site Name','013210113_IC_Drokasa_2'),
            ('Site Name','013312421_PN_UrbElCarmenDeJuliaca'),
            ('Site Name','013313079_PN_UrbConcordia'),
            ('Site Name','013214214_IC_IngresoFlorida'),
            ('Site Name','0130959_AQ_Casa_Blanca_Aqp'),
            ('Site Name','0131284_TA_Ovalo_Albarracin'),
            ('Site Name','0131349_CS_Aero_Cusco'),
            ('Site Name','0131364_CS_Santo_Cusco'),
            ('Site Name','0131476_PN_Volta_Congo'),
            ('Site Name','0131482_PN_Caracoto'),
            ('Site Name','0131638_JU_Hidra'),
            ('Site Name','0131647_CS_Quispiquilla'),
            ('Site Name','013182325_CS_Cultural_Koripata'),
            ('Site Name','013182352_CS_Zurimana_Uvima'),
            ('Site Name','013223270_JU_Bernabe_Pangoa'),
            ('Site Name','0132326_PN_Zepita_Aymara'),
            ('Site Name','0132406_AQ_Sector_IX'),
            ('Site Name','0132534_MD_Fitzcarrald'),
            ('Site Name','0132535_MD_Florida_Alta'),
            ('Site Name','0132715_CS_Plaza_Sicuani'),
            ('Site Name','0132723_CS_Singuna'),
            ('Site Name','0132753_CS_Paucartambo_Cusco'),
            ('Site Name','0132794_CS_Huascaray'),
            ('Site Name','0133008_JU_San_Ramon_Plaza'),
            ('Site Name','0133023_JU_Zapatel'),
            ('Site Name','0133081_JU_Iglesia_Arcangel'),
            ('Site Name','0133531_LH_Unas'),
            ('Site Name','0133542_LH_Bomboncocha'),
            ('Site Name','0133675_CS_Quillabamba_Ciudad'),
            ('Site Name','0133696_TA_Pasaje_Industrial'),
            ('Site Name','0133818_AQ_Berlin_Aqp'),
            ('Site Name','0133824_AQ_Bellapampa'),
            ('Site Name','0133873_AQ_Santa_Monica'),
            ('Site Name','0133890_AQ_Characato'),
            ('Site Name','0133944_AQ_Parque_Fujimori'),
            ('Site Name','0134615_AP_Anccohuayllo'),
            ('Site Name','0136505_AQ_Achoma'),
            ('Site Name','013214185_IC_LaPrometida'),
            ('Site Name','013214189_IC_CanteraParcona'),
            ('Site Name','013214220_IC_AvSolDeTinguina'),
            ('Site Name','0130806_IC_Huacachina_Hotel'),
            ('Site Name','0130922_AQ_San_Bernardo_Chigua'),
            ('Site Name','0130929_AQ_Cocachacra'),
            ('Site Name','0130988_AQ_Umacollo'),
            ('Site Name','0130990_AQ_Martinetti'),
            ('Site Name','0131248_TA_Fresnos_De_Tacna'),
            ('Site Name','0131331_CS_Atlanta_Cusco'),
            ('Site Name','0131352_CS_El_Mesias'),
            ('Site Name','0131355_CS_Los_Nogales'),
            ('Site Name','0131419_PN_Circunvalacion_2'),
            ('Site Name','0131425_PN_Torre_Tagle'),
            ('Site Name','0131649_JU_Hotel_Presidente'),
            ('Site Name','0131650_JU_Nuestra_Senora'),
            ('Site Name','013214100_IC_Marcona_San_Juan'),
            ('Site Name','0132329_AQ_Coscollo'),
            ('Site Name','0132666_PN_Banchero'),
            ('Site Name','0132674_PN_Inmaculada_Lampa'),
            ('Site Name','0132701_CS_San_Francisco_Cusco'),
            ('Site Name','0132705_CS_Ejercito_Park'),
            ('Site Name','0132776_CS_Huayopata'),
            ('Site Name','0132835_PN_San_Anton'),
            ('Site Name','0132837_PN_Yunguyo'),
            ('Site Name','0132860_PN_El_Dorado_Puno'),
            ('Site Name','0132864_AQ_Plaza_Cayma'),
            ('Site Name','0133030_JU_Juan_Santos_Atahual'),
            ('Site Name','0133097_JU_Huancan'),
            ('Site Name','0133324_TA_Locumba'),
            ('Site Name','0133352_CS_Tullumayo'),
            ('Site Name','0133563_JU_Tongoba_Mazamari'),
            ('Site Name','0133635_HU_Paucara'),
            ('Site Name','0133636_HU_Acobamba_Chocloco'),
            ('Site Name','0133802_AY_Pata_Cangallo'),
            ('Site Name','0133808_AQ_Bancarios_Arequipa'),
            ('Site Name','0133928_AQ_Villa_Belaunde'),
            ('Site Name','0133952_AQ_Goyoneche'),
            ('Site Name','0134041_AQ_Sta_Rita_De_Siguas'),
            ('Site Name','0134076_MQ_Cerro_Trapiche'),
            ('Site Name','0134610_CS_Pitumarca'),
            ('Site Name','013183084_CS_DirigentesCusco'),
            ('Site Name','013182379_CS_Urquillos_Huycho'),
            ('Site Name','013182381_CS_Sol_Libertad'),
            ('Site Name','013183078_CS_Backus_Cusco'),
            ('Site Name','013183089_CS_SalidaEspinar'),
            ('Site Name','013183090_CS_IglesiaYauri'),
            ('Site Name','0130804_IC_Vista_Alegre'),
            ('Site Name','0130828_IC_Hoja_Redonda'),
            ('Site Name','0130849_IC_Entel_Ica'),
            ('Site Name','0130964_AQ_Casa_Andina_Aqp'),
            ('Site Name','0131135_MQ_Cementerio_Moquegua'),
            ('Site Name','0131238_TA_Justo_Marin'),
            ('Site Name','0131268_TA_Mezquita_Babul'),
            ('Site Name','0131392_CS_Tierra_Prometida'),
            ('Site Name','013140090_AQ_Fundo_Peral'),
            ('Site Name','0131491_PN_Acco_Esquin'),
            ('Site Name','0131643_JU_Calixto'),
            ('Site Name','013182358_CS_Virgen_Rosario'),
            ('Site Name','013210626_IC_Yaurilla'),
            ('Site Name','013210731_IC_Romulo_Triveno'),
            ('Site Name','0132615_TA_Cerro_Intiorko'),
            ('Site Name','0132780_JU_Cow_Kingsmill'),
            ('Site Name','0132841_PN_Capachica'),
            ('Site Name','0132971_AP_Curahuasi'),
            ('Site Name','0133079_JU_Yauli_Pueblo'),
            ('Site Name','013311506_PN_Tambopata_Juliaca'),
            ('Site Name','0133558_LH_Chaglla'),
            ('Site Name','0133587_LH_Pedro_Puelles'),
            ('Site Name','0133600_JU_Vista_Paccha'),
            ('Site Name','0133800_AQ_Sidsur_Arequipa'),
            ('Site Name','0133859_AQ_Chavez_Bedoya'),
            ('Site Name','0133917_AQ_Calle_Manchego'),
            ('Site Name','0134010_AQ_Lomas'),
            ('Site Name','0134019_AQ_Plaza_Camana'),
            ('Site Name','0134024_AQ_Jaqui'),
            ('Site Name','0134064_AQ_La_Florida_aqp'),
            ('Site Name','0134065_AQ_El_Cruce'),
            ('Site Name','0134068_AQ_Nuevo_Vitor'),
            ('Site Name','0134259_JU_Punto_2_Chinalco'),
            ('Site Name','0134661_AQ_Gregorio_Camana'),
            ('Site Name','0136379_AQ_IB_RP_Arequipa'),
            ('Site Name','0136504_AQ_IB_MAP_Cayma_U'),
            ('Site Name','013313081_PN_SalcedoDePuno'),
            ('Site Name','0130840_IC_Rep_Marcona'),
            ('Site Name','0130924_AQ_Honduras'),
            ('Site Name','0130947_AQ_IB_MAP_Arequipa'),
            ('Site Name','0131113_MQ_Plaza_Ilo'),
            ('Site Name','0131139_IC_Tinguina_R1'),
            ('Site Name','0131157_MQ_Plaza_Moquegua'),
            ('Site Name','013143983_AQ_Israel'),
            ('Site Name','0131490_PN_Heroes_Pacifico'),
            ('Site Name','0132027_AQ_Rep_Ocona'),
            ('Site Name','013210729_IC_Jardines_Eden'),
            ('Site Name','013212534_IC_Garzas_Mir_1900'),
            ('Site Name','013212536_IC_Villa_Amaru'),
            ('Site Name','013214101_IC_Ingreso_Piscontes'),
            ('Site Name','013221003_JU_Kusimayu_Shullcas'),
            ('Site Name','0132662_PN_Yavero'),
            ('Site Name','0132722_CS_Hatumpampa'),
            ('Site Name','0132730_CS_Marcaconga'),
            ('Site Name','0132740_CS_Tinta'),
            ('Site Name','0132778_CS_Quillabamba'),
            ('Site Name','0132796_CS_Lamay'),
            ('Site Name','0132804_PN_Progreso'),
            ('Site Name','0132836_PN_Manazo'),
            ('Site Name','0132857_PN_Macusani'),
            ('Site Name','0132976_AP_Abancay_Alto'),
            ('Site Name','0133049_JU_Cochas_Chico'),
            ('Site Name','013310714_PN_Usicayos'),
            ('Site Name','013312653_PN_Puente_Ramis'),
            ('Site Name','0133484_JU_San_Pedro_De_Cajas'),
            ('Site Name','0133565_JU_Antonio_Lobato'),
            ('Site Name','0133648_HU_Yauli_Huancavelica'),
            ('Site Name','0133813_HU_Chaccocha'),
            ('Site Name','0133826_AQ_Leones_1900'),
            ('Site Name','0133914_AQ_Villa_Paraiso'),
            ('Site Name','0133930_AQ_Estacion_Aeropuerto'),
            ('Site Name','0134044_AQ_Polobaya'),
            ('Site Name','0134051_AQ_Plaza_Vea_Ejercito'),
            ('Site Name','0134389_AQ_El_Carmen_AQP'),
            ('Site Name','0134450_CS_Alto_Huasao'),
            ('Site Name','0134681_IC_Clavelles'),
            ('Site Name','0130887_IC_Berna_Parcona'),
            ('Site Name','0130979_AQ_Arequipa_Continenta'),
            ('Site Name','0131128_MQ_Rotonda_De_La_Juven'),
            ('Site Name','0131215_TA_Gamboa'),
            ('Site Name','0131255_TA_Av_Loreto'),
            ('Site Name','0131460_PN_Orizabal'),
            ('Site Name','0131461_PN_Autopista_Juliaca'),
            ('Site Name','0131622_JU_Huancayo_Proceres'),
            ('Site Name','0131654_JU_Parque_Pensamiento'),
            ('Site Name','0131655_JU_Campo_Ferial'),
            ('Site Name','0131674_JU_Electrocentro'),
            ('Site Name','013182363_CS_Cusco_La_Salle'),
            ('Site Name','0132041_CS_Songona'),
            ('Site Name','0132209_IC_Aahh_San_Isidro'),
            ('Site Name','013221088_JU_Perla_De_Los_Andes'),
            ('Site Name','013222570_JU_Aco_Junin'),
            ('Site Name','013222571_JU_Yavirironi'),
            ('Site Name','013224696_JU_Ocopilla'),
            ('Site Name','0132262_IC_COW_Coyote'),
            ('Site Name','0132266_IC_Pampa_De_La_Isla'),
            ('Site Name','0132799_CS_Urcos'),
            ('Site Name','0132863_PN_Putina'),
            ('Site Name','0132873_PN_Acora'),
            ('Site Name','0133416_CS_Quilla_Lucitana'),
            ('Site Name','0133548_LH_Aucayacu'),
            ('Site Name','0133583_LH_Bella_Durmiente'),
            ('Site Name','0133912_HU_Tinquerpata'),
            ('Site Name','0133953_AQ_Soldearequipa'),
            ('Site Name','013143969_AQ_Costanera_Bombon'),
            ('Site Name','0133997_AQ_Camana'),
            ('Site Name','0134001_AQ_San_Isidro_Labrador'),
            ('Site Name','0134091_AQ_Entrada_Apipa'),
            ('Site Name','0134097_AQ_Mohme_Llona'),
            ('Site Name','0134698_HU_Churcampa'),
            ('Site Name','0136486_TA_IB_Aerop_Tacna'),
            ('Site Name','0130843_IC_Caleta_San_Andres'),
            ('Site Name','0130892_IC_Pozo_Victoria'),
            ('Site Name','0130945_AQ_Pachacutec'),
            ('Site Name','0131333_CS_Obregosa'),
            ('Site Name','0131335_CS_Av_Libertad'),
            ('Site Name','0131457_PN_Nunez_Butron'),
            ('Site Name','0131493_PN_Coronel_Ponce'),
            ('Site Name','0131605_JU_Real'),
            ('Site Name','0131623_JU_Huancayo_Universidad_Andes'),
            ('Site Name','013211510_IC_Montecarmelo'),
            ('Site Name','013222444_JU_Rio_Chanchas_R1'),
            ('Site Name','013222569_JU_Tapo_Tarma'),
            ('Site Name','013224693_JU_Coronel_Parra'),
            ('Site Name','0132512_AQ_Tambomayo'),
            ('Site Name','0132625_AQ_Bano_de_Jesus'),
            ('Site Name','0132784_CS_Espinar_Bajo'),
            ('Site Name','0132790_CS_Yucay'),
            ('Site Name','0132793_CS_Awanacancha'),
            ('Site Name','0132813_PN_Torres_Belon'),
            ('Site Name','0133058_JU_Mazamari'),
            ('Site Name','0133657_AQ_Daniel_Comboni'),
            ('Site Name','0133706_AY_Las_Maravillas'),
            ('Site Name','0133809_AQ_Luna_Miranda'),
            ('Site Name','0133868_AQ_Belen_Aqp'),
            ('Site Name','0133927_AQ_Ala_Aerea_Aqp'),
            ('Site Name','0133932_AQ_Puerto_Rico_Aqp'),
            ('Site Name','0133975_AQ_El_Hebreo'),
            ('Site Name','0134352_AY_Querobamba_Ciudad'),
            ('Site Name','0134398_JU_Terminal_Hyo'),
            ('Site Name','0134689_IC_Sunampe_Grau'),
            ('Site Name','0134690_IC_Upis_Vilma'),
            ('Site Name','0134811_CS_IB_Hotel_Belmond'),
            ('Site Name','0134857_IC_IB_MP_Pisco'),
            ('Site Name','0134904_IC_IB_El_Quinde_Ica'),
            ('Site Name','0136365_IC_IB_MPExpress_Chinc'),
            ('Site Name','0136377_PN_IB_RP_Juliaca'),
            ('Site Name','0130968_AQ_Ingenieros_Aqp'),
            ('Site Name','0130989_AQ_Tahuaycani'),
            ('Site Name','0130993_IC_Calle_Mora'),
            ('Site Name','0130996_AQ_Malecon_Rivero'),
            ('Site Name','0131337_CS_Zarzuela_Cusco'),
            ('Site Name','0131347_CS_Ttio'),
            ('Site Name','013140239_AQ_Los_Gladiolos'),
            ('Site Name','013151591_AY_Carmen_De_Pacomarca'),
            ('Site Name','0131610_JU_Ingenio'),
            ('Site Name','0131612_JU_Concepcion'),
            ('Site Name','0131648_JU_Prado_De_Huancayo'),
            ('Site Name','0131662_JU_Plaza_Integracion'),
            ('Site Name','0131678_JU_Huaytapallana'),
            ('Site Name','0131688_IC_AV_Melchorita'),
            ('Site Name','013181534_CS_Wari_Kori'),
            ('Site Name','013182365_CS_Los_Heroes_Cusco'),
            ('Site Name','0132089_AY_Pausa'),
            ('Site Name','0132271_IC_Los_Libertadores'),
            ('Site Name','0132272_IC_Juan_Pablo'),
            ('Site Name','0132286_IC_Guadalupe_Salas'),
            ('Site Name','0132287_IC_Ciudad_Tate'),
            ('Site Name','0132509_AQ_Cow_Orcopampa'),
            ('Site Name','0132539_MD_Caychiwe'),
            ('Site Name','0132672_PN_Chucuito_Puno'),
            ('Site Name','0132811_PN_Plaza_Del_Faro'),
            ('Site Name','0132812_PN_Ovalo_Urbina'),
            ('Site Name','0132820_PN_Sillustani'),
            ('Site Name','0132832_PN_Huancane_Pueblo'),
            ('Site Name','0133076_JU_San_Martin_Pangoa'),
            ('Site Name','0133089_JU_Curipata_Junin'),
            ('Site Name','0133544_LH_Tallamonte'),
            ('Site Name','0133604_JU_Curicaca'),
            ('Site Name','0133794_AY_Ccowisa'),
            ('Site Name','0133797_AY_Acocro'),
            ('Site Name','0133806_AQ_Monterrey_Aqp'),
            ('Site Name','0133819_AQ_Vina_Del_Mar_Aqp'),
            ('Site Name','0133862_AQ_Victor_Lira'),
            ('Site Name','0133883_AQ_El_Pedregal'),
            ('Site Name','0134005_AQ_Huanca_Lluta'),
            ('Site Name','0134269_CS_Echarate_Ciudad'),
            ('Site Name','0134658_AQ_El_Chaparral'),
            ('Site Name','0134701_JU_Umuto'),
            ('Site Name','013313083_PN_Taparachi'),
            ('Site Name','0130912_AQ_Leones_Del_Misti'),
            ('Site Name','0130955_AQ_Vietnam'),
            ('Site Name','0131338_CS_Carmen_Alto'),
            ('Site Name','0131353_CS_La_Cultura'),
            ('Site Name','013144657_AQ_Catedral_Sachaca'),
            ('Site Name','0131608_JU_Hualhuas'),
            ('Site Name','0131609_JU_SanJeronimo_Tunan'),
            ('Site Name','0131656_JU_Yanama'),
            ('Site Name','0131664_JU_Colegio_Bertol'),
            ('Site Name','0131679_JU_Bosque_El_Porvenir'),
            ('Site Name','013210601_IC_Agricola_Yaurilla'),
            ('Site Name','013222156_JU_Orcotuna'),
            ('Site Name','0132291_IC_Jose_Tijero_Ica'),
            ('Site Name','0132343_AQ_Majes_Botija'),
            ('Site Name','0132387_IC_COW_Chincha'),
            ('Site Name','0132581_IC_Juan_Quinones'),
            ('Site Name','0132583_IC_Est_Hugo_Sotil'),
            ('Site Name','0132800_PN_Sesquicentenario'),
            ('Site Name','0132819_PN_Palomani'),
            ('Site Name','0132833_PN_Azangaro_Puno'),
            ('Site Name','0132844_PN_Lampa_Cerro'),
            ('Site Name','0132858_PN_Ananea'),
            ('Site Name','0133527_LH_Pilcomarca_Muni'),
            ('Site Name','0133528_LH_Huanuco_Centro'),
            ('Site Name','0133543_LH_Mapresa'),
            ('Site Name','0133590_JU_Chanchamarca'),
            ('Site Name','0133658_AQ_Misti_Invasion'),
            ('Site Name','0133774_AY_Cerro_Yanaorco'),
            ('Site Name','0133799_AY_Coracora'),
            ('Site Name','0133804_AQ_Villa_Medica'),
            ('Site Name','0133807_AQ_Pedro_Diez_Canseco'),
            ('Site Name','0133999_AQ_Selva_Alegre'),
            ('Site Name','0134058_AQ_Curva_Mollendo'),
            ('Site Name','0134578_JU_Chacapalpa'),
            ('Site Name','0131171_MQ_Torata_Plaza'),
            ('Site Name','013140101_AQ_El_Sillar'),
            ('Site Name','0131472_PN_Chullurin'),
            ('Site Name','0131492_PN_Republica_Juliaca'),
            ('Site Name','0131498_PN_Rivera_Lago'),
            ('Site Name','0131619_JU_La_Merced'),
            ('Site Name','0131637_JU_Cerrito_La_Libertad'),
            ('Site Name','0131675_JU_Palian'),
            ('Site Name','0132035_JU_Rep_Trampajase'),
            ('Site Name','013210704_IC_Rene_Toche'),
            ('Site Name','013221582_JU_Concho'),
            ('Site Name','013221741_JU_Puente_Shullcas'),
            ('Site Name','013222572_JU_Huayre'),
            ('Site Name','0132330_PN_Huatasani'),
            ('Site Name','0132345_PN_Sollocota'),
            ('Site Name','0132651_PN_Pasincha'),
            ('Site Name','0132654_PN_Putina_Bajo'),
            ('Site Name','0132810_PN_Arco_Deustua'),
            ('Site Name','013291008_CP_Sector_Uliachin'),
            ('Site Name','013291084_CP_Selvamonos'),
            ('Site Name','0132928_PN_Sandia'),
            ('Site Name','0133034_JU_Quilla'),
            ('Site Name','0133310_IC_Acequia_Bocatoma'),
            ('Site Name','0133502_LH_Barroso'),
            ('Site Name','0133535_LH_Universidad_Huanuco'),
            ('Site Name','0133803_AQ_Blanca_Arequipa'),
            ('Site Name','0133805_AQ_Alas_Del_Sur'),
            ('Site Name','0133878_AQ_Hipodromo_Arequipa'),
            ('Site Name','0134077_AQ_Las_Flores_Chala'),
            ('Site Name','0134098_AQ_Menelik'),
            ('Site Name','0134392_IC_Aahh_Miguel_Grau'),
            ('Site Name','0134662_AQ_Cuartel_Salaverry'),
            ('Site Name','0134672_IC_Avenida_Siete'),
            ('Site Name','0134673_IC_Blanca_Progreso'),
            ('Site Name','0134676_IC_Calle_Osores'),
            ('Site Name','0136549_AQ_Ccachaylla'),
            ('Site Name','0130871_IC_Puente_Blanco'),
            ('Site Name','0130919_AQ_La_Joya'),
            ('Site Name','0130930_AQ_Atico'),
            ('Site Name','0131323_CS_Estadio_Garcilazo'),
            ('Site Name','0131360_CS_Los_Jardines_Cusco'),
            ('Site Name','0131441_PN_Caceres_Prada'),
            ('Site Name','0131626_JU_Esalud_Huancayo'),
            ('Site Name','0131629_JU_Pio_Pata'),
            ('Site Name','0131644_JU_Husares'),
            ('Site Name','0131665_JU_Antonio_De_Zela'),
            ('Site Name','0131680_JU_Hospital_Alcides'),
            ('Site Name','0131687_JU_Leoncio_Prado'),
            ('Site Name','013210718_IC_Ramon_Saravia'),
            ('Site Name','013221002_JU_Jr_La_Resentida'),
            ('Site Name','0132525_AQ_Coropuna'),
            ('Site Name','0132789_CS_Urubamba_Centro'),
            ('Site Name','0133054_JU_Chanchamayo_Ciudad'),
            ('Site Name','0133504_LH_Real_Hotel_Huanuco'),
            ('Site Name','0133509_LH_Via_Pilcomarca'),
            ('Site Name','0133822_AQ_El_Porvenir_Aqp'),
            ('Site Name','0133923_AQ_Jaime_Nunez'),
            ('Site Name','0133933_AQ_Belvedere'),
            ('Site Name','0133984_AQ_Revolucion_Arequipa'),
            ('Site Name','0134089_AQ_Angel_Monteagudo'),
            ('Site Name','0136485_PN_IB_Aerop_Juliaca'),
            ('Site Name','0130914_AQ_Cerro_Gloria'),
            ('Site Name','0130932_AQ_Camana_Cerro'),
            ('Site Name','0130965_AQ_Moran_Uribe'),
            ('Site Name','0131381_CS_Andina_Del_Cusco'),
            ('Site Name','0131613_JU_Jauja'),
            ('Site Name','013220021_JU_Tres_De_Diciembre'),
            ('Site Name','013221783_JU_Pilcomayo_R1'),
            ('Site Name','0133046_JU_Cesar_Vallejo'),
            ('Site Name','0133867_AQ_Alto_Cayma'),
            ('Site Name','0133926_AQ_Avenida_Suasnabar'),
            ('Site Name','0134395_JU_Hatun_Sausa'),
            ('Site Name','0130801_IC_Palpa'),
            ('Site Name','0130889_IC_Viena_Madrid'),
            ('Site Name','0130904_AQ_Guillermo_Mercado'),
            ('Site Name','0131336_CS_Feria_Huancaro'),
            ('Site Name','0131362_CS_Almudena'),
            ('Site Name','013220015_JU_Estadio_Union_Tarma'),
            ('Site Name','013223025_JU_Raimondi_Satipo'),
            ('Site Name','013224397_JU_San_Juan_Iscos'),
            ('Site Name','0132415_AQ_Charcani_Chico'),
            ('Site Name','0132788_CS_Loma_Taray'),
            ('Site Name','0133004_JU_Pichanaki'),
            ('Site Name','0133032_JU_Cerro_Pichanaki'),
            ('Site Name','013310225_PN_Coasa'),
            ('Site Name','0133605_HU_Huancavelica_Plaza'),
            ('Site Name','0133608_HU_Huancavelica'),
            ('Site Name','0133673_CS_Andahuailillas'),
            ('Site Name','0133899_AQ_Umapalca'),
            ('Site Name','0133919_AQ_Villa_Contreras'),
            ('Site Name','0134043_AQ_Lluta'),
        ]
        for cat, val in opciones_sitios:
            db.session.add(OpcionDesplegable(categoria=cat, valor=val))

    db.session.commit()

# Inicializar BD al importar el módulo (compatible con gunicorn y python directo)
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
