# RefMate Handball 🤾‍♂️

RefMate is an intelligent assistant for the RFEBM (Royal Spanish Handball Federation) regulations. It allows users to query the official rules via Telegram and receive accurate, referenced answers.

## Features

- **Automated Scraping**: Keeps regulations up-to-date from the official RFEBM website.
- **Intelligent Processing**: Uses OCR (LightOnOCR-2-1B) to convert PDFs to structured Markdown.
- **Smart Search**: RAG (Retrieval-Augmented Generation) pipeline for accurate information retrieval.
- **Security**: Basic prompt injection filtering.
- **Telegram Bot**: User-friendly interface for querying rules.

## Setup

1. **Clone the repository:**
   ```bash
   git clone <repository_url>
   cd refmate-handball
   ```

2. **Create the environment:**
   Using Conda:
   ```bash
   conda env create -f environment.yml
   conda activate refmate
   ```

   Alternatively, using pip:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configuration:**
   - Copy `.env.example` to `.env`:
     ```bash
     cp .env.example .env
     ```
   - Fill in your `OPENAI_API_KEY` and `TELEGRAM_BOT_TOKEN`.

## Usage

(Coming soon)

## Structure

See [Project Structure](docs/project_structure.md) for details.
