# Adaptive Interview Frontend

Frontend работает с Python backend.

## Что делает интерфейс

- отправляет данные кандидата в `POST /sessions`;
- получает текущее задание из backend-а;
- отправляет ответ в `POST /sessions/{session_id}/answers`;
- передает агрегированные признаки поведения: вставки, уходы со страницы, паузы, резкие изменения текста;
- показывает BKT-вероятность и риск, которые возвращает backend;
- отображает итоговый отчёт из backend-а.

## Как запустить весь стек

1. База данных:
cd ../postgres
docker compose up -d

2. Backend:
cd ..
python3 -m uvicorn backend.main:app --reload --port 8000

3. Frontend:

Можно открыть `frontend/index.html` в браузере напрямую.
Если браузер ограничивает `file://`-страницу, запустите:
cd frontend
python3 -m http.server 5500

Затем открыть:
http://127.0.0.1:5500

Backend URL сейчас задан в `script.js`:
const API_BASE_URL = 'http://127.0.0.1:8000';
