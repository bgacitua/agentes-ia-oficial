# ==============================================================================
# Leer, procesar y cargar las políticas en la base de datos vectorial.
# ==============================================================================

import os
import fitz  # PyMuPDF
import chromadb
import numpy as np
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

print("Iniciando el proceso de vectorización de políticas...")
load_dotenv(override=True)

# Lista de políticas a procesar
RUTAS_POLITICAS = [
    "files/beca_estudio.pdf"
    #"/Users/gabrielpavez/Desktop/agents/desarrollos_propios/files/politica_vacaciones.pdf"
]
DB_PATH = "db_politicas"
NOMBRE_COLECCION = "politicas_empresariales"

# --- 2. FUNCIONES AUXILIARES ---
def cargar_y_dividir_politicas(lista_rutas):
    todos_los_splits = []
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    for ruta in lista_rutas:
        try:
            nombre_archivo = os.path.basename(ruta)
            doc_pdf = fitz.open(ruta)
            texto_completo = "".join(page.get_text() for page in doc_pdf)
            splits_del_documento = text_splitter.create_documents([texto_completo])
            for split in splits_del_documento:
                split.metadata = {"source": nombre_archivo}
            todos_los_splits.extend(splits_del_documento)
            print(f"Documento '{nombre_archivo}' procesado.")
        except Exception as e:
            print(f"Error procesando '{ruta}': {e}")
    return todos_los_splits

def quantize_vectors_to_int8(vectors_np):
    min_val = vectors_np.min(axis=1, keepdims=True)
    max_val = vectors_np.max(axis=1, keepdims=True)
    scale = 254.0 / (max_val - min_val + 1e-9)
    offset = min_val
    quantized_vectors = (vectors_np - offset) * scale - 127.0
    return quantized_vectors.astype(np.int8), min_val.flatten(), max_val.flatten()

# --- 3. LÓGICA PRINCIPAL DE INGESTA ---
def main():
    embeddings_model = OpenAIEmbeddings(model="text-embedding-3-small")
    cliente_chroma = chromadb.PersistentClient(path=DB_PATH)
    coleccion = cliente_chroma.get_or_create_collection(name=NOMBRE_COLECCION)

    # Cargar y procesar los PDFs
    print("\n[Paso 1/4] Cargando y dividiendo documentos PDF...")
    splits_con_metadatos = cargar_y_dividir_politicas(RUTAS_POLITICAS)
    if not splits_con_metadatos:
        print("No se encontraron documentos para procesar. Finalizando.")
        return

    # Obtener IDs existentes para no duplicar
    ids_existentes = set(coleccion.get(include=[])['ids'])
    print(f"Encontrados {len(ids_existentes)} chunks ya existentes en la base de datos.")

    # Filtrar chunks que ya han sido procesados
    chunks_a_procesar = []
    for i, split in enumerate(splits_con_metadatos):
        chunk_id = f"politica_{split.metadata['source']}_chunk_{i}"
        if chunk_id not in ids_existentes:
            split.metadata['id'] = chunk_id # Guardamos el ID en los metadatos temporalmente
            chunks_a_procesar.append(split)
    
    if not chunks_a_procesar:
        print("\nNo hay políticas nuevas para añadir. La base de datos está actualizada.")
        return

    print(f"\n[Paso 2/4] Se procesarán {len(chunks_a_procesar)} nuevos chunks.")
    
    # Generar embeddings para los nuevos chunks
    documentos_nuevos = [split.page_content for split in chunks_a_procesar]
    
    print("[Paso 3/4] Generando embeddings de alta precisión (float)...")
    float_embeddings = embeddings_model.embed_documents(documentos_nuevos)
    
    metadatos_finales = []
    ids_finales = []
    for split in chunks_a_procesar:
        meta = split.metadata.copy()
        ids_finales.append(meta.pop('id'))
        metadatos_finales.append(meta)

    # Añadir los nuevos datos a Chroma DB
    print(f"[Paso 4/4] Añadiendo {len(ids_finales)} nuevos chunks a la colección '{NOMBRE_COLECCION}'...")
    coleccion.add(
        # ✨ CAMBIO CLAVE: Guardamos los embeddings float originales ✨
        embeddings=float_embeddings,
        documents=documentos_nuevos,
        metadatas=metadatos_finales,
        ids=ids_finales
    )
    
    print(f"\n🎉 ¡Proceso completado! La base de datos ahora tiene un total de {coleccion.count()} documentos.")

if __name__ == "__main__":
    main()