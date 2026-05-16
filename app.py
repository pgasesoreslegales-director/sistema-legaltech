import streamlit as st
import sqlite3
import os
import pandas as pd
from datetime import datetime
import PyPDF2
import google.generativeai as genai
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import io

# ==========================================
# 1. CONFIGURACIÓN DE IA Y CREDENCIALES
# ==========================================
st.set_page_config(page_title="LegalTech | Asesores Palacios", layout="wide", page_icon="⚖️")
st.title("🏛️ Sistema de Gestión Procesal en la Nube")

try:
    MI_API_KEY = st.secrets["GEMINI_API_KEY"]
    genai.configure(api_key=MI_API_KEY)
except Exception as e:
    st.error("⚠️ Falta configurar GEMINI_API_KEY en Streamlit Secrets.")
    st.stop()

@st.cache_resource
def get_drive_service():
    try:
        cred_info = json.loads(st.secrets["GOOGLE_CREDENTIALS"])
        creds = service_account.Credentials.from_service_account_info(
            cred_info, scopes=['https://www.googleapis.com/auth/drive']
        )
        return build('drive', 'v3', credentials=creds)
    except Exception as e:
        st.error("⚠️ Error con las credenciales de Google. Revisa los Secrets.")
        st.stop()

drive_service = get_drive_service()

# ==========================================
# 2. CONEXIÓN AUTÓNOMA CON GOOGLE DRIVE
# ==========================================
DB_NAME = "legaltech_db.sqlite"

def get_folder_id(folder_name, parent_id=None):
    q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    results = drive_service.files().list(q=q, fields="files(id)").execute()
    items = results.get('files', [])
    return items[0]['id'] if items else None

def create_folder(folder_name, parent_id=None):
    file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_id: file_metadata['parents'] = [parent_id]
    folder = drive_service.files().create(body=file_metadata, fields='id').execute()
    return folder.get('id')

ROOT_FOLDER_ID = get_folder_id("LegalTech_Asesores")
if not ROOT_FOLDER_ID:
    st.error("⚠️ No se encontró la carpeta 'LegalTech_Asesores' en Drive. ¿Seguro que la compartiste con el robot?")
    st.stop()

def sync_db_down():
    results = drive_service.files().list(q=f"name='{DB_NAME}' and '{ROOT_FOLDER_ID}' in parents and trashed=false", fields="files(id)").execute()
    items = results.get('files', [])
    if items:
        request = drive_service.files().get_media(fileId=items[0]['id'])
        fh = io.FileIO(DB_NAME, 'wb')
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
    else:
        con = sqlite3.connect(DB_NAME)
        con.execute('CREATE TABLE IF NOT EXISTS Casos (id_caso INTEGER PRIMARY KEY AUTOINCREMENT, nomenclatura TEXT UNIQUE, partes TEXT, tribunal TEXT, materia TEXT, fecha_inicio DATE, estado_actual TEXT)')
        con.execute('CREATE TABLE IF NOT EXISTS Actuaciones (id_actuacion INTEGER PRIMARY KEY AUTOINCREMENT, id_caso INTEGER, fecha_actuacion DATE, tipo_actuacion TEXT, descripcion TEXT, ruta_archivo_pdf TEXT, proximo_paso_estimado TEXT)')
        con.commit()
        con.close()
        sync_db_up()

def sync_db_up():
    results = drive_service.files().list(q=f"name='{DB_NAME}' and '{ROOT_FOLDER_ID}' in parents and trashed=false", fields="files(id)").execute()
    items = results.get('files', [])
    media = MediaFileUpload(DB_NAME, mimetype='application/x-sqlite3', resumable=True)
    if items:
        drive_service.files().update(fileId=items[0]['id'], media_body=media).execute()
    else:
        file_metadata = {'name': DB_NAME, 'parents': [ROOT_FOLDER_ID]}
        drive_service.files().create(body=file_metadata, media_body=media).execute()

if not os.path.exists(DB_NAME):
    sync_db_down()

def conectar_db():
    return sqlite3.connect(DB_NAME)

def subir_pdf_drive(archivo_pdf, nombre_carpeta):
    caso_folder_id = get_folder_id(nombre_carpeta, ROOT_FOLDER_ID)
    if not caso_folder_id:
        caso_folder_id = create_folder(nombre_carpeta, ROOT_FOLDER_ID)
    temp_path = archivo_pdf.name
    with open(temp_path, "wb") as f: f.write(archivo_pdf.getbuffer())
    file_metadata = {'name': archivo_pdf.name, 'parents': [caso_folder_id]}
    media = MediaFileUpload(temp_path, mimetype='application/pdf', resumable=True)
    archivo_subido = drive_service.files().create(body=file_metadata, media_body=media, fields='id, webViewLink').execute()
    os.remove(temp_path)
    return archivo_subido.get('webViewLink')

# ==========================================
# 3. MOTORES DE INTELIGENCIA ARTIFICIAL
# ==========================================
def obtener_modelo_ia():
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods and 'gemini' in m.name:
            return m.name.replace("models/", "")
    return None

def analizar_pdf_con_gemini(archivo_pdf):
    texto = ""
    try:
        lector = PyPDF2.PdfReader(archivo_pdf)
        for pag in range(len(lector.pages)): texto += lector.pages[pag].extract_text()
        modelo_valido = obtener_modelo_ia()
        if not modelo_valido: return "Error: IA no disponible."
        modelo = genai.GenerativeModel(modelo_valido)
        instrucciones = "Eres un abogado procesalista venezolano. Lee el texto y extrae: 1. Actuación. 2. Próximo paso procesal (CPC). 3. Lapsos. Responde en un párrafo técnico."
        return modelo.generate_content(instrucciones + "\\n\\nTEXTO:\\n" + texto).text
    except Exception as e:
        return f"Error leyendo PDF: {e}"

def redactar_libelo_ia(hechos_caso):
    modelo_valido = obtener_modelo_ia()
    if not modelo_valido: return "Error: IA no disponible."
    modelo = genai.GenerativeModel(modelo_valido)
    prompt_maestro = '''
    PROMPT MAESTRO DE REDACCIÓN JURÍDICA:
    Eres el "Gem Redactor Procesal", un Abogado Litigante Venezolano experto.
    1. Prohibido generar datos falsos (nombres, cédulas, etc).
    2. Usa marcadores como [NOMBRE DEL DEMANDADO] si falta información.
    3. Ajuste estricto al Art. 340 CPC venezolano.
    4. Estructura: Encabezado, CAPÍTULO I: HECHOS, CAPÍTULO II: DERECHO, CAPÍTULO III: PETITORIO.
    '''
    return modelo.generate_content(prompt_maestro + "\\n\\nHECHOS:\\n" + hechos_caso).text

# ==========================================
# 4. LÓGICA DE GUARDADO AUTOMATIZADO
# ==========================================
def registrar_caso_inteligente(nomenclatura, partes, tribunal, materia, tipo_actuacion, archivo_pdf):
    sync_db_down() 
    conexion = conectar_db()
    cursor = conexion.cursor()
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    try:
        analisis_ia = "Sin análisis."
        link_drive = ""
        if archivo_pdf is not None:
            analisis_ia = analizar_pdf_con_gemini(archivo_pdf)
            archivo_pdf.seek(0)
            link_drive = subir_pdf_drive(archivo_pdf, nomenclatura.replace("/", "-"))

        cursor.execute("INSERT OR IGNORE INTO Casos (nomenclatura, partes, tribunal, materia, fecha_inicio, estado_actual) VALUES (?, ?, ?, ?, ?, ?)", (nomenclatura, partes, tribunal, materia, fecha_hoy, 'Activo'))
        cursor.execute("SELECT id_caso FROM Casos WHERE nomenclatura = ?", (nomenclatura,))
        id_caso = cursor.fetchone()[0]
        
        cursor.execute('''
            INSERT INTO Actuaciones (id_caso, fecha_actuacion, tipo_actuacion, descripcion, ruta_archivo_pdf, proximo_paso_estimado)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (id_caso, fecha_hoy, tipo_actuacion, "Carga Inteligente", link_drive, analisis_ia))
        
        conexion.commit()
        conexion.close()
        sync_db_up() 
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False

# ==========================================
# 5. INTERFAZ GRÁFICA (FRONTEND)
# ==========================================
tab1, tab2, tab3 = st.tabs(["🤖 Lector IA", "📊 Memoria Procesal", "✍️ Asistente de Libelos"])

with tab1:
    st.markdown("### Digitalización y Análisis de Expedientes")
    with st.form("form_inteligente", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            nomenclatura = st.text_input("Nomenclatura (Ej. AP-2026-001) *")
            partes = st.text_input("Partes *")
            tribunal = st.selectbox("Tribunal", ["Municipio", "1ra Instancia Civil", "Superior"])
            materia = st.selectbox("Materia", ["Civil Ordinario", "Arrendamiento", "Mercantil", "Laboral"])
        with col2:
            tipo_actuacion = st.selectbox("Documento", ["Auto de Admisión", "Boleta de Citación", "Contestación", "Diligencia"])
            archivo_pdf = st.file_uploader("Adjuntar PDF (Extracción de Lapsos)", type=['pdf'])
            
        if st.form_submit_button("🧠 Procesar y Guardar en Nube"):
            if nomenclatura and partes and archivo_pdf:
                with st.spinner('Analizando parámetros procesales y sincronizando con Google Drive...'):
                    if registrar_caso_inteligente(nomenclatura, partes, tribunal, materia, tipo_actuacion, archivo_pdf):
                        st.success("✅ Expediente resguardado. PDF subido a Drive. Lapsos extraídos.")
            else:
                st.warning("⚠️ Llene los campos obligatorios y adjunte el documento.")

with tab2:
    st.markdown("### Control Cronológico de la Firma")
    st.caption("Presione 'Actualizar' al iniciar para leer los últimos cambios de Drive.")
    if st.button("🔄 Actualizar Base de Datos"):
        sync_db_down()
    try:
        conexion = conectar_db()
        query = "SELECT c.nomenclatura, a.fecha_actuacion, a.tipo_actuacion, a.proximo_paso_estimado, a.ruta_archivo_pdf AS enlace_drive FROM Actuaciones a JOIN Casos c ON a.id_caso = c.id_caso ORDER BY a.id_actuacion DESC"
        df = pd.read_sql_query(query, conexion)
        if not df.empty:
            st.dataframe(df, use_container_width=True, column_config={"enlace_drive": st.column_config.LinkColumn("Enlace al PDF (Drive)")})
        else:
            st.info("Bóveda vacía.")
        conexion.close()
    except Exception as e:
        st.error(f"Error al leer datos: {e}")

with tab3:
    st.markdown("### Generador de Libelos (Art. 340 CPC)")
    hechos_input = st.text_area("Describa los hechos de la causa:", height=150)
    if st.button("⚖️ Redactar Proyecto"):
        if hechos_input:
            with st.spinner('Ensamblando estructura jurídica...'):
                borrador = redactar_libelo_ia(hechos_input)
                st.text_area("Copia el texto para Word:", borrador, height=400)
                st.success("✅ Redacción completada.")
