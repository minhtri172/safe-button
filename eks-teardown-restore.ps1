# Tear down / recreate the costly parts of safe-button (EKS Auto Mode cluster + NLB).
#
#   .\eks-teardown-restore.ps1 -Action delete
#   .\eks-teardown-restore.ps1 -Action recreate
#
# Kept (cheap / free): ECR, DynamoDB, Cognito, SSM, Route 53 zone, ACM cert, Lambda, CodePipeline, IAM roles.
# Removed by "delete": Kubernetes Service (and its NLB), EKS cluster (control plane + Auto Mode nodes),
# the Route 53 A record for the domain, and the hourly cron schedule (disabled, not deleted).
param(
    [Parameter(Mandatory = $true)][ValidateSet('delete', 'recreate')][string]$Action
)

$ErrorActionPreference = 'Stop'

$Region      = 'us-east-1'
$Cluster     = 'safe-button'
$Account     = '119997536442'
$K8sVersion  = '1.36'
$ClusterRole = "arn:aws:iam::${Account}:role/AmazonEKSAutoClusterRole"
$NodeRole    = "arn:aws:iam::${Account}:role/AmazonEKSAutoNodeRole"
$PodRole     = "arn:aws:iam::${Account}:role/PodRole"
$Subnets     = 'subnet-0e471cecc5c6b9006,subnet-02168fa63ff94dc7d,subnet-00ef86e85039d88be,subnet-0a43265d0635ecd42,subnet-078ab2f505fd3a409'
$Domain      = 'okcfisher.click.'
$ZoneId      = 'Z07058142VJEPDC8Q1CY0'
$EcrRepo     = 'safe-button'
$ServiceAcct = 'my-pod-service-account'
$TokenParam  = '/safe-button/cron-auth-token'
$CronRule    = 'safe-button-cron-hourly'
$AdminPolicy = 'arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy'
$AdminPrincipals = @(
    "arn:aws:iam::${Account}:user/minhle",
    "arn:aws:iam::${Account}:role/safe-button-deploy-role"
)
$Manifest = Join-Path $PSScriptRoot 'k8s-deployment.yaml'

function Invoke-Aws {
    $out = & aws @args
    if ($LASTEXITCODE -ne 0) { throw "aws $($args -join ' ') failed" }
    return $out
}

function Remove-DnsRecord {
    $rec = Invoke-Aws route53 list-resource-record-sets --hosted-zone-id $ZoneId `
        --query "ResourceRecordSets[?Name=='$Domain' && Type=='A']|[0]" --output json | ConvertFrom-Json
    if (-not $rec) { Write-Host 'No A record to delete.'; return }
    $batch = @{ Changes = @(@{ Action = 'DELETE'; ResourceRecordSet = $rec }) } | ConvertTo-Json -Depth 10
    $f = New-TemporaryFile
    [IO.File]::WriteAllText($f, $batch)
    Invoke-Aws route53 change-resource-record-sets --hosted-zone-id $ZoneId --change-batch "file://$f" | Out-Null
    Remove-Item $f
}

function Set-DnsRecord([string]$LbHostname) {
    $lb = Invoke-Aws elbv2 describe-load-balancers --region $Region `
        --query "LoadBalancers[?DNSName=='$LbHostname']|[0].[CanonicalHostedZoneId,DNSName]" --output json | ConvertFrom-Json
    $batch = @{ Changes = @(@{ Action = 'UPSERT'; ResourceRecordSet = @{
        Name = $Domain; Type = 'A'
        AliasTarget = @{ HostedZoneId = $lb[0]; DNSName = $lb[1]; EvaluateTargetHealth = $false }
    } }) } | ConvertTo-Json -Depth 10
    $f = New-TemporaryFile
    [IO.File]::WriteAllText($f, $batch)
    Invoke-Aws route53 change-resource-record-sets --hosted-zone-id $ZoneId --change-batch "file://$f" | Out-Null
    Remove-Item $f
}

if ($Action -eq 'delete') {
    # 1. Stop the cron Lambda from calling a dead endpoint.
    aws events disable-rule --name $CronRule --region $Region

    # 2. Point kubectl at the cluster, then delete the Service so the controller removes the NLB.
    aws eks update-kubeconfig --name $Cluster --region $Region
    kubectl delete svc safe-button --ignore-not-found --wait=true
    kubectl delete deploy safe-button --ignore-not-found

    # 3. Wait until the NLB is really gone.
    for ($i = 0; $i -lt 30; $i++) {
        $n = Invoke-Aws elbv2 describe-load-balancers --region $Region `
            --query "length(LoadBalancers[?starts_with(LoadBalancerName,'k8s-default-safebutt')])" --output text
        if ($n -eq '0') { break }
        Start-Sleep 10
    }

    # 4. Remove the DNS record that pointed at the NLB.
    Remove-DnsRecord

    # 5. Delete the cluster (also removes access entries, Pod Identity associations, and Auto Mode nodes).
    aws eks delete-cluster --name $Cluster --region $Region
    aws eks wait cluster-deleted --name $Cluster --region $Region
    Write-Host 'Teardown complete.'
}
else {
    # 1. Create the EKS Auto Mode cluster.
    $ErrorActionPreference = 'Continue'
    $existing = aws eks describe-cluster --name $Cluster --region $Region --query cluster.status --output text 2>$null
    $ErrorActionPreference = 'Stop'
    if ($existing) { Write-Host "Cluster already exists ($existing); skipping create." } else {
    aws eks create-cluster --name $Cluster --region $Region --kubernetes-version $K8sVersion `
        --role-arn $ClusterRole `
        --resources-vpc-config "subnetIds=$Subnets,endpointPublicAccess=true,endpointPrivateAccess=true" `
        --access-config authenticationMode=API `
        --compute-config "enabled=true,nodePools=[general-purpose,system],nodeRoleArn=$NodeRole" `
        --kubernetes-network-config 'elasticLoadBalancing={enabled=true}' `
        --storage-config 'blockStorage={enabled=true}' `
        --upgrade-policy supportType=STANDARD `
        --logging 'clusterLogging=[{types=[api,audit,authenticator,controllerManager,scheduler],enabled=true}]' `
        --no-bootstrap-self-managed-addons
    if ($LASTEXITCODE -ne 0) { throw 'create-cluster failed' }
    }
    aws eks wait cluster-active --name $Cluster --region $Region

    # 2. Cluster admin access for you and the pipeline deploy role.
    foreach ($p in $AdminPrincipals) {
        # The cluster creator already has an entry, so "already exists" errors are expected here.
        $ErrorActionPreference = 'Continue'
        aws eks create-access-entry --cluster-name $Cluster --region $Region --principal-arn $p 2>&1 | Out-Null
        $ErrorActionPreference = 'Stop'
        aws eks associate-access-policy --cluster-name $Cluster --region $Region --principal-arn $p `
            --policy-arn $AdminPolicy --access-scope type=cluster | Out-Null
    }

    # 3. Pod Identity association for the app's service account (DynamoDB / SSM access).
    aws eks update-kubeconfig --name $Cluster --region $Region
    kubectl create serviceaccount $ServiceAcct --dry-run=client -o yaml | kubectl apply -f -
    $ErrorActionPreference = 'Continue'
    aws eks create-pod-identity-association --cluster-name $Cluster --region $Region `
        --namespace default --service-account $ServiceAcct --role-arn $PodRole 2>&1 | Out-Null
    $ErrorActionPreference = 'Stop'

    # 4. Cron token secret, restored from SSM (never written to disk or printed).
    $tok = Invoke-Aws ssm get-parameter --name $TokenParam --with-decryption `
        --query Parameter.Value --output text --region $Region
    kubectl create secret generic safe-button-cron --from-literal=CRON_AUTH_TOKEN=$tok --dry-run=client -o yaml | kubectl apply -f -
    $tok = $null

    # 5. Deploy the newest tagged image from ECR using the manifest's IMAGE_URI placeholder.
    $tag = Invoke-Aws ecr describe-images --repository-name $EcrRepo --region $Region `
        --query "sort_by(imageDetails[?imageTags!=null],&imagePushedAt)[-1].imageTags[0]" --output text
    $image = "${Account}.dkr.ecr.${Region}.amazonaws.com/${EcrRepo}:${tag}"
    (Get-Content $Manifest -Raw) -replace 'IMAGE_URI', $image | kubectl apply -f -
    kubectl rollout status deploy/safe-button --timeout=600s

    # 6. Wait for the new NLB hostname, then repoint DNS at it.
    $lbHost = ''
    for ($i = 0; $i -lt 60 -and -not $lbHost; $i++) {
        $lbHost = kubectl get svc safe-button -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
        if (-not $lbHost) { Start-Sleep 10 }
    }
    if (-not $lbHost) { throw 'Load balancer hostname was not assigned.' }
    Set-DnsRecord $lbHost

    # 7. Resume the hourly cron check.
    aws events enable-rule --name $CronRule --region $Region

    # 8. Container Insights (CloudWatch observability add-on).
    $ErrorActionPreference = 'Continue'
    aws eks create-addon --cluster-name $Cluster --region $Region --addon-name amazon-cloudwatch-observability 2>&1 | Out-Null
    $ErrorActionPreference = 'Stop'
    Write-Host "Restore complete. Image: $image  NLB: $lbHost"
}
