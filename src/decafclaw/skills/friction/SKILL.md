---
name: friction
description: Scan recent conversation archives for user correction messages and output a summary of proposed persistent instruction updates.
user-invocable: true
context: inline
allowed-tools: friction_analyze
---

Call the `friction_analyze` tool to scan the archives for repeated user corrections. Format the resulting themes and proposed additions as a clear markdown summary for the user to review.
