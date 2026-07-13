# Полная инструкция по запуску проекта

Проект реализует MVP адаптивного технического интервью для аналитика данных. В запуске участвуют четыре части:

- PostgreSQL хранит кандидатов, сессии, компетенции, банк заданий, ответы, BKT-состояния и поведенческие признаки.
- Backend на FastAPI управляет сессией интервью, выбирает задания, проверяет ответы, обновляет BKT и считает риск аномального поведения.
- Ollama с Qwen2.5-Coder нужна для оценки открытых текстовых ответов и формирования итогового отчета.
- Frontend на HTML/CSS/JavaScript показывает интерфейс кандидата, отправляет ответы и собирает поведенческие признаки.

## 1. Требования к окружению

Нужно установить:

- Docker Desktop;
- Python 3.10+;
- `pip` и модуль `venv`;
- Ollama;
- браузер.

Проект запускается из корня:
cd /path/to/FINAL

## 2. Структура проекта

Ключевые папки:
backend/      FastAPI backend, BKT, оценка ответов, ML-античит
frontend/     HTML/CSS/JS интерфейс кандидата
postgres/     Docker Compose, схема БД и финальный банк заданий
ml/artifacts/ обученная модель риска аномального поведения

Backend напрямую использует:
ml/artifacts/anomaly_best_model.joblib
ml/artifacts/anomaly_model_metadata.json

Эти файлы нужны для расчета риска аномального поведения. Без них backend упадет при попытке оценить поведенческие признаки.

## 3. Запуск базы данных

База запускается первой, потому что backend при старте сессии сразу обращается к PostgreSQL: создает кандидата, сессию, BKT-состояния и берет первое задание из таблицы `tasks`.
cd /path/to/FINAL/postgres
docker compose up -d

Что происходит при первом запуске:

1. Docker создает контейнер `adaptive_interview_postgres`.
2. Создается база `interview_db`.
3. PostgreSQL один раз выполняет SQL-файлы из `postgres/init`:
   - `01_schema.sql` создает таблицы;
   - `03_full_task_bank.sql` загружает финальные компетенции и полный банк на 135 заданий.

Проверить контейнеры:
docker compose ps

Проверить подключение к БД:
docker compose exec postgres psql -U postgres_user -d interview_db -c "SELECT 1;"

Проверить, что активный банк заданий загружен:
docker compose exec postgres psql -U postgres_user -d interview_db -c "
SELECT c.code, t.difficulty, COUNT(*) AS tasks_count
FROM tasks t
JOIN competencies c ON c.id = t.competency_id
WHERE t.is_active = TRUE
GROUP BY c.code, t.difficulty
ORDER BY c.code, t.difficulty;
"

Ожидаемая логика результата: по каждой из 9 компетенций должно быть по 5 заданий `easy`, `medium`, `hard`, всего 135 активных заданий.

Для визуального просмотра базы можно использовать DBeaver или другой внешний SQL-клиент:
Host: localhost
Port: 5432
Database: interview_db
Username: postgres_user
Password: postgres_password

## 4. Пересоздание локальной базы при необходимости

PostgreSQL выполняет `postgres/init/*.sql` только при первом создании Docker volume. Если локальная база уже использовалась для тестов и нужно вернуться к финальному чистому состоянию проекта, пересоздайте volume:
cd /path/to/FINAL/postgres
docker compose down -v
docker compose up -d

Важно: `docker compose down -v` удаляет локальный volume PostgreSQL. Все кандидаты, сессии и ответы из локальной базы будут потеряны.

## 5. Запуск Ollama

Ollama нужна для открытых текстовых заданий. Закрытые задания (`single_choice`, `multiple_choice`, `matching`) backend проверяет сам по эталону из базы. Открытые задания (`text`) отправляются в локальную модель Qwen2.5-Coder, которая возвращает JSON с `score`, `is_correct` и `feedback`.

Скачать модель:
ollama pull qwen2.5-coder:1.5b

Запустить Ollama:
ollama serve

Если Ollama не запущена, backend все равно стартует, но открытые ответы не будут оцениваться: попытка сохранится как `evaluation_failed`, BKT по этой попытке не изменится.

## 6. Установка Python-зависимостей backend-а

Backend запускается из корня проекта, потому что команда использует модульный путь `backend.main:app`.
cd /path/to/FINAL
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt

Что устанавливается:

- FastAPI и Uvicorn для HTTP API;
- Psycopg2 для подключения к PostgreSQL;
- Pydantic для проверки входных JSON;
- Pandas, joblib и scikit-learn для ML-модели аномального поведения.

Для повторного запуска ML-ноутбуков установите дополнительные зависимости:
pip install -r ml/requirements.txt

Готовая модель уже лежит в `ml/artifacts`, поэтому для обычного запуска
backend-а этот шаг не требуется.

## 7. Переменные окружения backend-а

По умолчанию проект уже настроен на локальный Docker PostgreSQL и локальную Ollama:
DATABASE_URL=postgresql://postgres_user:postgres_password@localhost:5432/interview_db
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5-coder:1.5b
OLLAMA_TIMEOUT_SEC=60
MAX_TASKS_PER_SESSION=27

Если стандартные значения подходят, ничего экспортировать не нужно.

Если нужно явно задать настройки:
export DATABASE_URL="postgresql://postgres_user:postgres_password@localhost:5432/interview_db"
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
export OLLAMA_MODEL="qwen2.5-coder:1.5b"
export OLLAMA_TIMEOUT_SEC=60
export MAX_TASKS_PER_SESSION=27

Логика `MAX_TASKS_PER_SESSION`: frontend и backend сейчас ожидают 27 заданий за одну сессию. Это соответствует схеме 9 активных компетенций × 3 задания. Выбор следующего задания остается BKT-адаптивным: backend смотрит на текущие вероятности владения компетенциями, не продолжает давить на уже зафиксированную слабую компетенцию и выбирает следующее задание по текущему BKT-профилю. После достижения лимита backend завершает интервью, сохраняет расширенный профиль компетенций, итоговый индекс общей компетентности, риск аномального поведения и текст отчета. Итоговый индекс считается по `score` с учетом сложности заданий.

## 8. Запуск backend-а

В отдельном терминале:
cd /path/to/FINAL
source .venv/bin/activate
python3 -m uvicorn backend.main:app --reload --port 8000

Backend будет доступен по адресу:
http://127.0.0.1:8000

Проверка здоровья backend-а:
curl http://127.0.0.1:8000/health

Ожидаемый ответ:
{"status":"ok","database":true}

Проверка активных компетенций:
curl http://127.0.0.1:8000/competencies

Проверка банка заданий:
curl http://127.0.0.1:8000/tasks

## 9. Запуск frontend-а

Frontend обращается к backend-у по адресу, заданному в `frontend/script.js`:
const API_BASE_URL = 'http://127.0.0.1:8000';

Лучше запускать frontend через простой статический сервер:
cd /path/to/FINAL/frontend
python3 -m http.server 5500

Открыть в браузере:
http://127.0.0.1:5500

Можно открыть `frontend/index.html` напрямую.

## 10. Полный порядок запуска по терминалам

Терминал 1: PostgreSQL.
cd /path/to/FINAL/postgres
docker compose up -d
docker compose ps

Терминал 2: Ollama.
ollama serve

Терминал 3: Backend.
cd /path/to/FINAL
source .venv/bin/activate
python3 -m uvicorn backend.main:app --reload --port 8000

Терминал 4: Frontend.
cd /path/to/FINAL/frontend
python3 -m http.server 5500

Потом открыть:
http://127.0.0.1:5500

## 11. Логика прохождения интервью

1. Кандидат вводит ФИО и email.
2. Frontend отправляет `POST /sessions`.
3. Backend создает или обновляет кандидата в `candidates`.
4. Backend создает новую запись в `interview_sessions`.
5. Backend создает начальные BKT-состояния в `bkt_states` по всем активным компетенциям.
6. Backend выбирает первое задание из `tasks`.
7. Frontend показывает задание кандидату.
8. Кандидат отвечает.
9. Frontend отправляет `POST /sessions/{session_id}/answers`.
10. Backend проверяет ответ:
    - закрытые типы проверяются по `correct_answer`;
    - открытый текст оценивается через Ollama по `rubric.criteria`.
11. Backend обновляет BKT-вероятность по компетенции задания.
12. Frontend передает поведенческие признаки: время ответа, вставки, копирования, уходы со вкладки, паузы, резкие изменения длины ответа.
13. Backend нормализует признаки и передает их в модель из `ml/artifacts`.
14. Backend сохраняет риск аномального поведения в `behavior_features`.
15. Backend выбирает следующее задание по текущему BKT-профилю.
16. После достижения лимита заданий backend завершает сессию.
17. В `interview_sessions` сохраняются:
    - `final_score` - итоговый индекс общей компетентности по score-профилю;
    - `final_bkt_profile` - расширенный JSON-профиль компетенций: score, уровень, BKT-вероятность и число оцененных заданий;
    - `anomaly_probability`;
    - `anomaly_risk`;
    - `report_text`.
18. Frontend показывает итоговый индекс, score-профиль компетенций, траекторию заданий и риск.

## 12. Как backend выбирает сложность

У каждой компетенции есть BKT-вероятность владения навыком. На старте она равна `0.4500`.

Правило выбора сложности находится в `backend/bkt.py`:
probability < 0.40  -> easy
probability < 0.75  -> medium
probability >= 0.75 -> hard

После правильного ответа вероятность растет, после неправильного снижается. Если компетенция несколько раз проверялась и осталась слабой, backend перестает давить на нее и переключается на другие компетенции.

## 13. Как устроен банк заданий

Полный банк лежит в:
postgres/init/03_full_task_bank.sql

В нем:

- 9 компетенций;
- 15 заданий на каждую компетенцию;
- 5 заданий `easy`, 5 `medium`, 5 `hard`;
- всего 135 активных заданий.

Типы заданий соответствуют архитектуре backend-а:
text             открытый текстовый ответ, проверяется LLM по rubric.criteria
single_choice    один правильный вариант, проверяется по correct_answer.option_id
multiple_choice  несколько правильных вариантов, проверяется по correct_answer.option_ids
matching         сопоставление пар, проверяется по correct_answer.matches

## 14. Быстрый чек-лист перед запуском

1. Docker Desktop запущен.
2. `docker compose ps` показывает работающий `postgres`.
3. `curl http://127.0.0.1:8000/health` возвращает `database: true`.
4. Ollama запущена, модель `qwen2.5-coder:1.5b` скачана.
5. В `ml/artifacts` есть `anomaly_best_model.joblib` и `anomaly_model_metadata.json`.
6. Frontend открыт на `http://127.0.0.1:5500`.
7. В базе активен полный банк заданий на 135 заданий.
