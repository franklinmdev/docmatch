# PR Judge

`.github/workflows/pr-judge.yml` runs [jev-pr-judge](https://github.com/juanegido/jev-pr-judge), pinned to commit `5566cb41cac6656433a06b7614436e4657f8623e` (v1.0.1), on every pull request. It sends the PR's title, body and diff to TypeSafe's Jev model, asks a fixed set of typed questions, and posts one sticky comment with a decision (`approve`, `human_review` or `send_back`), a composite score, four rubric scores and ten red-flag probabilities. The comment is updated in place on every push and on every edit of the description. `fail-on` is `none`: the step passes whatever the verdict.

## When to read it

- When a pull request is opened, after the job has run.
- Before landing, as one input to the two-axis review. The comment lives on the PR, not on the ticket, so paste it into the review's inputs beside the ticket and its comments.

Read it with `--json`, since plain `gh pr view` fails on this machine:

```bash
gh pr view <N> --json comments --jq '.comments[] | select(.body | startswith("<!-- pr-judge -->")) | .body'
```

## How to weigh it

The judge is on probation. Its thresholds are the action's defaults, never calibrated on this repo, and on its own pull request (#110, a workflow file and one README line) it decided `human_review` while the model's own verdict was `approve` at 13 percent confidence.

- A flag is a prompt to look at that spot, never a finding on its own. Confirm it in the diff or dismiss it.
- A flag the review dismisses gets one line in the review comment naming the flag and why.
- A low score on a dimension the repo's rules already cover (scope, description) is worth a second read of the PR body. A low score on test evidence is checked against the diff, since a prose-only PR has no test to write.
- It never counts as approval, never replaces the two-axis review, and never blocks a merge.

## Exit

Revisit after the Phase 3 pull requests have landed: compare the judge's flags and decisions against what the two-axis reviews found on the same PRs. Keep it, drop it, or tune its profile on that evidence. Until then, nothing in the process depends on it.
