# Deployment — sepsis early-warning demo on AWS

This directory contains everything needed to run the demo as a public,
HTTPS-enabled service (AGENTS-ENGINEERING.md §21-22). The stack is deliberately
small: a `t3.small` EC2 instance (x86_64 — GitHub's standard runners build
native amd64 images) running Docker Compose.

```
browser ──https──▶ Caddy (TLS) ──▶ dashboard (nginx) ──▶ cds (ASP.NET Core)
                                            │ hook ──────▶ model-api (FastAPI)
                                            └─/api────────▶ cds ──▶ hapi (FHIR)
```

## What is deployed

| Piece | Image | Notes |
|---|---|---|
| HAPI FHIR server | `hapiproject/hapi:v6.4.0` (digest-pinned) | pre-loaded demo seed |
| FhirLoader (one-shot) | `fhirloader` (GHCR, baked CSV) | loads 200 patients |
| Model API | `model-api` (GHCR) | trained model baked into the image |
| CDS service | `cds` (GHCR) | HAPI + model callers |
| Dashboard | `dashboard` (GHCR) | static bundle + `/api` proxy + rate limit |
| Caddy | `caddy:2.9-alpine` (digest-pinned) | automatic TLS, `http://`→`https://` |

Because the repository is MIT-licensed and the PhysioNet/CinC Challenge 2019
data's licence permits a demo subset, all four first-party images are published
to **GHCR as public packages**; the EC2 instance needs no registry credentials.

## Files

- `template.yml` — CloudFormation: security group, SSM role, one instance whose
  user-data boots `/opt/sepsis/app` from the git tag and starts `docker compose`.
  Also a $20/month budget alarm and a CloudWatch health alarm.
- `docker-compose.prod.yml` — offline instance compose (the images already
  carry everything; no `.env` defaults for the domain).
- `Caddyfile` — TLS edge, healthz, API reverse proxy.
- `deploy.sh` — tag deploy via CloudFormation + SSM Run Command (run by CI).
- `bootstrap.sh` — one-time AWS IAM OIDC setup (run by a human once).

## One-time setup (run once, with admin credentials)

```bash
aws --version   # v2, with admin access
# creates the OIDC provider + a tags-only deployment role in the account:
./deploy/bootstrap.sh <account-id> <aws-region> <owner/repo>
```

Then store GitHub Actions secrets (never in the repo):

| Secret | Value |
|---|---|
| `AWS_ACCOUNT_ID` | account id from bootstrap output |
| `AWS_REGION` | e.g. `us-east-1` |
| `DEPLOY_ROLE_ARN` | role ARN printed by bootstrap |
| `DEPLOY_DOMAIN` | public domain, DNS A record → the instance Elastic IP |
| `BUDGET_EMAIL` | for the `$20/month` alarm |
| `HEALTH_EMAIL` | optional, for the instance-health alarm |

No GHCR token is needed — the built-in `GITHUB_TOKEN` pushes the images
(`packages: write`).

## Deploying

Tagging a release deploys automatically: `git tag v1.0 && git push origin v1.0`.

The workflow:
1. runs the full training pipeline on a `ubuntu-large` runner (the deployed
   model is baked into the `model-api` image, so training happens at deploy
   time) — also regenerates the 200-patient demo seed CSV;
2. builds and pushes the four images to GHCR tagged with the short SHA;
3. identifies the instance via SSM, then redeploys in place with
   `docker compose up -d --pull always`.

## Rollback

```bash
git tag v0.9-before-x && git push origin v0.9-before-x   # keeps the prior images at GHCR
# or, from a machine with admin credentials and jq:
aws --region <region> cloudformation deploy \
  --stack-name sepsis-demo --template-file deploy/template.yml \
  --parameter-overrides ImageTag=<old-tag> \
  --capabilities CAPABILITY_IAM
```

## Tearing down

```bash
aws --region <region> cloudformation delete-stack --stack-name sepsis-demo
# the GitHub OIDC provider and deploy role are harmless to leave; to remove:
#   aws iam delete-role-policy --role-name github-actions-sepsis-deploy --policy-name sepsis-deploy
#   aws iam delete-role --role-name github-actions-sepsis-deploy
#   aws iam delete-open-id-connect-provider --open-id-connect-provider-arn \
#     arn:aws:iam::<id>:oidc-provider/token.actions.githubusercontent.com
```

## Cost estimate

`t3.small` (2 vCPU / 2 GiB, Linux) ≈ **$10–15/month**
including 5 GiB of EBS and one Elastic IP; the cloudwatch/budget alarms are
free. The budget alarm on `BudgetEmail` fires at 80% of the $20/month cap.

## Data and licence

- PhysioNet/CinC Challenge 2019 ("Early Prediction of Sepsis from Clinical
  Data") — see `docs/data_notes.md` for the citation; the demo seed subset is
  permitted by the Challenge data-use terms and is served only through the
  public loader image, never committed to the repository.
- The identity model here is untrained demo data only — the models are trained
  on the real cohort in CI/AWS, not on a laptop.