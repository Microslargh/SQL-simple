"""Integration tests for multi-turn conversation context fixes."""
from apps.chat.context.extractors import EntityReferenceExtractor
from apps.chat.context.question_enhancer import is_any_follow_up


class TestTimeRangeDisplay:
    """Task 1: Human-readable time display."""

    def test_format_ym_range(self):
        result = EntityReferenceExtractor._format_time_display("202512")
        assert result == "2025年12月"

    def test_format_ymd_single(self):
        result = EntityReferenceExtractor._format_time_display("20251201")
        assert result == "2025年12月01日"

    def test_format_short_string(self):
        result = EntityReferenceExtractor._format_time_display("2025")
        assert result == "2025"


class TestTimeKeywordDetection:
    """Task 3: Time keyword detection for injection."""

    TIME_KEYWORDS = ["年", "月", "日", "时间", "日期", "期", "本月", "本年", "今年", "去年", "前年", "明年"]

    def test_has_time_keyword_explicit(self):
        q = "2025年12月深圳纳税情况"
        assert any(kw in q for kw in self.TIME_KEYWORDS)

    def test_no_time_keyword_comparison(self):
        q = "广东省比江西省的对比情况"
        has_time = any(kw in q for kw in self.TIME_KEYWORDS)
        assert not has_time, f"Unexpected time keyword match in: {q}"

    def test_has_time_keyword_month_only(self):
        q = "7月的呢"
        assert any(kw in q for kw in self.TIME_KEYWORDS)

    def test_no_time_entity_question(self):
        q = "深圳市的纳税情况"
        has_time = any(kw in q for kw in self.TIME_KEYWORDS)
        assert not has_time


class TestFollowUpDetection:
    """Verify follow-up detection not broken by changes."""

    def test_short_region_follow_up(self):
        assert is_any_follow_up("山西省的呢")

    def test_short_metric_follow_up(self):
        assert is_any_follow_up("利润呢？")

    def test_long_comparison_not_follow_up(self):
        assert not is_any_follow_up("广东省的比江西省的对比情况")

    def test_explicit_time_question_not_follow_up(self):
        assert not is_any_follow_up("2025年12月深圳市的纳税情况")
