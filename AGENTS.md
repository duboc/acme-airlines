# AGENTS.md — rules for agents working in this repository

This repo holds both the acme-airlines application (`app/`) and the docs/infra
for the Scion software factory that builds it.

## Rules

1. **Never commit secrets.** No tokens, keys, `.env*` files, or service account
   JSON. Credentials come from the Scion Hub (secrets / VM service account).
2. **Work on a branch and open a PR.** Never push directly to `main`.
3. **Application code lives in `app/`.** Do not touch `infra/` or `docs/setup/`
   unless your task is explicitly about the factory itself.
4. **Docs travel with changes.** If you change how something is built, run, or
   deployed, update the matching doc in the same PR.
5. **Vertex AI is global-only.** Model calls use `locations/global`; do not add
   regional fallbacks.
6. **Verify before claiming done.** Run the relevant tests / `infra/verify.sh`
   check and include the result in the PR description.
