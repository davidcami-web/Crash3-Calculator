import streamlit as st
import pandas as pd
import json
import re
import PyPDF2
import io
import requests

# ==========================================
# LÓGICA DE FÍSICA Y MATEMÁTICAS (CRASH3)
# ==========================================
class StiffnessCalculator:
    """Clase para calcular los coeficientes de rigidez A, B y G usando el algoritmo CRASH3."""
    
    def __init__(self, system='US', impact_type='Frontal/Trasero'):
        self.system = system
        self.impact_type = impact_type
        # Gravedad en in/sec^2
        self.g_us = 386.4 

    def get_b0(self):
        """Devuelve b0 (velocidad sin deformación permanente) según el tipo de impacto."""
        if self.impact_type == 'Lateral':
            return {'US': 2.0, 'SI': 3.22}[self.system]  # 2 mph o 3.22 km/h
        else:
            return {'US': 5.0, 'SI': 8.05}[self.system]  # 5 mph o 8.05 km/h

    def calculate(self, weight, v_imp, dv, l_test, crush_profile, velocity_source='Delta V'):
        """Ejecuta los cálculos secuenciales de CRASH3."""
        # Selección de la velocidad a utilizar para el cálculo de b1 según el investigador
        v_test = dv if velocity_source == 'Delta V' else v_imp

        # 1. Velocidad sin daño (b0)
        b0_raw = self.get_b0()
        
        # Área de deformación (Regla Trapezoidal de 6 puntos)
        c1, c2, c3, c4, c5, c6 = crush_profile
        area = (l_test / 5.0) * ((c1 / 2) + c2 + c3 + c4 + c5 + (c6 / 2))
        
        # 2. Deformación Media (C_avg)
        c_avg = area / l_test if l_test > 0 else 0.001
        
        # Conversión de unidades para la matemática interna
        if self.system == 'US':
            # US: v en mph -> in/s
            b0 = b0_raw * 17.6
            v_test_in = v_test * 17.6
            
            # 3. Pendiente b1 (1/s)
            b1 = (v_test_in - b0) / c_avg if c_avg > 0 else 0
            b1 = max(0, b1) # Evitar pendientes negativas
            
            # 4 y 5. Coeficientes A y B
            A = (weight * b0 * b1) / (self.g_us * l_test)  # lb/in
            B = (weight * (b1**2)) / (self.g_us * l_test)  # lb/in^2
            
        else:
            # SI: v en km/h -> m/s, c_avg y l_test en cm -> m
            b0 = (b0_raw * 1000) / 3600
            v_test_m = (v_test * 1000) / 3600
            c_avg_m = c_avg / 100.0
            l_test_m = l_test / 100.0
            
            # 3. Pendiente b1 (1/s)
            b1 = (v_test_m - b0) / c_avg_m if c_avg_m > 0 else 0
            b1 = max(0, b1)
            
            # 4 y 5. Coeficientes A y B internos (N/m, N/m^2)
            A_m = (weight * b0 * b1) / l_test_m
            B_m = (weight * (b1**2)) / l_test_m
            
            # Convertir a formato de presentación estándar SI (N/cm, N/cm^2)
            A = A_m / 100.0
            B = B_m / 10000.0

        # 6. Coeficiente G
        G = (A**2) / (2 * B) if B > 0 else 0.0

        return {
            'b0_raw': b0_raw, 'c_avg': c_avg, 'b1': b1,
            'A': A, 'B': B, 'G': G
        }

# ==========================================
# PARSER DE DATOS (NHTSA METADATA)
# ==========================================
def extract_metadata(file, ext):
    """Extrae la metadata estructurada o por expresiones regulares de archivos NHTSA."""
    text = ""
    data = {}
    
    # Lectura del archivo
    if ext == 'json':
        try:
            json_data = json.load(file)
            text = json.dumps(json_data) # Convertir a texto para el regex fallback
            # Búsqueda directa en JSON (nombres comunes de la NHTSA)
            data['Peso (WT/mt)'] = json_data.get('VEHTWT', json_data.get('test_weight', None))
            data['Vel. Impacto (v)'] = json_data.get('CLSSPD', json_data.get('impact_velocity', None))
            data['Cambio Vel. (dV)'] = json_data.get('velocity_change', json_data.get('delta_v', None))
            data['Ancho Zona (L_test)'] = json_data.get('damage_length', None)
        except:
            st.error("Error al procesar el archivo JSON.")
    elif ext == 'pdf':
        try:
            reader = PyPDF2.PdfReader(file)
            for page in reader.pages:
                text += page.extract_text() + "\n"
        except:
            st.error("Error al procesar el archivo PDF.")
    else:
        text = file.read().decode('utf-8')

    # Diccionario de expresiones regulares para fallback en texto plano
    regex_patterns = {
        'Peso (WT/mt)': r'(?i)(?:test weight|VEHTWT|peso).*?[:=]?\s*([\d\.]+)',
        'Vel. Impacto (v)': r'(?i)(?:impact velocity|CLSSPD|velocidad de impacto).*?[:=]?\s*([\d\.]+)',
        'Cambio Vel. (dV)': r'(?i)(?:velocity change|delta[_\-\s]?v).*?[:=]?\s*([\d\.]+)',
        'Ancho Zona (L_test)': r'(?i)(?:damage region length|contact width|ancho de daño|LENCNT).*?[:=]?\s*([\d\.]+)',
        'C1': r'(?i)(?:C1|DPD1).*?[:=]?\s*([\d\.]+)',
        'C2': r'(?i)(?:C2|DPD2).*?[:=]?\s*([\d\.]+)',
        'C3': r'(?i)(?:C3|DPD3).*?[:=]?\s*([\d\.]+)',
        'C4': r'(?i)(?:C4|DPD4).*?[:=]?\s*([\d\.]+)',
        'C5': r'(?i)(?:C5|DPD5).*?[:=]?\s*([\d\.]+)',
        'C6': r'(?i)(?:C6|DPD6).*?[:=]?\s*([\d\.]+)',
    }

    # Búsqueda mediante REGEX para completar valores vacíos
    for key, pattern in regex_patterns.items():
        if data.get(key) is None:
            match = re.search(pattern, text)
            data[key] = float(match.group(1)) if match else 0.0

    # Lógica de aproximación del dV si no se proporcionó (Restitución de 0.1)
    if not data.get('Cambio Vel. (dV)') and data.get('Vel. Impacto (v)'):
        data['Cambio Vel. (dV)'] = data['Vel. Impacto (v)'] * 1.1

    return data, text

# ==========================================
# INTERFAZ DE USUARIO (STREAMLIT)
# ==========================================
st.set_page_config(page_title="Cálculo Coeficientes Rigidez", layout="wide")

st.title("🚗 App Cálculo Coeficientes Rigidez")
st.markdown("Extrae metadata de la NHTSA y calcula automáticamente los coeficientes **A, B y G**.")

# --- BARRA LATERAL (SIDEBAR) ---
with st.sidebar:
    st.header("1. Configuración del Test")
    
    data_source = st.radio("Origen de datos", ["NHTSA Online (Auto)", "Subir Archivo Manual"])
    
    uploaded_file = None
    test_id = ""
    
    if data_source == "Subir Archivo Manual":
        uploaded_file = st.file_uploader("Sube el metadata de la NHTSA", type=['json', 'pdf', 'txt'])
    else:
        test_id = st.text_input("NHTSA Test ID (Ej. 1234):", help="Descargará los datos automáticamente de la base de datos oficial de la NHTSA")
    
    system = st.selectbox("Sistema de Unidades", ['SI', 'US'], 
                          help="SI: kg, km/h, cm | US: lb, mph, in")
                          
    is_mm = False
    if system == 'SI':
        is_mm = st.checkbox("Medidas de deformación (L, C1-C6) en mm", value=True, help="Si marcas esta opción, en la interfaz se indicará en mm y se convertirá a cm internamente.")
    
    impact_type = st.selectbox("Tipo de Impacto", ['Frontal/Trasero', 'Lateral'],
                               help="Modifica el b0 por defecto (Frontal: 5mph / Lat: 2mph)")
                               
    velocity_source = st.radio("Velocidad para cálculo de b1:", ["Delta V", "Velocidad de Impacto"],
                               help="Distintas corrientes investigadoras difieren en el criterio. Por defecto CRASH usa el diferencial.")
    
    st.markdown("---")
    st.markdown("**Desarrollado para validación de datos CRASH3.**")

# --- ÁREA PRINCIPAL ---
raw_data = None
raw_text = ""

if data_source == "NHTSA Online (Auto)" and test_id:
    api_url = f"https://nrd.api.nhtsa.dot.gov/nhtsa/vehicle/api/v1/vehicle-database-test-results/metadata/{test_id.strip()}"
    with st.spinner("Descargando metadata desde NHTSA..."):
        try:
            response = requests.get(api_url, timeout=15)
            if response.status_code == 200:
                json_data = response.json()
                json_bytes = io.BytesIO(json.dumps(json_data).encode('utf-8'))
                json_bytes.name = 'auto.json'
                raw_data, raw_text = extract_metadata(json_bytes, 'json')
            elif response.status_code == 404:
                st.error("Test no encontrado. Comprueba el ID proporcionado.")
            else:
                st.error(f"Error al conectar con la NHTSA. Código HTTP: {response.status_code}")
        except Exception as e:
            st.error(f"Error de conexión con el servidor de NHTSA: {e}")

elif data_source == "Subir Archivo Manual" and uploaded_file is not None:
    # Parsing manual
    ext = uploaded_file.name.split('.')[-1].lower()
    raw_data, raw_text = extract_metadata(uploaded_file, ext)

if raw_data is not None:
    
    st.subheader("2. Validación de Datos Extraídos")
    st.info("Revisa y corrige los datos extraídos por el parser en la tabla inferior antes de calcular.")
    
    # Unidades para mostrar en la interfaz
    units_dict = {
        'Peso (WT/mt)': 'lb' if system == 'US' else 'kg',
        'Vel. Impacto (v)': 'mph' if system == 'US' else 'km/h',
        'Cambio Vel. (dV)': 'mph' if system == 'US' else 'km/h',
        'Ancho Zona (L_test)': 'in' if system == 'US' else ('mm' if is_mm else 'cm'),
    }
    for i in range(1, 7):
        units_dict[f'C{i}'] = 'in' if system == 'US' else ('mm' if is_mm else 'cm')
        
    # Diccionario para mapear del nombre mostrado al nombre original
    display_to_original = {f"{k} [{units_dict.get(k, '')}]": k for k in raw_data.keys()}
    
    # Preparar el DataFrame para la tabla editable
    df_params = pd.DataFrame({
        'Parámetro': list(display_to_original.keys()),
        'Valor (Base de Datos NHTSA)': list(raw_data.values())
    })
    
    # Mostrar tabla editable para validación manual
    edited_df = st.data_editor(df_params, use_container_width=True, hide_index=True)
    
    # Reconstruir diccionario validado
    valid_data = {}
    for _, row in edited_df.iterrows():
        orig_key = display_to_original[row['Parámetro']]
        val = pd.to_numeric(row['Valor (Base de Datos NHTSA)'], errors='coerce')
        valid_data[orig_key] = 0.0 if pd.isna(val) else float(val)
    
    # 2. Ejecutar Cálculo
    if st.button("🚀 Calcular Coeficientes A, B y G", type="primary"):
        # Extraer variables validadas
        wt = valid_data.get('Peso (WT/mt)', 0)
        v = valid_data.get('Vel. Impacto (v)', 0)
        dv = valid_data.get('Cambio Vel. (dV)', 0)
        l_test = valid_data.get('Ancho Zona (L_test)', 0)
        c_prof =[valid_data.get(f'C{i}', 0) for i in range(1, 7)]
        
        if system == 'SI' and is_mm:
            l_test = l_test / 10.0
            c_prof = [c / 10.0 for c in c_prof]
            
        if wt == 0 or l_test == 0:
            st.error("Error: El Peso y el Ancho de la zona (L_test) deben ser mayores a 0 para el cálculo.")
        else:
            # Instanciar el calculador
            calc = StiffnessCalculator(system=system, impact_type=impact_type)
            res = calc.calculate(wt, v, dv, l_test, c_prof, velocity_source=velocity_source)
            
            # Definir strings de unidades
            units = {
                'v': 'mph' if system == 'US' else 'km/h',
                'l': 'in' if system == 'US' else 'cm',
                'A': 'lb/in' if system == 'US' else 'N/cm',
                'B': 'lb/in²' if system == 'US' else 'N/cm²',
                'G': 'lb' if system == 'US' else 'N'
            }
            
            st.markdown("---")
            st.subheader("3. Resultados Finales CRASH3")
            
            # Revisión del Tipo de Test y Metadata Adicional del Vehículo
            issue_msgs = []
            
            # --- Extracción de información vehículo ---
            make = re.search(r'(?i)(?:(?:\"?MAKED\"?|\"?MAKE\"?)\s*(?:[:=]?)\s*\"([^\"]+)\"|MAKED\s*(?:[:=]?)\s*([^,\n\{]+))', raw_text)
            model = re.search(r'(?i)(?:(?:\"?MODELD\"?|\"?MODEL\"?)\s*(?:[:=]?)\s*\"([^\"]+)\"|MODELD\s*(?:[:=]?)\s*([^,\n\{]+))', raw_text)
            year = re.search(r'(?i)(?:\"?YEAR\"?)\s*(?:[:=]?)\s*\"?(\d{4})\"?', raw_text)
            cdc = re.search(r'(?i)(?:(?:\"?CDCD\"?|\"?CDC\"?|\"?VDI\"?)\s*(?:[:=]?)\s*\"([^\"]+)\"|(?:CDCD|CDC|VDI)\s*(?:[:=]?)\s*([^,\n\{]+))', raw_text)
            
            veh_info = []
            if make: veh_info.append(f"**Marca:** {(make.group(1) or make.group(2)).strip().title()}")
            if model: veh_info.append(f"**Modelo:** {(model.group(1) or model.group(2)).strip().title()}")
            if year: veh_info.append(f"**Año:** {year.group(1).strip()}")
            if cdc: veh_info.append(f"**CDC/VDI:** {(cdc.group(1) or cdc.group(2)).strip()}")
            
            # --- Alertas de Configuración de Test ---
            # Busqueda de configuración en texto puro con comillas o sin ellas
            cfg_match = re.search(r'(?i)(?:\"?TSTCFND\"?|\"?TEST[\s_]CONFIGURATION\"?)\s*(?:[:=]?)\s*\"([^\"]+)\"', raw_text)
            if not cfg_match:
                cfg_match = re.search(r'(?i)(?:TSTCFND|test configuration)\s*(?:[:=]?)\s*([^,\n\{]+)', raw_text)
            
            ang_match = re.search(r'(?i)(?:\"?IMPANG\"?|\"?IMPACT[\s_]ANGLE\"?)\s*(?:[:=]?)\s*\"?(\d+)\"?', raw_text)
            
            if cfg_match:
                cfg = cfg_match.group(1).upper().strip()
                veh_info.append(f"**Impacto contra:** {cfg.title()}")
                if 'BARRIER' not in cfg or 'POLE' in cfg or 'OFFSET' in cfg or 'MOVING' in cfg or 'MDB' in cfg or 'TO VEHICLE' in cfg:
                    issue_msgs.append(f"Configuración detectada: **{cfg}** (No estándar CRASH frontal puro).")
                    
            if ang_match:
                ang = int(ang_match.group(1))
                if impact_type == 'Frontal/Trasero' and ang not in [0, 360, 180]:
                    issue_msgs.append(f"Ángulo de impacto principal: **{ang}º** (difícil cuadrar con frontal/trasero puro).")
                elif impact_type == 'Lateral' and ang not in [90, 270]:
                    issue_msgs.append(f"Ángulo de impacto lateral detectado: **{ang}º**.")
            
            # --- Mostrar Información al Usuario ---
            if veh_info:
                st.info("ℹ️ **Información del Vehículo Ensayado:**\n\n" + " | ".join(veh_info))
                
            if issue_msgs:
                st.warning("⚠️ **Alerta Criterio Test CRASH3:** " + " | ".join(issue_msgs) + 
                           "\n\n*Los coeficientes CRASH3 estandarizados presuponen impactos planos directos contra barrera rígida ancha inamovible.*")
            
            # Despliegue de métricas intermedias
            col_m1, col_m2, col_m3 = st.columns(3)
            col_m1.metric("Vel. sin daño (b0)", f"{res['b0_raw']:.2f} {units['v']}")
            col_m2.metric("Deformación Media (C_avg)", f"{res['c_avg']:.2f} {units['l']}")
            col_m3.metric("Pendiente (b1)", f"{res['b1']:.3f} 1/s")

            # Despliegue de Coeficientes Finales
            st.markdown("### Coeficientes de Rigidez:")
            col1, col2, col3 = st.columns(3)
            col1.success(f"### **A:** {res['A']:.0f}\n#### {units['A']}")
            col2.warning(f"### **B:** {res['B']:.0f}\n#### {units['B']}")
            col3.info(f"### **G:** {res['G']:.0f}\n#### {units['G']}")

            # Exportar Resultados
            export_dict = {
                'Inputs': valid_data,
                'System': system,
                'Intermediate': {
                    'b0': res['b0_raw'],
                    'C_avg': res['c_avg'],
                    'b1': res['b1']
                },
                'Results': {
                    'A': res['A'],
                    'B': res['B'],
                    'G': res['G']
                }
            }
            json_export = json.dumps(export_dict, indent=4)
            
            st.download_button(
                label="📥 Exportar Resultados (JSON)",
                data=json_export,
                file_name="coeficientes_crash3.json",
                mime="application/json"
            )
else:
    if data_source == "Subir Archivo Manual":
        st.write("👈 Sube un archivo de test desde el panel izquierdo para comenzar.")
    else:
        st.write("👈 Introduce un número de Test Válido en el panel izquierdo y pulsa Enter para procesarlo.")
    
    st.markdown(r"""
    ### Notas de la Reconstrucción:
    - **Aproximación de $\Delta V$:** Si la velocidad de rebote no está definida explícitamente, la base del algoritmo asume una restitución del 10%.
    
    $$ \Delta V = V_{impacto} \times 1.1 $$
    
    - **Área y Deformación Media:** Se calcula usando la *regla trapezoidal* para 6 espacios equidistantes, asumiendo $L_{test}$ como la longitud total del contacto directo.
    """)