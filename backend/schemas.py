"""Схемы входных данных для API.

FastAPI использует эти классы, чтобы проверить JSON, который приходит от
frontend-а. Если поле отсутствует или имеет неправильный формат, FastAPI сам
вернет ошибку 422 до вызова service.py.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Field - это способ задать дополнительные ограничения на поля модели. Например,
# min_length=1 означает, что строка не может быть пустой, а pattern задает регулярное выражение, которому должна соответствовать строка.
class RequestModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)


class StartSessionRequest(RequestModel):
    """Данные, которые frontend отправляет при старте интервью."""

    full_name: str = Field(min_length=1, max_length=200, examples=["Иванов Иван"])
    email: str = Field(max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SubmitAnswerRequest(RequestModel):
    """Данные одного ответа кандидата."""

    task_id: int = Field(gt=0)
    answer_text: str | None = None
    answer_payload: dict[str, Any] = Field(default_factory=dict)
    behavior: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    submitted_at: datetime | None = None


class FinishSessionRequest(RequestModel):
    """Данные для ручного завершения интервью."""

    report_text: str | None = None
