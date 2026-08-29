---
name: syntraceai-micro1
description: Architecture, guidelines, mutation testing rules, and micro1 challenge specs for SyntraceAI built for the Frontier Engineering Challenge.
---

# 🧪 SyntraceAI — micro1 Frontier Engineering Challenge Skill & Execution Guide

Use this skill whenever working on, reviewing, or developing **SyntraceAI** — the Autonomous Agentic Mutation Testing & Hallucination Stress-Tester for micro1.

## 📌 Project Overview & Target
- **Target Event:** micro1 Frontier Engineering Challenge 2026
- **Submission Deadline:** August 31, 2026 @ 18:00 UTC (7:00 PM WAT)
- **Prize Target:** 1st Place ($10,000 Cash + Paid Engineering Role)
- **Core Tech Stack:** Python 3.11+ / AST Mutation Engine + FastAPI + pytest + Trajectory Logger

## 🏗️ Technical Architecture Rules

### 1. Dual-Solution Model
- Maintain `baseline/` (standard line coverage script) and `advanced/` (AST mutation + prompt perturbation engine).

### 2. Measured Improvement Metric
- Demonstrate a 20% + jump in bug detection rate (e.g. Baseline 40% vs Advanced 96.8% mutation score).

### 3. Trajectory Logging
- Log agent execution steps, tool outputs, and human checkpoints in `trajectories/agent_trace_01.json`.

## 🚨 Submission Checklist
- Public GitHub repo under Apache 2.0 / MIT License.
- Complete `README.md`, `REPRODUCTION_GUIDE.md`, and `CHANGELOG.md`.
- `trajectories/` trace files.
- 5-Minute video walkthrough.
