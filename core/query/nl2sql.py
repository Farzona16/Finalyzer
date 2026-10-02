"""Question to answer: generate candidate queries, run them, vote by result hash.

An answer is ``verified`` when more than half of all candidates return the same result.
Offline mode only answers the golden demo questions (no model is involved).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from core.config import Settings
from core.errors import (
    LLMUnavailableError,
    PrivacyBlockedError,
    QueryTimeoutError,
    UserFacingError,
    ValidationError,
)
from core.llm.gateway import LLMGateway
from core.llm.providers import OFFLINE_MESSAGE
from core.models import DatasetModel, Metric, QueryResult
from core.query.anonymizer import IdentifierMask
from core.query.charts import ChartSpec, choose_chart, column_kinds
from core.query.executor import execute_query, result_hash
from core.query.prompts import (
    MAX_QUESTION_CHARS,
    build_messages,
    build_user_payload,
    repair_message,
)

MAX_REPAIRS = 2
DIVERSE_TEMPERATURE = 0.5
Status = Literal["ok", "clarify", "refuse", "offline", "failed"]


class NLAnswer(BaseModel):
    """The JSON the model must return."""

    intent: Literal["query", "clarify", "refuse"]
    sql: str | None = None
    clarification: str | None = None
    chart_hint: str | None = None
    metric_ids: list[str] = []


class Answer(BaseModel):
    """What the UI shows for one question."""

    status: Status
    text: str
    result: QueryResult | None = None
    chart: ChartSpec | None = None
    clarification: str | None = None


@dataclass
class Candidate:
    """One attempt by the model: a result, or the reason there is none."""

    answer: NLAnswer | None = None
    result: QueryResult | None = None
    digest: str | None = None
    error: str | None = None
    unavailable: bool = False


def format_number(value: Any) -> str:
    """Readable number: thousands separators, at most two decimals."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if isinstance(value, int) or float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"


def describe_result(result: QueryResult) -> str:
    """A plain sentence about a result, built from the numbers only."""
    if result.row_count == 0:
        return "The query ran, but no rows matched."
    if result.row_count == 1 and len(result.columns) == 1:
        label = result.columns[0].replace("_", " ").capitalize()
        return f"{label}: {format_number(result.rows[0][0])}."
    kinds = column_kinds(result)
    if len(result.columns) == 2 and kinds[1] == "number" and kinds[0] in ("text", "date"):
        best = max((r for r in result.rows if r[1] is not None), key=lambda r: r[1], default=None)
        if best is not None:
            label = "Highest" if kinds[0] == "text" else "Peak"
            return (
                f"{result.row_count} rows. {label}: {best[0]} "
                f"({format_number(best[1])} {result.columns[1].replace('_', ' ')})."
            )
    bits = [", ".join(format_number(cell) for cell in row) for row in result.rows[:5]]
    preview = "; ".join(bits)
    return f"{result.row_count} rows and {len(result.columns)} columns. Values: {preview}."


def count_summary(agreeing: int, total: int) -> str:
    """Text such as ``2 of 3 candidate queries agreed``."""
    return f"{agreeing} of {total} candidate queries agreed"


class NL2SQLAgent:
    """Turns a question into a verified (or clearly unverified) answer."""

    def __init__(
        self,
        con: duckdb.DuckDBPyConnection,
        model: DatasetModel,
        gateway: LLMGateway,
        settings: Settings,
        metrics: list[Metric],
        demo_key: str | None = None,
    ) -> None:
        self.con = con
        self.model = model
        self.gateway = gateway
        self.settings = settings
        self.metrics = metrics
        self.demo_key = demo_key
        self._metric_ids = {m.id for m in metrics}

    def ask(self, question: str, filters_text: str = "") -> Answer:
        """Answer one question. Never raises for expected problems; returns a status instead."""
        question = question.strip()
        if not question:
            return Answer(status="clarify", text="Please type a question.")
        if len(question) > MAX_QUESTION_CHARS:
            text = f"Please keep the question under {MAX_QUESTION_CHARS} characters."
            return Answer(status="failed", text=text)
        try:
            if self.gateway.is_offline:
                return self._ask_offline(question)
            return self._ask_live(question, filters_text)
        except UserFacingError as exc:
            return Answer(status="failed", text=exc.message)

    def _ask_offline(self, question: str) -> Answer:
        golden = self.gateway.offline.lookup(question, self.demo_key)
        if golden is None:
            return Answer(status="offline", text=OFFLINE_MESSAGE)
        result = execute_query(self.con, golden.expected_sql, self.settings)
        result.warnings.append("Offline demo answer from the built-in question set.")
        return self._ok(result, None)

    def _date_range(self) -> tuple[str, str] | None:
        date_col = self.model.roles.get("date")
        if not date_col:
            return None
        column = '"' + date_col.replace('"', '""') + '"'
        low, high = self.con.execute(f"SELECT MIN({column}), MAX({column}) FROM data").fetchone()
        return (str(low), str(high)) if low is not None else None

    def _ask_live(self, question: str, filters_text: str) -> Answer:
        mask = IdentifierMask.from_model(self.model)
        payload = build_user_payload(
            self.model, self.metrics, question, self.settings, self._date_range(), filters_text
        )
        messages = build_messages(payload, mask)
        first = self._candidate(messages, 0.0, mask)
        if first.answer is not None and first.answer.intent != "query":
            return self._non_query(first.answer, mask)
        extra = max(self.settings.sql_candidates - 1, 0)
        with ThreadPoolExecutor(max_workers=max(extra, 1)) as pool:
            futures = [
                pool.submit(self._candidate, messages, DIVERSE_TEMPERATURE, mask)
                for _ in range(extra)
            ]
            others = [f.result() for f in futures]
        return self._vote([first, *others], mask)

    @staticmethod
    def _non_query(answer: NLAnswer, mask: IdentifierMask) -> Answer:
        if answer.intent == "clarify":
            raw = answer.clarification or "Could you rephrase the question more precisely?"
            text = mask.unmask_text(raw)
            return Answer(status="clarify", text=text, clarification=text)
        raw = answer.clarification or "I can only answer questions that read the data."
        text = mask.unmask_text(raw)
        return Answer(status="refuse", text=text)

    def _candidate(
        self, messages: list[dict[str, str]], temperature: float, mask: IdentifierMask
    ) -> Candidate:
        """One attempt, with up to ``MAX_REPAIRS`` repairs using the exact error."""
        conversation = list(messages)
        error: str | None = None
        for _ in range(MAX_REPAIRS + 1):
            try:
                reply = self.gateway.complete_json("nl2sql", conversation, NLAnswer, temperature)
            except (LLMUnavailableError, PrivacyBlockedError) as exc:
                return Candidate(error=exc.message, unavailable=True)
            if reply.intent != "query":
                return Candidate(answer=reply)
            try:
                ids = [mask.restore_name(i) for i in reply.metric_ids]
                ids = [i for i in ids if i in self._metric_ids]
                sql = mask.unmask_sql(reply.sql or "")
                result = execute_query(self.con, sql, self.settings, ids)
                return Candidate(answer=reply, result=result, digest=result_hash(result.rows))
            except QueryTimeoutError as exc:
                return Candidate(answer=reply, error=exc.message)
            except ValidationError as exc:
                error = exc.message
                conversation = [
                    *conversation,
                    {"role": "assistant", "content": reply.model_dump_json()},
                    {"role": "user", "content": repair_message(mask.mask_error(error))},
                ]
        return Candidate(error=error)

    def _vote(self, candidates: list[Candidate], mask: IdentifierMask) -> Answer:
        done = [c for c in candidates if c.result is not None]
        if not done:
            if all(c.unavailable for c in candidates):
                return Answer(status="failed", text=candidates[0].error or "The AI is unavailable.")
            reason = next((c.error for c in candidates if c.error), "no valid query was produced")
            return Answer(
                status="failed",
                text=f"I could not answer this reliably. Last problem: {mask.unmask_text(reason)}",
            )
        groups: dict[str, list[Candidate]] = {}
        for c in done:
            groups.setdefault(c.digest or "", []).append(c)
        best = max(groups.values(), key=len)  # ties keep the earliest group
        total, agreeing = len(candidates), len(best)
        verified = total == 1 or (agreeing >= 2 and agreeing > total / 2)
        result = best[0].result
        assert result is not None
        result = result.model_copy(
            update={
                "verified": verified,
                "candidates_total": total,
                "candidates_agreeing": agreeing,
                "warnings": list(result.warnings),
            }
        )
        if agreeing < total and verified:
            result.warnings.append(f"{count_summary(agreeing, total)}.")
        if not verified:
            result.warnings.append(
                f"Unverified: only {count_summary(agreeing, total)}. Treat this number with care."
            )
        hint = best[0].answer.chart_hint if best[0].answer else None
        return self._ok(result, hint)

    @staticmethod
    def _ok(result: QueryResult, hint: str | None) -> Answer:
        return Answer(
            status="ok",
            text=describe_result(result),
            result=result,
            chart=choose_chart(result, hint),
        )
