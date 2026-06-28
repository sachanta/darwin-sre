"""Tests for the degradation detector (Option C: Arize + local fallback)."""
import pytest
from collections import deque
from unittest.mock import patch, MagicMock


class TestShouldTriggerLocal:
    """Local fallback path — no Arize calls."""

    def _trigger(self, scores):
        from darwin.degradation import should_trigger
        with patch("darwin.degradation._fetch_arize_scores", return_value=None):
            return should_trigger(deque(scores, maxlen=3))

    def test_triggers_when_avg_below_threshold(self):
        # Three scores all low → rolling avg 0.30 < 0.60
        result = self._trigger([0.30, 0.28, 0.32])
        assert result.should_trigger is True
        assert result.source == "local"

    def test_does_not_trigger_above_threshold(self):
        result = self._trigger([0.80, 0.85, 0.90])
        assert result.should_trigger is False

    def test_does_not_trigger_on_partial_window(self):
        # Only 2 scores in a window that requires 3 — must NOT fire
        d = deque([0.10, 0.10], maxlen=3)
        from darwin.degradation import should_trigger
        with patch("darwin.degradation._fetch_arize_scores", return_value=None):
            result = should_trigger(d)
        assert result.should_trigger is False

    def test_rolling_avg_correct(self):
        result = self._trigger([0.40, 0.50, 0.60])
        assert abs(result.rolling_avg - 0.50) < 0.001

    def test_empty_window_does_not_trigger(self):
        result = self._trigger([])
        assert result.should_trigger is False
        assert result.rolling_avg == 1.0

    def test_exactly_at_threshold_does_not_trigger(self):
        # avg == 0.60 → NOT < threshold → no trigger
        result = self._trigger([0.60, 0.60, 0.60])
        assert result.should_trigger is False

    def test_window_scores_returned(self):
        result = self._trigger([0.20, 0.25, 0.30])
        assert len(result.window_scores) == 3

    def test_source_is_local(self):
        result = self._trigger([0.80, 0.85, 0.90])
        assert result.source == "local"


class TestShouldTriggerArize:
    """Arize primary path — mock HTTP calls."""

    def _arize_scores(self, scores: list[float]):
        """Patch _fetch_arize_scores to return the given scores."""
        return patch("darwin.degradation._fetch_arize_scores", return_value=scores)

    def test_arize_path_used_when_available(self):
        from darwin.degradation import should_trigger
        with self._arize_scores([0.20, 0.25, 0.30]):
            result = should_trigger(deque([0.80, 0.85, 0.90], maxlen=3))
        assert result.source == "arize"
        assert result.should_trigger is True

    def test_arize_triggers_even_if_local_ok(self):
        # Local window looks fine but Arize says bad
        from darwin.degradation import should_trigger
        with self._arize_scores([0.10, 0.15, 0.12]):
            result = should_trigger(deque([0.85, 0.90, 0.88], maxlen=3))
        assert result.should_trigger is True
        assert result.source == "arize"

    def test_local_fallback_when_arize_unavailable(self):
        from darwin.degradation import should_trigger
        with self._arize_scores(None):
            result = should_trigger(deque([0.25, 0.30, 0.20], maxlen=3))
        assert result.source == "local"
        assert result.should_trigger is True

    def test_local_fallback_when_arize_returns_empty(self):
        from darwin.degradation import should_trigger
        with self._arize_scores([]):
            result = should_trigger(deque([0.25, 0.30, 0.20], maxlen=3))
        assert result.source == "local"

    def test_arize_ignored_when_fewer_than_window_size(self):
        # Arize returns only 1 score but window requires 3 → falls back to local
        from darwin.degradation import should_trigger
        with self._arize_scores([0.10]):
            result = should_trigger(deque([0.80, 0.85, 0.90], maxlen=3))
        assert result.source == "local"


class TestFetchArizeScores:
    """Unit-test the Arize HTTP fetch in isolation."""

    def test_returns_none_when_no_api_key(self):
        from darwin.degradation import _fetch_arize_scores
        with patch("darwin.degradation.ARIZE_API_KEY", ""), \
             patch("darwin.degradation.ARIZE_SPACE_ID", ""):
            result = _fetch_arize_scores()
        assert result is None

    def test_returns_none_on_non_200(self):
        from darwin.degradation import _fetch_arize_scores
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"
        with patch("darwin.degradation.ARIZE_API_KEY", "key"), \
             patch("darwin.degradation.ARIZE_SPACE_ID", "space"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp
            result = _fetch_arize_scores()
        assert result is None

    def test_parses_score_from_attributes(self):
        from darwin.degradation import _fetch_arize_scores
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "spans": {
                    "edges": [
                        {"node": {"spanId": "a", "startTime": "t", "attributes": {"score.composite": 0.72}}},
                        {"node": {"spanId": "b", "startTime": "t", "attributes": {"score.composite": 0.45}}},
                    ]
                }
            }
        }
        with patch("darwin.degradation.ARIZE_API_KEY", "key"), \
             patch("darwin.degradation.ARIZE_SPACE_ID", "space"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp
            result = _fetch_arize_scores()
        assert result == [0.72, 0.45]

    def test_returns_none_on_http_exception(self):
        from darwin.degradation import _fetch_arize_scores
        with patch("darwin.degradation.ARIZE_API_KEY", "key"), \
             patch("darwin.degradation.ARIZE_SPACE_ID", "space"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.post.side_effect = Exception("timeout")
            result = _fetch_arize_scores()
        assert result is None

    def test_returns_none_when_no_scores_in_attributes(self):
        from darwin.degradation import _fetch_arize_scores
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {"spans": {"edges": [
                {"node": {"spanId": "a", "attributes": {"unrelated_key": "value"}}},
            ]}}
        }
        with patch("darwin.degradation.ARIZE_API_KEY", "key"), \
             patch("darwin.degradation.ARIZE_SPACE_ID", "space"), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.return_value.__enter__.return_value.post.return_value = mock_resp
            result = _fetch_arize_scores()
        assert result is None
