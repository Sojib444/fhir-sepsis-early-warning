#!/usr/bin/env bash
# deploy/bootstrap.sh — one-time AWS bootstrap for the sepsis demo (AGENTS-ENGINEERING.md §21-22).
#
# Run this ONCE with an admin AWS CLI profile. It creates:
#   1. the GitHub OIDC provider (if absent) so GitHub Actions can assume a role;
#   2. a deployment role whose trust policy is scoped to the repo and to tag
#      refs only, and which can manage the sepsis-demo stack and run SSM commands;
#   3. prints the role ARN to store as the DEPLOY_ROLE secret for the repo.
#
# No long-lived AWS keys are installed anywhere: the workflow obtains temporary
# credentials through OIDC at every run.
#
# Usage:
#   AWS_PROFILE=admin ./deploy/bootstrap.sh <account-id> <region> <repo-owner/repo-name>
set -euo pipefail

ACCOUNT_ID="${1:?usage: bootstrap.sh <account-id> <region> <owner/repo>}"
REGION="${2:?usage: bootstrap.sh <account-id> <region> <owner/repo>}"
REPO="${3:?usage: bootstrap.sh <account-id> <region> <owner/repo>}"
ROLE_NAME="github-actions-sepsis-deploy"

aws() { command aws --region "$REGION" "$@"; }

PROVIDER_BASE="token.actions.githubusercontent.com"

# 1. OIDC provider (idempotent).
if aws iam get-open-id-connect-provider --open-id-connect-provider-arn \
    "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${PROVIDER_BASE}" >/dev/null 2>&1; then
  echo "OIDC provider already exists."
else
  aws iam create-open-id-connect-provider \
    --url "https://${PROVIDER_BASE}" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "$(openssl s_client -servername "${PROVIDER_BASE}" -connect "${PROVIDER_BASE}:443" </dev/null 2>/dev/null | openssl x509 -fingerprint -noout | cut -d= -f2 | tr -d ':')"
  echo "OIDC provider created."
fi

# 2. Deployment role, trusted only for tag refs of this repo (§21: deploy is tags-only).
cat > /tmp/sepsis-trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/${PROVIDER_BASE}" },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "${PROVIDER_BASE}:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "${PROVIDER_BASE}:sub": "repo:${REPO}:ref:refs/tags/*"
        }
      }
    }
  ]
}
EOF

cat > /tmp/sepsis-permissions.json <<'EOF'
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudFormationForDemoStack",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateStack",
        "cloudformation:DescribeStacks",
        "cloudformation:DescribeStackResources"
      ],
      "Resource": "arn:aws:cloudformation:*:*:stack/sepsis-demo*"
    },
    {
      "Sid": "CreateResourcesTheStackNeeds",
      "Effect": "Allow",
      "Action": [
        "cloudformation:CreateChangeSet",
        "cloudformation:ExecuteChangeSet",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack"
      ],
      "Resource": "arn:aws:cloudformation:*:*:stack/sepsis-demo*"
    },
    {
      "Sid": "InstanceDiscovery",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:DescribeSecurityGroups"
      ],
      "Resource": "*"
    },
    {
      "Sid": "RedeployViaSSM",
      "Effect": "Allow",
      "Action": [
        "ssm:SendCommand",
        "ssm:ListCommands",
        "ssm:GetCommandInvocation",
        "ssm:DescribeInstanceInformation"
      ],
      "Resource": "*"
    },
    {
      "Sid": "PassInstanceProfile",
      "Effect": "Allow",
      "Action": "iam:PassRole",
      "Resource": "arn:aws:iam::*:role/*sepsis*"
    }
  ]
}
EOF

ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Role ${ROLE_NAME} already exists."
else
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file:///tmp/sepsis-trust-policy.json \
    --description "Deployment role for the sepsis early-warning demo (tags only, OIDC)"
fi

aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name sepsis-deploy \
  --policy-document file:///tmp/sepsis-permissions.json

echo "----------------------------------------"
echo "Done. Set these GitHub Actions secrets:"
echo "  AWS_ACCOUNT_ID  = ${ACCOUNT_ID}"
echo "  AWS_REGION      = ${REGION}"
echo "  DEPLOY_ROLE_ARN = ${ROLE_ARN}"
echo "
Also set (use Actions secrets, never the repo):
  DEPLOY_DOMAIN   = public domain for the demo (Caddy TLS; A record -> the
                    instance's public IP, see the stack's PublicIp output)
  BUDGET_EMAIL    = email address for the cost alarm
  HEALTH_EMAIL    = optional email for the instance-health alarm
No GHCR token is needed: the built-in GITHUB_TOKEN pushes the images.
"