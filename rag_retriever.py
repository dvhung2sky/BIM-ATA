"""
RAG Retriever: Query the FAISS index with Qwen3-Embedding-0.6B embeddings
to retrieve relevant Eurocode 3 clauses and truss design examples.

Used at runtime by the assess_eurocode_compliance tool in truss_tools.py.
"""

import os
import json
import math
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAG_DATA_DIR = os.path.join(SCRIPT_DIR, "rag_data")
FAISS_INDEX_PATH = os.path.join(RAG_DATA_DIR, "faiss_index.bin")
CHUNK_METADATA_PATH = os.path.join(RAG_DATA_DIR, "chunk_metadata.json")
RESULT_FILE = os.path.join(SCRIPT_DIR, "result.txt")

# Lazy-loaded globals (loaded once on first call)
_model = None
_index = None
_chunks = None


def _load_resources():
    """Lazy-load the embedding model, FAISS index, and chunk metadata."""
    global _model, _index, _chunks

    if _model is not None:
        return

    import faiss
    from sentence_transformers import SentenceTransformer

    if not os.path.exists(FAISS_INDEX_PATH):
        raise FileNotFoundError(
            f"FAISS index not found at {FAISS_INDEX_PATH}. "
            "Run rag_indexer.py first to build the index."
        )

    model_path = os.path.join(SCRIPT_DIR, "Qwen3-Embedding-0.6B")
    if not os.path.isdir(model_path):
        model_path = "Qwen/Qwen3-Embedding-0.6B"  # downloaded from Hugging Face on first use
    print(f"Loading Qwen3-Embedding-0.6B from {model_path}...")
    _model = SentenceTransformer(model_path)

    print("Loading FAISS index...")
    _index = faiss.read_index(FAISS_INDEX_PATH)

    with open(CHUNK_METADATA_PATH, "r", encoding="utf-8") as f:
        _chunks = json.load(f)

    print(f"RAG ready: {_index.ntotal} chunks indexed.")


# ---------------------------------------------------------------------------
#  Core retrieval
# ---------------------------------------------------------------------------

def retrieve(query, top_k=5):
    """
    Retrieve the top-k most relevant chunks for a query.

    Args:
        query: Natural-language query string
        top_k: Number of chunks to return

    Returns:
        List of dicts: [{'text', 'source', 'section', 'type', 'score'}, ...]
    """
    import faiss as _faiss

    _load_resources()

    # Encode query with instruction prefix (Qwen3-Embedding convention)
    query_embedding = _model.encode(
        [query],
        prompt_name="query",
    ).astype("float32")
    _faiss.normalize_L2(query_embedding)

    scores, indices = _index.search(query_embedding, top_k)

    results = []
    for score, idx in zip(scores[0], indices[0]):
        if idx < 0:
            continue
        chunk = _chunks[idx]
        results.append({
            "text": chunk["text"],
            "source": chunk.get("source", ""),
            "source_label": chunk.get("source_label", ""),
            "section": chunk.get("section", ""),
            "type": chunk.get("type", ""),
            "score": float(score),
        })

    return results


# ---------------------------------------------------------------------------
#  Result file parser
# ---------------------------------------------------------------------------

def parse_result_file(result_path=RESULT_FILE):
    """
    Parse result.txt into structured data for query construction.

    Returns dict with:
        'analysis_status': str
        'sections': {name: {'A': float}}
        'max_displacement': {'value': float, 'joint': int}
        'max_stress': {'value': float, 'element': int, 'type': str}
        'elements': [{id, section, area, force, stress}, ...]
        'displacements': [{joint, dx, dy, dz, total}, ...]
    """
    if not os.path.exists(result_path):
        return None

    with open(result_path, "r") as f:
        content = f.read()

    data = {
        "analysis_status": "UNKNOWN",
        "sections": {},
        "max_displacement": {"value": 0.0, "joint": 0},
        "max_stress": {"value": 0.0, "element": 0, "type": ""},
        "elements": [],
        "displacements": [],
    }

    lines = content.split("\n")
    current_section = None

    for line in lines:
        stripped = line.strip()

        # Analysis status
        if "Analysis Status:" in stripped:
            data["analysis_status"] = "SUCCESS" if "SUCCESS" in stripped else "FAILED"

        # Detect sections
        if "SECTION PROPERTIES" in stripped:
            current_section = "sections"
            continue
        elif "MEMBER AXIAL FORCES" in stripped or "ELEMENT RESULTS" in stripped:
            current_section = "elements"
            continue
        elif "JOINT DISPLACEMENTS" in stripped:
            current_section = "displacements"
            continue
        elif stripped.startswith("=") and current_section:
            if current_section not in ("sections", "elements", "displacements"):
                current_section = None
            continue

        if stripped.startswith("=") or stripped.startswith("-") or not stripped:
            continue

        # Parse section properties
        if current_section == "sections":
            if stripped.startswith("Section") or stripped.startswith("---"):
                continue
            parts = stripped.split()
            if len(parts) >= 2:
                try:
                    data["sections"][parts[0]] = {"A": float(parts[1])}
                except ValueError:
                    pass

        # Parse elements
        elif current_section == "elements":
            if "Elem" in stripped and "Section" in stripped:
                continue
            parts = stripped.split()
            if len(parts) >= 5:
                try:
                    elem = {
                        "id": int(parts[0]),
                        "section": parts[1],
                        "area": float(parts[2]),
                        "force": float(parts[3]),
                        "stress": float(parts[4]),
                    }
                    data["elements"].append(elem)
                except (ValueError, IndexError):
                    pass

        # Parse displacements
        elif current_section == "displacements":
            if stripped.startswith("Joint") or stripped.startswith("---"):
                continue
            parts = stripped.split()
            if len(parts) >= 4:
                try:
                    dx, dy, dz = float(parts[1]), float(parts[2]), float(parts[3])
                    total = math.sqrt(dx**2 + dy**2 + dz**2)
                    data["displacements"].append({
                        "joint": int(parts[0]),
                        "dx": dx, "dy": dy, "dz": dz,
                        "total": total,
                    })
                except ValueError:
                    pass

    # Compute max displacement
    if data["displacements"]:
        max_d = max(data["displacements"], key=lambda d: d["total"])
        data["max_displacement"] = {"value": max_d["total"], "joint": max_d["joint"]}

    # Compute max stress
    if data["elements"]:
        max_e = max(data["elements"], key=lambda e: abs(e["stress"]))
        data["max_stress"] = {
            "value": max_e["stress"],
            "element": max_e["id"],
            "type": "Tension" if max_e["stress"] > 0 else "Compression",
        }

    return data


# ---------------------------------------------------------------------------
#  Query construction from analysis results
# ---------------------------------------------------------------------------

def build_assessment_queries(results, check_type="all"):
    """
    Build targeted retrieval queries from analysis results.

    Args:
        results: Parsed result data from parse_result_file()
        check_type: "all", "tension", "compression", "buckling",
                    "serviceability", "slenderness"

    Returns:
        List of query strings
    """
    queries = []

    # Collect unique sections used
    section_names = set()
    tension_members = []
    compression_members = []
    for elem in results.get("elements", []):
        section_names.add(elem["section"])
        if elem["stress"] > 0:
            tension_members.append(elem)
        elif elem["stress"] < 0:
            compression_members.append(elem)

    # Tension checks (§6.2.3)
    if check_type in ("all", "tension") and tension_members:
        queries.append(
            "Eurocode 3 EN 1993-1-1 Section 6.2.3 design plastic resistance "
            "to normal forces tension Npl,Rd equals A times fy divided by gamma M0"
        )
        for sec in section_names:
            queries.append(
                f"Eurocode 3 tension resistance check for steel tubular section "
                f"CHS area cross-section classification {sec}"
            )

    # Compression checks (§6.2.4)
    if check_type in ("all", "compression") and compression_members:
        queries.append(
            "Eurocode 3 EN 1993-1-1 Section 6.2.4 compression resistance "
            "Nc,Rd design resistance cross-section class"
        )

    # Buckling checks (§6.3.1)
    if check_type in ("all", "buckling") and compression_members:
        queries.append(
            "Eurocode 3 EN 1993-1-1 Section 6.3.1 uniform members in compression "
            "flexural buckling resistance Nb,Rd chi reduction factor buckling curve"
        )
        queries.append(
            "Eurocode 3 non-dimensional slenderness lambda bar imperfection factor "
            "alpha buckling curve a b c d for CHS hollow section"
        )
        queries.append(
            "truss design example buckling resistance check compression chord member "
            "effective length buckling length"
        )

    # Slenderness checks
    if check_type in ("all", "slenderness"):
        queries.append(
            "Eurocode 3 maximum slenderness ratio limit for compression members "
            "truss bracing member lambda limit 200"
        )

    # Serviceability checks (§7.2)
    if check_type in ("all", "serviceability"):
        queries.append(
            "Eurocode 3 EN 1993-1-1 Section 7 serviceability limit state "
            "vertical deflection limit span over 250 L/250 truss"
        )
        queries.append(
            "steel truss design deflection limit serviceability check "
            "allowable deflection span to depth ratio"
        )

    # General truss design workflow
    if check_type == "all":
        queries.append(
            "steel truss design to Eurocode 3 member verification unity check "
            "step by step design procedure"
        )

    return queries


def format_retrieved_context(retrieved_chunks, max_total_chars=8000):
    """
    Format retrieved chunks into a readable context string for the LLM.
    Deduplicates by text content and limits total length.
    """
    seen_texts = set()
    formatted = []
    total_chars = 0

    # Sort by score descending
    sorted_chunks = sorted(retrieved_chunks, key=lambda c: c["score"], reverse=True)

    for chunk in sorted_chunks:
        # Deduplicate
        text_key = chunk["text"][:200]
        if text_key in seen_texts:
            continue
        seen_texts.add(text_key)

        # Check total length
        if total_chars + len(chunk["text"]) > max_total_chars:
            continue

        source_info = chunk.get("source_label", chunk["source"])
        section_info = chunk.get("section", "")
        score = chunk.get("score", 0.0)

        header = f"--- [{source_info}]"
        if section_info:
            header += f" § {section_info}"
        header += f" (relevance: {score:.3f}) ---"

        formatted.append(f"{header}\n{chunk['text']}")
        total_chars += len(chunk["text"])

    return "\n\n".join(formatted)


# ---------------------------------------------------------------------------
#  High-level assessment function
# ---------------------------------------------------------------------------

def assess_results(check_type="all", top_k_per_query=3):
    """
    Full assessment pipeline: parse results → build queries → retrieve → format.

    Args:
        check_type: Type of Eurocode 3 check
        top_k_per_query: Number of chunks to retrieve per query

    Returns:
        Tuple of (results_summary: str, context: str)
    """
    results = parse_result_file()
    if results is None:
        return "No analysis results found. Run the analysis first.", ""

    if results["analysis_status"] != "SUCCESS":
        return "Analysis did not succeed. Fix errors before assessing compliance.", ""

    # Build summary
    summary_lines = [
        f"Analysis Status: {results['analysis_status']}",
        f"Sections used: {', '.join(results['sections'].keys())}",
        f"Total elements: {len(results['elements'])}",
        f"Tension members: {sum(1 for e in results['elements'] if e['stress'] > 0)}",
        f"Compression members: {sum(1 for e in results['elements'] if e['stress'] < 0)}",
        f"Max displacement: {results['max_displacement']['value']:.6f} mm "
        f"at Joint {results['max_displacement']['joint']}",
        f"Max stress: {abs(results['max_stress']['value']):.4f} MPa "
        f"({results['max_stress']['type']}) in Element {results['max_stress']['element']}",
        "",
        "Element details:",
    ]
    for elem in results["elements"]:
        stress_type = "T" if elem["stress"] > 0 else "C"
        summary_lines.append(
            f"  Elem {elem['id']:3d} | {elem['section']:<20s} | "
            f"A={elem['area']:.1f} mm² | F={elem['force']:.1f} N | "
            f"σ={abs(elem['stress']):.4f} MPa ({stress_type})"
        )

    results_summary = "\n".join(summary_lines)

    # Build queries and retrieve
    queries = build_assessment_queries(results, check_type)

    all_retrieved = []
    for query in queries:
        retrieved = retrieve(query, top_k=top_k_per_query)
        all_retrieved.extend(retrieved)

    context = format_retrieved_context(all_retrieved)

    return results_summary, context


# ---------------------------------------------------------------------------
#  CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("RAG RETRIEVER — Test Query")
    print("=" * 60)

    # Test with a sample query
    test_query = "Eurocode 3 buckling resistance for CHS compression member"
    print(f"\nQuery: {test_query}\n")

    results = retrieve(test_query, top_k=3)
    for i, r in enumerate(results, 1):
        print(f"\n--- Result {i} (score: {r['score']:.3f}) ---")
        print(f"Source: {r['source_label']} | Section: {r['section']}")
        print(r["text"][:400])
        print("...")
