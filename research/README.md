# Research record

Start here when resuming strategy research. This index and the dated experiment
folders preserve the findings independently of a chat session.

## Current state — September 9, 2026

The original hypothesis is that some sharp one-session declines overreact to
news, creating a recovery opportunity. Our current dataset tests **all qualifying
close-to-close drops**, not specifically earnings-related drops. Outcomes are
stock returns, not option returns.

The recovery-exit experiment improved median returns and win rates versus a
30-session hold, but reduced average returns and still suffered large losses.
It is an exploratory candidate, not a validated strategy. It did not change the
app or Qwen scoring. No unseen-data validation or portfolio simulation has been
completed for this candidate.

## Experiments

| Record | Status | Main finding |
|---|---|---|
| [2026-09-09: Recovery exits](experiments/2026-09-09-recovery-exits/README.md) | Exploratory; data already inspected | Recovery exits looked steadier by median/win rate; large losses and lower mean returns remain. |

The [detailed findings](../docs/market-consistency-findings.md) explain the first
run. The dated folder holds its machine-readable aggregate results and provenance.

## Pending work

The top-level [TODO.md](../TODO.md) is the single backlog for research and
application improvements. This file records completed findings and experiment
history rather than maintaining a second task list.

## What belongs where

- **Commit:** this index, dated notes, small aggregate result CSVs, methodology,
  manifests, analysis code, and tests.
- **Local/private backup:** raw bars, event-level data, individual simulated
  trades, and other large outputs under `exports/`. These remain Git-ignored.
- **Never add to research records:** API keys, config secrets, account data,
  authentication responses, private logs, or absolute user-specific paths.

The current raw files have **not** been backed up outside this workspace by this
workflow. Copy the two specific export directories listed in the experiment
record to a private backup location if exact reruns must survive loss of this
computer. Do not copy the whole project/config directory for this purpose.

A manifest's hash identifies the exact input, but is not a backup of that input.
Downloading again may yield revised prices or a changed universe. A future clone
can inspect committed results without raw files; recomputation needs the original
raw files restored under the documented relative paths.

## Saving the next experiment

1. Before running it, create a new dated folder with a descriptive suffix and
   copy [EXPERIMENT_TEMPLATE.md](EXPERIMENT_TEMPLATE.md) to its `README.md`.
   Write the hypothesis, fixed parameters, evaluation window, and success/failure
   criteria before inspecting new outcomes.
2. Run the analysis into a **new** local export directory. Keep prior runs intact.
3. Review and copy only its small aggregate results into the dated record.
   Preserve numeric precision and missing values. Keep raw data out of Git.
4. Record relative input/output locations, source dates/feed/universe, exclusions,
   exact commands, implementation revision (and dirty state), and SHA-256 hashes.
   Capture the code revision at run time for future experiments. If it was not
   captured, say so rather than inventing one.
5. Write findings and limitations, including negative outcomes. Mark whether the
   data had already been seen, and list the next unresolved question.
6. Update this index. Review `git status --short`, then stage only the intended
   record/code/test files. Check the staged diff for sensitive material before
   committing. Do not force-add ignored exports or credentials.

Use a separate record for a rerun or correction and link it to its predecessor.
Git preserves the notes/results only after you commit them; pushing or another
backup is needed to preserve that commit off this computer.
