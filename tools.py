"""
Módulo de herramientas para el agente de RRHH.
Contiene todas las definiciones de tools y sus handlers.
"""

import json
import os
from datetime import datetime
import win32com.client as win32  
from datetime import datetime     
import mysql.connector    
import pythoncom
from dotenv import load_dotenv      

# ==============================================================================
# CONFIGURACIÓN
# ==============================================================================
load_dotenv(override=True)

PUSHOVER_USER = os.getenv("PUSHOVER_USER")
PUSHOVER_TOKEN = os.getenv("PUSHOVER_TOKEN")
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"


MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'database': os.getenv('MYSQL_DATABASE'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),  # Agregar puerto
    'connection_timeout': 10  # Timeout de 10 segundos
}

EMAIL_RRHH = os.getenv('EMAIL_RRHH')
# ==============================================================================
# FUNCIONES DE UTILIDAD
# ==============================================================================

def push(message):
    """
    Función de marcador de posición para enviar notificaciones.
    """
    print(f"[NOTIFICACIÓN PUSH]: {message}")

# ==============================================================================
# HERRAMIENTAS (TOOL FUNCTIONS)
# ==============================================================================

# def record_user_details(email, name="Nombre no proporcionado", notes="Sin notas"):
#     """Registra el interés de un usuario y envía una notificación."""
#     push(f"Registrando interés de {name} con email {email} y notas: '{notes}'. "
#          f"Registrado en base de datos y enviando correo.")
#     return {"status": "ok", "recorded_email": email}


# def record_unknown_question(question):
#     """Registra una pregunta que el agente no pudo responder."""
#     push(f"Registrando pregunta no respondida: '{question}'")
#     return {"status": "ok", "recorded_question": question}


def registrar_pregunta_mysql(pregunta, politica="No especificada", 
                             contexto_encontrado=True, respuesta="", notas=""):
    """Registra las preguntas realizadas por el usuario en la base de datos MySQL."""
    try:
        
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cursor = conn.cursor()
        
        query = """
            INSERT INTO question_agent_ia
            (question, file_consulted, contexts, 
             answer_ia, notes)
            VALUES ( %s, %s, %s, %s, %s)
        """
        valores = (pregunta, politica, contexto_encontrado, respuesta,  notas)
        
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


def enviar_email_rrhh(asunto, pregunta, rut_usuario="", nombre_usuario="", notas=""):
    """registra y envía un correo electrónico al departamento de RRHH usando Outlook local."""
    try:
        # Registrar en MySQL primero
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
        
        print(f"Pregunta registrada en MySQL con ID: {registro_id}")
        print("Redactando el correo")



        
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
            
            print(f"✓ Email enviado a RRHH")
            return {
                "status": "ok",
                "message": "Email enviado exitosamente a RRHH"
            }
        finally:
            # Siempre liberar COM al finalizar
            pythoncom.CoUninitialize()
            
    except Exception as e:
        print(f"Error al enviar email: {e}")
        return {
            "status": "error",
            "message": f"No se pudo enviar el email: {str(e)}"
        }


# ==============================================================================
# DEFINICIONES JSON DE HERRAMIENTAS (TOOL SCHEMAS)
# ==============================================================================

# record_user_details_json = {
#     "type": "function",
#     "function": {
#         "name": "record_user_details",
#         "description": "Utiliza esta herramienta para registrar que un usuario está "
#                       "interesado en estar en contacto y proporcionó una dirección de correo electrónico.",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "email": {
#                     "type": "string", 
#                     "description": "La dirección de correo electrónico del usuario."
#                 },
#                 "name": {
#                     "type": "string", 
#                     "description": "El nombre del usuario, si lo proporcionó."
#                 },
#                 "notes": {
#                     "type": "string", 
#                     "description": "Cualquier información adicional sobre la conversación "
#                                   "que merezca ser registrada para dar contexto."
#                 }
#             },
#             "required": ["email"],
#         }
#     }
# }

# record_unknown_question_json = {
#     "type": "function",
#     "function": {
#         "name": "record_unknown_question",
#         "description": "Utiliza siempre esta herramienta para registrar cualquier pregunta "
#                       "que no puedas responder basándote en la información de la que dispones.",
#         "parameters": {
#             "type": "object",
#             "properties": {
#                 "question": {
#                     "type": "string", 
#                     "description": "La pregunta exacta que no se pudo responder."
#                 }
#             },
#             "required": ["question"],
#         }
#     }
# }

registrar_pregunta_mysql_json = {
    "type": "function",
    "function": {
        "name": "registrar_pregunta_mysql",
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
                    "description": "Un asunto breve y descriptivo para el correo. Ej: 'Consulta sobre Beneficios de Estudio'."
                },
                "pregunta": {  
                    "type": "string",
                    "description": "La pregunta original y completa que hizo el usuario y que no se pudo responder."
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



# ==============================================================================
# REGISTRO DE HERRAMIENTAS
# ==============================================================================

# Lista de definiciones JSON para el LLM
TOOLS_JSON = [
    # record_user_details_json, 
    # record_unknown_question_json,
    registrar_pregunta_mysql_json,
    enviar_email_rrhh_json
]

# Diccionario de funciones ejecutables
AVAILABLE_TOOLS = {
    # "record_user_details": record_user_details,
    # "record_unknown_question": record_unknown_question,
    "registrar_pregunta_mysql": registrar_pregunta_mysql,
    "enviar_email_rrhh": enviar_email_rrhh
}

# ==============================================================================
# HANDLER DE HERRAMIENTAS
# ==============================================================================

def handle_tool_calls(tool_calls):
    """
    Manejador para ejecutar las llamadas a las herramientas solicitadas por el LLM.    
    """
    tool_outputs = []
    
    for tool_call in tool_calls:
        function_name = tool_call.function.name
        function_to_call = AVAILABLE_TOOLS.get(function_name)
        
        if not function_to_call:
            tool_outputs.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": f"Error: La herramienta '{function_name}' no existe.",
            })
            continue

        try:
            function_args = json.loads(tool_call.function.arguments)
            print(f"Ejecutando herramienta: {function_name} con argumentos: {function_args}")
            
            function_response = function_to_call(**function_args)
            
            tool_outputs.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": json.dumps(function_response),
            })
        except Exception as e:
            print(f"Error al ejecutar la herramienta {function_name}: {e}")
            tool_outputs.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": f"Error al ejecutar la herramienta: {e}",
            })
    
    return tool_outputs


# ==============================================================================
# FUNCIONES DE INICIALIZACIÓN
# ==============================================================================
def init_mysql_database():
    """Inicializa la base de datos y crea la tabla si no existe."""
    try:
        import mysql.connector
        
        # Debug: Imprimir configuración (sin mostrar password completo)
        print("🔍 Configuración MySQL:")
        print(f"   Host: {MYSQL_CONFIG['host']}")
        print(f"   Port: {MYSQL_CONFIG['port']}")
        print(f"   User: {MYSQL_CONFIG['user']}")
        print(f"   Database: {MYSQL_CONFIG['database']}")
        print(f"   Password configurado: {'✓' if MYSQL_CONFIG['password'] else '✗'}")
        
        # Verificar que las variables estén configuradas
        if not all([MYSQL_CONFIG['host'], MYSQL_CONFIG['user'], MYSQL_CONFIG['password']]):
            print("❌ Error: Variables de entorno MySQL no configuradas correctamente")
            return False
        
        print(f"🔄 Intentando conectar a MySQL en {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}...")
        
        # Conexión inicial sin especificar base de datos
        conn = mysql.connector.connect(
            host=MYSQL_CONFIG['host'],
            user=MYSQL_CONFIG['user'],
            password=MYSQL_CONFIG['password'],
            port=MYSQL_CONFIG['port'],
            connection_timeout=MYSQL_CONFIG['connection_timeout']
        )
        cursor = conn.cursor()
        
        print("✅ Conexión a MySQL exitosa")
        
        # Crear base de datos si no existe
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {MYSQL_CONFIG['database']}")
        cursor.execute(f"USE {MYSQL_CONFIG['database']}")
        
        # Crear tabla de preguntas
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS question_agent_ia (
                id INT AUTO_INCREMENT PRIMARY KEY,
                question TEXT NOT NULL,
                file_consulted VARCHAR(255),
                contexts BOOLEAN DEFAULT FALSE,
                answer_ia TEXT,
                fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
                notes TEXT,
                INDEX idx_fecha (fecha_registro)
            )
        """)

        #crear tabla preguntas sin respuesta
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS unknown_question (
                id INT AUTO_INCREMENT PRIMARY KEY,
                pregunta TEXT NOT NULL,
                respuesta TEXT, 
                rut VARCHAR(255),
                nombre_usuario VARCHAR(255),
                fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
                notas TEXT,
                INDEX idx_fecha (fecha_registro)
            )
        """)
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print("✅ Base de datos MySQL inicializada correctamente")
        print(f"   Tablas creadas en base de datos: {MYSQL_CONFIG['database']}")
        return True
        
    except mysql.connector.Error as err:
        print(f"❌ Error de MySQL: {err}")
        print(f"   Código de error: {err.errno}")
        print(f"   Mensaje SQL: {err.msg}")
        print("  Continuando sin base de datos MySQL...")
        return False
    except Exception as e:
        print(f"❌ Error al inicializar MySQL: {e}")
        print("  Continuando sin base de datos MySQL...")
        return False


