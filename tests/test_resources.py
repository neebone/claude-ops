# tests/test_resources.py
"""Tests for the resource monitoring module."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path
from claude_ops.resources import get_process_resources, ResourceStats


def test_get_process_resources_reads_proc():
    """Should read CPU and memory from /proc for given PIDs."""
    proc_stat_content = "1234 (node) S 1 1234 1234 0 -1 4194304 1000 0 0 0 500 200 0 0 20 0 1 0 100 0 0 18446744073709551615 0 0 0 0 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0"
    system_stat_content = "cpu  1000 2000 3000 4000 5000 6000 7000 8000 9000 10000"
    status_content = "Name:\tnode\nVmRSS:\t145920 kB\n"

    original_path = Path

    def mock_path_init(path_str):
        mock = MagicMock(spec=Path)
        mock.__str__ = lambda self: path_str

        def read_text():
            if path_str == "/proc/1234/stat":
                return proc_stat_content
            if path_str == "/proc/stat":
                return system_stat_content
            if path_str == "/proc/1234/status":
                return status_content
            return ""

        mock.read_text = read_text
        return mock

    with patch("claude_ops.resources.Path", side_effect=mock_path_init):
        result = get_process_resources([1234])

    assert 1234 in result
    assert result[1234].rss_mb == pytest.approx(142.5, abs=0.1)


def test_get_process_resources_missing_proc():
    """Should return empty dict when /proc is unavailable."""
    with patch("claude_ops.resources.Path.read_text", side_effect=FileNotFoundError):
        result = get_process_resources([9999])
    assert result == {}


def test_get_process_resources_empty_pids():
    """Should return empty dict for empty PID list."""
    result = get_process_resources([])
    assert result == {}
