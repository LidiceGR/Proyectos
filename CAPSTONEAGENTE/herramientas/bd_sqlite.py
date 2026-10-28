import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
RUTA_BD = BASE / "datos" / "demo.db"


def saludar():
    return "Hola mundo desde la herramienta de BD"


def crear_bd_demo():

    with sqlite3.connect(RUTA_BD) as conexion:

        conexion.executescript("""
            CREATE TABLE IF NOT EXISTS Clientes (
                ClienteId INTEGER PRIMARY KEY,
                Nombre TEXT NOT NULL,
                Estado TEXT DEFAULT 'ACTIVO'
            );

            CREATE TABLE IF NOT EXISTS Operaciones (
                OperacionId INTEGER PRIMARY KEY,
                ClienteId INTEGER NOT NULL,
                TipoOperacion INTEGER NOT NULL,
                Monto REAL NOT NULL CHECK (Monto >= 0),
                FOREIGN KEY (ClienteId)
                    REFERENCES Clientes(ClienteId)
            );
        """)

    return str(RUTA_BD)


def obtener_esquema():

    with sqlite3.connect(RUTA_BD) as conexion:

        filas = conexion.execute("""
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()

    return [
        {"tabla": nombre, "ddl": definicion}
        for nombre, definicion in filas
    ]