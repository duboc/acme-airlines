# 8. New-factory checklist

Use this before and while running [`scripts/bootstrap.sh`](scripts/bootstrap.sh).

## Decide (before anything else)
- [ ] **Repository** the factory works on (`GITHUB_REPO`); the hub slug is
      `<owner>-<repo>` (`HUB_PROJECT_SLUG`)
- [ ] **GCP folder and billing account** (`FOLDER_ID`, `BILLING_ACCOUNT`);
      new project ID, region and hub name (`infra/deploy-config.json`)
- [ ] **Who gets in:** the org domain for the operator CLI (`ORG_DOMAIN`) and
      the web domains (`WEB_DOMAINS`). Any domain outside your org needs the
      custom IAP OAuth client.
- [ ] **Product owner email** (`ADMIN_EMAIL`): the hub super-admin, and the
      person the lead messages
- [ ] **Monthly budget** (`BUDGET_USD`) and loop interval (`TICK_CRON`, at
      least 30 minutes)
- [ ] **Models** you will enable in Model Garden. They must match the
      templates (Opus/Sonnet/Haiku plus Gemini Flash)
- [ ] **Product brief** (`docs/product/<brief>.md`), the lead's first input

## Adapt the team to the product (`factory/team/`)
- [ ] `team-rules.md`: product name, brief path, product-specific rules
      (ours: the "Two Goals, One Guardrail" governance, and mocking every
      external system)
- [ ] `roles/*.md`: one paragraph per role describing what it does in *this*
      product
- [ ] Optional: change the role table (models, caps, extra skills) in
      `infra/40-build-team-templates.sh`

## Manual steps you can't script (🖐)
- [ ] **Model Garden:** enable each Claude model the templates use
- [ ] **GitHub fine-grained PAT** on the repo: Contents, Pull requests, Issues
      and Workflows read/write. Store it with `31-set-project-secret.sh`
      (stdin).
- [ ] **Google Auth Platform:** Branding, then Audience **External** + **In
      production**, then a Web client with redirect URI
      `https://iap.googleapis.com/v1/oauth/clientIds/<ID>:handleRedirect`.
      Apply it with `55-iap-oauth-client.sh`.
- [ ] **Billing export** (standard usage cost) to dataset `billing_export`,
      for the Observatory spend panel
- [ ] **Observatory GitHub token** (read-only is enough) into
      `acme-observatory-github-token`

## Go-live gates
- [ ] `infra/verify.sh all`: every check PASS
- [ ] Below-budget wiring test: the guard logs `-> no action` with HTTP 204
- [ ] Log in through IAP with a user from each `WEB_DOMAINS` domain
- [ ] The lead is running and the pinned **🏭 Factory status** issue exists
      and updates every pass
- [ ] The lineage graph shows the roster under the lead (`verify.sh topology`)
- [ ] `infra/30-hub-admin-access.sh status` → `disabled`

## Operating rhythm
- [ ] Watch for `needs operator` issues. Verify the diagnosis, grant least
      privilege, codify it in `infra/` plus `verify.sh`, and close with
      evidence.
- [ ] Approve or reject `status:proposed` issues when the lead asks
- [ ] Rotate the 90-day hub UATs (guard and collector), and the PAT per your
      policy
- [ ] Re-run `50-web-access.sh` and `55-iap-oauth-client.sh --reapply` after
      any hub redeploy
- [ ] Before tearing down: `infra/90-teardown.sh` (see
      [`docs/setup/04-teardown.md`](../setup/04-teardown.md))
