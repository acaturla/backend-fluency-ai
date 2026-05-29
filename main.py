from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
import edge_tts
import json
import os
import shutil
import uuid # <--- NUEVO
import time

def limpiar_audios_viejos():
    ruta_audios = "audios"
    if not os.path.exists(ruta_audios):
        return
    ahora = time.time()
    for archivo in os.listdir(ruta_audios):
        ruta_completa = os.path.join(ruta_audios, archivo)
        # Si el archivo tiene más de 10 minutos (600 segundos), se elimina
        if os.path.isfile(ruta_completa) and (ahora - os.path.getmtime(ruta_completa) > 600):
            try:
                os.remove(ruta_completa)
                print(f"🧹 Limpieza automática: Archivo residual eliminado -> {archivo}")
            except Exception as e:
                print(f"No se pudo eliminar el archivo {archivo}: {e}")

# 1. Configuración de Gemini
GOOGLE_API_KEY = "AIzaSyBGWZwG0ohh7RYvdwYGAPLftWqtrsLNwNU"
genai.configure(api_key=GOOGLE_API_KEY)

instrucciones_sistema = """
Eres el motor de una app para aprender inglés. 
Actúa como un camarero en un restaurante de Londres. El usuario es tu cliente.
Tu tarea es doble:
1. Evaluar el inglés del usuario en la frase que te acaba de decir (precisión del 0 al 100).
2. Responder a lo que te diga siguiendo tu rol de camarero, siempre en inglés.

SIEMPRE debes responder ÚNICAMENTE con un objeto JSON válido con esta estructura exacta:
{
  "precision": 95,
  "correccion_gramatical": "Explicación de errores o vacío si es perfecto",
  "respuesta_personaje": "Hello, sir. Here is the menu. What would you like to order?"
}
"""

model = genai.GenerativeModel(
    'gemini-3.5-flash', 
    system_instruction=instrucciones_sistema
) 

# 2. Inicializamos la aplicación web
app = FastAPI(title="Motor IA - English App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Permite que cualquier app se conecte (ideal para desarrollo)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- NOVEDAD: CARPETA DE AUDIOS ---
# Creamos la carpeta física si no existe y la "montamos" en el servidor web
os.makedirs("audios", exist_ok=True)
app.mount("/audios", StaticFiles(directory="audios"), name="audios")

# 3. Definimos la estructura de datos
class MensajeUsuario(BaseModel):
    texto: str

class PistaRequest(BaseModel):
    situacion: str

class ChatRequest(BaseModel):
    mensajes: list 
    situacion: str
    ultimo_turno: bool = False 
    nivel: str = "Intermedio"
    es_test: bool = False

# 4. Nuestras Rutas
@app.get("/")
def ruta_principal():
    return {"mensaje": "El servidor está activo y listo para hablar."}

@app.post("/hablar")
async def hablar(req: ChatRequest):
    try:
        limpiar_audios_viejos()
        historial_texto = "\n".join([f"{m['emisor']}: {m['texto']}" for m in req.mensajes])
        
        # 2. Lógica de cierre: Si es el último turno, somos tajantes con Gemini
        instruccion_cierre = ""
        if req.ultimo_turno:
            instruccion_cierre = "ATENCIÓN: Este es tu ÚLTIMO turno. Debes cerrar la conversación o despedirte de forma natural. PROHIBIDO hacer preguntas al usuario o dejar la conversación abierta."
        
        # Adaptamos el vocabulario del personaje IA a 5 niveles
        comportamiento_ia = ""
        if req.nivel == "Básico":
            comportamiento_ia = "Usa frases muy cortas, vocabulario A1, habla despacio y haz preguntas muy cerradas (sí/no)."
        elif req.nivel == "Pre-Intermedio":
            comportamiento_ia = "Usa vocabulario A2. Frases sencillas y cotidianas. Haz preguntas directas y fáciles de entender, sin modismos complejos."
        elif req.nivel == "Intermedio":
            comportamiento_ia = "Usa nivel B1. Conversación estándar, introduce alguna expresión común, pero mantén un ritmo asequible."
        elif req.nivel == "Intermedio-Alto":
            comportamiento_ia = "Usa nivel B2. Introduce 'phrasal verbs', oraciones compuestas y habla con fluidez y naturalidad."
        elif req.nivel == "Avanzado":
            comportamiento_ia = "Usa vocabulario C1/C2, expresiones idiomáticas ('idioms'), ironía o estructuras complejas. Tu lenguaje debe ser altamente sofisticado."

        if req.es_test:
            comportamiento_ia = """
            ATENCIÓN: Eres un examinador oficial de Cambridge. 
            El usuario acaba de iniciar la interacción (probablemente saludando o diciendo que está listo). 
            REGLA DE ORO: IGNORA SU SALUDO Y LOS FORMALISMOS. No le preguntes si está preparado. Tu respuesta DEBE ser directamente la primera pregunta evaluativa.
            
            - Si es el primer turno, lanza una pregunta básica (ej. "Where do you live and what do you like about it?"). 
            - En los siguientes turnos, AUMENTA drásticamente la complejidad de tus preguntas (ej. debates sobre tecnología, situaciones hipotéticas complejas, opiniones abstractas). 
            - Haz UNA SOLA pregunta por turno.
            NO corrijas sus errores, solo escucha y lanza la siguiente pregunta del examen.
            """
            instruccion_cierre = "ATENCIÓN: Este es el último turno del test. Despídete de forma muy breve y dile que vas a calcular su nivel. NO hagas más preguntas."
        
        prompt = f"""
        Eres un personaje en la siguiente situación: "{req.situacion}".
        
        REGLAS ESTRICTAS:
        1. 'respuesta_personaje': Responde al usuario en INGLÉS. {comportamiento_ia} {instruccion_cierre}
        2. 'sugerencia_espanol': Sugerencia muy breve EN ESPAÑOL de lo que el usuario podría responderte.
        3. 'objetivo_cumplido': true si terminó lógicamente, false si debe seguir.
        
        Historial:
        {historial_texto}
        
        Devuelve SOLO un JSON: {{"respuesta_personaje": "...", "sugerencia_espanol": "...", "objetivo_cumplido": false}}
        """

        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        
        datos = json.loads(response.text.replace("```json", "").replace("```", ""))
        
        # Generación de audio con edge_tts
        texto_ia = datos["respuesta_personaje"]
        nombre_mp3 = f"respuesta_{uuid.uuid4().hex}.mp3"
        ruta_mp3 = f"audios/{nombre_mp3}"
        
        communicate = edge_tts.Communicate(texto_ia, "en-US-AriaNeural")
        await communicate.save(ruta_mp3)
        
        datos["audio_url"] = f"http://127.0.0.1:8000/{ruta_mp3}"
        
        return datos

    except Exception as e:
        print(f"Error en /hablar: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/pista")
async def pedir_pista(req: PistaRequest):
    try:
        # Le pedimos a Gemini que actúe como un profesor que nos sopla la respuesta
        prompt = f"""
        El estudiante está en esta situación: '{req.situacion}'.
        Se ha quedado en blanco. Sugiérele 3 frases cortas, naturales y muy útiles en inglés que podría decir ahora mismo.
        Devuelve SOLO las frases en este formato exacto:
        1. [Frase en inglés] - [Traducción al español]
        2. [Frase en inglés] - [Traducción al español]
        3. [Frase en inglés] - [Traducción al español]
        """
        
        # OJO: Asegúrate de usar la variable de tu modelo (genai.GenerativeModel)
        # Si la llamaste diferente al principio del archivo, usa ese nombre.
        model = genai.GenerativeModel('gemini-2.5-flash') 
        response = model.generate_content(prompt)
        
        return {"pistas": response.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))    

import edge_tts # Asegúrate de que esto está importado arriba si no lo estaba

@app.post("/transcribir")
async def transcribir_y_evaluar(
    file: UploadFile = File(...), 
    nivel: str = Form("Intermedio") # <--- RECIBE EL NIVEL AQUÍ
):
    limpiar_audios_viejos()
    print("\n--- INICIANDO TRANSCRIPCIÓN ---")
    nombre_unico = f"temp_{uuid.uuid4().hex}.m4a"
    
    try:
        with open(nombre_unico, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        archivo_gemini = genai.upload_file(nombre_unico)
        model = genai.GenerativeModel('gemini-2.5-flash')

        # Adaptamos la severidad a 5 niveles
        severidad = ""
        if nivel == "Básico":
            severidad = "El alumno es A1. Sé extremadamente comprensivo. Solo penaliza si la palabra es totalmente incomprensible. Ignora el acento fuerte y prioriza la motivación."
        elif nivel == "Pre-Intermedio":
            severidad = "El alumno es A2. Exige frases completas simples, pero sé tolerante con el acento y errores gramaticales menores. Penaliza el Spanglish obvio."
        elif nivel == "Intermedio":
            severidad = "El alumno es B1. Corrige errores gramaticales básicos, traducciones literales y pronunciación muy castellanizada. Exige un nivel medio."
        elif nivel == "Intermedio-Alto":
            severidad = "El alumno es B2. Exige fluidez, buen uso de modismos y tiempos verbales complejos. Penaliza la pronunciación plana y la falta de naturalidad."
        elif nivel == "Avanzado":
            severidad = "El alumno es C1/C2. Sé extremadamente purista. Penaliza cualquier mínimo rastro de acento español, entonación o vocabulario simple."

        # PROMPT ACTUALIZADO: Pedimos el texto ideal
        prompt = """
        Actúa como un profesor de fonética y gramática inglesa exigente, pero muy directo, breve y constructivo.
        {severidad}
        Escucha atentamente el audio y evalúa OBLIGATORIAMENTE ambos aspectos (texto y voz).
        
        1. 'transcripcion': Transcribe exactamente las palabras que dijo el usuario.
        2. 'precision': Evalúa de 0 a 100.
           - PENALIZA la gramática: traducciones literales, falta de cortesía, o mala estructura.
           - PENALIZA la pronunciación: leer con fonética española, falta de fluidez.
           - Si falla en gramática O pronunciación, la nota máxima es 80. Si falla en ambos, es 60.
        3. 'correccion': Explica los fallos en español de forma SÚPER DIRECTA y CONCISA.
           - REGLA DE ORO DE BREVEDAD: Escribe una sola frase corta (máximo 2 líneas). Prohibido hacer introducciones, rodeos o usar lenguaje dramático. Di directamente qué cambiar.
           - Si la nota es de 90 a 100, pon un elogio de tres palabras (ej. "¡Excelente pronunciación y estructura!").
        4. 'texto_ideal': Escribe la frase exacta, natural y gramaticalmente perfecta que el usuario DEBERÍA haber dicho (en inglés).

        Devuelve SOLO un JSON con este formato exacto: 
        {"transcripcion": "...", "precision": 0, "correccion": "...", "texto_ideal": "..."}
        """

        respuesta = model.generate_content([prompt, archivo_gemini])
        
        os.remove(nombre_unico)
        archivo_gemini.delete()
        
        # Limpieza del JSON
        texto_limpio = respuesta.text.strip().replace("```json", "").replace("```", "")
        inicio_json = texto_limpio.find('{')
        fin_json = texto_limpio.rfind('}') + 1
        if inicio_json != -1 and fin_json != 0:
            texto_limpio = texto_limpio[inicio_json:fin_json]
            
        resultado = json.loads(texto_limpio)
        
        # --- NUEVO: GENERAMOS EL AUDIO DE LA CORRECCIÓN ---
        texto_ideal = resultado.get("texto_ideal", resultado.get("transcripcion", ""))
        nombre_audio_corr = f"corr_{uuid.uuid4().hex}.mp3"
        ruta_audio_corr = f"audios/{nombre_audio_corr}"
        
        # Usamos la misma voz que la IA para pronunciar la frase correcta
        communicate = edge_tts.Communicate(texto_ideal, "en-US-AriaNeural")
        await communicate.save(ruta_audio_corr)
        
        audio_correccion_url = f"http://127.0.0.1:8000/{ruta_audio_corr}"
        
        print("6. ¡Éxito! Enviando resultado al frontend.")
        return {
            "transcripcion": resultado.get("transcripcion", ""),
            "precision": resultado.get("precision", 0),
            "correccion": resultado.get("correccion", ""),
            "audio_correccion": audio_correccion_url # Enviamos la URL del audio
        }

    except Exception as e:
        print(f"!!! ERROR GRAVE EN /transcribir: {e}")
        if os.path.exists(nombre_unico): os.remove(nombre_unico)
        raise HTTPException(status_code=500, detail=str(e))

# Definimos el modelo de datos para la evaluación final
class ResumenRequest(BaseModel):
    mensajes: list
    situacion: str
    es_test: bool = False

@app.post("/evaluar_sesion")
async def evaluar_sesion(req: ResumenRequest):
    try:
        limpiar_audios_viejos()
        # 1. Calculamos la media real basada en las evaluaciones parciales
        mensajes_usuario = [m for m in req.mensajes if m.get('emisor') == 'yo' and 'evaluacion' in m]
        
        if not mensajes_usuario:
            nota_media_real = 0
            resumen_correcciones = "No hubo evaluaciones."
        else:
            suma_notas = sum([m['evaluacion'].get('precision', 0) for m in mensajes_usuario])
            nota_media_real = round(suma_notas / len(mensajes_usuario))
            # Recopilamos qué le corrigió el profesor durante la charla
            resumen_correcciones = "\n".join([f"- Frase: '{m.get('texto')}'. Corrección previa: {m['evaluacion'].get('correccion')}" for m in mensajes_usuario])

        # 2. Preparamos el texto del chat
        historial_texto = "\n".join([f"{m['emisor']}: {m['texto']}" for m in req.mensajes if m['emisor'] != 'sistema'])

        # 3. Le damos a Gemini las instrucciones y los datos reales
        if req.es_test:
            prompt = f"""
            Actúa como un evaluador de Cambridge. Analiza este test de diagnóstico.
            Historial de la conversación:
            {historial_texto}
            
            Evalúa el nivel de inglés del usuario ('yo') basándote en la complejidad de su vocabulario, su gramática y cómo ha respondido a preguntas cada vez más difíciles.
            Asigna UNO de estos 5 niveles exactos: 'Básico', 'Pre-Intermedio', 'Intermedio', 'Intermedio-Alto', 'Avanzado'.
            
            Devuelve SOLO un JSON con este formato exacto:
            {{
                "nota_global": 0,
                "nivel_asignado": "Escribe aquí el nivel",
                "fortalezas": "Un breve párrafo en español justificando su nivel.",
                "areas_mejora": "Un breve párrafo en español con lo que le falta para subir al siguiente nivel."
            }}
            """
        else:
            prompt = f"""
            Actúa como un profesor de inglés redactando el reporte final del alumno.
            Situación: "{req.situacion}"
            
            Historial de la conversación (solo texto):
            {historial_texto}
            
            Feedback de voz y gramática que el alumno YA recibió en esta sesión:
            {resumen_correcciones}

            REGLA ESTRICTA: La nota global matemática es EXACTAMENTE {nota_media_real}/100. NO la cambies ni la recalcules.
            
            Tu tarea es redactar el reporte final basándote EXCLUSIVAMENTE en el historial y en el feedback que ya recibió. 
            Devuelve SOLO un JSON con este formato exacto:
            {{
                "nota_global": {nota_media_real},
                "fortalezas": "Un breve párrafo en español destacando lo que hizo bien.",
                "areas_mejora": "Un breve párrafo en español resumiendo los errores principales que se le corrigieron (fonética, gramática, cortesía)."
            }}
            """
        
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        
        # Limpieza de seguridad del JSON
        texto_limpio = response.text.strip().replace("```json", "").replace("```", "")
        inicio_json = texto_limpio.find('{')
        fin_json = texto_limpio.rfind('}') + 1
        if inicio_json != -1 and fin_json != 0:
            texto_limpio = texto_limpio[inicio_json:fin_json]
            
        return json.loads(texto_limpio)

    except Exception as e:
        print(f"Error en /evaluar_sesion: {e}")
        raise HTTPException(status_code=500, detail=str(e))