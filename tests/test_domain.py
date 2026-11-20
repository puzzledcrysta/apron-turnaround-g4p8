from decimal import Decimal
import pytest
from app.errors import DataError
from app.modules.domain.rule import evaluate


def test_overlap(cases):
    actual, evidence = evaluate(cases['overlap'])
    assert actual == Decimal('45')
    assert evidence and all(len(row)==3 for row in evidence)


def test_boundary(cases):
    # TRAP-23
    actual, evidence = evaluate(cases['boundary'])
    assert actual == Decimal('30')


def test_full(cases):
    actual, evidence = evaluate(cases['full'])
    assert actual == Decimal('0')


def test_cross_day(cases):
    actual, evidence = evaluate(cases['cross_day'])
    assert actual == Decimal('30')


def test_round_up(cases):
    actual, evidence = evaluate(cases['round_up'])
    assert actual == Decimal('30')


def test_reversed(cases):
    with pytest.raises(DataError) as error:
        evaluate(cases['reversed'])
    assert error.value.code in {'BUSINESS','NUMBER','RANGE','TIMESTAMP'}


def test_bad_block(cases):
    with pytest.raises(DataError) as error:
        evaluate(cases['bad_block'])
    assert error.value.code in {'BUSINESS','NUMBER','RANGE','TIMESTAMP'}

