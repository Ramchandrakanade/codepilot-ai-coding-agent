\# CodePilot AI — AI Coding Agent



A small, deployable AI Coding Agent built for the IntegrationWings shortlisted-candidate assignment.



\## What it does



CodePilot AI accepts a developer task in natural language and runs an end-to-end coding-agent workflow:



1\. Scans a sample codebase.

2\. Identifies files relevant to the developer request.

3\. Creates an implementation plan.

4\. Uses an LLM (when configured) to propose code changes as a patch/diff.

5\. Explains what the proposed change does and why.

6\. Runs the sample project's test suite as a validation step.

7\. Presents the result in a web UI.



The included `sample\_project/` is a small QC automation toolkit with validators, reporting, CLI code, data, and pytest tests. It gives the agent a real multi-file codebase to inspect.



\## Architecture



```text

Developer Task

&#x20;     ↓

Flask Web UI

&#x20;     ↓

Agent Orchestrator

&#x20;     ├── Codebase Scanner

&#x20;     ├── Relevant File Selector

&#x20;     ├── LLM Planner / Patch Generator

&#x20;     └── Validation Runner

&#x20;     ↓

Plan → Relevant Files → Diff → Validation → Explanation

\## Tech Stack



\* Python 3.10+

\* Flask

\* LLM provider: OpenRouter (deployed) or Ollama (local)

\* Pandas

\* Pytest

\* HTML/CSS/JavaScript



\## Run locally



```bash

python -m venv .venv



\# Windows PowerShell

.\\.venv\\Scripts\\Activate.ps1



\# macOS/Linux

\# source .venv/bin/activate



pip install -r requirements.txt

python run.py



