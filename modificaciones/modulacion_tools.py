'''
Módulo de herramientas para el agente de RRHH.
Contiene todas las definiciones de tools y sus handlers.

pendientes:
    - CONEXIONES A LA BASE DE DATOS DE SNOWFLAKE  
'''
import json
import os
import pythoncom
import win32com.client as win32
from datetime import datetime

from config import (
    MYSQL_CONFIG, 
    cliente_openai, 
    EMBEDDINGS_MODEL, 
    COLECCION_CHROMA,
    POLITICAS_CON_DESCRIPCION,
    NOMBRES_POLITICAS
)


# -------------- CONFIGURACIÓN E INICIALIZACIÓN --------------

# CREATE TABLE IF NOT EXISTS whatsapp_chat_history (
#     id INT AUTO_INCREMENT PRIMARY KEY,
#     phone_number VARCHAR(25) NOT NULL,
#     user_message TEXT NOT NULL,
#     bot_message TEXT NOT NULL,
#     created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
#     INDEX idx_phone (phone_number)
# );

# -------------- TOOLS --------------  
def registrar_pregunta(pregunta, politica="No especificada", contexto_encontrado=True, respuesta="", notas=""):
    """
    Registra las preguntas realizadas por el usuario en la base de datos MySQL.
    """
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO question_agent_ia
            (question, file_consulted, contexts, 
             answer_ia, notes)
            VALUES ( %s, %s, %s, %s, %s)
        """
        valores = (pregunta, politica, contexto_encontrado, respuesta, notas)
        
        cursor.execute(query, valores)
        conn.commit()
        
        registro_id = cursor.lastrowid
        cursor.close()
        conn.close()
        
        print(f"Pregunta registrada en MySQL con ID: {registro_id}")
        return {
            "status": "ok", 
            "message": "Pregunta registrada exitosamente",
            "id": registro_id
        }
    except Exception as e:
        print(f"✗ Error al registrar en MySQL: {e}")
        return {
            "status": "error",
            "message": f"Error al registrar: {str(e)}"
        }

#CONFIGURACIÓN DE JSON - REGISTRAR_PREGUNTA        
registrar_pregunta_json = {
    "type": "function",
    "function": {
        "name": "registrar_pregunta",
        "description": "Registra la pregunta del usuario en la base de datos para seguimiento posterior.",
        "parameters": {
            "type": "object",
            "properties": {
                "pregunta": {
                    "type": "string",
                    "description": "La pregunta que hizo el usuario."
                },
                "politica": {
                    "type": "string",
                    "description": "La política consultada (ej. mutuo_acuerdo.pdf)"
                },
                "contexto_encontrado": {
                    "type": "boolean",
                    "description": "Si se encontró contexto relevante o no."
                },
                "respuesta": {
                    "type": "string",
                    "description": "respuesta que se entregó al usuario."
                },
                "notas": {
                    "type": "string",
                    "description": "Notas adicionales sobre la consulta."
                }
            },
            "required": ["pregunta"],
        }
    }
}

def enviar_email_rrhh(asunto="", contexto_encontrado=False, pregunta="", rut_usuario="", nombre_usuario=""):
    """
    envía un correo electrónico al departamento de RRHH usando Outlook local (CAMBIAR).
    """
    try:
        print("Redactando el correo...")
        
        pythoncom.CoInitialize()
        
        try:
            outlook = win32.Dispatch('outlook.application')
            mail = outlook.CreateItem(0)

            emails = os.getenv("EMAIL_RRHH")
            destinatarios = [e.strip() for e in emails.split(",") if e.strip()]

            mail.To = "; ".join(destinatarios)
            mail.Subject = asunto
            cuerpo = f"""
Consulta recogida desde el Chatbot de WTSP:
De: {nombre_usuario if nombre_usuario else 'Usuario anónimo'}
Rut: {rut_usuario if rut_usuario else 'No proporcionado'}

Pregunta:
{pregunta}

---
Este mensaje fue enviado automáticamente.
Fecha: {datetime.now().strftime('%d/%m/%Y')}
"""
            mail.Body = cuerpo
            mail.Send()
            
            print(f"✓ Email enviado a RRHH")
            return {
                "status": "ok",
                "message": "Email enviado exitosamente a RRHH"
            }
        finally:
            pythoncom.CoUninitialize()
            
    except Exception as e:
        print(f"Error al enviar email: {e}")
        return {
            "status": "error",
            "message": f"No se pudo enviar el email: {str(e)}"
        }

#CONFIGURACIÓN DE JSON - CORREO RRHH  
enviar_email_rrhh_json = {
    "type": "function",
    "function": {
        "name": "enviar_email_rrhh",
        "description": "Envía un correo electrónico al departamento de RRHH con una pregunta del usuario que no pudo ser respondida. Esta herramienta se usa después de haberle solicitado sus datos al usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "asunto": {
                    "type": "string",
                    "description": "Un asunto breve y descriptivo para el correo."
                },
                "pregunta": {  
                    "type": "string",
                    "description": "La pregunta original y completa que hizo el usuario y que no se logro responder."
                },
                "rut_usuario": {  
                    "type": "string",
                    "description": "El RUT del usuario, si lo proporcionó."
                },
                "nombre_usuario": {
                    "type": "string",
                    "description": "El nombre del usuario, si lo proporcionó."
                }
            },
            "required": ["asunto", "pregunta"],
        }
    }
}

def seleccionar_politica_con_llm(pregunta_usuario):
    """
    Usa un modelo de lenguaje para determinar qué política es la más relevante.
    """
    lista_politicas_formateada = "\n".join(
        [f"- {nombre}: {desc}" for nombre, desc in POLITICAS_CON_DESCRIPCION.items()]
    )

    prompt_enrutador = f"""
Tu única tarea es actuar como un clasificador de documentos.
Lee la pregunta del usuario y decide cuál de los documentos es el más relevante 
para encontrar la respuesta basándote en su descripción.

Documentos disponibles:
{lista_politicas_formateada}

Pregunta del usuario: "{pregunta_usuario}"

IMPORTANTE: 
- Si ningún documento es relevante, responde EXACTAMENTE: "sin_coincidencias"
- Si un documento es relevante, responde SOLO con su nombre exacto (ej: "beca_estudio.pdf")
- NO agregues explicaciones, solo el nombre del archivo.
"""
    try:
        response = cliente_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": prompt_enrutador}],
            temperature=0.0
        )
        respuesta_llm = response.choices[0].message.content.strip()
        print(f"   📄 Respuesta del LLM enrutador: '{respuesta_llm}'")
        
        # ✅ Normalizar respuesta
        respuesta_llm_normalizada = respuesta_llm.lower().replace(" ", "_")
        
        # Buscar coincidencia exacta
        for nombre in NOMBRES_POLITICAS:
            if nombre.lower() in respuesta_llm_normalizada:
                print(f"Política seleccionada: '{nombre}'")
                return nombre
        
        print("   ⚠️ El LLM no identificó una política relevante.")
        return "sin_coincidencias"  

    except Exception as e:
        print(f"   ❌ Error en llamada al LLM enrutador: {e}")
        return "sin_coincidencias"



#CONFIGURACIÓN DE JSON - SELECCIONADOR POLITICA  
seleccionar_politica_json = {
    "type": "function",
    "function": {
        "name": "seleccionar_politica_con_llm",
        "description": "Usa un modelo de lenguaje para determinar qué política es la más relevante para responder a la pregunta del usuario.",
        "parameters": {
            "type": "object",
            "properties": {
                "pregunta_usuario": {
                    "type": "string",
                    "description": "la pregunta que realiza el usuario."
                }
            },
            "required": ["pregunta_usuario"],
        }
    }
}
    
def buscar_contexto_relevante(pregunta, nombre_politica, n_resultados=5):
    """
    Busca los chunks más relevantes para una pregunta dentro de una política específica.
    """
    embedding_pregunta = EMBEDDINGS_MODEL.embed_query(pregunta)

    resultados = COLECCION_CHROMA.query(
        query_embeddings=[embedding_pregunta],
        n_results=n_resultados,
        where={"source": nombre_politica},
        include=["documents"]
    ) 
    documentos_relevantes = resultados['documents'][0] if resultados['documents'] else []
    print(f"Se encontraron {len(documentos_relevantes)} chunks relevantes.")
    
    return documentos_relevantes

#CONFIGURACIÓN DE JSON - BUSCA CONTEXTO RELEVANTE PARA RESPONDER 
busca_contexto_json = {
    "type": "function",
    "function": {
        "name": "buscar_contexto_relevante", 
        "description": "Busca los chunks más relevantes para responder a una pregunta dentro de un documento específico.",
        "parameters": {
            "type": "object",
            "properties": {
                "pregunta": { 
                    "type": "string",
                    "description": "la pregunta que realiza el usuario."
                },
                "nombre_politica": {
                    "type": "string",
                    "description": "el nombre del documento donde se debe realizar la búsqueda"
                }
            },
            "required": ["pregunta", "nombre_politica"], 
        }
    }
}

def generar_respuesta(pregunta_usuario: str, contexto: str = "", tipo_interaccion: str = "consulta"):
    """
    Genera la respuesta para enviar al usuario por WhatsApp.
    """
    try:
        system_prompt = """
Eres el asistente virtual de RRHH de Cramer. 
Tu objetivo es redactar una respuesta cordial, profesional y útil para enviar por WhatsApp.

DIRECTRICES:
- Si recibes 'contexto', úsalo para responder la 'pregunta_usuario' con precisión. Cita la política si es relevante.
- Si NO recibes 'contexto' y es una pregunta de RRHH, di honestamente que no tienes esa información a mano y ofrece escalar la consulta.
- Si 'tipo_interaccion' es 'saludo' o 'charla', responde amigable y brevemente, recordando que estás para temas de RRHH.
- Si 'tipo_interaccion' es 'error', explica que no tienes esa información y pregunta si desea que su consulta sea enviada a RRHH.
- Usa formato WhatsApp (*negritas*, emojis) para dar claridad, pero sin exagerar.
- Mantén la respuesta concisa, ideal para lectura rápida en móvil.
- NUNCA menciones "chunks", "base de datos", o términos técnicos.
"""
        
        user_prompt = f"""
TIPO INTERACCIÓN: {tipo_interaccion}
PREGUNTA ORIGINAL DEL USUARIO: "{pregunta_usuario}"
CONTEXTO ENCONTRADO (puede estar vacío):
{contexto if contexto else "[Sin contexto disponible]"}

Genera la respuesta final para WhatsApp (solo el texto, sin metadatos):
"""

        response = cliente_openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3
        )
        
        respuesta = response.choices[0].message.content
        print(f"✅ Respuesta generada: {respuesta[:100]}...")
        
        # ✅ Retornar string limpio (sin JSON)
        return respuesta
        
    except Exception as e:
        print(f"❌ Error generando respuesta final: {e}")
        return "Disculpa, hay un problema técnico. Intenta nuevamente más tarde por favor."

generar_respuesta_json = {
    "type": "function",
    "function": {
        "name": "generar_respuesta",
        "description": "herramienta para comunicarse con el usuario. Úsala siempre que necesites enviar un mensaje de texto, ya sea una respuesta basada en políticas, un saludo, o una despedida.",
        "parameters": {
            "type": "object",
            "properties": {
                "pregunta_usuario": {
                    "type": "string",
                    "description": "La pregunta o mensaje original del usuario."
                },
                "contexto": {
                    "type": "string",
                    "description": "El texto de los documentos encontrados (si aplica). Dejar vacío si es solo charla."
                },
                "tipo_interaccion": {
                    "type": "string",
                    "enum": ["consulta", "saludo", "charla", "despedida", "error"],
                    "description": "Define el tono de la respuesta. Usa 'consulta' por defecto para preguntas de políticas."
                }
            },
            "required": ["pregunta_usuario"]
        }
    }
}   

# -------------- DEFINICIONES JSON DE HERRAMIENTAS (TOOL SCHEMAS) --------------
TOOLS_JSON_MANAGER = [
    registrar_pregunta_json,
    enviar_email_rrhh_json,
    seleccionar_politica_json,
    busca_contexto_json,
    generar_respuesta_json
]

# -------------- FUNCIONES EJECUTABLES --------------
AVAILABLE_TOOLS = {
    'registrar_pregunta': registrar_pregunta,
    'enviar_email_rrhh': enviar_email_rrhh,
    'seleccionar_politica_con_llm': seleccionar_politica_con_llm,
    'buscar_contexto_relevante': buscar_contexto_relevante, 
    'generar_respuesta': generar_respuesta 
}

# --- AGREGAR AL FINAL DE modulacion_tools.py ---
def obtener_historial_bd(phone_number, limite=6):
    """
    Recupera los últimos 'limite' pares de interacción para un número.
    Devuelve una lista de tuplas: [(user_msg, bot_msg), ...] en orden cronológico.
    """
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        query = """
            SELECT user_message, bot_message 
            FROM (
                SELECT user_message, bot_message, created_at 
                FROM whatsapp_chat_history 
                WHERE phone_number = %s 
                ORDER BY created_at DESC 
                LIMIT %s
            ) AS sub
            ORDER BY created_at ASC
        """
        cursor.execute(query, (phone_number, limite))
        result = cursor.fetchall()
        
        cursor.close()
        conn.close()
        return result
    except Exception as e:
        print(f"Error obteniendo historial de BD: {e}")
        return []

def guardar_interaccion_bd(phone_number, user_msg, bot_msg):
    """
    Guarda un par de interacción (pregunta usuario / respuesta bot) en la BD.
    """
    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO whatsapp_chat_history (phone_number, user_message, bot_message)
            VALUES (%s, %s, %s)
        """
        cursor.execute(query, (phone_number, user_msg, bot_msg))
        conn.commit()
        
        cursor.close()
        conn.close()
        print(f"Interacción guardada en BD para {phone_number}")
        return True
    except Exception as e:
        print(f"Error guardando interacción en BD: {e}")
        return False

# --------------  HERRAMIENTAS -------------- 
def handle_tool_calls(tool_calls):
    """
    Manejador para ejecutar las llamadas a las herramientas solicitadas por el LLM.    
    """
    tool_outputs = []
    
    for tool_call in tool_calls:
        function_name = tool_call.function.name
        function_to_call = AVAILABLE_TOOLS.get(function_name)
        
        if not function_to_call:
            print(f"❌ ERROR: Herramienta '{function_name}' no existe en AVAILABLE_TOOLS")
            print(f"   Herramientas disponibles: {list(AVAILABLE_TOOLS.keys())}")
            tool_outputs.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": json.dumps({
                    "error": f"La herramienta '{function_name}' no existe.",
                    "available_tools": list(AVAILABLE_TOOLS.keys())
                }),
            })
            continue

        try:
            function_args = json.loads(tool_call.function.arguments)
            print(f"🔧 Ejecutando: {function_name}")
            print(f"   Argumentos: {function_args}")
            
            function_response = function_to_call(**function_args)
            
            print(f"   ✅ Resultado: {str(function_response)[:100]}...")
            
            tool_outputs.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": json.dumps(function_response) if not isinstance(function_response, str) else function_response,
            })
            
        except Exception as e:
            print(f"❌ Error al ejecutar {function_name}: {e}")
            import traceback
            traceback.print_exc()
            
            tool_outputs.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": json.dumps({
                    "error": f"Error al ejecutar la herramienta: {str(e)}",
                    "type": type(e).__name__
                }),
            })
    
    return tool_outputs

