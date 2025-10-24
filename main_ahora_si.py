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
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor

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

# Executor para operaciones síncronas
executor = ThreadPoolExecutor(max_workers=10)

# Políticas disponibles
RUTAS_POLITICAS = [
    "files/beca_estudio.pdf",
    "files/centro_recreacion.pdf",
    "files/mutuo_acuerdo.pdf"
]

NOMBRES_POLITICAS = [os.path.basename(ruta) for ruta in RUTAS_POLITICAS]
POLITICAS_CON_DESCRIPCION = {
    "sin_coincidencias": "no se encontró ninguna coincidencia",
    "beca_estudio.pdf": "Información sobre beneficios y becas para estudios.",
    "centro_recreacion.pdf": "Reglas para pertenecer al centro de recreación.",
    "mutuo_acuerdo.pdf": "Procedimientos para terminación de contrato laboral."
}

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
instrucciones_orquestador = """
Eres un asistente de Recursos Humanos experto de la empresa Cramer. Eres amable y profesional.

1. **Analiza la pregunta:**
   - Si es un saludo o despedida, responde sin usar herramientas.
   - Si es sobre políticas de la empresa, procede al paso 2.

2. **Proceso RAG:**
   a. Usa `seleccionar_politica_con_llm` para identificar el documento.
   b. Si es 'sin_coincidencias', informa al usuario y ofrece escalar a RRHH.
   c. Si hay política, usa `buscar_contexto_relevante` para obtener información.

3. **Formulación de Respuesta:**
   a. Basa tu respuesta ÚNICAMENTE en el contexto encontrado.
   b. Transfiere la pregunta al agente `registrador_preguntas_usuarios`.

4. **Escalamiento:**
   - Si no hay respuesta, ofrece escalar a RRHH.
   - Si acepta, transfiere al agente `registrador_preguntas_desconocidas`.

5. **Interacción:** Siempre mantén la conversación activa.
"""

# ✅ CORRECCIÓN: tools debe ser una lista, no lista de listas
orquestador_agente = Agent(
    name="asistente_rrhh_cramer",
    instructions=instrucciones_orquestador,
    tools=[seleccionar_politica_con_llm, buscar_contexto_relevante],
    handoffs=[registro_pregunta, registro_pregunta_desconocida],
    model="gpt-4o-mini"
)

# ============================================================================
# FUNCIÓN ASÍNCRONA PARA EJECUTAR EL AGENTE
# ============================================================================
async def ejecutar_agente_async(mensaje: str) -> str:
    """Ejecuta el agente de forma asíncrona."""
    
    try:
        # Método 1: Intentar llamar directamente
        if hasattr(orquestador_agente, 'run'):
            # --- CORRECCIÓN: AÑADIR AWAIT ---
            result = await orquestador_agente.run(mensaje)
        
        # Método 2: Usar el contexto de Runner
        else:
            runner = Runner()
            # --- CORRECCIÓN: AÑADIR AWAIT ---
            result = await runner.run(orquestador_agente, mensaje)
        
        # Extraer la respuesta (esta lógica está bien)
        if isinstance(result, str):
            return result
        elif hasattr(result, 'final_output'):
            return result.final_output
        elif hasattr(result, 'content'):
            return result.content
        elif hasattr(result, 'messages') and result.messages:
            last_message = result.messages[-1]
            if isinstance(last_message, dict):
                return last_message.get('content', str(result))
            return str(last_message)
        else:
            return str(result)
            
    except Exception as e:
        print(f"Error ejecutando agente: {e}")
        import traceback
        traceback.print_exc()
        return "Lo siento, hubo un error procesando tu mensaje. Por favor, intenta de nuevo en otro momento."

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
                
                # ✅ Ejecutar el agente de forma asíncrona
                chatbot_response = await ejecutar_agente_async(user_message)
                
                print(f"🤖 Respuesta generada: '{chatbot_response}'")

                # Enviar respuesta
                await send_whatsapp_message_async(user_phone_number, chatbot_response)
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






