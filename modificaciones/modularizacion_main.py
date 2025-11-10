"""
AGENTE RRHH - MODULARIZADO
"""
import os
import json
import requests
from fastapi import FastAPI, Request, Response
import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
import time
from modulacion_tools import (
    TOOLS_JSON_MANAGER, 
    handle_tool_calls, 
    obtener_historial_bd,    
    guardar_interaccion_bd   
)
load_dotenv(override=True)

try:
    cliente_openai = OpenAI()
    embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")    
    print(f"Conexión con OpenAI establecida.")
        
except Exception as e:
    print(f"Error al inicializar los clientes")
    exit()
    
    
# -------------- LÓGICA ORQUESTADOR (MANAGER) -------------- 
def orquestador_agent(user_message, history):
    """
    Orquestador de la interacción con el usuario.
    """
    # Agregar estado de workflow
    workflow_state = {
        "politica_seleccionada": None,
        "contexto_obtenido": False,
        "respuesta_generada": False
    }
    INSTRUCCIONES_ORQUESTADOR = """
    Estado actual del workflow: {json.dumps(workflow_state)}
    
    REGLAS ESTRICTAS:
    1. Si politica_seleccionada es None → llama seleccionar_politica
    2. Si politica_seleccionada existe y contexto_obtenido es False → llama buscar_contexto
    3. Solo llama generar_respuesta cuando tengas el contexto O sea saludo/charla
    
    Eres el Orquestador central del sistema de RRHH de Cramer.
    TU FLUJO DE TRABAJO:
    - Si es saludo/charla: Llama DIRECTAMENTE a `generar_respuesta` con el mensaje del usuario.
    - Si es pregunta de política o beneficio: 
        1) Usa `seleccionar_politica`, Si seleccionar_politica devuelve "sin_coincidencias" → NO llames buscar_contexto, genera respuesta inmediatamente 
        2) Luego `buscar_contexto`,  Si buscar_contexto devuelve lista vacía → mismo flujo que sin_coincidencias
        3) Posteriormente, llama a `generar_respuesta` pasando el contexto encontrado.
    - Finaliza el turno registrando la interacción con `registrar_pregunta`, en caso que no se haya logrado una
    respuesta satisfactoria al usuario usa 'enviar_email_rrhh' para comunicarse con la persona.
    """

    MAX_TOOL_ITERATIONS = 10
    
    messages = [{"role": "system", "content": INSTRUCCIONES_ORQUESTADOR}]
    for user_msg, assistant_msg in history:
        messages.append({"role": "user", "content": user_msg})
        messages.append({"role": "assistant", "content": assistant_msg})
    messages.append({"role": "user", "content": user_message})

    iteration = 0
    respuesta_final_para_usuario = None 

    while iteration < MAX_TOOL_ITERATIONS:
        try:
            INSTRUCCIONES_ACTUALIZADAS = INSTRUCCIONES_ORQUESTADOR + f"\nESTADO ACTUAL DEL WORKFLOW: {json.dumps(workflow_state)}"
            if messages[0]["role"] == "system":
                messages[0]["content"] = INSTRUCCIONES_ACTUALIZADAS
                
            response = cliente_openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                temperature=0.0,
                tools=TOOLS_JSON_MANAGER,
                tool_choice="auto" 
            )
            
            response_message = response.choices[0].message
            tool_calls = response_message.tool_calls

            # --- EJECUCIÓN DE HERRAMIENTAS ---
            messages.append(response_message)
            tool_outputs = handle_tool_calls(tool_calls)
            messages.extend(tool_outputs)
            
            for output in tool_outputs:
                tool_name = output['name']
                tool_result = output['content']

                if tool_name == "seleccionar_politica_con_llm":
                    workflow_state["politica_seleccionada"] = tool_result
                    # Si no hay coincidencias, podemos marcar contexto como 'saltado' o manejado
                    if tool_result == "sin_coincidencias":
                        workflow_state["contexto_obtenido"] = "N/A" 

                elif tool_name == "buscar_contexto_relevante":
                    workflow_state["contexto_obtenido"] = True

                elif tool_name == "generar_respuesta":
                    workflow_state["respuesta_generada"] = True

            iteration += 1

        except Exception as e:
            print(f"Error en orquestador: {e}")
            return "Ocurrió un error interno procesando tu solicitud."

    return respuesta_final_para_usuario if respuesta_final_para_usuario else "No se pudo generar una respuesta final."

    
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

                history = obtener_historial_bd(user_phone_number, limite=6) 
                print(f"Historial recuperado de BD: {len(history)} turnos previos")

                chatbot_response = orquestador_agent(user_message, history=history)
                print(f"Respuesta generada para {user_phone_number}: '{chatbot_response}'")

                guardar_interaccion_bd(user_phone_number, user_message, chatbot_response)

                send_whatsapp_message(user_phone_number, chatbot_response)
            else:
                print(f"Tipo de mensaje no-texto recibido: {message_info.get('type')}, ignorando.")
        else:
            print("Evento de estado o no-texto recibido, ignorando.")

    except (IndexError, KeyError) as e:
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
