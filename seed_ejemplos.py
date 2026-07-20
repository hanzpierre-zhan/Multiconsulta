"""Script para insertar 10 incidencias de ejemplo en la base de datos"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app import app, db, Incidencia
from datetime import datetime, timedelta
import random

ejemplos = [
    ("INC-1001", "Lima",        "Falla en enlace de fibra óptica en nodo central",       "Carlos Quispe",    "Jius",       "Maria Ramos",   "8HRS",  "Asignado"),
    ("INC-1002", "Arequipa",    "Antena sin señal por caída de energía",                 "Luis Torres",      "Gesitel",    "Pedro Diaz",    "16HRS", "Pendiente"),
    ("INC-1003", "Cusco",       "Router caído en site remoto - cliente crítico",         "Ana Flores",       "HBA Proyect","Jose Vela",     "8HRS",  "Cierre Operativo"),
    ("INC-1004", "Piura",       "Intermitencia en radio enlace microondas",              "Juan Mamani",      "Satelecom",  "Rosa Gutierrez","48HRS", "Parada de Reloj"),
    ("INC-1005", "La Libertad", "Vandalismo en gabinete de telecomunicaciones",          "Pedro Salinas",    "Cobra",      "Carlos Mendez", "16HRS", "Asignado"),
    ("INC-1006", "Callao",      "Equipo OLT con alarma de temperatura alta",             "Sofia Mendoza",    "Nastel",     "Lucia Vargas",  "8HRS",  "Pendiente"),
    ("INC-1007", "Junin",       "Cable de alimentación dañado por roedor",               "Miguel Condor",    "Jius",       "Antonio Huanca","48HRS", "Liquidado"),
    ("INC-1008", "Tacna",       "Site sin alimentación por corte de SEAL",               "Rosa Tapia",       "Gesitel",    "Beatriz Callo", "8HRS",  "Asignado"),
    ("INC-1009", "Ica",         "Degradación de BER en enlace SDH anillo norte",        "Jorge Vargas",     "HBA Proyect","Fernando Pinto","16HRS", "Parada de Reloj"),
    ("INC-1010", "Ancash",      "Torre con inclinación peligrosa reportada por cliente", "Diana Castro",     "Satelecom",  "Guillermo Rios","48HRS", "Cierre Operativo"),
]

with app.app_context():
    insertados = 0
    for idx, (ticket, dep, desc, tec, cont, gest, sla, estado) in enumerate(ejemplos):
        if Incidencia.query.filter_by(numero_ticket=ticket).first():
            print(f"  [SKIP] {ticket} ya existe")
            continue
        fecha = datetime.utcnow() - timedelta(hours=random.randint(1, 72))
        inc = Incidencia(
            numero_ticket   = ticket,
            departamento    = dep,
            descripcion     = desc,
            tecnico_asignado= tec,
            contrata        = cont,
            gestor          = gest,
            sla             = sla,
            estado          = estado,
            usuario_creador = "hvargas",
            fecha_hora      = fecha
        )
        db.session.add(inc)
        insertados += 1
        print(f"  [OK] {ticket} - {dep} - {estado}")
    
    db.session.commit()
    print(f"\n✅ Listo: {insertados} incidencias insertadas.")
