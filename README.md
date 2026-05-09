# 🌌 Project Scraper

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB)](https://reactjs.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-007ACC?style=for-the-badge&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vite](https://img.shields.io/badge/Vite-646CFF?style=for-the-badge&logo=vite&logoColor=white)](https://vitejs.dev/)
[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)

A high-performance, full-stack media extraction engine. Designed for precision scraping of images, videos, and links, featuring built-in SSRF protection and stealth capabilities for modern web architectures.

---

## ✨ Key Features

- **🎯 Multi-Source Intelligence** — Aggregates and groups results by source URL for clean, organized data separation.
- **🛡️ SSRF Protected** — Hardened backend that blocks requests to internal/private IP ranges.
- **🕵️ Stealth Mode** — Leverages headless automation to bypass bot detection and solve complex challenges like Cloudflare.
- **📡 Network Interception** — Sniffs out HLS/Dash video streams and media assets directly from network traffic.
- **🖼️ High-Res Refinement** — Automatically resolves high-resolution versions of thumbnails and lazy-loaded assets.
- **📦 Download Proxy** — Bypass CORS restrictions and browser sandboxing with an integrated asset proxy.

---

## 🛠️ Tech Stack

| Layer | Technology |
| :--- | :--- |
| **Frontend** | React 18, TypeScript, Vite, Vanilla CSS |
| **Backend** | Python 3.11+, FastAPI, [Scrapling](https://github.com/D4Vinci/Scrapling) |
| **Networking** | HTTPX, Playwright (via StealthyFetcher) |
| **Security** | Pydantic validation, SSRF Filter, CORS Middleware |

---

## 🚀 Getting Started

### 1. Backend Setup
```bash
cd backend
python -m venv venv
# Windows
.\venv\Scripts\activate   
# Unix/macOS
source venv/bin/activate

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 2. Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

The frontend will be available at `http://localhost:5173`.

---

## ⚙️ Configuration

### Social Media Sessions
The scraper supports session persistence for authenticated scraping on platforms like Instagram, Facebook, and TikTok. 
- Sessions are stored as JSON in `backend/sessions/`.
- Use the `/api/auth/login` endpoint to initialize a new session.

### Stealth Mode
Enable `stealth: true` in your scrape requests to trigger the **StealthyFetcher**, which uses advanced browser automation to handle dynamic content and anti-bot measures.

---

## 🔒 Security Policy

This project implements strict **SSRF (Server-Side Request Forgery) protection**. By default, it blocks any requests targeting:
- `localhost` / `127.0.0.1`
- Private network ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- Link-local addresses

---

## 📜 Credits

Special thanks to **[D4Vinci](https://github.com/D4Vinci)** for the **[Scrapling](https://github.com/D4Vinci/Scrapling)** framework, which powers the high-performance extraction engine of this project.

## 📄 License

This project is intended for educational and ethical scraping purposes. Please respect the `robots.txt` and Terms of Service of any target website.
