#!/usr/bin/env bash
# deploy/deploy.sh — deploy a tagged image set to AWS (AGENTS-ENGINEERING.md §21-22).
#
# Intended to be invoked from .github/workflows/deploy.yml, already holding
# OIDC-assumed temporary AWS credentials in the environment.
#
# Steps:
#   1. upsert the sepsis-demo CloudFormation stack (fresh instances boot from
#      the tag; the instance's user-data writes /opt/sepsis/app/.env);
#   2. find the running instance;
#   3. wait until the SSM agent is reachable;
#   4. push a new deploy via SSM Run Command: fetch the tag, write .env,
#      pull the pinned images, `docker compose up -d --pull always`, prune;
#   5. poll the command to completion and fail the run on a non-zero exit.
#
# Env inputs (set by the workflow, pumped from repository secrets):
#   AWS_REGION, DEPLOY_STACK_NAME, DEPLOY_DOMAIN, BUDGET_EMAIL, HEALTH_EMAIL,
#   GITHUB_TAG, GITHUB_REPO (owner/name), GITHUB_SHA (kept for provenance),
#   optional KEY_NAME.
set -euo pipefail

REGION="${AWS_REGION:?AWS_REGION required}"
STACK="${DEPLOY_STACK_NAME:-sepsis-demo}"
DOMAIN="${DEPLOY_DOMAIN:?DEPLOY_DOMAIN required}"
BUDGET_EMAIL="${BUDGET_EMAIL:-}"
HEALTH_EMAIL="${HEALTH_EMAIL:-}"
TAG="${GITHUB_TAG:?GITHUB_TAG required}"
REPO="${GITHUB_REPO:?GITHUB_REPO required}"
KEY_NAME="${KEY_NAME:-}"
IMAGE_TAG="$(git rev-parse --short HEAD 2>/dev/null || echo "${GITHUB_SHA:-$TAG}")"

echo ">> Deploying ${REPO}@${TAG} (images tagged ${IMAGE_TAG}) to ${STACK}"

# 1. CloudFormation upsert.
PARAMS=( "DomainName=${DOMAIN}" "ImageTag=${TAG}" "GithubRepo=${REPO}" )
[ -n "${BUDGET_EMAIL}" ] && PARAMS+=( "BudgetEmail=${BUDGET_EMAIL}" )
[ -n "${HEALTH_EMAIL}" ] && PARAMS+=( "HealthEmail=${HEALTH_EMAIL}" )
[ -n "${KEY_NAME}" ]     && PARAMS+=( "KeyName=${KEY_NAME}" )

aws cloudformation deploy \
  --region "$REGION" \
  --stack-name "$STACK" \
  --template-file template.yml \
  --parameter-overrides "${PARAMS[@]}" \
  --capabilities CAPABILITY_IAM

# 2. Locate the running instance (single instance by design).
instance_id="$(
  aws cloudformation describe-stack-resources \
    --region "$REGION" --stack-name "$STACK" \
    --query "StackResources[?ResourceType=='AWS::EC2::Instance'].PhysicalResourceId" \
    --output text
)"
[ -n "${instance_id}" ] || { echo "No EC2 instance found in stack $STACK" >&2; exit 1; }
echo ">> Instance: ${instance_id}"

# 3. Wait for SSM to be able to reach the instance (up to ~6 minutes).
echo ">> Waiting for SSM agent on ${instance_id}..."
deadline=$(( $(date +%s) + 360 ))
until aws ssm describe-instance-information --region "$REGION" \
  --filters "Key=InstanceIds,Values=${instance_id}" \
  --query "InstanceInformationList[0].PingStatus" --output text 2>/dev/null | grep -q Online; do
  [ "$(date +%s)" -lt "$deadline" ] || { echo "SSM agent never came online" >&2; exit 1; }
  sleep 15
done

# 4. Redeploy on the instance via SSM (no SSH key required).
#
# The bootstrap user-data does the same thing on fresh instances; this path is
# for every subsequent tag/rollback without touching the instance directly.
# Each `commands` element is one script line (SSM joins them with newlines),
# so the parameter document is built as JSON and passed with file://.
ssm_params="$(mktemp)"
cat > "$ssm_params" <<JSON
{
  "commands": [
    "set -eux",
    "cd /opt/sepsis/app",
    "sudo git fetch --depth 1 origin tag ${TAG}",
    "sudo git checkout -f ${TAG}",
    "printf 'GHCR_TAG=%s\n' '${IMAGE_TAG}' | sudo tee .env >/dev/null",
    "printf 'DOMAIN=%s\n' '${DOMAIN}' | sudo tee -a .env >/dev/null",
    "sudo docker compose -f deploy/docker-compose.prod.yml --env-file .env pull --quiet",
    "sudo docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --remove-orphans",
    "sudo docker image prune -f >/dev/null 2>&1 || true",
    "curl -fsS --retry 20 --retry-delay 3 --retry-all-errors https://${DOMAIN}/healthz >/dev/null"
  ]
}
JSON

command_id="$(
  aws ssm send-command \
    --region "$REGION" \
    --instance-ids "$instance_id" \
    --document-name "AWS-RunShellScript" \
    --comment "sepsis demo deploy ${REPO}@${TAG}" \
    --parameters "file://${ssm_params}" \
    --output text --query Command.CommandId
)"
echo ">> SSM CommandId ${command_id}"

# 5. Poll to completion.
status=""
deadline=$(( $(date +%s) + 900 ))
while :; do
  status="$(
    aws ssm get-command-invocation \
      --region "$REGION" --command-id "$command_id" --instance-id "$instance_id" \
      --query Status --output text
  )"
  case "$status" in
    Success) echo ">> Deploy succeeded (${status})"; exit 0 ;;
    Failed|Cancelled) ;;
    *) echo "  status=${status}"; sleep 10; continue ;;
  esac
  echo ">> Deploy ${status}"
  aws ssm get-command-invocation --region "$REGION" \
    --command-id "$command_id" --instance-id "$instance_id" --output text \
    --query "StandardErrorContent" | tail -n 40 >&2 || true
  exit 1
done