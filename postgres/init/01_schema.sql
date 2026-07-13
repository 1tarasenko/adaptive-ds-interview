-- Таблица кандидатов.
-- Здесь хранится базовая информация о человеке, который проходит интервью.
CREATE TABLE IF NOT EXISTS candidates (
    id BIGSERIAL PRIMARY KEY, -- Уникальный идентификатор кандидата. PRIMARY KEY гарантирует уникальность.
    full_name TEXT NOT NULL, -- Полное имя кандидата.
    email TEXT NOT NULL UNIQUE, -- email кандидата; UNIQUE запрещает дубли одного и того же email
    created_at TIMESTAMPTZ NOT NULL DEFAULT now() -- Временная метка создания записи
);

-- Таблица компетенций.
-- Компетенция - это навык, который мы хотим оценивать через задания и BKT.
CREATE TABLE IF NOT EXISTS competencies (
    id BIGSERIAL PRIMARY KEY, -- внутренний уникальный ID компетенции
    code TEXT NOT NULL UNIQUE, -- код компетенции, например sql_databases - нужен backend-у, чтобы удобно ссылаться на навык
    title TEXT NOT NULL, -- человекочитаемое название для отображения в интерфейсе
    group_name TEXT NOT NULL, -- крупная группа компетенций: Подготовка данных, Анализ данных и т.д.
    description TEXT, -- описание, что именно проверяет компетенция
    initial_probability NUMERIC(5, 4) NOT NULL DEFAULT 0.4500
        CHECK (initial_probability >= 0 AND initial_probability <= 1), -- стартовая вероятность BKT 45%;
    is_active BOOLEAN NOT NULL DEFAULT TRUE -- флаг, который позволяет отключать компетенцию без удаления из базы (например, если она устарела или была заменена другой)
);

-- Таблица заданий.
-- Здесь хранится банк вопросов/кейсов для интервью.
CREATE TABLE IF NOT EXISTS tasks (
    id BIGSERIAL PRIMARY KEY, -- внутренний уникальный ID задания
    competency_id BIGINT NOT NULL REFERENCES competencies(id), -- к какой компетенции относится задание
    title TEXT NOT NULL, -- короткое название задания
    question_text TEXT NOT NULL,  -- полный текст вопроса/кейса для кандидата
    task_type TEXT NOT NULL
        CHECK (task_type IN ('text', 'single_choice', 'multiple_choice', 'matching')),
        -- тип задания:
        -- text            = развернутый текстовый ответ
        -- single_choice   = один вариант ответа
        -- multiple_choice = несколько вариантов
        -- matching        = сопоставление
    difficulty TEXT NOT NULL
        CHECK (difficulty IN ('easy', 'medium', 'hard')), -- сложность задания
    options JSONB NOT NULL DEFAULT '[]'::jsonb, -- варианты ответов для типов single_choice и multiple_choice. Для text-заданий обычно пустой массив []
    correct_answer JSONB, -- правильный ответ в структурированном виде. Для text-заданий может быть null, для single_choice - индекс правильного варианта, для multiple_choice - массив индексов, для matching - массив пар.
    rubric JSONB NOT NULL DEFAULT '{}'::jsonb, -- критерии проверки открытого ответа. Для тестовых заданий обычно пустой объект {}
    is_active BOOLEAN NOT NULL DEFAULT TRUE, -- можно ли выдавать задание кандидату
    created_at TIMESTAMPTZ NOT NULL DEFAULT now() -- когда задание было добавлено в базу
);

-- Таблица сессий интервью.
-- Одна запись = одно прохождение интервью конкретным кандидатом.
CREATE TABLE IF NOT EXISTS interview_sessions (
    id BIGSERIAL PRIMARY KEY, -- внутренний уникальный ID сессии
    candidate_id BIGINT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
        -- кандидат, который проходит интервью;
        -- ON DELETE CASCADE значит: если удалить кандидата, его сессии тоже удалятся
    status TEXT NOT NULL DEFAULT 'in_progress'
        CHECK (status IN ('in_progress', 'finished', 'abandoned')),
        -- статус сессии:
        -- in_progress = идёт
        -- finished    = завершена
        -- abandoned   = брошена/прервана
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- время начала интервью
    finished_at TIMESTAMPTZ, -- время завершения интервью
    final_score NUMERIC(5, 4)
        CHECK (final_score IS NULL OR (final_score >= 0 AND final_score <= 1)), -- итоговый индекс общей компетентности от 0 до 1, рассчитанный по score-профилю компетенций
    final_bkt_profile JSONB NOT NULL DEFAULT '[]'::jsonb, -- расширенный итоговый профиль по компетенциям: score, уровень, BKT-вероятность и число оцененных заданий
    anomaly_probability NUMERIC(5, 4)
        CHECK (anomaly_probability IS NULL OR (anomaly_probability >= 0 AND anomaly_probability <= 1)),  -- итоговая вероятность аномального поведения
    anomaly_risk TEXT CHECK (anomaly_risk IS NULL OR anomaly_risk IN ('low', 'medium', 'high')), -- текстовый уровень риска: low / medium / high
    report_text TEXT -- итоговый текст отчета по сессии
);

-- Таблица текущих BKT-состояний.
-- Хранит вероятность владения каждой компетенцией в рамках конкретной сессии.
CREATE TABLE IF NOT EXISTS bkt_states (
    id BIGSERIAL PRIMARY KEY, -- внутренний уникальный ID записи BKT
    session_id BIGINT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE, -- сессия интервью, удаление сессии удаляет связанные BKT-состояния
    competency_id BIGINT NOT NULL REFERENCES competencies(id), -- компетенция, для которой хранится вероятность
    probability NUMERIC(5, 4) NOT NULL
        CHECK (probability >= 0 AND probability <= 1), -- текущая BKT-вероятность владения компетенцией
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- когда вероятность обновлялась последний раз
    UNIQUE (session_id, competency_id) -- в одной сессии по одной компетенции должна быть только одна текущая вероятность
);

-- Таблица ответов кандидата.
-- Одна запись = один ответ на одно задание в рамках одной сессии.
CREATE TABLE IF NOT EXISTS answers (
    id BIGSERIAL PRIMARY KEY, -- внутренний уникальный ID ответа
    session_id BIGINT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE, -- сессия, в которой был дан ответ, удаление сессии удаляет связанные ответы
    task_id BIGINT NOT NULL REFERENCES tasks(id), -- задание, на которое отвечал кандидат
    competency_id BIGINT NOT NULL REFERENCES competencies(id),
        -- компетенция задания;
        -- дублируется из tasks
    position_in_session INTEGER NOT NULL CHECK (position_in_session > 0), -- порядковый номер ответа внутри сессии: 1, 2, 3...
    answer_text TEXT, -- текстовое представление ответа кандидата
    answer_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        -- структурированный ответ;
        -- например выбранные option_id, option_ids или matches
    is_correct BOOLEAN, -- правильно ли выполнено задание
    score NUMERIC(5, 4) CHECK (score IS NULL OR (score >= 0 AND score <= 1)), -- численная оценка ответа от 0 до 1
    llm_feedback TEXT,
        -- текстовый feedback по ответу;
        -- для открытых ответов сюда сохраняется результат локальной LLM
    bkt_before NUMERIC(5, 4)
        CHECK (bkt_before IS NULL OR (bkt_before >= 0 AND bkt_before <= 1)), -- BKT-вероятность до этого ответа
    bkt_after NUMERIC(5, 4)
        CHECK (bkt_after IS NULL OR (bkt_after >= 0 AND bkt_after <= 1)), -- BKT-вероятность после этого ответа
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- когда кандидат начал отвечать на задание
    submitted_at TIMESTAMPTZ, -- когда кандидат отправил ответ
    response_time_sec INTEGER CHECK (response_time_sec IS NULL OR response_time_sec >= 0), -- время ответа в секундах
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- когда запись ответа появилась в базе
    UNIQUE (session_id, position_in_session) -- в одной сессии не может быть двух ответов с одинаковым порядковым номером
);

-- Таблица агрегированных поведенческих признаков.
-- Здесь хранятся не сырые события браузера, а уже рассчитанные признаки для античит-модуля.
CREATE TABLE IF NOT EXISTS behavior_features (
    id BIGSERIAL PRIMARY KEY, -- внутренний уникальный ID строки с признаками
    session_id BIGINT NOT NULL REFERENCES interview_sessions(id) ON DELETE CASCADE, -- сессия, к которой относятся признаки
    answer_id BIGINT REFERENCES answers(id) ON DELETE CASCADE, -- конкретный ответ, по которому рассчитаны признаки
    response_time_sec NUMERIC(10, 3) NOT NULL DEFAULT 0, -- общее время ответа в секундах
    tab_switch_count INTEGER NOT NULL DEFAULT 0, -- сколько раз кандидат уходил со страницы/вкладки
    total_hidden_time_sec NUMERIC(10, 3) NOT NULL DEFAULT 0, -- сколько секунд страница была скрыта суммарно
    hidden_time_ratio NUMERIC(7, 6) NOT NULL DEFAULT 0 CHECK (hidden_time_ratio >= 0 AND hidden_time_ratio <= 1),
        -- доля времени вне страницы:
        -- total_hidden_time_sec / response_time_sec
        -- отношение времени, когда страница была скрыта, к общему времени сессии
    paste_count INTEGER NOT NULL DEFAULT 0, -- количество вставок текста из буфера обмена
    pasted_chars_total INTEGER NOT NULL DEFAULT 0, -- общее количество вставленных символов
    paste_ratio NUMERIC(7, 6) NOT NULL DEFAULT 0 CHECK (paste_ratio >= 0 AND paste_ratio <= 1),
        -- доля вставленного текста:
        -- pasted_chars_total / answer_length_chars
        -- отношение количества вставленных символов к общему количеству символов в ответе
    input_event_count INTEGER NOT NULL DEFAULT 0,
        -- количество событий изменения ответа
    delete_count INTEGER NOT NULL DEFAULT 0, -- количество удалений/правок
    max_pause_sec NUMERIC(10, 3) NOT NULL DEFAULT 0, -- максимальная пауза между вводом в секундах
    max_input_burst_chars INTEGER NOT NULL DEFAULT 0, -- максимальный резкий прирост длины ответа за одно изменение
    copy_count INTEGER NOT NULL DEFAULT 0, -- количество копирований текста со страницы
    answer_length_chars INTEGER NOT NULL DEFAULT 0, -- итоговая длина ответа в символах
    anomaly_probability NUMERIC(5, 4)
        CHECK (anomaly_probability IS NULL OR (anomaly_probability >= 0 AND anomaly_probability <= 1)),
        -- вероятность аномального поведения от 0 до 1
    anomaly_risk TEXT CHECK (anomaly_risk IS NULL OR anomaly_risk IN ('low', 'medium', 'high')), -- уровень риска: low / medium / high
    created_at TIMESTAMPTZ NOT NULL DEFAULT now() -- когда признаки были записаны в базу
);
