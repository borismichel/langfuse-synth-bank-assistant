"""
PII redaction — scrub customer data before a span leaves the process (ENT-03).

Customers type card numbers, account numbers, national IDs and one-time codes
into a banking assistant as a matter of course. The agent still sees the real
text; the copy exported to Langfuse carries `[REDACTED_CARD ****1111]` instead
(last four digits only). The guarantee therefore holds no matter who can read
the project.

Wired in `northwind.config.get_langfuse()` through the SDK's `mask_otel_spans=`
hook (Python SDK >= 4.9). It runs at export time over the final OpenTelemetry
attributes of every span the Langfuse client exports — including spans from
third-party instrumentation such as the LangChain callback handler. The legacy
`mask=` hook only sees data set through Langfuse SDK calls.

Layering in production:
  1. this client-side hook (per application, before data leaves the pod)
  2. server-side ingestion masking (Enterprise: LANGFUSE_INGESTION_MASKING_CALLBACK_URL),
     one policy enforced for every team and every SDK
  3. data retention per project (Enterprise), so what is kept expires

Known limits — say them out loud:
  * names and street addresses have no reliable surface form; regex cannot catch
    them (use an NER model or an LLM classifier inside the hook)
  * `user_id` is deliberately left alone: it is a pseudonymous customer handle,
    and the Users view, cost attribution and session grouping depend on it
  * account numbers, national IDs and one-time codes are anchored on their keyword
    (within 20 characters), so a number echoed later WITHOUT it is not caught:
    "Your ID 1020304050 cannot be changed" passes through. A bare digit run cannot be
    told from an amount or an internal id; only values with their own shape (Luhn-valid
    cards, IBANs, SSNs, phone numbers, emails) are caught without a keyword
"""

from __future__ import annotations

import os
import json
import re
from typing import Optional


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, char in enumerate(digits):
        value = int(char)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _redact_card(match: "re.Match[str]") -> str:
    """Redact a 13-19 digit run only when it passes Luhn, as every real PAN does.

    Shape alone is not enough: transaction ids and epoch-ms timestamps look the
    same, and redacting those would corrupt data nobody asked to protect.

    The last four digits stay ("[REDACTED_CARD ****1111]"). A PAN truncated to its
    last four is how customers and agents name a card (PCI DSS permits showing
    them), and the assistant answers with them ("your card ending 1111"). Without
    them a judge grading against the masked context marks that true claim as
    unsupported.
    """
    text = match.group(0)
    digits = re.sub(r"\D", "", text)
    return f"[REDACTED_CARD ****{digits[-4:]}]" if _luhn(digits) else text


# Balance and statement phrasing around an account keyword: the digits are money, not
# an account number — "account balance is 12500 00 USD", "cuenta con saldo 12500 00",
# "el saldo de la cuenta es 12500 00 USD". Redacting them corrupts exactly the figures
# the judges grade. "What's the balance of account 12345678?" is still redacted: the
# balance word comes first and nothing says "is", so the number names the account.
_MONEY_AFTER_KEYWORD = re.compile(r"(?i)\b(balance|saldo|statement|extracto)\b")
_MONEY_BEFORE_KEYWORD = re.compile(r"(?i)\b(balance|saldo) (of|de|en)( the| my| your| la| su| mi| tu)? $")
_COPULA = re.compile(r"(?i)\b(is|was|es|era|fue)\b|[:=]")


def _redact_account(match: "re.Match[str]") -> str:
    """Redact the number after an account keyword, keeping the keyword — unless the
    phrase is a balance or statement figure (see above)."""
    label = match.group(1)
    before = match.string[max(0, match.start() - 24):match.start()]
    if _MONEY_AFTER_KEYWORD.search(label) or (_MONEY_BEFORE_KEYWORD.search(before) and _COPULA.search(label)):
        return match.group(0)
    return f"{label}[REDACTED_ACCOUNT]"


def _keep_label(replacement: str):
    """Replacement that keeps the anchoring keyword ("account 12345678" →
    "account [REDACTED_ACCOUNT]"), so the redacted text still reads naturally
    for humans and for LLM judges grading it."""
    return lambda m: f"{m.group(1)}{replacement}"


# Accented Spanish keywords also match their JSON-escaped form (c\u00e9dula): LangChain
# serialises message lists with escaped Unicode, so the exported attribute never
# contains the literal "é".
# ORDER MATTERS — most specific first. The card pattern is a long digit run and
# would otherwise eat account numbers and phone numbers.
_PATTERNS: "list[tuple[str, re.Pattern[str], object]]" = [
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b"), "[REDACTED_EMAIL]"),
    # One-time passcodes and PINs: always anchored on the keyword, never bare digits.
    ("otp", re.compile(r"(?i)(\b(?:otp|one[- ]time (?:code|password|passcode)|verification code|security code|"
                       r"pin|cvv|cvc|c(?:[oó]|\\u00f3)digo(?: de (?:verificaci(?:[oó]|\\u00f3)n|seguridad|un solo uso))?|clave(?: din(?:[aá]|\\u00e1)mica)?)"
                       r"\b[^0-9]{0,20}?)\d{3,8}\b"),
     _keep_label("[REDACTED_SECRET]")),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,3})?\b"),
     "[REDACTED_IBAN]"),
    # US SSN
    ("national_id", re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_NATIONAL_ID]"),
    # Any national id that follows its keyword (cédula, DNI, CPF, passport …). The
    # value must CONTAIN DIGITS — "national ID number is 1020304050" must redact the
    # number, not the word "number" (a case-insensitive letters-only match did that).
    ("national_id", re.compile(r"(?i)(\b(?:national id|id number|id no\.?|ssn|passport|c(?:[ée]|\\u00e9)dula(?: de ciudadan(?:[ií]|\\u00ed)a)?|"
                               r"documento(?: de identidad)?|dni|cpf|curp|rut|nit|tax id)\b[^0-9]{0,20}?)"
                               r"([A-Z]{0,3}\d[\d.\-]{4,17}[A-Z]?)\b"),
     _keep_label("[REDACTED_NATIONAL_ID]")),
    ("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), _redact_card),
    # Account numbers: anchored on the keyword so balances, amounts and internal
    # ids (ACC-1001-01, TX-88101) survive. English and Spanish.
    ("account", re.compile(r"(?i)(\b(?:account|acct|a/c|n(?:[uú]|\\u00fa)mero de cuenta|cuenta)\b[^0-9]{0,20}?)"
                           r"\d[\d -]{6,18}\d\b"),
     _redact_account),
    ("phone", re.compile(r"\+\d{1,3}[\s.\-()]*(?:\d[\s.\-()]*){6,14}\d"), "[REDACTED_PHONE]"),
    ("phone", re.compile(r"\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b"), "[REDACTED_PHONE]"),
]

MARKER_ATTRIBUTE = "langfuse.observation.metadata.pii_redacted"

_PAYLOAD_ATTRIBUTES = (
    "langfuse.observation.input",
    "langfuse.observation.output",
    "langfuse.trace.input",
    "langfuse.trace.output",
)


def enabled() -> bool:
    """Default ON — a redaction control defaults closed."""
    return os.environ.get("LANGFUSE_MASK_PII", "true").strip().lower() not in (
        "false", "0", "no", "off")


def scrub(text: str) -> "tuple[str, set[str]]":
    """Redact known PII in `text`; return (masked_text, categories_that_fired)."""
    found: "set[str]" = set()
    for category, pattern, replacement in _PATTERNS:
        before = text
        text = pattern.sub(replacement, text)
        if text != before:
            found.add(category)
    return text, found


# These markers are set by the named application callsites, never copied from
# customer/model data. The export hook also checks SDK scope and span context.
CONDUCT_EVIDENCE_SCHEMA = "northwind-conduct-evidence-v1"
APPLICATION_EVIDENCE_SCHEMA = "northwind-application-evidence-v1"
CALIBRATION_EVIDENCE_SCHEMA = "northwind-calibration-evidence-v1"
COMPLAINT_EVIDENCE_SCHEMA = "northwind-complaint-evidence-v1"
VOICE_EVIDENCE_SCHEMA = "northwind-voice-evidence-v1"
_EVIDENCE_ATTRIBUTE = "langfuse.observation.metadata.northwind_evidence_schema"
_ROOT_ATTRIBUTE = "langfuse.experiment.item.root_observation_id"
_EXPERIMENT_METADATA = "langfuse.experiment.metadata"
_OUTPUT_ID_PATHS = {
    CONDUCT_EVIDENCE_SCHEMA: {
        ("trace_id",):32, ("observation_id",):16,
        ("source_trace_id",):32, ("source_observation_id",):16,
        ("calls", "*", "generation_id"):16, ("calls", "*", "generation_trace_id"):32,
    },
    APPLICATION_EVIDENCE_SCHEMA: {("trace_id",):32, ("observation_id",):16},
    CALIBRATION_EVIDENCE_SCHEMA: {
        ("source_trace_id",):32, ("source_observation_id",):16,
        ("generation_id",):16, ("generation_trace_id",):32,
    },
    COMPLAINT_EVIDENCE_SCHEMA: {("trace_id",):32, ("observation_id",):16},
}
_CALIBRATION_ITEM_PATHS = {("source_trace_id",):32, ("source_observation_id",):16}


def _owned_schema(span):
    """Only an app marker on the expected SDK span grants output exemptions."""
    if span.instrumentation_scope_name != "langfuse-sdk":
        return None
    schemas = {json.dumps(schema):schema for schema in (*_OUTPUT_ID_PATHS, VOICE_EVIDENCE_SCHEMA)}
    schema = schemas.get(span.attributes.get(_EVIDENCE_ATTRIBUTE))
    if schema in (CONDUCT_EVIDENCE_SCHEMA, APPLICATION_EVIDENCE_SCHEMA, CALIBRATION_EVIDENCE_SCHEMA):
        return schema if span.attributes.get(_ROOT_ATTRIBUTE) == span.span_id else None
    names = {COMPLAINT_EVIDENCE_SCHEMA:"n8n-complaint-triage", VOICE_EVIDENCE_SCHEMA:"northwind-voice-call"}
    return schema if schema in names and span.name == names[schema] else None


def _evidence_output(text: str, paths: dict) -> "tuple[str, set[str]]":
    """Mask payload leaves while retaining the owned schema's exact linkage IDs."""
    try:
        output = json.loads(text)
    except (ValueError, TypeError):
        return scrub(text)
    if not isinstance(output, dict):
        return scrub(text)
    hits, links = set(), {}
    # Shield only validated schema paths. A collision-free sentinel lets the
    # normal whole-document scrub retain keyword context (e.g. {"PIN": 1234}).
    prefix = "NORTHWINDEVIDENCELINK"
    rendered = json.dumps(output, ensure_ascii=False)
    while prefix in rendered:
        prefix += "X"

    def visit(value, path=()):
        if isinstance(value, str):
            lookup = ("calls", "*", path[2]) if len(path) == 3 and path[0] == "calls" else path
            length = paths.get(lookup)
            if length and re.fullmatch(r"[0-9a-f]{%d}" % length, value):
                token = prefix + str(len(links))
                links[token] = value
                return token
            return value
        if isinstance(value, dict):
            return {key: visit(item, (*path, key)) for key, item in value.items()}
        if isinstance(value, list):
            return [visit(item, (*path, index)) for index, item in enumerate(value)]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            # Keep a redacted numeric payload valid JSON; ordinary metrics retain
            # their numeric type. Include its field label for PIN/account rules.
            label = str(path[-1]) + ": " if path else ""
            masked, found = scrub(label + str(value))
            hits.update(found)
            return masked[len(label):] if found else value
        return value

    masked, found = scrub(json.dumps(visit(output), ensure_ascii=False))
    hits.update(found)
    for token, value in links.items():
        masked = masked.replace(json.dumps(token), json.dumps(value))
    return (masked, hits) if hits else (text, hits)



def mask_otel_spans(*, params) -> Optional[object]:
    """Export-stage hook: return sparse patches for spans whose strings changed.

    Never raises — an exception here makes the SDK drop the whole export batch.
    A per-span failure strips that span's payloads instead (fail closed).
    """
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        try:
            replacements: dict = {}
            hits: "set[str]" = set()
            sdk_span = span.instrumentation_scope_name == "langfuse-sdk"
            schema = _owned_schema(span)
            # Calibration owns its experiment metadata. The SDK propagates that
            # reserved attribute with item metadata across independent batches.
            experiment_metadata = span.attributes.get(_EXPERIMENT_METADATA, "{}")
            try:
                calibration = sdk_span and json.loads(experiment_metadata).get("northwind_evidence_schema") == CALIBRATION_EVIDENCE_SCHEMA
            except (ValueError, TypeError, AttributeError):
                calibration = False
            for key, value in span.attributes.items():
                if isinstance(value, str):
                    # The SDK propagates this reserved attribute to task, wrapper
                    # and child spans, which may export in different batches.
                    # Payload keys and other instrumentation never get this exemption.
                    if sdk_span and key == _ROOT_ATTRIBUTE and re.fullmatch(r"[0-9a-f]{16}", value):
                        continue
                    if (schema == VOICE_EVIDENCE_SCHEMA and key == "langfuse.observation.metadata.assistant_observation_id"
                            and re.fullmatch(r'"[0-9a-f]{16}"', value)):
                        continue
                    paths = (_OUTPUT_ID_PATHS.get(schema) if key == "langfuse.observation.output" else
                        _CALIBRATION_ITEM_PATHS if calibration and key == "langfuse.experiment.item.metadata" else None)
                    masked, found = _evidence_output(value, paths) if paths else scrub(value)
                    if found:
                        replacements[key] = masked
                        hits |= found
                elif (isinstance(value, (list, tuple)) and value
                      and all(isinstance(item, str) for item in value)):
                    masked_items, changed = [], False
                    for item in value:
                        masked_item, found = scrub(item)
                        masked_items.append(masked_item)
                        hits |= found
                        changed = changed or bool(found)
                    if changed:
                        replacements[key] = masked_items
            if replacements:
                replacements[MARKER_ATTRIBUTE] = json.dumps(",".join(sorted(hits)))
                patches[identifier] = OtelSpanPatch(set_attributes=replacements)
        except Exception as exc:  # noqa: BLE001 — must not break the batch
            patches[identifier] = OtelSpanPatch(
                delete_attributes=_PAYLOAD_ATTRIBUTES,
                set_attributes={MARKER_ATTRIBUTE: json.dumps(f"error: {type(exc).__name__}")},
            )
    return MaskOtelSpansResult(span_patches=patches) if patches else None
