param(
    [Parameter(Mandatory = $true)] [ValidateSet('bare', 'groundtruth')] [string]$Treatment,
    [switch]$AllowBaselineRerun
)

# Codex-side safety hook: the recorded GT-off run is the baseline.  Never
# launch another bare arm unless the operator explicitly overrides this guard.
if ($Treatment -eq 'bare' -and -not $AllowBaselineRerun) {
    throw 'Refusing GT-off benchmark dispatch: use the existing baseline artifact. Pass -AllowBaselineRerun only for an intentional protocol change.'
}

Write-Output "benchmark dispatch allowed: treatment=$Treatment"
