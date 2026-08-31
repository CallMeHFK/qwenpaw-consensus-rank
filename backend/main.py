# -*- coding: utf-8 -*-
"""Listwise Rank Tool Plugin Entry Point.

Registers the ``rank_candidates_listwise`` tool into the agent toolkit.
Implements the listwise-rank-eval skill methodology: anonymized
listwise ranking by multiple cross-family LLM judges + Borda
aggregation + Spearman consistency report.
"""

import importlib.util
import logging
import os

from qwenpaw.plugins.api import PluginApi

logger = logging.getLogger(__name__)

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_tool_module():
    """Load tool_impl.py from this plugin's directory via importlib."""
    tool_path = os.path.join(_PLUGIN_DIR, "tool_impl.py")
    spec = importlib.util.spec_from_file_location(
        "listwise_rank_tool_impl",
        tool_path,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ListwiseRankToolPlugin:
    """Listwise multi-judge consensus ranking tool plugin."""

    def register(self, api: PluginApi):
        """Register the consensus ranking tool.

        Args:
            api: PluginApi instance.
        """
        tool = _load_tool_module()

        api.register_tool(
            tool_name="rank_candidates_listwise",
            tool_func=tool.rank_candidates_listwise,
            description=(
                "Rank candidates via multi-judge consensus: cross-family "
                "LLM judges independently rank anonymized candidates "
                "(A/B/C...), Borda-aggregated into a consensus with a "
                "Spearman consistency report. Use for option selection, "
                "RAG re-ranking, data labeling de-biasing."
            ),
            icon="📊",
            tool_type="network",
        )

        logger.info("Listwise Rank tool plugin registered")


# QwenPaw 2.1.0 loader contract: the backend entry module must export
# a module-level ``plugin`` object implementing register(api).
plugin = ListwiseRankToolPlugin()
