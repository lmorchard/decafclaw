# Implementation plan

1. File the historical findings and observable acceptance criteria in #1257.
2. Run the clean baseline and demonstrate prefix/tail/cleanup failures.
3. Recover tail persistence, stabilize tool assembly, and preserve sent history.
4. Add provider-bound and manager-level regressions, then update docs and routing evals.
5. Run repository gates and compare unrelated eval failures against unchanged main.
6. Commit and publish a draft checkpoint. Review the exact diff, address findings, and submit the same PR for review. Do not merge.
