# CodePilot AI — AI Coding Agent

A small, deployable AI Coding Agent built for the IntegrationWings shortlisted-candidate assignment.

## What it does

CodePilot AI accepts a developer task in natural language and runs an end-to-end coding-agent workflow:

1. Scans a sample codebase.
2. Identifies files relevant to the developer request.
3. Creates an implementation plan.
4. Uses an LLM (when configured) to propose code changes as a patch/diff.
5. Explains what the proposed change does and why.
6. Runs the sample project's test suite as a validation step.
7. Presents the result in a web UI.

The included `sample\_project/` is a small QC automation toolkit with validators, reporting, CLI code, data, and pytest tests. It gives the agent a real multi-file codebase to inspect.

## Architecture

```text
Developer Task
      ↓
Flask Web UI
      ↓
Agent Orchestrator
      ├── Codebase Scanner
      ├── Relevant File Selector
      ├── LLM Planner / Patch Generator
      └── Validation Runner
      ↓
Plan → Relevant Files → Diff → Validation → Explanation
```

## Tech Stack

* Python 3.10+
* Flask
* OpenAI API (optional for live LLM patch generation)
* Pandas
* Pytest
* HTML/CSS/JavaScript

## Run locally

```bash
python -m venv .venv
# Windows PowerShell
.\\.venv\\Scripts\\Activate.ps1
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
python run.py
```

Open `http://localhost:5000`.

Enable live AI patch generation


The application reads the key from the environment. Never commit `.env` or a real API key.

Without an API key, the application runs in clearly labelled **Demo Mode**: it performs repository scanning, relevant-file selection, planning, and validation, but it does not pretend that an AI patch was generated.

## Example task

```text
Add input validation to the score field and write a test for invalid values.
```

The UI will show the selected files, implementation plan, proposed patch when live AI is configured, validation output, and a final explanation.

## Testing

Run the complete test suite:

```bash
python -m pytest -q --import-mode=importlib
```

## Assumptions

* The assignment uses a small local sample repository rather than arbitrary remote GitHub repositories.
* The agent proposes changes as a patch; it does not silently overwrite source files.
* The validation step runs the sample project's automated tests.
* Live patch generation requires an LLM provider API key configured as an environment variable.

## Limitations

* File relevance is currently selected using lightweight lexical retrieval before the LLM sees the selected files.
* The prototype does not execute an AI-generated patch automatically. This is intentional for safety and reviewability.
* The sample repository is bundled with the application; arbitrary repository upload/import is outside the assignment scope.
* Demo Mode is not a substitute for live LLM functionality in the final evaluation deployment; configure the required environment variable for the deployed version.

## Security

No API keys, passwords, or secrets belong in this repository. `.env` is ignored by Git and `.env.example` contains only placeholders.

## Assignment mapping

|Requirement|Implementation|
|-|-|
|Web UI / CLI|Flask web UI|
|Natural-language developer task|Task textarea|
|Show agent plan|Agent Plan panel|
|Read multiple files|Codebase scanner|
|Identify relevant files|Relevance selector|
|Understand task|LLM analysis prompt|
|Suggest code changes|LLM patch generation|
|Explain changes|Agent Summary|
|Show changed files / diff|Proposed patch panel|
|Meaningful validation|Pytest validation runner|
|Local run instructions|This README|
|Deployment-ready|Flask app + production entry point|
|Secrets protection|`.env` + `.gitignore`|



