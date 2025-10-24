from agents import Agent, Runner, trace, function_tool
from openai.types.responses import ResponseTextDeltaEvent
from typing import Dict
import os
import certifi
import chromadb
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
import mysql.connector
import pythoncom
from datetime import datetime
import win32com.client as win32
from dotenv import load_dotenv
import json
from fastapi import FastAPI, Request, Response
import requests 
import asyncio
from concurrent.futures import ThreadPoolExecutor
import redis

os.environ['SSL_CERT_FILE'] = certifi.where()
load_dotenv(override=True)

MYSQL_CONFIG = {
    "host": os.getenv("DB_HOST"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}

# --- Configuración de WhatsApp ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

if not ACCESS_TOKEN or not VERIFY_TOKEN or not PHONE_NUMBER_ID:
    print("Error: Faltan variables de entorno de WhatsApp.")
    exit()

# Inicialización de clientes globales
cliente_openai = OpenAI()
embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
cliente_chroma = chromadb.PersistentClient(path="db_politicas")
coleccion = cliente_chroma.get_collection(name="politicas_empresariales")

try:
    redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
    redis_client.ping()
    print("✅ Conectado exitosamente a Redis")
except redis.exceptions.ConnectionError as e:
    print(f"❌ ERROR: No se pudo conectar a Redis. Asegúrate de que esté corriendo.")
    print(f"Detalle: {e}")
    # En un caso real, podrías querer salir si Redis es esencial
    # exit()

# Executor para operaciones síncronas
executor = ThreadPoolExecutor(max_workers=10)

#Cambios de rutas relativas
CARPETA_FILES = "files"
POLITICAS_CON_DESCRIPCION = {
    "sin_coincidencias": "no se encontró ninguna coincidencia",
    "beca_estudio.pdf": "Información sobre beneficios y becas para estudios.",
    "centro_recreacion.pdf": "Reglas para pertenecer al centro de recreación.",
    "mutuo_acuerdo.pdf": "Procedimientos para terminación de contrato laboral."
}

RUTAS_POLITICAS_DETECTADAS = []
if os.path.isdir(CARPETA_FILES):
    RUTAS_POLITICAS_DETECTADAS = [
        os.path.join(CARPETA_FILES, f) 
        for f in os.listdir(CARPETA_FILES) 
        if f.endswith(".pdf") and os.path.isfile(os.path.join(CARPETA_FILES, f))
    ]
else:
    print(f"Error: La carpeta '{CARPETA_FILES}' no existe.")

# Estas listas ahora se construyen dinámicamente
RUTAS_POLITICAS = []
NOMBRES_POLITICAS = []

print("Verificando archivos detectados contra descripciones conocidas...")
for ruta_detectada in RUTAS_POLITICAS_DETECTADAS:
    nombre_archivo = os.path.basename(ruta_detectada)
    
    # Comprobamos si el archivo encontrado tiene una descripción
    if nombre_archivo in POLITICAS_CON_DESCRIPCION:
        RUTAS_POLITICAS.append(ruta_detectada)
        NOMBRES_POLITICAS.append(nombre_archivo)
    else:
        # Advertencia si encontramos un PDF "huérfano"
        print(f"Advertencia: Se encontró '{nombre_archivo}' en la carpeta 'files',")
        print(f"pero no tiene descripción en 'POLITICAS_CON_DESCRIPCION'.")
        print(f"Será IGNORADO.")

# ============================================================================
# TOOLS ORQUESTADOR
# ============================================================================
@function_tool
def seleccionar_politica_con_llm(pregunta_usuario: str):
    """Usa un LLM para determinar qué política es la más relevante."""
    lista_politicas_formateada = "\n".join(
        [f"- {nombre}: {desc}" for nombre, desc in POLITICAS_CON_DESCRIPCION.items()]
    )

    prompt_enrutador = f"""
    Tu única tarea es actuar como un clasificador de documentos.
    Lee la pregunta del usuario y decide cuál de los siguientes documentos es el más relevante.

    Documentos disponibles:
    {lista_politicas_formateada}

    Pregunta del usuario: "{pregunta_usuario}"

    Responde únicamente con el nombre exacto del archivo del documento más relevante. 
    Si ninguno parece relevante, responde con "sin_coincidencias".
    """
    try:
        response = cliente_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt_enrutador}],
            temperature=0.0
        )
        respuesta_llm = response.choices[0].message.content.strip()
        
        for nombre in NOMBRES_POLITICAS:
            if nombre in respuesta_llm:
                return nombre
        
        return "sin_coincidencias"

    except Exception as e:
        print(f"Error en LLM enrutador: {e}")
        return "sin_coincidencias"
    
@function_tool
def buscar_contexto_relevante(pregunta: str, nombre_politica: str, n_resultados: int = 5) -> str:
    """Busca los chunks más relevantes para una pregunta y devuelve texto plano."""
    embedding_pregunta = embeddings_model.embed_query(pregunta)

    resultados = coleccion.query(
        query_embeddings=[embedding_pregunta],
        n_results=n_resultados,
        where={"source": nombre_politica},
        include=["documents"]
    )

    documentos_relevantes = resultados['documents'][0] if resultados['documents'] else []
    print(f"Se encontraron {len(documentos_relevantes)} chunks relevantes.")

    contexto_combinado = "\n\n---\n\n".join(map(str, documentos_relevantes))

    return f"Contexto relevante encontrado en {nombre_politica}:\n\n{contexto_combinado}"


# ============================================================================
# AGENTE DE REGISTRO DE PREGUNTAS
# ============================================================================
@function_tool
def registrar_pregunta_mysql(pregunta: str, politica: str = "No especificada", contexto_encontrado: bool = True, respuesta: str = "", notas: str = ""):
    """Registra las preguntas en la base de datos MySQL."""
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO question_agent_ia
            (question, file_consulted, contexts, answer_ia, notes)
            VALUES (%s, %s, %s, %s, %s)
        """
        valores = (pregunta, politica, contexto_encontrado, respuesta, notas)
        
        cursor.execute(query, valores)
        conn.commit()
        
        registro_id = cursor.lastrowid
        cursor.close()
        conn.close()
        
        return {
            "status": "ok", 
            "message": "Pregunta registrada exitosamente",
            "id": registro_id
        }
    except Exception as e:
        return {
            "status": "error",
            "message": f"Error al registrar: {str(e)}"
        }

instrucciones_registro = "Registra las preguntas de los usuarios en la base de datos MySQL"
registro_pregunta = Agent(
    name="registrador_preguntas_usuarios",                       
    instructions=instrucciones_registro, 
    tools=[registrar_pregunta_mysql],
    model="gpt-4o-mini",
    handoff_description="ingresa las preguntas del usuario a una base mysql"
)

# ============================================================================
# AGENTE DE PREGUNTAS DESCONOCIDAS
# ============================================================================
@function_tool
def enviar_email_rrhh(asunto: str, pregunta: str, rut_usuario: str = "", nombre_usuario: str = "", notas: str = ""):
    """Registra y envía un correo electrónico al departamento de RRHH."""
    try:
        # Registrar en MySQL
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO unknown_question
            (pregunta, rut, nombre_usuario, notas)
            VALUES (%s, %s, %s, %s)
        """
        valores = (pregunta, rut_usuario, nombre_usuario, notas)
        
        cursor.execute(query, valores)
        conn.commit()
        
        registro_id = cursor.lastrowid
        cursor.close()
        conn.close()

        # Enviar email
        pythoncom.CoInitialize()
        
        try:
            outlook = win32.Dispatch('outlook.application')
            mail = outlook.CreateItem(0)

            emails = os.getenv("EMAIL_RRHH")
            destinatarios = [e.strip() for e in emails.split(",") if e.strip()]

            mail.To = "; ".join(destinatarios)
            mail.Subject = asunto
            cuerpo = f"""Consulta recogida desde el Chatbot de RRHH
De: {nombre_usuario if nombre_usuario else 'Usuario anónimo'}
Rut: {rut_usuario if rut_usuario else 'No proporcionado'}

Pregunta:
{pregunta}

---
Este mensaje fue enviado automáticamente.
Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}
"""
            mail.Body = cuerpo
            mail.Send()
            
            return {
                "status": "ok",
                "message": "Email enviado exitosamente a RRHH"
            }
        finally:
            pythoncom.CoUninitialize()
            
    except Exception as e:
        return {
            "status": "error",
            "message": f"No se pudo enviar el email: {str(e)}"
        }
    
instrucciones_registro_desconocido = "Registra preguntas sin respuesta y envía correos informativos a RRHH"
registro_pregunta_desconocida = Agent(
    name="registrador_preguntas_desconocidas",                       
    instructions=instrucciones_registro_desconocido, 
    tools=[enviar_email_rrhh],
    model="gpt-4o-mini",
    handoff_description="registra preguntas desconocidas y envía correos a RRHH"
)

# ============================================================================
# AGENTE ORQUESTADOR
# ============================================================================
instrucciones_orquestador_json = """
Eres un asistente de Recursos Humanos experto de la empresa Cramer.
Tu única función es analizar la solicitud del usuario, usar las herramientas RAG 
y generar un objeto JSON estructurado con tu plan y tu respuesta.

**Esquema JSON de Salida OBLIGATORIO:**
Debes responder SIEMPRE con un único bloque de código JSON válido, y nada más.

{
"accion": "tipo_de_accion",
"respuesta_al_usuario": "El mensaje que el usuario final debe leer.",
"politica_identificada": "nombre_del_pdf_o_null",
"contexto_utilizado": "El texto del contexto RAG obtenido, o null",
"necesita_escalar_a_rrhh": false,
"necesita_registrar_pregunta": false
}

**Tipos de 'accion':**
- "responder_con_contexto": Si usaste RAG y encontraste respuesta.
- "responder_sin_contexto": Si es un saludo, despedida o chat general.
- "ofrecer_escalamiento": Si no se encontró el documento o contexto.
- "confirmar_escalamiento": Si el usuario *acepta* escalar (p.ej. dice "sí", o "sí, por favor").
- "error": Si ocurrió un error interno.

**Reglas del Proceso:**
1.  **Analiza la pregunta:**
    - Si es un saludo/despedida: 
    `accion`="responder_sin_contexto", 
    `respuesta_al_usuario`="Hola, soy tu asistente de RRHH. ¿En qué puedo ayudarte hoy?".
2.  **Proceso RAG:**
    a. Usa `seleccionar_politica_con_llm`.
    b. Si es 'sin_coincidencias':
    `accion`="ofrecer_escalamiento",
    `respuesta_al_usuario`="No encontré un documento que hable sobre eso. ¿Quieres que envíe tu consulta a RRHH?",
    `politica_identificada`=null.
    c. Si hay política:
    Usa `buscar_contexto_relevante`.
    `accion`="responder_con_contexto",
    `respuesta_al_usuario`="[Aquí va tu respuesta basada ÚNICAMENTE en el contexto]",
    `politica_identificada`="nombre.pdf",
    `contexto_utilizado`="[Texto del RAG]",
    `necesita_registrar_pregunta`=true.
3.  **Escalamiento:**
    - Si el usuario *acepta* el escalamiento (ej: "sí, envía la consulta"):
    `accion`="confirmar_escalamiento",
    `respuesta_al_usuario`="Perfecto, he enviado tu consulta a RRHH. Te contactarán pronto.",
    `necesita_escalar_a_rrhh`=true.
"""

# ✅ CORRECCIÓN: tools debe ser una lista, no lista de listas
orquestador_agente = Agent(
    name="asistente_rrhh_cramer",
    instructions=instrucciones_orquestador_json,
    tools=[seleccionar_politica_con_llm, buscar_contexto_relevante],
    #handoffs=[registro_pregunta, registro_pregunta_desconocida],
    model="gpt-4o-mini",
)

# ============================================================================
# FUNCIÓN ASÍNCRONA PARA EJECUTAR EL AGENTE
# ============================================================================
async def ejecutar_agente_async(historical_messages: list, new_message: str) -> (str, list):
    """Ejecuta el agente de forma asíncrona."""
    
    try:
        runner = Runner()
            
        if historical_messages:
            print(f"Hidratando runner con {len(historical_messages)} mensajes previos.")
            runner.messages = historical_messages

        # 4. Ejecutamos el nuevo mensaje
        result_obj = await runner.run(orquestador_agente, new_message)
    
        print("--- 🏁 FIN DE TRAZA ---")

        updated_history = runner.messages

        # Extraer la respuesta (que esperamos sea un JSON string)
        raw_response = ""
        if isinstance(result_obj, str):
            raw_response = result_obj
        elif hasattr(result_obj, 'content') and result_obj.content:
            raw_response = result_obj.content
        elif hasattr(result_obj, 'messages') and result_obj.messages:
            last_message = result_obj.messages[-1]
            if isinstance(last_message, dict):
                raw_response = last_message.get('content', str(last_message))
            elif hasattr(last_message, 'content'):
                raw_response = last_message.content
            else:
                raw_response = str(last_message)
        else:
            raw_response = str(result_obj)
        
        # Limpiar la respuesta: los LLM a veces envuelven JSON en ```json ... ```
        if "```json" in raw_response:
            raw_response = raw_response.split("```json", 1)[-1].split("```", 1)[0]
        raw_response = raw_response.strip()

        # Validar si es un JSON antes de devolver
        try:
            json.loads(raw_response)
            return raw_response, updated_history
        except json.JSONDecodeError:
            print(f"Error: La respuesta del agente no fue un JSON válido: {raw_response}")
            error_json = {
                "accion": "error_interno", "respuesta_al_usuario": "Lo siento, tuve un problema para procesar tu solicitud. Por favor, intenta más tarde.",
                "politica_identificada": None, "contexto_utilizado": None, "necesita_escalar_a_rrhh": False, "necesita_registrar_pregunta": False
            }
            # 8. Devolvemos el error, pero el historial ANTIGUO
            return json.dumps(error_json), historical_messages
            
    except Exception as e:
        print(f"Error crítico ejecutando agente: {e}")
        import traceback
        traceback.print_exc()
        # Generar un JSON de error
        error_json = {
            "accion": "error_critico",
            "respuesta_al_usuario": "Lo siento, hubo un error interno. El equipo técnico ha sido notificado.",
            "politica_identificada": None,
            "contexto_utilizado": str(e),
            "necesita_escalar_a_rrhh": False,
            "necesita_registrar_pregunta": False
        }
        return json.dumps(error_json), historical_messages
        
# ============================================================================
# FASTAPI APPLICATION
# ============================================================================
app = FastAPI()

@app.get("/webhook")
def verify_webhook(request: Request):
    """Verifica la URL del webhook con Meta."""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("✅ WEBHOOK VERIFICADO")
        return Response(content=challenge, status_code=200)
    else:
        print("❌ ERROR DE VERIFICACIÓN DE WEBHOOK")
        return Response(status_code=403)

@app.post("/webhook")
async def receive_message(request: Request):
    """Recibe y procesa mensajes de WhatsApp de forma asíncrona."""
    body = await request.json()
    print("📨 Mensaje recibido:")
    print(json.dumps(body, indent=2))

    # Procesar en segundo plano para responder rápido a WhatsApp
    asyncio.create_task(process_message_async(body))
    
    # Responder inmediatamente a WhatsApp
    return Response(status_code=200)

async def process_message_async(body: dict):
    """Procesa el mensaje de forma asíncrona."""
    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        
        if "messages" in value and len(value["messages"]) > 0:
            message_info = value["messages"][0]
            
            if message_info.get("type") == "text":
                user_phone_number = message_info["from"]
                user_message = message_info["text"]["body"]

                print(f"👤 Procesando mensaje de {user_phone_number}: '{user_message}'")
                
                # === INICIO DE CAMBIOS: LÓGICA DE CONTROL ===
                
                # 1. Definimos la clave de Redis para este usuario
                redis_key = f"chatbot:history:{user_phone_number}"
                
                # 2. Obtenemos el historial de Redis
                try:
                    history_json = redis_client.get(redis_key)
                    if history_json:
                        historical_messages = json.loads(history_json)
                        print(f"🔄 Historial recuperado de Redis ({len(historical_messages)} mensajes)")
                    else:
                        historical_messages = []
                        print(f"✨ No se encontró historial. Empezando conversación nueva.")
                except Exception as e:
                    print(f"⚠️ Error al LEER de Redis: {e}. Empezando conversación nueva.")
                    historical_messages = []

                # 3. Ejecutamos el agente (ahora devuelve 2 cosas)
                json_string_response, updated_history = await ejecutar_agente_async(
                    historical_messages, 
                    user_message
                )

                # 4. Guardamos el nuevo historial en Redis (de forma asíncrona)
                try:
                    # Guardamos el historial actualizado con un TTL de 1 hora (3600s)
                    redis_client.set(redis_key, json.dumps(updated_history), ex=3600)
                    print(f"💾 Historial actualizado ({len(updated_history)} mensajes) guardado en Redis.")
                except Exception as e:
                    print(f"⚠️ Error al ESCRIBIR en Redis: {e}.")
                
                print(f"🤖 JSON de respuesta generado: {json_string_response}")

                # 2. Parsear el JSON
                try:
                    data = json.loads(json_string_response)
                except Exception as e:
                    print(f"Error fatal parseando JSON, enviando error: {e}")
                    data = {
                        "respuesta_al_usuario": "Lo siento, tuve un problema interno para entender la respuesta. Intenta de nuevo.",
                        "necesita_registrar_pregunta": False,
                        "necesita_escalar_a_rrhh": False,
                        "accion": "error_parseo_json",
                        "politica_identificada": None,
                        "contexto_utilizado": None
                    }

                # === INICIO DE CAMBIOS: LOGGING DETALLADO ===
                # Imprimimos un "informe" claro en la consola
                print("="*60)
                print("🤖 INFORME DE PROCESAMIENTO DEL AGENTE")
                print(f"  > Acción Decidida:     {data.get('accion')}")
                print(f"  > Política Identificada: {data.get('politica_identificada')}")
                
                # Esto responde directamente a tu pregunta:
                contexto_encontrado = bool(data.get('contexto_utilizado'))
                print(f"  > Contexto Encontrado: {'SÍ ✅' if contexto_encontrado else 'NO ❌'}")
                
                print(f"  > Respuesta P/ Usuario:  {data.get('respuesta_al_usuario')}")
                print("="*60)
                # === FIN DE CAMBIOS ===

                # 3. Extraer la respuesta para el usuario
                respuesta_para_enviar = data.get(
                    "respuesta_al_usuario", 
                    "No pude procesar tu solicitud."
                )

                # 4. Enviar respuesta a WhatsApp
                await send_whatsapp_message_async(user_phone_number, respuesta_para_enviar)

                # 5. === AQUÍ ESTÁ EL CONTROL ===
                # Ejecutar acciones post-respuesta (handoffs) de forma asíncrona
                
                loop = asyncio.get_event_loop()

                if data.get("necesita_registrar_pregunta", False):
                    print("Ejecutando handoff: registrador_preguntas_usuarios")
                    
                    # Construir un prompt claro para el agente de registro
                    prompt_registro = f"""
                    Registra la siguiente interacción:
                    - Pregunta Original: "{user_message}"
                    - Política Consultada: "{data.get('politica_identificada')}"
                    - Contexto Encontrado: {data.get('contexto_utilizado') is not None}
                    - Respuesta dada al usuario: "{respuesta_para_enviar}"
                    """
                    
                    # Ejecutamos el agente de registro en el pool de hilos
                    await loop.run_in_executor(
                        executor,
                        lambda: asyncio.run(Runner().run(registro_pregunta, prompt_registro))
                    )

                if data.get("necesita_escalar_a_rrhh", False):
                    print("Ejecutando handoff: registrador_preguntas_desconocidas")
                    
                    # Construir un prompt claro para el agente de escalamiento
                    prompt_escalamiento = f"""
                    El usuario necesita escalar la siguiente consulta a RRHH. 
                    Asunto: "Consulta de Chatbot para RRHH"
                    Pregunta: "{user_message}"
                    Notas: El bot no pudo encontrar una respuesta.
                    """
                    
                    # Ejecutamos el agente de escalamiento en el pool de hilos
                    await loop.run_in_executor(
                        executor,
                        lambda: asyncio.run(Runner().run(registro_pregunta_desconocida, prompt_escalamiento))
                    )
                
                # === FIN DE CAMBIOS ===
            else:
                print(f"ℹ️ Tipo de mensaje no-texto: {message_info.get('type')}")
        else:
            print("ℹ️ Evento de estado recibido")

    except Exception as e:
        print(f"❌ Error al procesar mensaje: {e}")
        import traceback
        traceback.print_exc()

async def send_whatsapp_message_async(to_number: str, message: str, retries=3, delay=2):
    """Envía un mensaje de WhatsApp de forma asíncrona."""
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to_number,
        "type": "text",
        "text": {"body": message}
    }
    
    loop = asyncio.get_event_loop()
    
    for attempt in range(retries):
        try:
            # Ejecutar la petición HTTP en thread pool
            response = await loop.run_in_executor(
                executor,
                lambda: requests.post(url, headers=headers, json=payload)
            )
            response.raise_for_status()
            print(f"✅ Respuesta enviada a {to_number}")
            return
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Error en intento {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(delay)
            else:
                print("❌ Máximo de reintentos alcanzado")

# ============================================================================
# HEALTH CHECK ENDPOINT
# ============================================================================
@app.get("/health")
def health_check():
    """Endpoint para verificar que el servidor está funcionando."""
    return {"status": "ok", "message": "WhatsApp Bot is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)






