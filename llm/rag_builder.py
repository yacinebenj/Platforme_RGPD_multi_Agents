
# partie hedhi ta3 RAG builder li howa responibble 3al indexing w semantic search 3al rgpd w loi2004
# w t5ali agents yjibo articles li yhebo alehom 3al rgpd w loi2004 bch yesta3mlohom f analys w recommendations

import os
import pickle
import logging
import warnings
import numpy as np

# SUPPRESS WARNINGS AND LOGGING
# The HuggingFace libraries (sentence-transformers, transformers, torch)
# produce many verbose warnings. We suppress these for cleaner output.
warnings.filterwarnings("ignore")
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["HF_HUB_VERBOSITY"] = "error"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"
os.environ["SENTENCE_TRANSFORMERS_HOME"] = os.path.join(os.path.expanduser("~"), ".cache", "sentence_transformers")

logging.getLogger("sentence_transformers").setLevel(logging.ERROR)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("torch").setLevel(logging.ERROR)
logging.disable(logging.WARNING)

# SentenceTransformer: t7awel text l vector embeddings 
# FAISS: Facebook's efficient similarity search library
from sentence_transformers import SentenceTransformer
import faiss

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)


# chunk size:kol chunk feha 400 word w hedha ykafi 3al context window ta3 el LLM
CHUNK_SIZE = 400
#chunk overlap hiya the amount of shared text between two consecutive chunks
# oerlap: 50-word overlap between consecutive chunks , el overlap y5ali l agent yefhem el contexte w matfaltch l information 3al chunk boundary
# pevents losing important legal articles at chunk boundaries
CHUNK_OVERLAP = 50

# File paths for storing the FAISS index and chunk metadata
INDEX_PATH = "llm/faiss_index.bin"      # Binary FAISS index (vector embeddings)
CHUNKS_PATH = "llm/chunks.pkl"           # Pickle file (original text + source)

# Multilingual embedding model:
# paraphrase-multilingual-MiniLM-L12-v2
#   - 384 dimensions per embedding
#   - Supports French, English, Arabic (Tunisian law context)
#   - Fast inference, good for production use
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def split_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i+chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap
    return chunks



#BUILD FAISS INDEX
def build_index():
    """
    Build semantic search index from RGPD and Tunisian law documents.
    
    This function:
    1. Loads raw legal texts
    2. Splits them into chunks with overlap
    3. Generates vector embeddings for each chunk
    4. Creates FAISS index for fast semantic search
    5. Saves index and chunks to disk
    
    Returns:
        tuple: (faiss_index, all_chunks)
    
    FLOW DIAGRAM:
        uploads/rgpd.txt ──┐
                          ├─→ split_text() ──→ chunks
        uploads/loi2004.txt┘                    
                                ↓
                        SentenceTransformer
                        (embed each chunk)
                                ↓
                            embeddings
                            (384-dim vectors)
                                ↓
                            FAISS.IndexFlatL2
                            (L2 distance search)
                                ↓
                        Save to disk:
                        - faiss_index.bin (embeddings)
                        - chunks.pkl (original text)
    """
    print("Loading legal texts...")
    # tloadi rgpd text
    with open("uploads/rgpd.txt", "r", encoding="utf-8") as f:
        rgpd_text = f.read()
    
    # tloadi loi2004 text
    with open("uploads/loi2004.txt", "r", encoding="utf-8") as f:
        loi_text = f.read()

    print("Splitting into chunks...")
    # kol chunk tagged b source bch agents ya3rfo el info mnin jeya
    rgpd_chunks = [{"text": c, "source": "RGPD"} for c in split_text(rgpd_text)]
    loi_chunks = [{"text": c, "source": "Loi 2004-63"} for c in split_text(loi_text)]
    all_chunks = rgpd_chunks + loi_chunks
    print(f"Total chunks: {len(all_chunks)}")

    print("Embedding chunks...")
    # Initialize multilingual embedding model
    # This converts text → 384-dimensional vectors representing meaning
    model = SentenceTransformer(MODEL_NAME)
    
    # Extract text content from chunk dictionaries
    texts = [c["text"] for c in all_chunks]
    
    # Generate embeddings for all chunks
    # batch_size=32: process 32 chunks at a time (GPU memory efficient)
    # Returns shape (num_chunks, 384)
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=32)
    embeddings = np.array(embeddings).astype("float32")  # FAISS requires float32

    print("Building FAISS index...")
    # IndexFlatL2: Exact L2 (Euclidean) distance search
    # More accurate than approximate methods (like IVF)
    # Suitable for ~1000-10000 chunks
    dimension = embeddings.shape[1]  # 384
    index = faiss.IndexFlatL2(dimension)
    index.add(embeddings)

    print("Saving index and chunks...")
    # Save FAISS index (compressed embeddings, fast search)
    faiss.write_index(index, INDEX_PATH)
    
    # Save original text chunks for retrieval
    # When search finds match by vector, we get text from here
    with open(CHUNKS_PATH, "wb") as f:
        pickle.dump(all_chunks, f)

    print(f"Index built: {index.ntotal} vectors, dimension {dimension}")
    return index, all_chunks


#LOAD OR BUILD INDEX
def load_index():
    """
    Load existing FAISS index from disk, or build it if missing.
    
    Smart behavior:
    - First run: builds index (slow, ~1-2 minutes)
    - Subsequent runs: loads from disk (fast, <1 second)
    
    Returns:
        tuple: (faiss_index, all_chunks)
    """
    # Check if precomputed index files exist
    if not os.path.exists(INDEX_PATH) or not os.path.exists(CHUNKS_PATH):
        print("Index not found, building from scratch...")
        # First-time setup: embed all documents and create index
        # This is computationally expensive but only happens once
        return build_index()

    # Fast path: load pre-computed index from disk
    # FAISS index loads in memory for millisecond-level search
    index = faiss.read_index(INDEX_PATH)
    
    # Load original text chunks
    # These are matched with FAISS results to return readable text
    with open(CHUNKS_PATH, "rb") as f:
        chunks = pickle.load(f)
    
    print(f"Index loaded: {index.ntotal} vectors")
    return index, chunks

# SEMANTIC SEARCH (Core retrieval logic)

def search(query, index, chunks, model, top_k=4):
    """
    Search for legally relevant content using semantic similarity.
    
    Args:
        query (str): Natural language question
                    Example: "Quel est la base légale requise pour un traitement?"
        index: FAISS index (pre-built)
        chunks: List of text chunks with metadata
        model: SentenceTransformer model for embeddings
        top_k (int): Return top-K most similar chunks (default 4)
    
    Returns:
        list: Top-K chunks matching the query semantically
              Each chunk contains {"text": ..., "source": "RGPD" or "Loi 2004-63"}
    
    SEARCH FLOW:
        Input query: "Quel consentement est valide?"
                            ↓
                    Embed query → 384-dim vector
                            ↓
                    FAISS search (L2 distance)
                            ↓
                    Find 4 nearest chunk embeddings
                            ↓
                    Retrieve chunk indices [42, 156, 203, 89]
                            ↓
                    Look up original text in chunks.pkl
                            ↓
                    Return: [chunk_42, chunk_156, chunk_203, chunk_89]
    
    Use case in agents:
    - Agent A: "What are GDPR lawful bases?" → retrieves Article 6
    - Agent B: "What triggers DPIA/AIPD?" → retrieves Article 35
    - Agent C: "What's the response deadline?" → retrieves Article 12
    - Agent D: "What documentation is required?" → retrieves Article 30
    """
    # Embed the user's query into the same 384-dimensional space
    query_embedding = model.encode([query]).astype("float32")
    
    # Search FAISS index: find top_k nearest neighbors by L2 distance
    # Lower distance = more semantically similar
    distances, indices = index.search(query_embedding, top_k)
    
    results = []
    # Retrieve actual chunk text for each matched index
    for idx in indices[0]:
        if idx < len(chunks):
            results.append(chunks[idx])
    
    return results


# PUBLIC API: Initialize RAG System

def get_rag():
    """
    Initialize the RAG (Retrieval-Augmented Generation) system.
    
    This is the main entry point for all agents.
    
    Returns:
        tuple: (faiss_index, chunks, embedding_model)
               Ready for semantic search operations
    
    Usage in agents:
        index, chunks, model = get_rag()
        relevant_laws = search("query", index, chunks, model)
    
    What is RAG?
        RAG = Retrieval-Augmented Generation
        
        Without RAG: Agent asks LLM a legal question
                    ↓ LLM uses only training data (may be outdated/wrong)
        
        With RAG:    Agent retrieves actual RGPD/Tunisian law
                    ↓ Agent combines law + LLM for accurate answer
        
        This ensures our agents cite actual legal articles,
        not hallucinated or outdated information.
    """
    # Load or build the FAISS search index
    index, chunks = load_index()
    
    # Initialize embedding model for converting text to vectors
    model = SentenceTransformer(MODEL_NAME)
    
    return index, chunks, model



# This module provides RAG capabilities for the RGPD platform:
#
# 1. Stores RGPD + Tunisian law as semantic vectors
# 2. Enables agents to retrieve relevant legal articles
# 3. Ensures compliance recommendations are law-backed
#
# Files created:
#   - llm/faiss_index.bin  → Vector embeddings (384-dim per chunk)
#   - llm/chunks.pkl       → Original text + source attribution
#
# Performance:
#   - Build time: ~1-2 minutes (one-time)
#   - Search time: ~10-50ms per query (production-ready)
# ============================================================
