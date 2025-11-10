import os
from dotenv import load_dotenv
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings
import chromadb
import mysql.connector

# 1. Cargar variables de entorno una sola vez
load_dotenv(override=True)

# 2. Configuraciones Globales
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', 'localhost'),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'database': os.getenv('MYSQL_DATABASE'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'connection_timeout': 10
}
cliente_openai = OpenAI()
DB_PATH = "db_politicas"
NOMBRE_COLECCION = "politicas_empresariales"

DB_PATH = "db_politicas"
NOMBRE_COLECCION = "politicas_empresariales"
POLITICAS_CON_DESCRIPCION = {
    "sin_coincidencias": "no se encontro ninguna coincidencia, responde que no conoces la respuesta a su consulta",
    "beca_estudio.pdf": "Contiene información sobre beneficios y becas para estudios superiores para los empleados y sus familias.",
    "centro_recreación.pdf": "Describe las reglas para pertenecer al centro de recreación de la empresa.",
    "mutuo_acuerdo.pdf": "Explica los procedimientos y condiciones para la terminación del contrato laboral de mutuo acuerdo."
}
NOMBRES_POLITICAS = [p for p in POLITICAS_CON_DESCRIPCION.keys() if p != "sin_coincidencias"]
try:
    EMBEDDINGS_MODEL = OpenAIEmbeddings(model="text-embedding-3-small")
    CLIENTE_CHROMA = chromadb.PersistentClient(path=DB_PATH)
    COLECCION_CHROMA = CLIENTE_CHROMA.get_collection(name=NOMBRE_COLECCION)
except Exception as e:
    print(f"Error al inicializar clientes RAG en tools: {e}")


# 3. Inicialización ÚNICA de Clientes
try:
    print("🔌 Inicializando clientes globales...")
    CLIENTE_OPENAI = OpenAI()  # Cliente único para todo el app
    EMBEDDINGS_MODEL = OpenAIEmbeddings(model="text-embedding-3-small")
    
    # ChromaDB
    _chroma_client = chromadb.PersistentClient(path=DB_PATH)
    COLECCION_CHROMA = _chroma_client.get_collection(name=NOMBRE_COLECCION)
    print("✅ Clientes inicializados correctamente.")
except Exception as e:
    print(f"❌ Error crítico inicializando clientes: {e}")