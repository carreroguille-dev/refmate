# Project Structure

```text
refmate-handball/
├── .env.example
├── .gitignore
├── README.md
├── requirements.txt
├── config/
│   ├── __init__.py
│   ├── logging_config.py
│   ├── settings.py
│   └── telegram_config.py
├── data/
│   ├── cache/
│   │   └── .gitkeep
│   ├── chunks/
│   │   └── .gitkeep
│   ├── index/
│   │   ├── article_index.json
│   │   ├── keyword_index.json
│   │   └── main_index.json
│   ├── processed/
│   │   └── .gitkeep
│   └── raw/
│       └── .gitkeep
├── docs/
│   ├── project_structure.md
├── notebooks/
│   ├── chunking_analysis.ipynb
│   ├── index_optimization.ipynb
│   ├── ocr_testing.ipynb
│   └── query_testing.ipynb
├── scripts/
│   ├── rebuild_index.py
│   ├── run_chunker.py
│   ├── run_indexer.py
│   ├── run_processor.py
│   ├── run_scraper.py
│   ├── setup_project.sh
│   └── test_pipeline.py
└── src/
    ├── __init__.py
    ├── agent/
    │   ├── __init__.py
    │   ├── kimi_agent.py
    │   ├── prompt_templates.py
    │   ├── rag_engine.py
    │   └── response_formatter.py
    ├── chunker/
    │   ├── __init__.py
    │   ├── metadata_generator.py
    │   ├── smart_chunker.py
    │   └── token_counter.py
    ├── filter/
    │   ├── __init__.py
    │   ├── classifier.py
    │   ├── prompt_templates.py
    │   └── security_filter.py
    ├── indexer/
    │   ├── __init__.py
    │   ├── index_builder.py
    │   ├── keyword_extractor.py
    │   └── search_engine.py
    ├── processor/
    │   ├── __init__.py
    │   ├── markdown_cleaner.py
    │   ├── ocr_processor.py
    │   └── pdf_handler.py
    ├── scraper/
    │   ├── __init__.py
    │   ├── pdf_downloader.py
    │   ├── rfebm_scraper.py
    │   └── version_tracker.py
    ├── telegram/
    │   ├── __init__.py
    │   ├── bot.py
    │   ├── handlers.py
    │   ├── keyboards.py
    │   └── messages.py
    └── utils/
        ├── __init__.py
        ├── file_utils.py
        ├── logger.py
        ├── text_utils.py
        └── validators.py
```
