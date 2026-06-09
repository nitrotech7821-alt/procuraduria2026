import os
from io import BytesIO
from datetime import datetime

import pandas as pd
import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import requests
from google.oauth2 import service_account
from google.auth.transport.requests import Request
from geopy.geocoders import Nominatim
import folium
from streamlit_folium import st_folium
from fpdf import FPDF

# =====================================================
# CONFIGURACIÓN
# =====================================================
st.set_page_config(
    page_title="Trayectoria Académica y Social DIF",
    page_icon="📍",
    layout="wide"
)

COLECCION = "trayectoria_escuelas"
JSON_LOCAL = "dif-hermosillo-firebase-adminsdk-fbsvc-02227ae71c.json"
LIMITE_REGISTROS = 1000


CONFIG_NIVELES = {
    "PREESCOLAR": {"color": "green", "rgb": (0, 128, 0), "icon": "child"},
    "PRIMARIA": {"color": "blue", "rgb": (0, 0, 255), "icon": "graduation-cap"},
    "SECUNDARIA": {"color": "orange", "rgb": (255, 165, 0), "icon": "graduation-cap"},
    "PREPARATORIA": {"color": "red", "rgb": (255, 0, 0), "icon": "graduation-cap"},
    "UNIVERSIDAD": {"color": "purple", "rgb": (128, 0, 128), "icon": "university"},
    "FUNDACIÓN O ORG": {"color": "cadetblue", "rgb": (95, 158, 160), "icon": "heart"},
}

# =====================================================
# FIRESTORE REST - evita que grpc se quede pensando en Streamlit
# =====================================================
def obtener_firebase_config():
    try:
        if "firebase" in st.secrets:
            cfg = dict(st.secrets["firebase"])
            if "private_key" in cfg:
                cfg["private_key"] = cfg["private_key"].replace("\\n", "\n").strip()
            return cfg
    except Exception:
        return None

    if os.path.exists(JSON_LOCAL):
        import json
        with open(JSON_LOCAL, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "private_key" in cfg:
            cfg["private_key"] = cfg["private_key"].replace("\\n", "\n").strip()
        return cfg

    return None


@st.cache_resource
def obtener_token_firestore():
    cfg = obtener_firebase_config()
    if not cfg:
        return None, None

    scopes = ["https://www.googleapis.com/auth/datastore"]
    creds = service_account.Credentials.from_service_account_info(cfg, scopes=scopes)
    creds.refresh(Request())
    return creds.token, cfg.get("project_id", "dif-hermosillo")


def firestore_headers():
    token, _ = obtener_token_firestore()
    if not token:
        return None
    return {"Authorization": f"Bearer {token}"}


def parse_firestore_value(value):
    if "stringValue" in value:
        return value["stringValue"]
    if "integerValue" in value:
        return int(value["integerValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "booleanValue" in value:
        return bool(value["booleanValue"])
    if "nullValue" in value:
        return None
    return ""


def parse_firestore_document(doc):
    fields = doc.get("fields", {})
    data = {k: parse_firestore_value(v) for k, v in fields.items()}
    data["id"] = doc.get("name", "").split("/")[-1]
    return data


def firestore_url(collection, doc_id=None):
    _, project_id = obtener_token_firestore()
    base = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/(default)/documents/{collection}"
    if doc_id:
        return f"{base}/{doc_id}"
    return base

# =====================================================
# DISEÑO
# =====================================================
st.markdown("""
<style>
.stApp {
    background:
        radial-gradient(circle at top left, rgba(8,123,117,0.25), transparent 30%),
        radial-gradient(circle at bottom right, rgba(233,78,27,0.35), transparent 34%),
        linear-gradient(135deg, #EEF8F5 0%, #FFF7E7 50%, #F8C2A5 100%);
}

.block-container { padding-top: 25px; }

.header-card {
    background: linear-gradient(135deg, rgba(219,246,241,0.96), rgba(255,242,216,0.96));
    padding: 25px;
    border-radius: 22px;
    box-shadow: 0px 8px 24px rgba(0,0,0,0.12);
    text-align: center;
    margin-bottom: 20px;
}

.header-card h1 { color: #087B75; font-weight: 900; }

.card {
    background: rgba(255,255,255,0.78);
    padding: 22px;
    border-radius: 18px;
    box-shadow: 0px 5px 15px rgba(0,0,0,0.09);
    border-left: 7px solid #087B75;
    margin-bottom: 18px;
}

.stButton > button {
    background: linear-gradient(90deg, #E94E1B, #F2B233);
    color: white;
    border: none;
    border-radius: 14px;
    padding: 12px;
    font-weight: 900;
    width: 100%;
}

.stDownloadButton > button {
    background: linear-gradient(90deg, #087B75, #14A39A);
    color: white;
    border: none;
    border-radius: 14px;
    padding: 12px;
    font-weight: 900;
    width: 100%;
}
</style>
""", unsafe_allow_html=True)

# =====================================================
# FIREBASE
# =====================================================
@st.cache_resource
def conectar_firebase():
    """
    Conexión a Firebase por REST.
    Esto evita que Firestore/grpc se quede cargando en Streamlit Cloud.
    """
    try:
        token, project_id = obtener_token_firestore()
        if token and project_id:
            return True
    except Exception as e:
        st.error(f"Error al conectar Firebase con Secrets: {e}")
        return None

    st.error(
        "No se encontró configuración de Firebase. "
        "En Streamlit Cloud agrega las credenciales en Secrets con la sección [firebase]."
    )
    return None


db = conectar_firebase()
if db is None:
    st.error("Firebase NO conectó. Revisa Secrets.")
else:
    st.success("Firebase conectado correctamente.")

geolocator = Nominatim(user_agent="sistema_trayectoria_dif_web")

# =====================================================
# FUNCIONES
# =====================================================
def get_coleccion():
    if db is None:
        return None
    return COLECCION


@st.cache_data(ttl=300, show_spinner=False)
def cargar_datos():
    columnas_base = ["id", "nombre", "nivel", "lat", "lon"]

    if db is None:
        return pd.DataFrame(columns=columnas_base)

    headers = firestore_headers()
    if not headers:
        st.error("No se pudo generar token de Firebase.")
        return pd.DataFrame(columns=columnas_base)

    try:
        params = {"pageSize": LIMITE_REGISTROS}
        r = requests.get(firestore_url(COLECCION), headers=headers, params=params, timeout=12)

        if r.status_code != 200:
            st.error(f"Error Firestore REST {r.status_code}: {r.text[:500]}")
            return pd.DataFrame(columns=columnas_base)

        documentos = r.json().get("documents", [])
        registros = [parse_firestore_document(doc) for doc in documentos]

    except Exception as e:
        st.error(f"Error al leer Firestore por REST: {e}")
        return pd.DataFrame(columns=columnas_base)

    if not registros:
        return pd.DataFrame(columns=columnas_base)

    df = pd.DataFrame(registros)

    for col in columnas_base:
        if col not in df.columns:
            df[col] = ""

    return df[columnas_base + [c for c in df.columns if c not in columnas_base]]


def firestore_fields(data):
    fields = {}
    for k, v in data.items():
        if isinstance(v, (int, float)):
            fields[k] = {"doubleValue": float(v)}
        else:
            fields[k] = {"stringValue": str(v)}
    return {"fields": fields}


def guardar_institucion(nombre, nivel, lat, lon):
    if db is None:
        return False, "No hay conexión a Firebase."
    if not nombre or not nivel:
        return False, "Nombre y nivel son obligatorios."

    headers = firestore_headers()
    if not headers:
        return False, "No se pudo generar token de Firebase."

    try:
        payload = firestore_fields({
            "nombre": nombre.upper().strip(),
            "nivel": nivel,
            "lat": float(lat),
            "lon": float(lon)
        })
        r = requests.post(firestore_url(COLECCION), headers=headers, json=payload, timeout=12)

        if r.status_code not in (200, 201):
            return False, f"Error al guardar en Firestore: {r.text[:500]}"

        st.cache_data.clear()
        return True, "Registro guardado correctamente."
    except Exception as e:
        return False, f"Error al guardar: {e}"


def actualizar_institucion(doc_id, nombre, nivel, lat, lon):
    if db is None:
        return False, "No hay conexión a Firebase."

    headers = firestore_headers()
    if not headers:
        return False, "No se pudo generar token de Firebase."

    try:
        payload = firestore_fields({
            "nombre": nombre.upper().strip(),
            "nivel": nivel,
            "lat": float(lat),
            "lon": float(lon)
        })
        params = {
            "updateMask.fieldPaths": ["nombre", "nivel", "lat", "lon"]
        }
        r = requests.patch(firestore_url(COLECCION, doc_id), headers=headers, json=payload, params=params, timeout=12)

        if r.status_code != 200:
            return False, f"Error al modificar en Firestore: {r.text[:500]}"

        st.cache_data.clear()
        return True, "Registro modificado correctamente."
    except Exception as e:
        return False, f"Error al modificar: {e}"


def eliminar_institucion(doc_id):
    if db is None:
        return False, "No hay conexión a Firebase."

    headers = firestore_headers()
    if not headers:
        return False, "No se pudo generar token de Firebase."

    try:
        r = requests.delete(firestore_url(COLECCION, doc_id), headers=headers, timeout=12)

        if r.status_code != 200:
            return False, f"Error al eliminar en Firestore: {r.text[:500]}"

        st.cache_data.clear()
        return True, "Registro eliminado correctamente."
    except Exception as e:
        return False, f"Error al eliminar: {e}"


def geocodificar(nombre):
    try:
        loc = geolocator.geocode(f"{nombre}, Hermosillo, Sonora, México", timeout=10)
        if loc:
            return loc.latitude, loc.longitude
    except Exception:
        pass
    return None, None




def crear_mapa(df):
    mapa = folium.Map(location=[29.0892, -110.9613], zoom_start=12)

    if df.empty:
        return mapa

    puntos_validos = []

    for _, row in df.iterrows():
        try:
            lat = float(row.get("lat", ""))
            lon = float(row.get("lon", ""))
            puntos_validos.append([lat, lon])
        except Exception:
            continue

        nivel = row.get("nivel", "PRIMARIA")
        nombre = row.get("nombre", "")
        conf = CONFIG_NIVELES.get(nivel, CONFIG_NIVELES["PRIMARIA"])

        folium.Marker(
            [lat, lon],
            popup=f"<b>{nombre}</b><br>{nivel}",
            tooltip=nombre,
            icon=folium.Icon(color=conf["color"], icon=conf["icon"], prefix="fa")
        ).add_to(mapa)

        etiqueta = (
            f'<div style="font-size:10pt;color:{conf["color"]};'
            f'font-weight:bold;background:white;padding:2px 5px;'
            f'border:1px solid gray;border-radius:4px;display:inline-block;">'
            f'{nombre}</div>'
        )

        folium.map.Marker(
            [lat, lon],
            icon=folium.DivIcon(
                icon_size=(180, 36),
                html=etiqueta
            )
        ).add_to(mapa)

    if puntos_validos:
        mapa.fit_bounds(puntos_validos, padding=(30, 30))

    return mapa


def mapa_html_bytes(df):
    mapa = crear_mapa(df)
    html = mapa.get_root().render()
    return html.encode("utf-8")


def texto_limpio(txt):
    if txt is None:
        return ""
    txt = str(txt)
    reemplazos = {
        "Á": "A", "É": "E", "Í": "I", "Ó": "O", "Ú": "U", "Ñ": "N",
        "á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u", "ñ": "n"
    }
    for a, b in reemplazos.items():
        txt = txt.replace(a, b)
    return txt



def crear_pdf(df):
    output = BytesIO()
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Arial", "B", 16)
    pdf.cell(190, 10, texto_limpio("REPORTE EJECUTIVO - ESCUELAS VISITADAS DIF"), ln=True, align="C")
    pdf.set_font("Arial", "", 10)
    pdf.cell(190, 8, f"Fecha de generacion: {datetime.now().strftime('%d/%m/%Y %H:%M')}", ln=True, align="C")
    pdf.ln(6)

    total = len(df)
    resumen = df["nivel"].value_counts().to_dict() if not df.empty else {}

    pdf.set_font("Arial", "B", 12)
    pdf.cell(190, 8, "Resumen general", ln=True)
    pdf.set_font("Arial", "", 10)
    pdf.cell(190, 7, f"Total de instituciones registradas: {total}", ln=True)

    for nivel, cantidad in resumen.items():
        pdf.cell(190, 7, texto_limpio(f"{nivel}: {cantidad}"), ln=True)

    pdf.ln(5)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(190, 8, "Listado de instituciones", ln=True)

    pdf.set_font("Arial", "B", 8)
    pdf.cell(90, 8, "NOMBRE", 1)
    pdf.cell(45, 8, "NIVEL", 1)
    pdf.cell(55, 8, "COORDENADAS", 1, ln=True)

    pdf.set_font("Arial", "", 7)
    for _, row in df.iterrows():
        nombre = texto_limpio(str(row.get("nombre", ""))[:45])
        nivel = texto_limpio(str(row.get("nivel", ""))[:20])
        coords = texto_limpio(f'{row.get("lat","")}, {row.get("lon","")}'[:25])

        pdf.cell(90, 7, nombre, 1)
        pdf.cell(45, 7, nivel, 1)
        pdf.cell(55, 7, coords, 1, ln=True)

    output.write(pdf.output(dest="S").encode("latin-1", errors="ignore"))
    output.seek(0)
    return output


def crear_excel(df):
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    return output

# =====================================================
# ENCABEZADO
# =====================================================
st.markdown("""
<div class="header-card">
<h1>📍 Gestor Académico y Social</h1>
<p>Sistema web de trayectoria académica, organizaciones y mapa institucional</p>
</div>
""", unsafe_allow_html=True)

menu = st.sidebar.radio(
    "Menú",
    ["🏠 Inicio", "➕ Registrar", "🗺️ Mapa", "📊 Dashboard", "⚙️ Administración", "📄 Reportes"]
)

st.caption(f"Mostrando máximo {LIMITE_REGISTROS} registros de Firebase.")

with st.spinner("Cargando datos de Firebase..."):
    df = cargar_datos()

# =====================================================
# INICIO
# =====================================================
if menu == "🏠 Inicio":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Resumen general")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total registros", len(df))
    c2.metric("Primarias", len(df[df["nivel"] == "PRIMARIA"]) if not df.empty else 0)
    c3.metric("Universidades", len(df[df["nivel"] == "UNIVERSIDAD"]) if not df.empty else 0)
    c4.metric("Fundaciones / Org", len(df[df["nivel"] == "FUNDACIÓN O ORG"]) if not df.empty else 0)
    st.markdown("</div>", unsafe_allow_html=True)

    st.subheader("Últimos registros")
    st.dataframe(df.tail(20), use_container_width=True)

# =====================================================
# REGISTRAR
# =====================================================
elif menu == "➕ Registrar":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Registrar institución u organización")

    with st.form("form_registro"):
        col1, col2 = st.columns(2)
        with col1:
            nombre = st.text_input("Nombre de la institución / organización")
            nivel = st.selectbox("Nivel / Tipo", list(CONFIG_NIVELES.keys()))
        with col2:
            modo = st.radio("Modo de ubicación", ["Automático", "Manual"], horizontal=True)
            lat = st.text_input("Latitud", value="29.08", disabled=(modo == "Automático"))
            lon = st.text_input("Longitud", value="-110.96", disabled=(modo == "Automático"))
        guardar = st.form_submit_button("💾 Guardar")

    if guardar:
        if modo == "Automático":
            lat, lon = geocodificar(nombre)
            if not lat or not lon:
                st.error("No se encontró ubicación automática. Intenta con modo Manual.")
            else:
                ok, msg = guardar_institucion(nombre, nivel, lat, lon)
                st.success(msg) if ok else st.error(msg)
        else:
            ok, msg = guardar_institucion(nombre, nivel, lat, lon)
            st.success(msg) if ok else st.error(msg)

    st.markdown("</div>", unsafe_allow_html=True)

    st.link_button("🤝 Buscar fundaciones en Google Maps", "https://www.google.com/maps/search/fundaciones+en+Hermosillo")

# =====================================================
# MAPA
# =====================================================
elif menu == "🗺️ Mapa":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Mapa general")
    if df.empty:
        st.warning("Todavía no hay registros.")
    else:
        niveles = ["Todos"] + list(CONFIG_NIVELES.keys())
        nivel_sel = st.selectbox("Filtrar por nivel", niveles)
        df_mapa = df.copy()
        if nivel_sel != "Todos":
            df_mapa = df_mapa[df_mapa["nivel"] == nivel_sel]
        st_folium(crear_mapa(df_mapa), width=None, height=650)

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "🗺️ Descargar mapa interactivo HTML",
                data=mapa_html_bytes(df_mapa),
                file_name="Mapa_Escuelas_Visitadas_DIF.html",
                mime="text/html"
            )
        with col2:
            st.info("Para presentación: abre el HTML descargado, pon pantalla completa y toma captura con Windows + Shift + S.")
    st.markdown("</div>", unsafe_allow_html=True)

# =====================================================
# DASHBOARD
# =====================================================
elif menu == "📊 Dashboard":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Estadísticas")
    if df.empty:
        st.warning("Todavía no hay registros.")
    else:
        resumen = df["nivel"].value_counts().reset_index()
        resumen.columns = ["Nivel", "Total"]
        st.bar_chart(resumen.set_index("Nivel"))
        st.dataframe(resumen, use_container_width=True)
    st.markdown("</div>", unsafe_allow_html=True)

# =====================================================
# ADMINISTRACIÓN
# =====================================================
elif menu == "⚙️ Administración":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Modificar o eliminar registros")

    if df.empty:
        st.warning("Todavía no hay registros.")
    else:
        busqueda = st.text_input("Buscar por nombre o categoría")
        df_admin = df.copy()
        if busqueda:
            b = busqueda.upper()
            df_admin = df_admin[
                df_admin["nombre"].astype(str).str.upper().str.contains(b, na=False) |
                df_admin["nivel"].astype(str).str.upper().str.contains(b, na=False)
            ]

        st.dataframe(df_admin[["id", "nombre", "nivel", "lat", "lon"]], use_container_width=True)

        if not df_admin.empty:
            ids = df_admin["id"].tolist()
            id_sel = st.selectbox("Selecciona ID", ids)
            registro = df[df["id"] == id_sel].iloc[0]

            with st.form("form_editar"):
                nombre_edit = st.text_input("Nombre", value=registro.get("nombre", ""))
                niveles_lista = list(CONFIG_NIVELES.keys())
                nivel_actual = registro.get("nivel", "PRIMARIA")
                idx = niveles_lista.index(nivel_actual) if nivel_actual in niveles_lista else 1
                nivel_edit = st.selectbox("Nivel", niveles_lista, index=idx)
                lat_edit = st.text_input("Latitud", value=str(registro.get("lat", "")))
                lon_edit = st.text_input("Longitud", value=str(registro.get("lon", "")))
                col1, col2 = st.columns(2)
                modificar = col1.form_submit_button("✏️ Modificar")
                eliminar = col2.form_submit_button("🗑️ Eliminar")

            if modificar:
                ok, msg = actualizar_institucion(id_sel, nombre_edit, nivel_edit, lat_edit, lon_edit)
                st.success(msg) if ok else st.error(msg)
            if eliminar:
                ok, msg = eliminar_institucion(id_sel)
                st.success(msg) if ok else st.error(msg)

    st.markdown("</div>", unsafe_allow_html=True)

# =====================================================
# REPORTES
# =====================================================
elif menu == "📄 Reportes":
    st.markdown('<div class="card">', unsafe_allow_html=True)
    st.subheader("Reportes ejecutivos")

    if df.empty:
        st.warning("Todavía no hay registros.")
    else:
        st.write("Vista previa de registros:")
        st.dataframe(df, use_container_width=True)

        st.markdown("### Descargas")
        col1, col2, col3 = st.columns(3)

        with col1:
            st.download_button(
                "📥 Descargar Excel",
                data=crear_excel(df),
                file_name="reporte_trayectoria_academica_social.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        with col2:
            st.download_button(
                "📄 Descargar PDF Ejecutivo",
                data=crear_pdf(df),
                file_name="Reporte_Ejecutivo_DIF.pdf",
                mime="application/pdf"
            )

        with col3:
            st.download_button(
                "🗺️ Descargar Mapa HTML",
                data=mapa_html_bytes(df),
                file_name="Mapa_Escuelas_Visitadas_DIF.html",
                mime="text/html"
            )

        st.info("Para meter el mapa en PowerPoint: descarga el Mapa HTML, ábrelo, ponlo en pantalla completa y toma captura con Windows + Shift + S.")

    st.markdown("</div>", unsafe_allow_html=True)
