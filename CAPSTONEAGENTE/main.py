import sys
import json
from pathlib import Path

from herramientas.bd_sqlite import (
    saludar,
    crear_bd_demo,
    obtener_esquema
)
from herramientas.test import(saluda_test)


def main():

    # Primera prueba
    print(saluda_test())

    # Segunda prueba (cuando indiquemos "esquema")
    if len(sys.argv) > 1 and sys.argv[1] == "esquema":

        ruta_bd = crear_bd_demo()
        print("Base de datos:", ruta_bd)

        esquema = obtener_esquema()

        ruta_resultado = (
            Path(__file__).resolve().parent
            / "resultados" / "esquema.json"
        )

        ruta_resultado.write_text(
            json.dumps(esquema, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print(json.dumps(esquema, indent=2, ensure_ascii=False))
        print("Esquema guardado en:", ruta_resultado)


if __name__ == "__main__":
    main()
  