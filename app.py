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
        'Ticket', 'Fecha Ticket (Agente)', 'Fecha Captura (Sistema)',
        'Gestor/Registrador', 'Departamento', 'Descripcion',
        'Tecnico', 'Contrata', 'SLA', 'Estado'
    ])
    for i in incidencias:
        writer.writerow([
            i.numero_ticket,
            i.fecha_ticket.strftime('%Y-%m-%d %H:%M') if i.fecha_ticket else '',
            i.fecha_captura.strftime('%Y-%m-%d %H:%M') if i.fecha_captura else '',
            i.gestor, i.departamento,
            i.descripcion, i.tecnico_asignado, i.contrata, i.sla, i.estado
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
    resultado = {'Departamento': [], 'Contrata': [], 'SLA': [], 'Estado': []}
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
            for col, tipo in [('fecha_ticket', 'DATETIME'), ('fecha_captura', 'DATETIME')]:
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

    db.session.commit()

# Inicializar BD al importar el módulo (compatible con gunicorn y python directo)
with app.app_context():
    init_db()

if __name__ == '__main__':
    app.run(debug=True, port=5001)
