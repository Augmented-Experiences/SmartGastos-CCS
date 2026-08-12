"""
Gestión de base de datos SQLite para SmartGastos.
Inicialización, sesiones y utilidades de persistencia.

Mejoras de concurrencia (Sprint 2):
- Reemplazado StaticPool por NullPool para evitar bloqueos en FastAPI async.
- Configurado WAL mode y busy_timeout para mejor concurrencia en SQLite.
- Agregado context manager para sesiones seguras.
"""
import os
import json
import logging
import sqlite3
from pathlib import Path
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import NullPool
from models import Base

logger = logging.getLogger(__name__)

# Configuración — ruta absoluta desde __file__ como fallback
# database.py está en server/, el plugin está en server/../
_BASE_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = Path(os.environ.get("DATA_DIR", str(_BASE_DIR / "data")))
DB_PATH = DATA_DIR / "smartgastos.db"
# El nombre histórico se construye sin exhibir la marca anterior. Solo se usa
# durante la migración automática para preservar datos de instalaciones previas.
_LEGACY_DB_PATH = DATA_DIR / "_".join(("pyme", "ledger.db"))

# Crear directorio si no existe
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _migrate_database_filename() -> None:
    """Copia atómicamente la base histórica al nombre SmartGastos.

    ``sqlite3.Connection.backup`` incorpora un posible archivo WAL pendiente,
    a diferencia de renombrar solo el archivo principal. La fuente se elimina
    únicamente después de terminar la copia para impedir pérdida de datos.
    """
    if DB_PATH.exists() or not _LEGACY_DB_PATH.exists():
        return
    source = destination = None
    try:
        source = sqlite3.connect(str(_LEGACY_DB_PATH))
        destination = sqlite3.connect(str(DB_PATH))
        source.backup(destination)
        destination.commit()
    except Exception:
        try:
            if DB_PATH.exists():
                DB_PATH.unlink()
        except OSError:
            pass
        raise
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
    _LEGACY_DB_PATH.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        (_LEGACY_DB_PATH.parent / f"{_LEGACY_DB_PATH.name}{suffix}").unlink(missing_ok=True)
    logger.info("Base de datos local migrada al archivo SmartGastos")


_migrate_database_filename()

# Crear engine SQLite con NullPool (cada request obtiene su propia conexión)
# Esto evita el problema "database is locked" en entornos async con múltiples hilos.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={
        "check_same_thread": False,
        "timeout": 30,  # busy_timeout en segundos para esperar si hay lock
    },
    poolclass=NullPool,  # Sin pool: cada sesión abre/cierra su propia conexión
    echo=False
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Configura pragmas de SQLite para mejor concurrencia y rendimiento."""
    cursor = dbapi_conn.cursor()
    # WAL mode permite lecturas concurrentes con escrituras
    cursor.execute("PRAGMA journal_mode=WAL")
    # Timeout de 30 segundos si la DB está bloqueada
    cursor.execute("PRAGMA busy_timeout=30000")
    # Sync normal es suficiente con WAL
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Cache de 64MB para mejor rendimiento
    cursor.execute("PRAGMA cache_size=-65536")
    cursor.close()


# Crear session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db_session():
    """Context manager para obtener una sesión de base de datos con cierre garantizado."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _migrate_empresas_schema() -> None:
    """Migra instalaciones antiguas sin un ORM externo de migraciones.

    Las versiones previas usaban un RUT centinela compartido y una columna
    ``rut NOT NULL``. SQLite no permite quitar esa restricción con ALTER TABLE,
    por lo que se reconstruye exclusivamente la tabla de empresas, preservando
    IDs y relaciones. El RUT centinela se convierte en NULL para que cada
    empresa sin RUT conserve su identidad UUID propia.
    """
    with engine.begin() as connection:
        rows = connection.exec_driver_sql("PRAGMA table_info(empresas)").mappings().all()
        if not rows:
            return
        columns = {row["name"]: row for row in rows}
        rut_is_required = bool(columns.get("rut", {}).get("notnull"))
        giro_missing = "giro" not in columns
        if not rut_is_required and not giro_missing:
            return

        has_giro = "giro" in columns
        giro_expr = "giro" if has_giro else (
            "CASE WHEN lower(COALESCE(regimen_tributario, '')) NOT IN "
            "('propyme', 'pro pyme', 'general', 'régimen general', 'regimen general') "
            "THEN regimen_tributario ELSE NULL END"
        )
        regimen_expr = (
            "CASE WHEN lower(COALESCE(regimen_tributario, '')) IN "
            "('general', 'régimen general', 'regimen general') THEN 'General' ELSE 'ProPyme' END"
        )
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql("""
            CREATE TABLE empresas__migrating (
                id VARCHAR(36) PRIMARY KEY,
                razon_social VARCHAR(255) NOT NULL,
                nombre_fantasia VARCHAR(255),
                rut VARCHAR(20) UNIQUE,
                pais VARCHAR(50),
                moneda_base VARCHAR(3),
                regimen_tributario VARCHAR(50),
                giro VARCHAR(255),
                carpeta_raiz VARCHAR(500),
                reglas_contables TEXT,
                activa BOOLEAN,
                fecha_creacion DATETIME,
                fecha_actualizacion DATETIME
            )
        """)
        connection.exec_driver_sql(f"""
            INSERT INTO empresas__migrating (
                id, razon_social, nombre_fantasia, rut, pais, moneda_base,
                regimen_tributario, giro, carpeta_raiz, reglas_contables,
                activa, fecha_creacion, fecha_actualizacion
            )
            SELECT id, razon_social, nombre_fantasia,
                   CASE WHEN rut = '00.000.000-0' OR rut = '' THEN NULL ELSE rut END,
                   COALESCE(pais, 'Chile'), COALESCE(moneda_base, 'CLP'),
                   {regimen_expr}, {giro_expr}, carpeta_raiz, reglas_contables,
                   activa, fecha_creacion, fecha_actualizacion
            FROM empresas
        """)
        connection.exec_driver_sql("DROP TABLE empresas")
        connection.exec_driver_sql("ALTER TABLE empresas__migrating RENAME TO empresas")
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        logger.info("Migración de empresas completada: RUT opcional y giro separado")


def init_db():
    """Inicializa las tablas y migra esquemas previos de forma idempotente."""
    Base.metadata.create_all(bind=engine)
    _migrate_empresas_schema()
    Base.metadata.create_all(bind=engine)
    logger.info(f"Base de datos inicializada en: {DB_PATH}")
    print(f"✅ Base de datos inicializada en: {DB_PATH}")


def get_db() -> Session:
    """Obtiene una sesión de base de datos (generador para FastAPI Depends)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def save_json(path: Path, data: dict):
    """Guarda un diccionario como JSON con UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_json(path: Path, default=None) -> dict:
    """Carga un JSON desde archivo."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error cargando JSON {path}: {e}")
            return default or {}
    return default or {}


def save_list_json(path: Path, data: list):
    """Guarda una lista como JSON con UTF-8."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_list_json(path: Path, default=None) -> list:
    """Carga una lista JSON desde archivo."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Error cargando JSON {path}: {e}")
            return default or []
    return default or []
