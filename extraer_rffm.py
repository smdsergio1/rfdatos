import requests
from bs4 import BeautifulSoup
import json
import csv
import time
import unicodedata

TEMPORADA_ID = "22"  # 2026-2027
TIPO_JUEGO_ID = "1"  # 1 = Fútbol-11
CATEGORIAS_OBJETIVO = ("INFANTIL", "CADETE", "JUVENIL")
PALABRAS_EXCLUIDAS = ("FEMENIN",)  # Excluye FEMENINO, FEMENINA, FEMENINOS, etc.

BASE_URL = "https://www.rffm.es"
PAUSA_SEGUNDOS = 0.35

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html"
})


def normalizar_texto(texto):
    if not texto:
        return ""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )
    return sin_tildes.upper().strip()


def es_categoria_objetivo(competicion_dict):
    categoria = normalizar_texto(competicion_dict.get("NombreCategoria", ""))
    nombre = normalizar_texto(competicion_dict.get("nombre", ""))
    texto_combinado = f"{categoria} {nombre}"

    # 1. Descartar ligas/copas femeninas
    if any(excl in texto_combinado for excl in PALABRAS_EXCLUIDAS):
        return False

    # 2. Quedarnos solo con Infantil, Cadete o Juvenil
    return any(cat in texto_combinado for cat in CATEGORIAS_OBJETIVO)


def obtener_json_api(endpoint, params=None, reintentos=3):
    url = f"{BASE_URL}{endpoint}"
    for intento in range(reintentos):
        try:
            resp = session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if intento == reintentos - 1:
                print(f"  [!] Error en {url} {params}: {e}")
                return []
            time.sleep(1.5 * (intento + 1))
    return []


def obtener_next_data(url, reintentos=3):
    for intento in range(reintentos):
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            script = soup.find("script", id="__NEXT_DATA__")
            if script and script.string:
                return json.loads(script.string).get("props", {}).get("pageProps", {})
            return {}
        except Exception as e:
            if intento == reintentos - 1:
                print(f"  [!] Error descargando HTML {url}: {e}")
                return {}
            time.sleep(1.5 * (intento + 1))
    return {}


def extraer_equipos_grupo(temporada, tipo_juego, comp_id, grupo_id):
    # 1. Clasificación (método principal)
    url_clasif = f"{BASE_URL}/competicion/clasificaciones?temporada={temporada}&tipojuego={tipo_juego}&competicion={comp_id}&grupo={grupo_id}"
    page_props = obtener_next_data(url_clasif)
    clasificacion = page_props.get("standings", {}).get("clasificacion", [])

    equipos = []
    if clasificacion:
        for eq in clasificacion:
            img_relativa = eq.get("url_img", "")
            equipos.append({
                "codigo_equipo": str(eq.get("codequipo", "")).strip(),
                "nombre": eq.get("nombre", "").strip(),
                "escudo_url": f"{BASE_URL}{img_relativa}" if img_relativa.startswith("/") else img_relativa,
                "posicion": eq.get("posicion", ""),
                "puntos": eq.get("puntos", "")
            })
        return equipos

    # 2. Respaldo: Calendario (para Copas/Eliminatorias sin clasificación)
    url_cal = f"{BASE_URL}/competicion/calendario?temporada={temporada}&tipojuego={tipo_juego}&competicion={comp_id}&grupo={grupo_id}"
    page_props_cal = obtener_next_data(url_cal)
    vistos = {}

    def buscar_partidos(obj):
        if isinstance(obj, dict):
            cod_loc = obj.get("codigo_equipo_local") or obj.get("cod_equipo_local")
            nom_loc = obj.get("equipo_local") or obj.get("nombre_equipo_local")
            if cod_loc and nom_loc and str(cod_loc) not in vistos:
                img = obj.get("escudo_equipo_local", "")
                vistos[str(cod_loc)] = {
                    "codigo_equipo": str(cod_loc).strip(),
                    "nombre": nom_loc.strip(),
                    "escudo_url": f"{BASE_URL}{img}" if img.startswith("/") else img,
                    "posicion": "",
                    "puntos": ""
                }
            cod_vis = obj.get("codigo_equipo_visitante") or obj.get("cod_equipo_visitante")
            nom_vis = obj.get("equipo_visitante") or obj.get("nombre_equipo_visitante")
            if cod_vis and nom_vis and str(cod_vis) not in vistos:
                img = obj.get("escudo_equipo_visitante", "")
                vistos[str(cod_vis)] = {
                    "codigo_equipo": str(cod_vis).strip(),
                    "nombre": nom_vis.strip(),
                    "escudo_url": f"{BASE_URL}{img}" if img.startswith("/") else img,
                    "posicion": "",
                    "puntos": ""
                }
            for v in obj.values():
                buscar_partidos(v)
        elif isinstance(obj, list):
            for item in obj:
                buscar_partidos(item)

    buscar_partidos(page_props_cal)
    return list(vistos.values())


def ejecutar_extraccion():
    print(f"Iniciando extracción RFFM | Temporada {TEMPORADA_ID} | Fútbol-11 Masculino (Infantil, Cadete, Juvenil)...")

    competiciones = obtener_json_api("/api/competitions", {
        "temporada": TEMPORADA_ID,
        "tipojuego": TIPO_JUEGO_ID
    })

    estructura_completa = {
        "temporada": TEMPORADA_ID,
        "codigo_tipo_juego": TIPO_JUEGO_ID,
        "tipo_juego": "Futbol-11",
        "competiciones": []
    }
    filas_csv = []

    for comp in competiciones:
        cod_comp = str(comp.get("codigo"))
        nom_comp = comp.get("nombre", "").strip()
        categoria = comp.get("NombreCategoria", "").strip()

        if not es_categoria_objetivo(comp):
            print(f"  [Saltada] {nom_comp} [{categoria}]")
            continue

        print(f"\n-> Extrayendo: {nom_comp} [{categoria}] (ID: {cod_comp})")

        grupos = obtener_json_api("/api/groups", {"competicion": cod_comp})
        comp_nodo = {
            "codigo_competicion": cod_comp,
            "nombre": nom_comp,
            "categoria": categoria,
            "grupos": []
        }

        for grp in grupos:
            cod_grp = str(grp.get("codigo"))
            nom_grp = grp.get("nombre", "").strip()
            delegacion = grp.get("nombre_delegacion", "").strip()

            equipos = extraer_equipos_grupo(TEMPORADA_ID, TIPO_JUEGO_ID, cod_comp, cod_grp)
            print(f"   * {nom_grp} (ID: {cod_grp}): {len(equipos)} equipos")

            comp_nodo["grupos"].append({
                "codigo_grupo": cod_grp,
                "nombre": nom_grp,
                "delegacion": delegacion,
                "equipos": equipos
            })

            for eq in equipos:
                filas_csv.append({
                    "temporada": TEMPORADA_ID,
                    "tipo_juego": "Futbol-11",
                    "categoria": categoria,
                    "codigo_competicion": cod_comp,
                    "competicion": nom_comp,
                    "codigo_grupo": cod_grp,
                    "grupo": nom_grp,
                    "delegacion": delegacion,
                    "codigo_equipo": eq["codigo_equipo"],
                    "equipo": eq["nombre"],
                    "escudo_url": eq["escudo_url"]
                })

            time.sleep(PAUSA_SEGUNDOS)

        estructura_completa["competiciones"].append(comp_nodo)

        # Guardado parcial tras cada competición
        with open("rffm_f11_icj.json", "w", encoding="utf-8") as f_json:
            json.dump(estructura_completa, f_json, ensure_ascii=False, indent=2)

    # Guardado final JSON y CSV
    with open("rffm_f11_icj.json", "w", encoding="utf-8") as f_json:
        json.dump(estructura_completa, f_json, ensure_ascii=False, indent=2)

    if filas_csv:
        with open("rffm_f11_icj.csv", "w", newline="", encoding="utf-8-sig") as f_csv:
            writer = csv.DictWriter(f_csv, fieldnames=filas_csv[0].keys(), delimiter=";")
            writer.writeheader()
            writer.writerows(filas_csv)

    print(f"\n¡Listo! Total de registros guardados: {len(filas_csv)}")
    print("Archivos generados: 'rffm_f11_icj.json' y 'rffm_f11_icj.csv'")


if __name__ == "__main__":
    ejecutar_extraccion()