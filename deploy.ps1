#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Full TheraScout deployment: bundles Lambda dependencies, deploys all CDK
    stacks (including Fargate backend), then patches .env files with live URLs.

.USAGE
    # First time (bootstraps CDK for your account):
    .\deploy.ps1 -AccountId 446205069645 -Bootstrap

    # Subsequent deploys:
    .\deploy.ps1 -AccountId 446205069645

    # Skip Fargate (local dev only):
    .\deploy.ps1 -AccountId 446205069645 -SkipFargate
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$AccountId,

    [switch]$Bootstrap,
    [switch]$SkipFargate
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

function Log($msg)  { Write-Host "[deploy] $msg" -ForegroundColor Cyan }
function Warn($msg) { Write-Host "[warn]   $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "[ERROR]  $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# 1. Bundle reportlab into compose_pdf_report
# ---------------------------------------------------------------------------
Log "Bundling reportlab into tools/compose_pdf_report ..."
$reportDir = Join-Path $ProjectRoot "tools\compose_pdf_report"
pip install -r (Join-Path $reportDir "requirements.txt") -t $reportDir --quiet
if (-not $?) { Die "pip install failed for compose_pdf_report" }

# ---------------------------------------------------------------------------
# 2. CDK bootstrap (first-time only)
# ---------------------------------------------------------------------------
$region = "us-east-1"
$env:AWS_PROFILE = "saml"
Set-Location (Join-Path $ProjectRoot "infra")

if ($Bootstrap) {
    Log "Bootstrapping CDK environment aws://$AccountId/$region ..."
    cdk bootstrap "aws://$AccountId/$region" `
        --context "account=$AccountId" --context "region=$region" `
        --profile saml
    if (-not $?) { Die "cdk bootstrap failed" }
}

# ---------------------------------------------------------------------------
# 3. Deploy all CDK stacks
# ---------------------------------------------------------------------------
Log "Deploying CDK stacks (first run ~15 min — Docker build included) ..."
cdk deploy --all --require-approval never `
    --context "account=$AccountId" --context "region=$region" `
    --profile saml `
    --outputs-file (Join-Path $ProjectRoot "cdk_outputs.json")
if (-not $?) { Die "cdk deploy failed" }

# ---------------------------------------------------------------------------
# 4. Extract outputs and patch .env files
# ---------------------------------------------------------------------------
Log "Reading CDK outputs ..."
$outputs = Get-Content (Join-Path $ProjectRoot "cdk_outputs.json") | ConvertFrom-Json

$sfnArn     = $outputs."TheraScout-Orchestration".StateMachineArn
$apiGwUrl   = $outputs."TheraScout-Orchestration".ApiGatewayUrl
$backendUrl = $outputs."TheraScout-Backend".BackendUrl     # Fargate ALB URL

if (-not $sfnArn)  { Die "StateMachineArn not found in cdk_outputs.json" }

Log "STATE_MACHINE_ARN = $sfnArn"

# Patch root .env
$rootEnv = Join-Path $ProjectRoot ".env"
(Get-Content $rootEnv) -replace "^STATE_MACHINE_ARN=.*", "STATE_MACHINE_ARN=$sfnArn" |
    Set-Content $rootEnv -Encoding utf8
Log "Patched .env -> STATE_MACHINE_ARN"

# Decide which API base to use for the frontend
$apiBase = if ($backendUrl) { $backendUrl } elseif ($apiGwUrl) { $apiGwUrl } else { "" }

if ($apiBase) {
    $feEnv = Join-Path $ProjectRoot "frontend\.env"
    (Get-Content $feEnv) -replace "^VITE_API_BASE=.*", "VITE_API_BASE=$apiBase" |
        Set-Content $feEnv -Encoding utf8
    Log "Patched frontend/.env -> VITE_API_BASE=$apiBase"
} else {
    Warn "No backend URL found — frontend/.env not patched (run locally with http://localhost:8000)"
}

# ---------------------------------------------------------------------------
# 5. Build and deploy frontend static files to S3 (optional)
#    Uncomment when you have a CloudFront/S3 static hosting stack.
# ---------------------------------------------------------------------------
# $frontendBucket = $outputs."TheraScout-Frontend".BucketName
# if ($frontendBucket) {
#     Log "Building frontend ..."
#     Set-Location (Join-Path $ProjectRoot "frontend")
#     npm run build
#     Log "Uploading frontend to s3://$frontendBucket ..."
#     aws s3 sync dist/ "s3://$frontendBucket" --delete --profile saml
#     Set-Location $ProjectRoot
# }

Set-Location $ProjectRoot
Log ""
Log "=========================================="
Log "  Deployment complete!"
Log "=========================================="
if ($backendUrl) {
    Log "  Live backend:  $backendUrl"
    Log "  Health check:  $backendUrl/health"
    Log "  Swagger UI:    $backendUrl/docs"
}
Log ""
Log "  Local dev (alternative to Fargate):"
Log "    cd backend && python -m uvicorn main:app --reload"
Log "    cd frontend && npm run dev"
