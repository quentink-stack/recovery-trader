# Research continuity

Before changing market features, strategy experiments, or research scoring, read
`research/README.md` and the relevant experiment record it links to. These files
are the durable research history; do not rely on prior chat messages.

- Keep observed findings, proposed next experiments, and user-approved app changes
  distinct. An exploratory result does not authorize changes to Qwen scoring.
- Record material experiments in a new dated `research/experiments/` folder using
  `research/EXPERIMENT_TEMPLATE.md`. Include poor/null results and limitations.
- Preserve prior result snapshots. For reruns or corrections, add a new record
  and explain what it supersedes; do not silently replace old CSVs.
- Keep credentials, raw downloads, per-trade exports, and logs out of committed
  research records. Preserve compact aggregate results, relative data locations,
  parameters, and hashes. Never copy config files into a record.
- State whether evaluation data was already inspected. Do not call reused data
  an untouched holdout. Separate stock-price tests from options or earnings tests.
- Update the research index after a material result or an explicitly approved
  decision. Record pending work as pending, not as a completed finding.

Current implementation and tests remain the source of truth for app behavior;
the research records explain why experiments were run and what they found.
