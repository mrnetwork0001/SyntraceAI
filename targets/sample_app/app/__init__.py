"""TriageBot - a deterministic AI support-ticket triage pipeline.

Demo target application for SyntraceAI. No network and no real LLM:
``app.llm_pipeline.mock_llm`` is a rule-based, pure function of the prompt
string, so prompt perturbations produce realistic degraded model behavior
at zero cost.
"""
