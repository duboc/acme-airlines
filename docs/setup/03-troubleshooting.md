# Troubleshooting

Problems hit while building the factory, and their fixes.

## `gcloud org-policies list` hangs on a y/N prompt

**Symptom:** bootstrap stalled at `API [orgpolicy.googleapis.com] not enabled
... Would you like to enable and retry? (y/N)`.

**Fix:** enable `orgpolicy.googleapis.com` with the other APIs and export
`CLOUDSDK_CORE_DISABLE_PROMPTS=1` in scripts so gcloud never blocks on input.
