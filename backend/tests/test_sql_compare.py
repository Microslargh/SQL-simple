import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "scripts" / "eval"))

from sql_compare import format_sql_display, normalize_sql, sql_matches_golden


def test_normalize_sql_basic():
    a = "SELECT  *  FROM t WHERE id=1"
    b = "select * from t where id = 1"
    assert normalize_sql(a) == normalize_sql(b)


def test_sql_matches_golden():
    assert sql_matches_golden("SELECT a FROM t", "select a from t")
    assert not sql_matches_golden("SELECT a FROM t", "select b from t")


def test_format_sql_display_nonempty():
    s = format_sql_display("SELECT 1 FROM dual")
    assert "SELECT" in s
    assert "1" in s
