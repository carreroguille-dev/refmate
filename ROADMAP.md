# RefMate Handball - Project Roadmap

## Overview
Pipeline to scrape, process, and index handball regulations for an LLM-powered query agent.

---

## Phase 1: Data Acquisition ✅ COMPLETED
**Goal:** Download all relevant handball regulation PDFs

- [x] **1.1 Scraper** (`src/pipeline/scraper.py`)
  - Extract PDF links from RFEBM and FABM sources
  - Filter by patterns and exclude unwanted documents

- [x] **1.2 Downloader** (`src/pipeline/downloader.py`)
  - Download PDFs with retry logic
  - Track metadata (hash, date, size, URL)
  - Skip already downloaded files

---

## Phase 2: PDF Processing ✅ COMPLETED
**Goal:** Convert PDFs to clean images ready for OCR

- [x] **2.1 PDF to Image** (`src/pipeline/pdf_processor.py`)
  - Convert PDF pages to PNG images
  - Template-based cropping (headers, footers, margins)
  - Support for odd/even page layouts

- [x] **2.2 Crop Templates** (`config/image_templates/*.json`)
  - `reglas_juego.json` - Game rules template
  - `rgc.json` - Competition regulations template
  - Add more templates as needed for new document types

---

## Phase 3: OCR Processing ✅ COMPLETED
**Goal:** Extract text from images using LightOnOCR

- [x] **3.1 OCR Processor** (`src/pipeline/ocr_processor.py`)
  - LightOnOCR model integration
  - Stream processing (PDF → crop → OCR → text)
  - Memory-efficient with model loading/unloading
  - Output: Markdown files with page markers

---

## Phase 4: Text Segmentation 🔄 IN PROGRESS
**Goal:** Split OCR output into semantic chunks for retrieval

- [ ] **4.1 Markdown Parser** (`src/pipeline/segmenter.py`)
  - Parse OCR markdown output
  - Detect document structure (articles, rules, sections)
  - Handle handball-specific formatting (Rule 1:1, Art. 5, etc.)

- [ ] **4.2 Chunking Strategy**
  - Split by semantic boundaries (rules, articles, sections)
  - Maintain context (include parent section headers)
  - Target chunk size: ~500-1000 tokens
  - Overlap between chunks for continuity

- [ ] **4.3 Metadata Enrichment**
  - Source document name
  - Section hierarchy (Chapter > Section > Rule)
  - Rule/Article identifiers
  - Page references

---

## Phase 5: Index Generation ⏳ PENDING
**Goal:** Create navigable index for the agent

- [ ] **5.1 Index Structure** (`src/pipeline/indexer.py`)
  - Hierarchical index (document → chapter → section → rule)
  - JSON format for easy navigation
  - Quick lookups by rule number, topic, keyword

- [ ] **5.2 Embeddings (Optional)**
  - Generate embeddings for semantic search
  - Store in vector DB (ChromaDB, FAISS, etc.)
  - Hybrid search: keyword + semantic

- [ ] **5.3 Index Storage** (`data/indices/`)
  - `master_index.json` - Full document hierarchy
  - `rules_index.json` - Quick rule number lookup
  - `embeddings/` - Vector embeddings (if used)

---

## Phase 6: Query Agent ⏳ PENDING
**Goal:** LLM agent that answers handball regulation queries

- [ ] **6.1 Agent Core** (`src/agent/query_agent.py`)
  - Receive natural language query
  - Navigate index to find relevant segments
  - Retrieve context chunks
  - Generate answer with citations

- [ ] **6.2 Index Navigator** (`src/agent/navigator.py`)
  - Parse user query intent
  - Map to index structure
  - Multi-hop navigation for complex queries

- [ ] **6.3 Context Retrieval**
  - Fetch relevant chunks from segments
  - Rank by relevance
  - Respect context window limits

- [ ] **6.4 Response Generation**
  - Format answer with rule citations
  - Include source references
  - Handle ambiguous queries

---

## Phase 7: API & Interface ⏳ PENDING
**Goal:** Expose the agent for external use

- [ ] **7.1 REST API** (`src/api/`)
  - Query endpoint
  - Health checks
  - Rate limiting

- [ ] **7.2 CLI Interface**
  - Interactive query mode
  - Batch processing

- [ ] **7.3 Web UI (Optional)**
  - Simple chat interface
  - Source highlighting

---

## Current Status

| Phase | Status | Progress |
|-------|--------|----------|
| 1. Data Acquisition | ✅ Done | 100% |
| 2. PDF Processing | ✅ Done | 100% |
| 3. OCR Processing | ✅ Done | 100% |
| 4. Segmentation | 🔄 Next | 0% |
| 5. Index Generation | ⏳ Pending | 0% |
| 6. Query Agent | ⏳ Pending | 0% |
| 7. API & Interface | ⏳ Pending | 0% |

---

## Next Steps
1. **Start Phase 4**: Build the segmenter for OCR markdown output
2. Design chunk structure for handball regulations
3. Test with sample documents (Reglas del Juego, RGC)

---

## File Structure
```
refmate-handball/
├── config/
│   ├── settings.py              # Configuration
│   └── image_templates/         # PDF crop templates
├── data/
│   ├── raw/                     # Downloaded PDFs
│   ├── temp/                    # Intermediate images
│   ├── processed/               # OCR output (markdown)
│   └── indices/                 # Generated indices
├── src/
│   ├── pipeline/
│   │   ├── scraper.py          ✅
│   │   ├── downloader.py       ✅
│   │   ├── pdf_processor.py    ✅
│   │   ├── ocr_processor.py    ✅
│   │   ├── segmenter.py        🔄 TODO
│   │   └── indexer.py          ⏳ TODO
│   └── agent/
│       ├── query_agent.py      ⏳ TODO
│       └── navigator.py        ⏳ TODO
├── utils/
│   └── logger.py               ✅
└── scripts/
    └── test_*.py               # Test scripts
```
