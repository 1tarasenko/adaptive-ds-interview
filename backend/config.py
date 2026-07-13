import os


class Settings:
    """Настройки backend-а из переменных окружения."""

    def __init__(self):
        # Настройки базы данных
        self.database_url = os.getenv(
            "DATABASE_URL",
            "postgresql://postgres_user:postgres_password@localhost:5432/interview_db",
        )
        # Настройки сервера
        # число задач, которые могут выполняться в рамках одной сессии
        self.max_tasks_per_session = int(os.getenv("MAX_TASKS_PER_SESSION", "27"))

        # Настройки LLM
        self.ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        # модель, которая будет использоваться для проверки решений
        self.ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
        # таймаут на запрос к ollama в секундах
        self.ollama_timeout_sec = float(os.getenv("OLLAMA_TIMEOUT_SEC", "60"))
        # пороговое значение оценки решения LLM, ниже которого решение считается некорректным
        self.llm_score_threshold = float(os.getenv("LLM_SCORE_THRESHOLD", "0.65"))

        # Настройки BKT
        self.bkt_learn = float(os.getenv("BKT_LEARN", "0.08")) 
        self.bkt_slip = float(os.getenv("BKT_SLIP", "0.12"))
        self.bkt_guess = float(os.getenv("BKT_GUESS", "0.20"))

        # Настройки компетенций
        self.low_competency_threshold = float(os.getenv("LOW_COMPETENCY_THRESHOLD", "0.40"))
        self.max_low_competency_attempts = int(os.getenv("MAX_LOW_COMPETENCY_ATTEMPTS", "3"))


settings = Settings()
