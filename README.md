# Eval Discovery Agent

Helps voice agent builders break out of the preview run loop by auto-generating eval rubrics.

## Setup

**Backend** (requires Python 3.11+):

    pip install -r requirements.txt
    cp .env.example .env   # add your GROQ_API_KEY
    uvicorn backend.main:app --reload

**Frontend** (requires Node 18+):

    cd frontend
    npm install
    npm run dev

Open http://localhost:5173.

## How It Works

1. Chat with Alex (CloudSync's SDR) as the prospect
2. Click "Save Run & Start New" after each conversation
3. After 5 runs, transcripts are analyzed by Groq/Qwen
4. A suggested eval rubric appears — edit it and save as JSON

## Running Tests

    pytest tests/ -v
