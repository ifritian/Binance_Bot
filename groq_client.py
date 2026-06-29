"""
Общая обёртка над вызовом Groq chat completions.

Раньше каждый генератор (text_generator, opinion_generator, image_analyzer,
article_generator) делал requests.post(...).raise_for_status() сам по себе -
четыре копии одного и того же кода, и ни одна не отличала 429 (rate limit)
от любой другой ошибки. Из-за этого, например, временный 429 на статье
тушился одним и тем же фиксированным backoff'ом, без учёта Retry-After
от самого Groq, а на валютных постах 429 вообще не давал backoff и мог
за 3 быстрых попытки (MAX_PUBLISH_ATTEMPTS) выбросить совершенно
нормальный сигнал из очереди.
"""
import logging

import requests

import config

logger = logging.getLogger(__name__)

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

# Если Groq не прислал Retry-After - ждём вот столько (секунд) на всякий
# случай, чтобы не долбить API сразу на следующем тике.
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 60 * 5


class GroqRateLimited(Exception):
    """429 от Groq - превышен лимит запросов/токенов.

    retry_after_seconds - сколько секунд ждать, по данным самого Groq
    (заголовок Retry-After), либо DEFAULT_RATE_LIMIT_BACKOFF_SECONDS,
    если заголовка нет.
    """

    def __init__(self, retry_after_seconds: float):
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Groq rate limit, retry after {retry_after_seconds:.0f}s")


def call_groq(system_prompt: str, user_prompt: str, max_tokens: int = 600,
              temperature: float = 0.8, model: str | None = None, timeout: int = 45) -> str:
    """Делает один chat-completion запрос к Groq и возвращает текст ответа.

    Поднимает GroqRateLimited при 429 (с retry_after_seconds, посчитанным
    из заголовка Retry-After, если он есть). Любая другая ошибка HTTP или
    сети поднимается как обычно (requests.RequestException) - вызывающий
    код это уже умеет ловить.
    """
    payload = {
        "model": model or config.GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {config.GROQ_API_KEY}"}

    resp = requests.post(GROQ_ENDPOINT, json=payload, headers=headers, timeout=timeout)

    if resp.status_code == 429:
        retry_after = _parse_retry_after(resp)
        logger.warning(
            "Groq вернул 429 (rate limit) - жду %.0fс перед следующей попыткой "
            "(Retry-After из ответа: %s)",
            retry_after, resp.headers.get("Retry-After", "нет заголовка"),
        )
        raise GroqRateLimited(retry_after)

    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()


def _parse_retry_after(resp: requests.Response) -> float:
    raw = resp.headers.get("Retry-After")
    if raw:
        try:
            return max(float(raw), 1.0)
        except ValueError:
            pass
    return DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
