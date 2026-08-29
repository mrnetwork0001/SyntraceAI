"""Prompt templates for the TriageBot ticket-triage pipeline.

All constants are plain module-level string assignments (no f-strings):
the prompt layer of the application is data, and the mock LLM keys off
exact marker lines inside these strings.
"""

SYSTEM_PROMPT = """You are TriageBot, a senior support-ticket triage analyst.
Your job is to read one customer support ticket and classify it for the on-call team.
Respond ONLY with a single valid JSON object and nothing else.
Required JSON keys: "category", "priority", "confidence", "summary".
Valid categories are: billing, bug, account, performance, general.
Priority must be an integer from 1 (lowest) to 5 (critical).
Confidence must be a number between 0.0 and 1.0.
The summary must restate the ticket in eight words or fewer."""

FEW_SHOT_BLOCK = """Example 1
Ticket: I was charged twice for my subscription this month, please refund one charge.
Output: {"category": "billing", "priority": 3, "confidence": 0.95, "summary": "Customer was charged twice and wants a refund"}

Example 2
Ticket: The dashboard crashes with an error every time I open the reports tab.
Output: {"category": "bug", "priority": 4, "confidence": 0.75, "summary": "Dashboard crashes with an error on reports tab"}"""

TICKET_TEMPLATE = """### ROLE ###
{system}

### EXAMPLES ###
{few_shot}

### TICKET ###
{ticket}

### OUTPUT RULES ###
Return a single JSON object as the entire response, using exactly the required keys.
Do not add markdown fences, commentary, or any text before or after the JSON."""
