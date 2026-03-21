"""
Knowledge base search tool — RAG (Retrieval Augmented Generation).

HOW RAG WORKS HERE:
  1. User query → embed with Vertex AI text-embedding-gecko
  2. Nearest-neighbour search against Vertex AI Vector Search index
  3. Return top-k document chunks as context

IN THIS REFERENCE IMPLEMENTATION:
  We use an in-memory document store with keyword matching so the repo
  runs without a live Vector Search index. The retriever.py module shows
  exactly how to swap this for real Vertex AI Vector Search.
"""
from app.rag.retriever import retrieve_documents


def search_knowledge_base(query: str, top_k: int = 3) -> dict:
    """
    Searches the internal knowledge base and returns relevant document chunks.
    The calling agent includes these chunks in its context window for grounding.
    """
    results = retrieve_documents(query, top_k=top_k)
    return {
        "query": query,
        "results": results,
        "result_count": len(results),
        "note": "Include these documents as context when generating your response."
    }
