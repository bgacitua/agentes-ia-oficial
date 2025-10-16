# main.py
import os
import json
import requests
from fastapi import FastAPI, Request, Response
import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
import time

# Módulo de herramientas (asegúrate de que el archivo tools.py esté en el mismo directorio)
from tools import (
    TOOLS_JSON,
    handle_tool_calls,
    init_mysql_database
)

# ==============================================================================
# 1. CONFIGURACIÓN Y CARGA DE VARIABLES DE ENTORNO
# ==============================================================================
load_dotenv(override=True)

# --- Configuración de WhatsApp ---
ACCESS_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN")
PHONE_NUMBER_ID = os.environ.get("PHONE_NUMBER_ID")

# Verifica que las credenciales de WhatsApp estén cargadas.
if not ACCESS_TOKEN or not VERIFY_TOKEN or not PHONE_NUMBER_ID:
    print("Error: Faltan variables de entorno de WhatsApp. Asegúrate de configurar WHATSAPP_ACCESS_TOKEN, VERIFY_TOKEN y PHONE_NUMBER_ID.")
    exit()

# --- Configuración del Agente y RAG ---
DB_PATH = "db_politicas"
NOMBRE_COLECCION = "politicas_empresariales"

# Rutas a los documentos de políticas. Asegúrate de que estas rutas sean accesibles desde donde se ejecute el servidor.
RUTAS_POLITICAS = [
    r"C:\Users\gpavez\Desktop\Compensaciones\ia_agents\desarrollos_propios\files\beca_estudio.pdf",
    r"C:\Users\gpavez\Desktop\Compensaciones\ia_agents\desarrollos_propios\files\centro_recreación.pdf",
    r"C:\Users\gpavez\Desktop\Compensaciones\ia_agents\desarrollos_propios\files\mutuo_acuerdo.pdf", 

]
NOMBRES_POLITICAS = [os.path.basename(ruta) for ruta in RUTAS_POLITICAS]

POLITICAS_CON_DESCRIPCION = {
    "beca_estudio.pdf": "Contiene información sobre beneficios y becas para estudios superiores para los empleados y sus familias.",
    "centro_recreación.pdf": "Describe las reglas para pertenecer al centro de recreación de la empresa.",
    "mutuo_acuerdo.pdf": "Explica los procedimientos y condiciones para la terminación del contrato laboral de mutuo acuerdo."
}

# ==============================================================================
# 2. INICIALIZACIÓN DE CLIENTES Y BASE DE DATOS (Se ejecuta al iniciar FastAPI)
# ==============================================================================
try:
    # --- Clientes para el Agente RAG ---
    cliente_openai = OpenAI()
    embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
    cliente_chroma = chromadb.PersistentClient(path=DB_PATH)
    coleccion = cliente_chroma.get_collection(name=NOMBRE_COLECCION)
    
    # --- Inicialización de la base de datos para registro de preguntas ---
    init_mysql_database()
    
    print(f"Conexión con OpenAI y Chroma DB establecida. {coleccion.count()} documentos cargados en la colección.")
    if coleccion.count() == 0:
        print("ADVERTENCIA: La base de datos vectorial está vacía.")
        
except Exception as e:
    print(f"Error fatal al inicializar los clientes o la base de datos: {e}")
    exit()

# ==============================================================================
# 3. FUNCIONES DE SERVICIO (LÓGICA RAG)
# ==============================================================================

def seleccionar_politica_con_llm(pregunta_usuario):
    """
    Usa un LLM para determinar qué política es la más relevante.
    Si no encuentra ninguna, devuelve None.
    """
    print(f"Usando LLM para enrutar la pregunta: '{pregunta_usuario}'")

    lista_politicas_formateada = "\n".join(
        [f"- {nombre}: {desc}" for nombre, desc in POLITICAS_CON_DESCRIPCION.items()]
    )

    prompt_enrutador = f"""
    Tu única tarea es actuar como un clasificador de documentos.
    Lee la pregunta del usuario y decide cuál de los siguientes documentos es el más relevante 
    para encontrar la respuesta basándote en su descripción.

    Documentos disponibles:
    {lista_politicas_formateada}

    Pregunta del usuario: "{pregunta_usuario}"

    Responde únicamente con el nombre exacto del archivo del documento más relevante. 
    Si ninguno de los documentos parece relevante para la pregunta, responde con la palabra 'N/A'.
    """
    try:
        response = cliente_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt_enrutador}],
            temperature=0.0
        )
        respuesta_llm = response.choices[0].message.content.strip()
        print(f"   Respuesta del LLM enrutador: '{respuesta_llm}'")
        
        # Comprobar si el LLM devolvió un nombre de política válido
        for nombre in NOMBRES_POLITICAS:
            if nombre in respuesta_llm:
                print(f"   Política seleccionada: '{nombre}'")
                return nombre
        
        # Si el LLM devolvió 'N/A' o algo irreconocible, no se encontró una política.
        print("   El LLM no identificó una política relevante.")
        return None # Devolvemos None explícitamente

    except Exception as e:
        print(f"   Error en llamada al LLM enrutador: {e}. No se pudo seleccionar política.")
        return None # También devolvemos None en caso de error

def buscar_contexto_relevante(pregunta, nombre_politica, n_resultados=5):
    """Busca los chunks más relevantes para una pregunta dentro de una política específica."""
    print(f"Buscando contexto para la pregunta en la política '{nombre_politica}'...")
    embedding_pregunta = embeddings_model.embed_query(pregunta)

    # Filtra la búsqueda para que solo considere la política seleccionada
    resultados = coleccion.query(
        query_embeddings=[embedding_pregunta],
        n_results=n_resultados,
        where={"source": nombre_politica},
        include=["documents"]
    )
    
    documentos_relevantes = resultados['documents'][0] if resultados['documents'] else []
    print(f"   Se encontraron {len(documentos_relevantes)} chunks de contexto relevantes.")
    return documentos_relevantes

# ==============================================================================
# 4. LÓGICA PRINCIPAL DEL AGENTE (CHAT CON RAG)
# ==============================================================================
def chat_con_rag(message, history):
    """
    Función principal que maneja la conversación, aplicando RAG y el uso de herramientas.
    """
    MAX_TOOL_ITERATIONS = 10  # Límite de iteraciones para evitar loops infinitos
    
    # 1. Determinar la política más relevante para la pregunta del usuario
    politica_seleccionada = seleccionar_politica_con_llm(message)

    if not politica_seleccionada:
        respuesta_final = "Lo siento, no he podido encontrar información sobre tu consulta en las políticas disponibles. ¿Podrías reformular tu pregunta o ser más específico?"
        
        # Crear el mensaje de herramienta
        tool_message = {
            "id": "call_registrar",
            "type": "function",
            "function": {
                "name": "registrar_pregunta_mysql",
                "arguments": json.dumps({
                    "pregunta": message,
                    "respuesta": respuesta_final,
                    "contexto_usado": "No se encontró política relevante.",
                    "se_encontro_contexto": False
                })
            }
        }
        
        # Registrar la pregunta
        try:
            handle_tool_calls([tool_message])
            print("Pregunta sin política relevante registrada en la base de datos.")
        except Exception as e:
            print(f"Error al registrar pregunta: {e}")
        
        return respuesta_final

    
    # 2. Buscar contexto relevante en la base de datos vectorial
    contexto_relevante = buscar_contexto_relevante(message, politica_seleccionada, n_resultados=5)
    
    se_encontro_contexto = bool(contexto_relevante)

    if not se_encontro_contexto:
        contexto_concatenado = "No se encontró información relevante en los documentos."
        print("Advertencia: No se pudo recuperar contexto relevante para esta pregunta.")
    else:
        contexto_concatenado = "\n\n---\n\n".join(contexto_relevante)

    # 3. Construir el prompt del sistema con el contexto recuperado
    system_prompt = f"""
Eres un asistente de Recursos Humanos experto de la empresa Cramer.

**MISIÓN PRINCIPAL:**
Tu misión principal e ineludible es responder a la pregunta del usuario basándote ESTRICTA Y ÚNICAMENTE en el CONTEXTO proporcionado a continuación.

---
**CONTEXTO DISPONIBLE (extraído de '{politica_seleccionada}'):**
{contexto_concatenado}
---

**REGLAS DE PROCESAMIENTO Y RESPUESTA:**

1.  **ANALIZA EL CONTEXTO Y FORMULA UNA RESPUESTA:**
    -   **Si encuentras la respuesta en el contexto:** Formula una respuesta clara, directa y profesional.
    -   **Si el contexto NO es suficiente para responder:** Formula la siguiente respuesta: "No poseo información específica sobre lo que consultas. Para escalar tu pregunta al equipo de Recursos Humanos, ¿podrías indicarme tu nombre y RUT por favor?".

2.  **REGISTRA LA CONSULTA:**
    -   Después de formular la respuesta (sea positiva o negativa), DEBES invocar la herramienta `registrar_pregunta_mysql`.
    -   Usa la respuesta que formulaste en el paso anterior para el parámetro `respuesta` de la herramienta.

3.  **RESPONDE AL USUARIO:**
    -   Una vez completado el registro, entrega al usuario la respuesta que formulaste. No menciones el proceso de registro.

**GESTIÓN DE CONSULTAS SIN RESPUESTA (SEGUNDO TURNO):**
-   Si en el turno anterior le pediste al usuario su nombre/RUT y ahora te los está proporcionando, tu única acción es usar la herramienta `enviar_email_rrhh` con la pregunta original y los datos del usuario. Luego, agradécele y confirma que su consulta fue enviada.
"""
    
    # 4. Formatear el historial de la conversación
    history_openai_format = []
    for user, assistant in history:
        history_openai_format.append({"role": "user", "content": user})
        history_openai_format.append({"role": "assistant", "content": assistant})

    # 5. Construir el mensaje inicial para el LLM
    messages = [
        {"role": "system", "content": system_prompt},
        *history_openai_format,
        {"role": "user", "content": message}
    ]

    # 6. Bucle de conversación para manejar las llamadas a herramientas
    iteration = 0
    while iteration < MAX_TOOL_ITERATIONS:
        try:
            response = cliente_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.1,
                tools=TOOLS_JSON,
                tool_choice="auto"
            )
            
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            if not tool_calls:
                # No hay más herramientas que ejecutar, retornar la respuesta
                return response_message.content

            # Ejecutar las herramientas
            messages.append(response_message)
            
            try:
                tool_outputs = handle_tool_calls(tool_calls)
                if not tool_outputs:
                    print("Advertencia: handle_tool_calls retornó una lista vacía")
                    break
                messages.extend(tool_outputs)
            except Exception as e:
                print(f"Error al ejecutar herramientas: {e}")
                return f"Error al procesar tu solicitud: {str(e)}"
            
            iteration += 1

        except Exception as e:
            print(f"Error en iteración {iteration} del bucle de herramientas: {e}")
            return f"Error al procesar tu pregunta: {str(e)}"

    # Si se alcanza el límite de iteraciones
    if iteration >= MAX_TOOL_ITERATIONS:
        print(f"Advertencia: Se alcanzó el límite de {MAX_TOOL_ITERATIONS} iteraciones de herramientas")
        return "Hubo un problema procesando tu pregunta. Por favor, intenta de nuevo."
    
    return "No se pudo generar una respuesta."
# ==============================================================================
# 5. APLICACIÓN FASTAPI Y ENDPOINTS WEBHOOK
# ==============================================================================
app = FastAPI()

# --- Endpoint de Verificación (GET) ---
@app.get("/webhook")
def verify_webhook(request: Request):
    """
    Verifica la URL del webhook con Meta. Se llama una sola vez.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        print("WEBHOOK VERIFICADO")
        return Response(content=challenge, status_code=200)
    else:
        print("ERROR DE VERIFICACIÓN DE WEBHOOK")
        return Response(status_code=403)

# --- Endpoint de Recepción de Mensajes (POST) ---
@app.post("/webhook")
async def receive_message(request: Request):
    """
    Se activa cada vez que un usuario envía un mensaje de WhatsApp.
    """
    body = await request.json()
    print("Cuerpo de la petición recibida:")
    print(json.dumps(body, indent=2))

    try:
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        
        # Verificar si es un mensaje de texto real y no un status
        if "messages" in value and len(value["messages"]) > 0:
            message_info = value["messages"][0]
            
            # Solo procesar si es un mensaje de texto
            if message_info.get("type") == "text":
                user_phone_number = message_info["from"]
                user_message = message_info["text"]["body"]

                print(f"Procesando mensaje de {user_phone_number}: '{user_message}'")
                chatbot_response = chat_con_rag(user_message, history=[])
                print(f"Respuesta generada para {user_phone_number}: '{chatbot_response}'")

                send_whatsapp_message(user_phone_number, chatbot_response)
            else:
                # Si no es un mensaje de texto (ej. imagen, audio, etc.), lo ignoramos
                print(f"Tipo de mensaje no-texto recibido: {message_info.get('type')}, ignorando.")
        else:
            # Si no hay "messages" es un evento de estado (read, delivered, sent, etc.)
            print("Evento de estado o no-texto recibido, ignorando.")

    except (IndexError, KeyError) as e:
        # Si el payload no tiene el formato esperado, lo ignoramos.
        print(f"Evento no procesado (formato inesperado): {e}")
        pass

    return Response(status_code=200)

# ==============================================================================
# 6. FUNCIÓN PARA ENVIAR MENSAJES DE WHATSAPP
# ==============================================================================
def send_whatsapp_message(to_number: str, message: str, retries=3, delay=2):
    """
    Envía un mensaje de respuesta usando la API de Meta.
    """
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
    
    for attempt in range(retries):
        try:
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            print(f"Respuesta enviada a {to_number} exitosamente.")
            return # Si tiene éxito, salimos de la función
        except requests.exceptions.RequestException as e:
            print(f"Error en el intento {attempt + 1} de {retries}: {e}")
            if attempt < retries - 1:
                time.sleep(delay) # Esperar antes de reintentar
            else:
                print("Se alcanzó el número máximo de reintentos. El mensaje no se pudo enviar.")



# ==============================================================================
# 3. FUNCIONES DE SERVICIO (LÓGICA RAG)
# ==============================================================================

# def seleccionar_politica_con_llm(pregunta_usuario):
#     """Usa un LLM para determinar qué política es la más relevante."""
#     print(f"Usando LLM para enrutar la pregunta: '{pregunta_usuario}'")
#     lista_politicas_formateada = "\n".join([f"- {nombre}" for nombre in NOMBRES_POLITICAS])
    
#     prompt_enrutador = f"""
#     Tu única tarea es actuar como un clasificador de documentos. Lee la pregunta del usuario y decide
#     cuál de los siguientes documentos es el más relevante para encontrar la respuesta.

#     Documentos disponibles:
#     {lista_politicas_formateada}

#     Pregunta del usuario: "{pregunta_usuario}"

#     Responde únicamente con el nombre exacto del archivo del documento más relevante.
#     """
#     try:
#         response = cliente_openai.chat.completions.create(
#             model="gpt-4o-mini",
#             messages=[{"role": "system", "content": prompt_enrutador}],
#             temperature=0.0
#         )
#         respuesta_llm = response.choices[0].message.content.strip()
#         print(f"   Respuesta del LLM enrutador: '{respuesta_llm}'")
        
#         for nombre in NOMBRES_POLITICAS:
#             if nombre in respuesta_llm:
#                 print(f"   Política seleccionada: '{nombre}'")
#                 return nombre
        
#         print("   El LLM no devolvió un nombre reconocible. Usando la primera política como fallback.")
#         return NOMBRES_POLITICAS[0]
#     except Exception as e:
#         print(f"   Error en llamada al LLM enrutador: {e}. Usando fallback.")
#         return NOMBRES_POLITICAS[0]


# def buscar_contexto_relevante(pregunta, nombre_politica, n_resultados=5):
#     """Busca los chunks más relevantes para una pregunta dentro de una política específica."""
#     print(f"Buscando contexto para la pregunta en la política '{nombre_politica}'...")
#     embedding_pregunta = embeddings_model.embed_query(pregunta)

#     # Filtra la búsqueda para que solo considere la política seleccionada
#     resultados = coleccion.query(
#         query_embeddings=[embedding_pregunta],
#         n_results=n_resultados,
#         where={"source": nombre_politica},
#         include=["documents"]
#     )
    
#     documentos_relevantes = resultados['documents'][0] if resultados['documents'] else []
#     print(f"   Se encontraron {len(documentos_relevantes)} chunks de contexto relevantes.")
#     return documentos_relevantes

# # ==============================================================================
# # 4. LÓGICA PRINCIPAL DEL AGENTE (CHAT CON RAG)
# # ==============================================================================
# def chat_con_rag(message, history):
#     """
#     Función principal que maneja la conversación, aplicando RAG y el uso de herramientas.
#     """
#     # 1. Determinar la política más relevante para la pregunta del usuario
#     politica_seleccionada = seleccionar_politica_con_llm(message)
    
#     # 2. Buscar contexto relevante en la base de datos vectorial
#     contexto_relevante = buscar_contexto_relevante(message, politica_seleccionada, n_resultados=5)
    
#     # NUEVO: Determinar si se encontró contexto para el registro
#     se_encontro_contexto = bool(contexto_relevante)

#     if not se_encontro_contexto:
#         contexto_concatenado = "No se encontró información relevante en los documentos."
#         print("Advertencia: No se pudo recuperar contexto relevante para esta pregunta.")
#     else:
#         contexto_concatenado = "\n\n---\n\n".join(contexto_relevante)

#     # 3. Construir el prompt del sistema con el contexto recuperado (VERSIÓN CORREGIDA)
#     system_prompt = f"""
# Eres un asistente de Recursos Humanos experto de la empresa Cramer.

# **MISIÓN PRINCIPAL:**
# Tu misión principal e ineludible es responder a la pregunta del usuario basándote ESTRICTA Y ÚNICAMENTE en el CONTEXTO proporcionado a continuación.

# ---
# **CONTEXTO DISPONIBLE (extraído de '{politica_seleccionada}'):**
# {contexto_concatenado}
# ---

# **REGLAS DE PROCESAMIENTO Y RESPUESTA:**

# 1.  **ANALIZA EL CONTEXTO Y FORMULA UNA RESPUESTA:**
#     -   **Si encuentras la respuesta en el contexto:** Formula una respuesta clara, directa y profesional.
#     -   **Si el contexto NO es suficiente para responder:** Formula la siguiente respuesta: "No poseo información específica sobre lo que consultas. Para escalar tu pregunta al equipo de Recursos Humanos, ¿podrías indicarme tu nombre y RUT por favor?".

# 2.  **REGISTRA LA CONSULTA:**
#     -   Después de formular la respuesta (sea positiva o negativa), DEBES invocar la herramienta `registrar_pregunta_mysql`.
#     -   Usa la respuesta que formulaste en el paso anterior para el parámetro `respuesta` de la herramienta.

# 3.  **RESPONDE AL USUARIO:**
#     -   Una vez completado el registro, entrega al usuario la respuesta que formulaste. No menciones el proceso de registro.

# **GESTIÓN DE CONSULTAS SIN RESPUESTA (SEGUNDO TURNO):**
# -   Si en el turno anterior le pediste al usuario su nombre/RUT y ahora te los está proporcionando, tu única acción es usar la herramienta `enviar_email_rrhh` con la pregunta original y los datos del usuario. Luego, agradécele y confirma que su consulta fue enviada.
# """
    
#     # 4. Formatear el historial de la conversación (sin cambios)
#     history_openai_format = []
#     for user, assistant in history:
#         history_openai_format.append({"role": "user", "content": user})
#         history_openai_format.append({"role": "assistant", "content": assistant})

#     # 5. Construir el mensaje inicial para el LLM (sin cambios)
#     messages = [
#         {"role": "system", "content": system_prompt},
#         *history_openai_format,
#         {"role": "user", "content": message}
#     ]

#     # 6. Bucle de conversación para manejar las llamadas a herramientas (sin cambios)
#     while True:
#         response = cliente_openai.chat.completions.create(
#             model="gpt-4o-mini",
#             messages=messages,
#             temperature=0.1,
#             tools=TOOLS_JSON,
#             tool_choice="auto"
#         )
        
#         response_message = response.choices[0].message
#         tool_calls = response_message.tool_calls

#         if not tool_calls:
#             return response_message.content

#         messages.append(response_message)
#         tool_outputs = handle_tool_calls(tool_calls)
#         messages.extend(tool_outputs)
