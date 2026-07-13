# PostgreSQL

Локальная база данных для MVP адаптивного технического интервью.

## Состав

- `docker-compose.yml` - поднимает контейнер PostgreSQL.
- `init/01_schema.sql` - создает таблицы проекта.
- `init/03_full_task_bank.sql` - загружает финальные компетенции и полный банк заданий.

## Таблицы

- `candidates` - кандидаты: ФИО и email.
- `competencies` - 9 компетенций аналитика данных и стартовая BKT-вероятность.
- `tasks` - финальный банк заданий: текст, тип, сложность, варианты ответа, правильный ответ, критерии.
- `interview_sessions` - прохождения интервью кандидатом, итоговый индекс общей компетентности и расширенный профиль компетенций.
- `bkt_states` - текущая вероятность владения компетенцией внутри сессии.
- `answers` - ответы кандидата, score, feedback и BKT до/после ответа.
- `behavior_features` - агрегированные признаки поведения для ML-античит-модуля.

## Запуск
cd postgres
docker compose up -d

Подключение:
postgresql://postgres_user:postgres_password@localhost:5432/interview_db

Для визуального просмотра таблиц можно подключиться внешним SQL-клиентом,
например DBeaver:
Host: localhost
Port: 5432
Database: interview_db
Username: postgres_user
Password: postgres_password

## Что происходит при первом запуске

PostgreSQL выполняет init-скрипты только при первом создании Docker volume:

1. `01_schema.sql` создает структуру таблиц.
2. `03_full_task_bank.sql` загружает 9 компетенций и 135 активных заданий.

## Проверка банка заданий
cd postgres
docker compose exec postgres psql -U postgres_user -d interview_db -c "
SELECT c.code, t.difficulty, COUNT(*) AS tasks_count
FROM tasks t
JOIN competencies c ON c.id = t.competency_id
WHERE t.is_active = TRUE
GROUP BY c.code, t.difficulty
ORDER BY c.code, t.difficulty;
"

Ожидаемый результат: по каждой из 9 компетенций должно быть по 5 заданий
`easy`, `medium`, `hard`; всего 135 активных заданий.

## Пересоздать локальную базу с нуля

PostgreSQL выполняет init-скрипты только при первом создании Docker volume.
Если локальная база уже использовалась для тестов, ее можно пересоздать:
cd postgres
docker compose down -v
docker compose up -d
