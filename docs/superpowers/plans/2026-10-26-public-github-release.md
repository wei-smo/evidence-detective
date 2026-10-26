# GitHub Public Snapshot Release Plan

> **For agentic workers:** Use native execution for this already-authorized publication task, with one independent read-only release review. Steps use checkbox syntax for tracking.

**Goal:** Publish the current Evidence Detective snapshot and sanitized development/method records to the user's public GitHub repository without shipping service keys.

**Architecture:** Keep the working project and local secrets intact. Build a separate allowlisted release snapshot, archive only visible messages from the three specified chats, scan and test that snapshot, then upload and verify the remote contents.

**Tech Stack:** Existing Python application, Git, GitHub connector; browser creation only if available and authorized.

**Spec:** User request in this chat; AGENTS.md; docs/DEVELOPMENT_STANDARD.md; public visibility explicitly selected by the user.

- [ ] Prepare empty-key configuration, deployment instructions and accurate training/method documentation.
- [ ] Export and sanitize visible user/assistant development messages; exclude reasoning, tools and local attachments.
- [ ] Build an allowlisted release snapshot; verify no real configuration, logs or screenshots enter it.
- [ ] Scan known key values and credential patterns; run existing offline regressions and a no-key startup check.
- [ ] Obtain independent read-only review of snapshot and documented claims; resolve release blockers.
- [ ] Publish to wei-smo/evidence-detective after repository availability, then verify commit, assets and empty-key template remotely.

## Review Focus

- A secret in conversation text must be removed, even when .env.local itself is excluded.
- Docker must retain six role PNGs and the existing font/license.
- Research discussions must not be described as completed model-weight training or benchmark results.
- Remote upload may not be called complete until repository contents are verified.
