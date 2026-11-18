"""Phase 2 AI layer: model management, multimodal analysis, the clip director and edit plans.

Everything in ``schemas``, ``director`` and ``edit_plan`` is pure Python (no model
dependencies) so the API can always fall back to it when no AI worker is available.
"""
