#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Full TheraScout deployment: bundles Lambda dependencies, deploys CDK stacks,
    then patches .env files with the live ARNs and URLs.

.USAGE
    # First time (bootstraps CDK for your account):
    .\deploy.ps1 -AccountId 123456789012 -Bootstrap

    # Subsequent deploys:
    .\deploy.ps1 -AccountId 123456789012
#>
param(
    [Parameter(Mandatory=$true)]
    [string]$AccountId,

    [switch]$Bootstrap
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot

function Log($msg) { Write-Host "[deploy] $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# 1. Bundle reportlab into compose_pdf_report so CDK can zip it up
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
    cdk bootstrap "aws://$AccountId/$region" --context "account=$AccountId" --context "region=$region" --profile saml
    if (-not $?) { Die "cdk bootstrap failed" }
}

# ---------------------------------------------------------------------------
# 3. Deploy all three stacks
# ---------------------------------------------------------------------------
Log "Deploying all CDK stacks (this takes ~10 min first time) ..."
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

$sfnArn   = $outputs."TheraScout-Orchestration".StateMachineArn
$apiUrl   = $outputs."TheraScout-Orchestration".ApiGatewayUrl

if (-not $sfnArn) { Die "StateMachineArn not found in cdk_outputs.json" }
if (-not $apiUrl) { Die "ApiGatewayUrl not found in cdk_outputs.json" }

Log "STATE_MACHINE_ARN = $sfnArn"
Log "API_GATEWAY_URL   = $apiUrl"

# Patch root .env
$rootEnv = Join-Path $ProjectRoot ".env"
(Get-Content $rootEnv) -replace "^STATE_MACHINE_ARN=.*", "STATE_MACHINE_ARN=$sfnArn" |
    Set-Content $rootEnv -Encoding utf8
Log "Patched .env -> STATE_MACHINE_ARN"

# Patch frontend/.env
$feEnv = Join-Path $ProjectRoot "frontend\.env"
(Get-Content $feEnv) -replace "^VITE_API_BASE=.*", "VITE_API_BASE=$apiUrl" |
    Set-Content $feEnv -Encoding utf8
Log "Patched frontend/.env -> VITE_API_BASE"

Set-Location $ProjectRoot
Log ""
Log "Deployment complete."
Log "  Backend: cd backend && uvicorn main:app --reload"
Log "  Frontend: cd frontend && npm run dev"
