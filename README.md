# LinguaLink Chat SA

A real-time direct messaging starter app. Users register or log in, choose a preferred language, select another user and chat. Every message is stored in its original form and translated for the recipient into that recipient's preferred language.

## Architecture
- Next.js UI
- FastAPI REST and authenticated WebSocket notifications
- PostgreSQL 16 persistence (psycopg 3 with a connection pool)
- JWT login with Argon2 password hashes
- NLLB-200 distilled 600M translation

## Run locally
Database (PostgreSQL 16, via Docker):
```bash
docker run -d --name lingualink-pg -e POSTGRES_USER=lingualink -e POSTGRES_PASSWORD=lingualink_dev -e POSTGRES_DB=lingualink -p 5432:5432 postgres:16-alpine
```
Backend (set `DATABASE_URL` if your PostgreSQL is not on localhost:5433 with the credentials above; the default is `postgresql://lingualink:lingualink_dev@localhost:5433/lingualink`):
```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
set DATABASE_URL=postgresql://lingualink:lingualink_dev@localhost:5432/lingualink
set JWT_SECRET=replace-this-with-a-long-random-value
uvicorn app.main:app --reload
```
Frontend in a second terminal:
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:3000 and create two accounts in separate browsers. Give each user a different preferred language. The first translation downloads the model and may be slow.

Or run everything (PostgreSQL, API, web) with `docker compose up --build`.

## Flow
1. User registers with a preferred language.
2. Login returns a signed JWT.
3. The chat WebSocket is authenticated using that token.
4. Sender chooses the language they are typing in.
5. Backend looks up the recipient's preferred language.
6. Backend translates, stores the original and recipient translation, and pushes the message in real time.
7. A user can change their preferred language from the sidebar. New messages use the updated preference, and older received text messages can be re-translated from the message menu (the three dots) with **Re-translate**.

## Performance tuning
Translation runs on the CPU, so speed matters. The model is preloaded when the API starts, so the first message is not delayed. Environment variables for the API:
- `NUM_BEAMS` (default `2`): beam-search width. `5` gives slightly better quality but is about twice as slow on CPU; `1` is fastest.
- `QUANTIZE_INT8` (default `0`; `1` in `docker-compose.yml`): dynamic int8 quantisation on x86 CPUs to reduce memory use (do not use on ARM).
- Docker Desktop: give Docker at least 4 GB of RAM (Settings > Resources) and close heavy applications.

## Before production
Put translation jobs on a queue, use Redis for WebSocket scaling, enforce HTTPS/WSS, use a strong secret, add refresh-token rotation, rate limits, abuse controls, delivery/read states and data-retention settings. Do not treat machine translations as authoritative for legal, medical or financial communication.
