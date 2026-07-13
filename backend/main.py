"""HTTP API backend-а.

Этот файл является входной точкой FastAPI-приложения. Фронтенд обращается
именно к этим адресам: создать сессию, получить задание, отправить ответ,
завершить интервью.

Вся бизнес-логика лежит в service.py. main.py только принимает
HTTP-запрос, вызывает нужную функцию сервиса и возвращает результат.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import service
from .schemas import FinishSessionRequest, StartSessionRequest, SubmitAnswerRequest


app = FastAPI(
    title="Adaptive Interview Backend",
    description="Backend для MVP адаптивного технического интервью по ВКР.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def healthcheck():
    """Проверяет, что backend жив и база данных доступна."""

    return service.healthcheck()


@app.get("/competencies")
def list_competencies():
    """Возвращает список активных компетенций из базы данных."""

    return service.list_competencies()


@app.get("/tasks")
def list_tasks():
    """Возвращает список активных заданий.

    Endpoint нужен для отладки и просмотра банка вопросов.
    """

    return service.list_tasks()


@app.post("/sessions")
def start_session(payload: StartSessionRequest):
    """Создает кандидата и новую сессию интервью.

    из frontend-а: ФИО и email кандидата.
    В ответ backend возвращает созданную сессию, стартовые BKT-состояния и
    первое задание.
    """

    try:
        return service.start_session(payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/sessions/{session_id}")
def get_session(session_id: int):
    """Возвращает текущее состояние сессии по ее id.

    Здесь можно получить статус интервью, ответы кандидата, BKT-состояния и
    итоговый отчет, если сессия уже завершена.
    """

    try:
        return service.get_session(session_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.get("/sessions/{session_id}/next-task")
def get_next_task(session_id: int):
    """Возвращает следующее задание для активной сессии.

    Выбор задания делается в service.py: backend смотрит на BKT-состояния и
    не продолжает давить на компетенцию, уже зафиксированную как слабую.
    """

    try:
        task = service.get_next_task(session_id)
        return {"next_task": task}
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/sessions/{session_id}/answers")
def submit_answer(session_id: int, payload: SubmitAnswerRequest):
    """Принимает один ответ кандидата.

    Backend сохраняет ответ, оценивает его, пересчитывает BKT, сохраняет
    поведенческие признаки и возвращает следующее задание.
    """

    try:
        return service.submit_answer(session_id, payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@app.post("/sessions/{session_id}/finish")
def finish_session(session_id: int, payload: FinishSessionRequest | None = None):
    """Завершает интервью вручную.

    Обычно сессия завершается автоматически, когда задания закончились или
    достигнут лимит. Этот endpoint нужен для кнопки "завершить" на frontend-е.
    """

    try:
        report_text = payload.report_text if payload else None
        return service.finish_session(session_id, report_text)
    except Exception as exc:
        raise _http_error(exc) from exc


def _http_error(exc: Exception) -> HTTPException:
    """Переводит ошибки бизнес-логики в понятные HTTP-ответы."""

    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))

    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))

    return HTTPException(status_code=500, detail=str(exc))
