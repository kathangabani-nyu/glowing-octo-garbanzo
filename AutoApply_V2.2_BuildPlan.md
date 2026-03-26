AutoApply V2.2
Build Plan & Agent Assignment
Three agents, three phases, one pipeline

Based on: AutoApply V2.2 Quality-First Design (March 23, 2026)

# 1. Assignment Philosophy
Each agent gets work that plays to its strengths. The goal is not to parallelize for speed — it’s to get the best output quality from each tool for each type of task.

## 1.1 Handoff Rules
Claude Code goes first in each phase. It defines the interfaces, schemas, and module contracts that other agents build against.
Codex works against defined interfaces. It receives clear function signatures, input/output types, and test cases. It does not need to understand the full system.
Cursor works on user-facing code. It gets the interactive, iterative pieces where fast visual feedback matters.
No two agents edit the same file. Each module has one owner. If a module needs changes based on another agent’s work, the owning agent makes the change.
Tests are written by the same agent that writes the module. The module author understands the edge cases best.

# 2. Project Skeleton (Claude Code Creates First)
Before any module work begins, Claude Code sets up the repository structure, the database schema, and the orchestrator skeleton. This ensures all agents work against the same project shape.

# 3. Phase 1: Foundation + Discovery
Goal: at the end of Phase 1, you can run the pipeline and see jobs discovered from your watchlist, filtered by relevance, with contact paths resolved. No emails are sent yet.

## 3.1 Execution Order

## 3.2 Task Cards

### Step 1: Project Foundation

### Step 2: Email Infrastructure (Codex, parallel)

### Step 3: Job Pipeline (Codex, parallel with Step 2)

### Step 4: Contact Discovery (Claude Code, after Steps 2–3)

### Step 5: Config Authoring (Cursor, anytime after Step 1)

# 4. Phase 2: Assembly + Sending + Review
Goal: at the end of Phase 2, the pipeline sends real emails, detects replies, sends follow-ups, and presents uncertain targets in a review queue. This is a fully functional MVP.

## 4.1 Execution Order

## 4.2 Task Cards

### Step 6: Detail Extraction (Codex)

### Step 7: Assembly + Review Logic (Claude Code)

### Step 8: Sending Engine (Claude Code)

### Step 9: Follow-Up Manager (Claude Code)

### Step 10: Review CLI + Templates (Cursor)

### Step 11: Reporting (Codex)

# 5. Phase 3: LLM + Polish
Goal: enhance personalization quality on GPU-equipped devices, and improve reporting. Phase 3 is optional — the MVP is complete after Phase 2.

## 5.1 Execution Order

## 5.2 Task Cards

# 6. Complete Assignment Summary

## 6.1 By Agent

## 6.2 By Phase

## 6.3 Dependency Graph

# 7. Integration & Testing Strategy

## 7.1 Per-Module Testing (During Build)
Each agent writes tests for its own modules. Codex modules are pure functions and easy to unit test. Claude Code modules involve DB state and should use an in-memory SQLite instance for tests. Cursor modules are interactive and tested manually.

## 7.2 Integration Checkpoints

## 7.3 The --dry-run Flag
The orchestrator supports --dry-run from Phase 1 onward. In dry-run mode, every stage runs normally (discovery, filtering, contact resolution, assembly) but the sender skips actual Gmail API calls and instead writes rendered emails to a dry_run_output/ directory as .txt files. This lets you inspect the full pipeline output without sending anything.

# 8. Estimated Timeline
These are rough estimates assuming focused sessions. Real time will depend on how much debugging the contact discovery and Gmail auth require.

End of Document
