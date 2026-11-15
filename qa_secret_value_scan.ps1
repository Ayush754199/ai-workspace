# Does any committed blob contain a value that is currently in a local .env file?
# Prints commit + file names only; never prints the matched value.
Set-Location $PSScriptRoot

$needles = @{}
foreach ($f in @('.env', 'backend\.env')) {
    if (-not (Test-Path $f)) { continue }
    foreach ($line in Get-Content $f) {
        if ($line -match '^([A-Z0-9_]+)=(.+)$') {
            $val = $Matches[2].Trim()
            if ($val.Length -ge 8 -and $val -notmatch '^<.*>$') { $needles[$val] = $Matches[1] }
        }
    }
}
Write-Output ("values under test: " + $needles.Count)

# Enumerate every distinct file path that ever existed, once.
$paths = git log --all --pretty=format: --name-only | Sort-Object -Unique | Where-Object { $_ -ne '' }
$exposed = @{}
$scanned = 0
foreach ($p in $paths) {
    $blob = git rev-list --all --objects -- "$p" 2>$null
    $shas = $blob | ForEach-Object { ($_ -split ' ')[0] } | Sort-Object -Unique
    foreach ($sha in $shas) {
        if (-not $sha -or $sha.StartsWith('tree')) { continue }
        $scanned++
        $content = git cat-file -p $sha 2>$null
        if (-not $content) { continue }
        $text = $content -join "`n"
        foreach ($needle in $needles.Keys) {
            if ($text.Contains($needle)) {
                $key = $needles[$needle]
                $entry = "$p (variable $key)"
                if (-not $exposed.ContainsKey($entry)) { $exposed[$entry] = $true }
            }
        }
    }
}
Write-Output ("blobs scanned: " + $scanned)
if ($exposed.Count -eq 0) {
    Write-Output 'RESULT: no local .env value appears in any committed blob'
} else {
    Write-Output 'RESULT: local .env values found in committed blobs:'
    $exposed.Keys | Sort-Object | ForEach-Object { Write-Output ('EXPOSED: ' + $_) }
}
