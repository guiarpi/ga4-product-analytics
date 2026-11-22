#!/usr/bin/env bash
# ci/setup_gcp_wif.sh — one-time setup for the scheduled dashboard workflow (roadmap E8).
#
# Creates, in YOUR Google Cloud project, a keyless link between this GitHub repo
# and BigQuery (Workload Identity Federation), then stores three GitHub secrets.
# Read it before running. It changes your cloud project and your repo settings;
# every step is printed first and the script stops at the first error.
#
#   1. a service account that can only run BigQuery jobs (roles/bigquery.jobUser);
#      the public GA4 sample is readable by anyone, so no data role is needed
#   2. a workload identity pool + an OIDC provider for GitHub Actions, accepting
#      ONLY tokens from this repository's main branch
#   3. permission for that identity (and nothing else) to act as the account
#   4. the GitHub secrets GA4_PROJECT_ID, GCP_WIF_PROVIDER, GCP_SERVICE_ACCOUNT
#      (secrets, not variables: this repo's Actions logs are public)
#
# No key is created: nothing to store, leak or rotate.
#
# Needs: gcloud logged in as a project owner (gcloud auth login), gh logged in
# with admin rights on the repo (gh auth login). Safe to re-run: existing
# resources are kept.
#
# Usage:  bash ci/setup_gcp_wif.sh            (uses your gcloud default project)
#         GA4_PROJECT_ID=my-project bash ci/setup_gcp_wif.sh
set -euo pipefail

REPO="guiarpi/ga4-product-analytics"           # owner/name — the only repo allowed in
BRANCH_REF="refs/heads/main"                    # the only branch allowed in
SA_NAME="ga4-dashboard-ci"
POOL="github-actions"
PROVIDER="github"

PROJECT_ID="${GA4_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "${PROJECT_ID}" ]]; then
  echo "No project: set GA4_PROJECT_ID or run 'gcloud config set project …'." >&2; exit 1
fi
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
PROVIDER_ID="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

step() { printf '\n▶ %s\n' "$*"; }
exists() { "$@" >/dev/null 2>&1; }

echo "Project: ${PROJECT_ID}   Repo: ${REPO}   Branch: ${BRANCH_REF}"
read -r -p "Proceed with these changes? [y/N] " answer
[[ "${answer}" == "y" || "${answer}" == "Y" ]] || { echo "Stopped, nothing changed."; exit 0; }

step "Enable the APIs the federation needs (IAM, IAM Credentials, STS, BigQuery)"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  bigquery.googleapis.com --project "${PROJECT_ID}"

step "Service account ${SA_NAME} — BigQuery jobs only"
if ! exists gcloud iam service-accounts describe "${SA_EMAIL}" --project "${PROJECT_ID}"; then
  gcloud iam service-accounts create "${SA_NAME}" --project "${PROJECT_ID}" \
    --display-name "GA4 dashboard CI (BigQuery jobs only)"
fi
gcloud projects add-iam-policy-binding "${PROJECT_ID}" --condition=None --quiet \
  --member "serviceAccount:${SA_EMAIL}" --role roles/bigquery.jobUser >/dev/null

step "Workload identity pool ${POOL}"
if ! exists gcloud iam workload-identity-pools describe "${POOL}" --location global --project "${PROJECT_ID}"; then
  gcloud iam workload-identity-pools create "${POOL}" --location global --project "${PROJECT_ID}" \
    --display-name "GitHub Actions"
fi

step "OIDC provider ${PROVIDER} — tokens from ${REPO} on ${BRANCH_REF} only"
if ! exists gcloud iam workload-identity-pools providers describe "${PROVIDER}" \
     --workload-identity-pool "${POOL}" --location global --project "${PROJECT_ID}"; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
    --workload-identity-pool "${POOL}" --location global --project "${PROJECT_ID}" \
    --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition "assertion.repository == '${REPO}' && assertion.ref == '${BRANCH_REF}'"
fi

step "Let that identity — and nothing else — act as the service account"
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" --project "${PROJECT_ID}" --quiet \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}" \
  >/dev/null

step "GitHub secrets on ${REPO}"
gh secret set GA4_PROJECT_ID      --repo "${REPO}" --body "${PROJECT_ID}"
gh secret set GCP_WIF_PROVIDER    --repo "${REPO}" --body "${PROVIDER_ID}"
gh secret set GCP_SERVICE_ACCOUNT --repo "${REPO}" --body "${SA_EMAIL}"

echo
echo "Done. Run the workflow from the repo's Actions tab: 'regenerate dashboard' → Run workflow."
echo "To undo: delete the provider, the pool and the service account in the Cloud console,"
echo "and the three secrets under Settings → Secrets and variables → Actions."
