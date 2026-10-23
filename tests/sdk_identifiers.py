"""Deterministic system randomness for real-SDK structural linkage regressions."""
from itertools import count
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator


def numeric_span_ids(monkeypatch=None):
    """Every unique span ID is valid hex and also a synthetic Luhn-valid digit run."""
    identifiers = count(400000000000000)
    def span_id(generator):
        digits = str(next(identifiers))
        weighted = [int(digit) * (2 if index % 2 == 0 else 1) for index,digit in enumerate(digits)]
        checksum = -sum(value-9 if value>9 else value for value in weighted) % 10
        return int(digits+str(checksum),16)
    if monkeypatch is None:  # Standalone SDK fixture process; exits after its assertions.
        RandomIdGenerator.generate_span_id = span_id
    else:
        monkeypatch.setattr(RandomIdGenerator,'generate_span_id',span_id)
