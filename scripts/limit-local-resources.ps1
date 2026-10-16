param([string]$ProjectName = 'p119-session-qa')

$ErrorActionPreference = 'Stop'
# Updating limits preserves running jobs, environment, images and data mounts.
$limits = @(
    @{ Service = 'worker'; Cpu = '1'; Memory = '8g'; Swap = '9g' },
    @{ Service = 'database'; Cpu = '1.5'; Memory = '3g'; Swap = '3g' },
    @{ Service = 'backend'; Cpu = '1'; Memory = '1g'; Swap = '1g' },
    @{ Service = 'frontend'; Cpu = '0.5'; Memory = '512m'; Swap = '512m' }
)
foreach ($limit in $limits) {
    $container = "$ProjectName-$($limit.Service)-1"
    docker inspect --format '{{.State.Status}}' $container | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Container unavailable: $container" }
    docker update --cpus $limit.Cpu --memory $limit.Memory --memory-swap $limit.Swap $container
    if ($LASTEXITCODE -ne 0) { throw "Resource limit update failed: $container" }
}
docker stats --no-stream --format '{{.Name}} CPU={{.CPUPerc}} RAM={{.MemUsage}}'
if ($LASTEXITCODE -ne 0) { throw 'Unable to read resource usage' }
