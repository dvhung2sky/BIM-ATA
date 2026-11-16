"""
RAG Indexer: Read pre-parsed Marker output (.md files), chunk, embed with Qwen3, build FAISS index.

Marker has already been run — parsed markdown, JSON metadata, and images are stored
in subdirectories under rag_data/:
    rag_data/en3/en3.md
    rag_data/Designing-a-Steel-Trusses-to-EC3/Designing-a-Steel-Trusses-to-EC3.md
    rag_data/Steel_Truss_Design_EN3_EngineeringSkills/Steel_Truss_Design_EN3_EngineeringSkills.md
    rag_data/truss_example/truss_example.md

Usage (one-time, or when parsed data changes):
    python rag_indexer.py

Produces:
    rag_data/faiss_index.bin      — FAISS vector index
    rag_data/chunk_metadata.json  — chunk text + source metadata
"""

import os
import re
import json
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RAG_DATA_DIR = os.path.join(SCRIPT_DIR, "rag_data")
FAISS_INDEX_PATH = os.path.join(RAG_DATA_DIR, "faiss_index.bin")
CHUNK_METADATA_PATH = os.path.join(RAG_DATA_DIR, "chunk_metadata.json")

# Pre-parsed Marker output directories (each contains .md + .json + .jpeg files)
DOCUMENTS = [
    {
        "md_path": os.path.join(RAG_DATA_DIR, "en3", "en3.md"),
        "label": "EN 1993-1-1 (Eurocode 3)",
        "source": "en3.pdf",
        "type": "standard",
    },
    {
        "md_path": os.path.join(RAG_DATA_DIR,
                                "Designing-a-Steel-Trusses-to-EC3",
                                "Designing-a-Steel-Trusses-to-EC3.md"),
        "label": "Designing Steel Trusses to EC3",
        "source": "Designing-a-Steel-Trusses-to-EC3.pdf",
        "type": "example",
    },
    {
        "md_path": os.path.join(RAG_DATA_DIR,
                                "Steel_Truss_Design_EN3_EngineeringSkills",
                                "Steel_Truss_Design_EN3_EngineeringSkills.md"),
        "label": "Steel Truss Design to EC3 (EngineeringSkills)",
        "source": "Steel Truss Design to Eurocode 3 _ EngineeringSkills.com.pdf",
        "type": "example",
    },
    {
        "md_path": os.path.join(RAG_DATA_DIR, "truss_example", "truss_example.md"),
        "label": "Truss Design Example",
        "source": "truss_example.pdf",
        "type": "example",
    },
]


# ---------------------------------------------------------------------------
#  Step 1: Read pre-parsed Marker markdown
# ---------------------------------------------------------------------------

def read_marker_output(md_path):
    """Read the pre-parsed Marker markdown file."""
    with open(md_path, "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
#  Step 2: Chunking
# ---------------------------------------------------------------------------

def chunk_document(markdown_text, source_file, source_label, doc_type,
                   max_chunk_size=1500, overlap=200):
    """
    Split markdown into semantically meaningful chunks.

    Rules:
    - Split on level-2 headings (## ) as primary boundaries
    - Keep equations ($$...$$) with their surrounding paragraph
    - Keep markdown tables whole
    - Prefix each chunk with section hierarchy
    - Target size: 1000-1500 chars
    """
    chunks = []

    # Split on ## headings (keep the heading with the content that follows)
    sections = re.split(r'(?=^## )', markdown_text, flags=re.MULTILINE)

    current_h1 = source_label  # top-level heading context

    for section in sections:
        section = section.strip()
        if not section:
            continue

        # Track H1 headings for context
        h1_match = re.match(r'^# (.+)$', section, re.MULTILINE)
        if h1_match:
            current_h1 = h1_match.group(1).strip()

        # Extract section heading
        h2_match = re.match(r'^## (.+)$', section, re.MULTILINE)
        section_title = h2_match.group(1).strip() if h2_match else ""

        # Build hierarchy prefix
        hierarchy = source_label
        if section_title:
            hierarchy = f"{source_label} > {section_title}"

        # If section is small enough, keep as one chunk
        if len(section) <= max_chunk_size:
            chunks.append({
                "text": f"[{hierarchy}]\n\n{section}",
                "source": source_file,
                "source_label": source_label,
                "section": section_title,
                "type": doc_type,
            })
            continue

        # Split long sections into sub-chunks by ### headings first
        subsections = re.split(r'(?=^### )', section, flags=re.MULTILINE)

        for subsection in subsections:
            subsection = subsection.strip()
            if not subsection:
                continue

            h3_match = re.match(r'^### (.+)$', subsection, re.MULTILINE)
            sub_title = h3_match.group(1).strip() if h3_match else ""
            sub_hierarchy = hierarchy
            if sub_title:
                sub_hierarchy = f"{hierarchy} > {sub_title}"

            if len(subsection) <= max_chunk_size:
                chunks.append({
                    "text": f"[{sub_hierarchy}]\n\n{subsection}",
                    "source": source_file,
                    "source_label": source_label,
                    "section": sub_title or section_title,
                    "type": doc_type,
                })
                continue

            # Further split by paragraphs (double newline), keeping equations whole
            paragraphs = re.split(r'\n\n+', subsection)
            current_chunk = f"[{sub_hierarchy}]\n\n"

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                # Check if adding this paragraph exceeds limit
                if len(current_chunk) + len(para) + 2 > max_chunk_size and len(current_chunk) > len(sub_hierarchy) + 10:
                    # Save current chunk
                    chunks.append({
                        "text": current_chunk.strip(),
                        "source": source_file,
                        "source_label": source_label,
                        "section": sub_title or section_title,
                        "type": doc_type,
                    })
                    # Start new chunk with overlap (include hierarchy prefix)
                    overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else ""
                    current_chunk = f"[{sub_hierarchy}]\n\n{overlap_text}\n\n"

                current_chunk += para + "\n\n"

            # Don't forget the last chunk
            if len(current_chunk.strip()) > len(sub_hierarchy) + 10:
                chunks.append({
                    "text": current_chunk.strip(),
                    "source": source_file,
                    "source_label": source_label,
                    "section": sub_title or section_title,
                    "type": doc_type,
                })

    return chunks


# ---------------------------------------------------------------------------
#  Step 3: Embed with Qwen3-Embedding-0.6B and build FAISS index
# ---------------------------------------------------------------------------

def build_faiss_index(chunks):
    """Embed all chunks and build FAISS index."""
    import faiss
    from sentence_transformers import SentenceTransformer

    model_path = os.path.join(SCRIPT_DIR, "Qwen3-Embedding-0.6B")
    if not os.path.isdir(model_path):
        model_path = "Qwen/Qwen3-Embedding-0.6B"  # downloaded from Hugging Face on first use
    print(f"\nLoading Qwen3-Embedding-0.6B from {model_path}...")
    model = SentenceTransformer(model_path)

    print(f"Embedding {len(chunks)} chunks...")
    texts = [c["text"] for c in chunks]
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=16)
    embeddings = np.array(embeddings).astype("float32")

    # Normalise for cosine similarity (IndexFlatIP on normalised vectors = cosine)
    faiss.normalize_L2(embeddings)

    # Build index
    dimension = embeddings.shape[1]  # 1024
    index = faiss.IndexFlatIP(dimension)
    index.add(embeddings)

    # Save
    os.makedirs(RAG_DATA_DIR, exist_ok=True)
    faiss.write_index(index, FAISS_INDEX_PATH)
    print(f"FAISS index saved: {FAISS_INDEX_PATH} ({index.ntotal} vectors, dim={dimension})")

    # Save chunk metadata (without embeddings — text + source info only)
    with open(CHUNK_METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)
    print(f"Chunk metadata saved: {CHUNK_METADATA_PATH}")

    return index


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("RAG INDEXER — Eurocode 3 & Truss Examples")
    print("=" * 60)
    print("Reading pre-parsed Marker output from rag_data/")

    all_chunks = []

    # Read each pre-parsed .md file and chunk it
    for doc in DOCUMENTS:
        md_path = doc["md_path"]
        if not os.path.exists(md_path):
            print(f"\nWARNING: Marker output not found, skipping: {md_path}")
            continue

        print(f"\nProcessing: {doc['label']}")
        print(f"  Markdown: {md_path}")

        markdown = read_marker_output(md_path)
        print(f"  Markdown size: {len(markdown)} chars")

        chunks = chunk_document(
            markdown,
            source_file=doc["source"],
            source_label=doc["label"],
            doc_type=doc["type"],
        )
        print(f"  Chunks: {len(chunks)}")
        all_chunks.extend(chunks)

    print(f"\nTotal chunks: {len(all_chunks)}")

    if not all_chunks:
        print("ERROR: No chunks produced. Check that .md files exist in rag_data/.")
        return

    # Print chunk distribution
    by_source = {}
    for c in all_chunks:
        by_source.setdefault(c["source"], 0)
        by_source[c["source"]] += 1
    print("\nChunk distribution:")
    for src, count in by_source.items():
        print(f"  {src}: {count} chunks")

    # Print sample chunk
    print(f"\nSample chunk (first):")
    print("-" * 40)
    print(all_chunks[0]["text"][:500])
    print("-" * 40)

    # Build FAISS index
    build_faiss_index(all_chunks)

    print("\n" + "=" * 60)
    print("INDEXING COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    main()
