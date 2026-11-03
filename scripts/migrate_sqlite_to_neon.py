"""
Script ejecutable e idempotente de migración única desde SQLite local (eventos_bancomext.db)
hacia Neon Serverless PostgreSQL + pgvector (768 dimensiones).
"""

from __future__ import annotations

import os
import sqlite3
import sys

# Permitir importación desde el directorio raíz del proyecto
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sqlalchemy import text

import database as db
from ai_discovery.deduplicator import build_huella_text, generate_semantic_embedding


def migrate_sqlite_to_neon() -> dict:
    """
    1. Inicializa el esquema e índices vectoriales en Neon PostgreSQL.
    2. Si existe data/eventos_bancomext.db, migra eventos, usuarios y bitacora_notificaciones
       usando ON CONFLICT DO NOTHING.
    3. Para cada evento en `eventos` sin `huella_semantica`, calcula su embedding de 768 dims
       sobre `f"{categoria} | {sede or ''} | {promotor or ''} | {nombre}"` y actualiza la fila.
    """
    print("[1/4] Inicializando esquema y extensión pgvector en Neon PostgreSQL...")
    db.init_db()
    engine = db.get_engine()

    stats = {
        "sqlite_found": os.path.exists(db.DB_PATH),
        "eventos_migrados": 0,
        "usuarios_migrados": 0,
        "bitacora_migrada": 0,
        "vectores_generados": 0,
    }

    if os.path.exists(db.DB_PATH):
        print(f"[2/4] Leyendo base de datos SQLite local: {db.DB_PATH}")
        sq_conn = sqlite3.connect(db.DB_PATH)
        sq_conn.row_factory = sqlite3.Row
        cur = sq_conn.cursor()

        eventos_rows = [dict(r) for r in cur.execute("SELECT * FROM eventos").fetchall()]
        usuarios_rows = [dict(r) for r in cur.execute("SELECT * FROM usuarios").fetchall()]
        bitacora_rows = [dict(r) for r in cur.execute("SELECT * FROM bitacora_notificaciones").fetchall()]
        sq_conn.close()

        print(
            f"[3/4] Migrando a Neon PostgreSQL: {len(eventos_rows)} eventos, "
            f"{len(usuarios_rows)} usuarios, {len(bitacora_rows)} registros de bitácora..."
        )
        with engine.begin() as pg_conn:
            for ev in eventos_rows:
                res = pg_conn.execute(
                    text(
                        """
                        INSERT INTO eventos (
                            id, nombre, descripcion, fecha_inicio, fecha_fin, horario,
                            modalidad, sede, direccion, estado, estado_id, municipio,
                            codigo_postal, latitud, longitud, google_maps_url,
                            categoria, promotor, sitio_web, creado_por, created_at, updated_at
                        ) VALUES (
                            :id, :nombre, :descripcion, :fecha_inicio, :fecha_fin, :horario,
                            :modalidad, :sede, :direccion, :estado, :estado_id, :municipio,
                            :codigo_postal, :latitud, :longitud, :google_maps_url,
                            :categoria, :promotor, :sitio_web, :creado_por, :created_at, :updated_at
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": ev["id"],
                        "nombre": ev["nombre"],
                        "descripcion": ev["descripcion"],
                        "fecha_inicio": ev["fecha_inicio"],
                        "fecha_fin": ev["fecha_fin"],
                        "horario": ev["horario"],
                        "modalidad": ev["modalidad"],
                        "sede": ev.get("sede", ""),
                        "direccion": ev.get("direccion", ""),
                        "estado": ev.get("estado", "Ciudad de México"),
                        "estado_id": ev.get("estado_id", "MX-CMX"),
                        "municipio": ev.get("municipio", ""),
                        "codigo_postal": ev.get("codigo_postal", ""),
                        "latitud": float(ev.get("latitud", 19.4326)),
                        "longitud": float(ev.get("longitud", -99.1332)),
                        "google_maps_url": ev.get("google_maps_url", ""),
                        "categoria": ev["categoria"],
                        "promotor": ev.get("promotor", "NAFIN / BANCOMEXT"),
                        "sitio_web": ev.get("sitio_web", ""),
                        "creado_por": ev.get("creado_por", "sistema"),
                        "created_at": ev.get("created_at", "2026-10-28T09:00:00"),
                        "updated_at": ev.get("updated_at", "2026-10-28T09:00:00"),
                    },
                )
                stats["eventos_migrados"] += res.rowcount or 0

            for u in usuarios_rows:
                res = pg_conn.execute(
                    text(
                        """
                        INSERT INTO usuarios (
                            username, nombre_completo, cargo, email, telefono_whatsapp,
                            password_hash, rol, recibe_email, recibe_gcal, recibe_whatsapp,
                            filtro_estados, filtro_categorias, anticipacion_dias, activo, created_at
                        ) VALUES (
                            :username, :nombre_completo, :cargo, :email, :telefono_whatsapp,
                            :password_hash, :rol, :recibe_email, :recibe_gcal, :recibe_whatsapp,
                            :filtro_estados, :filtro_categorias, :anticipacion_dias, :activo, :created_at
                        ) ON CONFLICT (username) DO NOTHING
                        """
                    ),
                    {
                        "username": u["username"],
                        "nombre_completo": u["nombre_completo"],
                        "cargo": u["cargo"],
                        "email": u["email"],
                        "telefono_whatsapp": u.get("telefono_whatsapp", ""),
                        "password_hash": u["password_hash"],
                        "rol": u["rol"],
                        "recibe_email": int(u.get("recibe_email", 1)),
                        "recibe_gcal": int(u.get("recibe_gcal", 1)),
                        "recibe_whatsapp": int(u.get("recibe_whatsapp", 0)),
                        "filtro_estados": u.get("filtro_estados", "TODOS"),
                        "filtro_categorias": u.get("filtro_categorias", "TODAS"),
                        "anticipacion_dias": u.get("anticipacion_dias", "7,1,0"),
                        "activo": int(u.get("activo", 1)),
                        "created_at": u.get("created_at", "2026-10-28T09:00:00"),
                    },
                )
                stats["usuarios_migrados"] += res.rowcount or 0

            for b in bitacora_rows:
                res = pg_conn.execute(
                    text(
                        """
                        INSERT INTO bitacora_notificaciones (
                            id, evento_id, usuario_id, destinatario_email, canal,
                            tipo_disparo, estatus_envio, hash_version, fecha_envio
                        ) VALUES (
                            :id, :evento_id, :usuario_id, :destinatario_email, :canal,
                            :tipo_disparo, :estatus_envio, :hash_version, :fecha_envio
                        ) ON CONFLICT (id) DO NOTHING
                        """
                    ),
                    {
                        "id": int(b["id"]),
                        "evento_id": b["evento_id"],
                        "usuario_id": int(b["usuario_id"]),
                        "destinatario_email": b["destinatario_email"],
                        "canal": b["canal"],
                        "tipo_disparo": b["tipo_disparo"],
                        "estatus_envio": b["estatus_envio"],
                        "hash_version": b["hash_version"],
                        "fecha_envio": b["fecha_envio"],
                    },
                )
                stats["bitacora_migrada"] += res.rowcount or 0

            # Sincronizar secuencias SERIAL de PostgreSQL tras insertar IDs explícitos
            pg_conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('usuarios', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM usuarios), 1), true);"
                )
            )
            pg_conn.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('bitacora_notificaciones', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM bitacora_notificaciones), 1), true);"
                )
            )
    else:
        print("[2/4] No se encontró archivo SQLite local; usando poblado semilla en Neon.")

    print("[4/4] Verificando y generando embeddings semánticos (768 dims) para eventos en Neon...")
    with engine.connect() as pg_conn:
        missing_vec_rows = pg_conn.execute(
            text(
                """
                SELECT id, categoria, sede, promotor, nombre
                FROM eventos
                WHERE huella_semantica IS NULL
                ORDER BY id ASC
                """
            )
        ).mappings().all()

    for row in missing_vec_rows:
        cadena_huella = build_huella_text(
            categoria=row["categoria"],
            sede=row["sede"],
            promotor=row["promotor"],
            nombre=row["nombre"],
        )
        vec = generate_semantic_embedding(cadena_huella)
        vec_literal = "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"
        with engine.begin() as pg_conn:
            pg_conn.execute(
                text(
                    """
                    UPDATE eventos
                    SET huella_semantica = CAST(:vec AS vector)
                    WHERE id = :id
                    """
                ),
                {"vec": vec_literal, "id": row["id"]},
            )
        stats["vectores_generados"] += 1
        print(f"  -> Vector 768d generado para {row['id']}: {row['nombre'][:55]}")

    print(f"Migración finalizada con éxito: {stats}")
    return stats


if __name__ == "__main__":
    migrate_sqlite_to_neon()

