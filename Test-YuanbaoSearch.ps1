param(
    [string]$Query = 'Python official documentation venv',
    [string]$Site,
    [ValidateRange(1, 10)][int]$ResultLimit = 3,
    [switch]$Detailed,
    [switch]$WriteStatus
)

$ErrorActionPreference = 'Stop'
$statusPath = Join-Path $PSScriptRoot '.yuanbao-test-status.json'
$timer = [Diagnostics.Stopwatch]::StartNew()
$keyValue = [Environment]::GetEnvironmentVariable('YUANBAO_SEARCH_API_KEY', 'Process')
$resultStatus = $null
$stage = 'LoadLocalKey'
try {
    if ([string]::IsNullOrWhiteSpace($keyValue)) {
        $configPath = Join-Path $PSScriptRoot '.env.local'
        if (Test-Path -LiteralPath $configPath) {
            foreach ($configLine in [IO.File]::ReadAllLines($configPath)) {
                if ($configLine -match '^\s*YUANBAO_SEARCH_API_KEY\s*=\s*(.*)$') {
                    $keyValue = $Matches[1].Trim()
                    break
                }
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($keyValue)) { throw 'MissingLocalKey' }
    if ($keyValue -match '[\r\n]') { throw 'InvalidLocalKey' }

    $requestParameters = @{ Query = $Query }
    if (-not [string]::IsNullOrWhiteSpace($Site)) { $requestParameters.Site = $Site }
    $requestBody = $requestParameters | ConvertTo-Json -Compress
    $stage = 'SendRequest'
    # This machine's .NET HTTPS client fails during TLS negotiation with WSA.
    # Use the installed Windows curl with normal certificate verification.
    # Secret headers go through stdin, never process arguments or temporary files.
    $curlCommand = Get-Command curl.exe -ErrorAction Stop
    function ConvertTo-CurlConfigValue([string]$Text) {
        return '"' + $Text.Replace('\', '\\').Replace('"', '\"').Replace("`r", '\r').Replace("`n", '\n') + '"'
    }
    $curlConfig = @(
        'url = "https://api.wsa.cloud.tencent.com/SearchPro"'
        'request = "POST"'
        ('header = ' + (ConvertTo-CurlConfigValue ('Authorization: Bearer ' + $keyValue)))
        'header = "Content-Type: application/json; charset=utf-8"'
        ('data = ' + (ConvertTo-CurlConfigValue $requestBody))
    ) -join "`n"
    $curlInfo = New-Object Diagnostics.ProcessStartInfo
    $curlInfo.FileName = $curlCommand.Path
    $curlInfo.Arguments = '--silent --show-error --max-time 45 --config -'
    $curlInfo.UseShellExecute = $false
    $curlInfo.CreateNoWindow = $true
    $curlInfo.RedirectStandardInput = $true
    $curlInfo.RedirectStandardOutput = $true
    $curlInfo.RedirectStandardError = $true
    $curlInfo.StandardOutputEncoding = New-Object Text.UTF8Encoding($false)
    $curlProcess = [Diagnostics.Process]::Start($curlInfo)
    $curlOutTask = $curlProcess.StandardOutput.ReadToEndAsync()
    $curlErrTask = $curlProcess.StandardError.ReadToEndAsync()
    $curlInputBytes = [Text.Encoding]::UTF8.GetBytes($curlConfig)
    $curlProcess.StandardInput.BaseStream.Write($curlInputBytes, 0, $curlInputBytes.Length)
    $curlProcess.StandardInput.Close()
    $curlInputBytes = $null
    $curlConfig = $null
    $curlProcess.WaitForExit()
    $curlOutput = $curlOutTask.Result
    $null = $curlErrTask.Result
    if ($curlProcess.ExitCode -ne 0) { throw ('CurlExit:' + $curlProcess.ExitCode) }
    $stage = 'DecodeResponse'
    $responseBody = $curlOutput | ConvertFrom-Json
    $curlOutput = $null
    if ($null -ne $responseBody.Response) { $payload = $responseBody.Response } else { $payload = $responseBody }
    if ($null -ne $payload.Error) { throw ('YuanbaoAPI:' + [string]$payload.Error.Code) }
    $pages = @()
    foreach ($entry in @($payload.Pages)) {
        if ($null -eq $entry) { continue }
        if ($entry -is [string]) { $pages += ($entry | ConvertFrom-Json) } else { $pages += $entry }
    }
    $timer.Stop()
    $resultStatus = [ordered]@{
        success = $true
        hasResults = ($pages.Count -gt 0)
        count = $pages.Count
        elapsedMs = $timer.ElapsedMilliseconds
        version = [string]$payload.Version
        requestId = [string]$payload.RequestId
        fields = @($pages | Select-Object -First 1 | ForEach-Object { $_.PSObject.Properties.Name })
        checkedAt = [DateTimeOffset]::Now.ToString('o')
    }
    Write-Host ('Search OK. Results: {0}. Elapsed: {1} ms.' -f $pages.Count, $timer.ElapsedMilliseconds)
    foreach ($page in ($pages | Select-Object -First $ResultLimit)) {
        Write-Host ('Title: ' + [string]$page.title)
        Write-Host ('URL:   ' + [string]$page.url)
        $excerpt = [string]$page.passage
        if ($Detailed -and -not [string]::IsNullOrWhiteSpace([string]$page.content)) { $excerpt = [string]$page.content }
        $excerptLimit = 240
        if ($Detailed) { $excerptLimit = 5000 }
        if ($excerpt.Length -gt $excerptLimit) { $excerpt = $excerpt.Substring(0, $excerptLimit) + '...' }
        Write-Host ('Summary: ' + $excerpt)
        Write-Host ''
    }
    if ($pages.Count -eq 0) { Write-Host 'The API responded, but returned no search results.' }
} catch {
    $caughtFailure = $_
    $timer.Stop()
    $httpCode = $null
    if ($null -ne $_.Exception.Response) {
        try { $httpCode = [int]$_.Exception.Response.StatusCode } catch { }
    }
    $rawCode = [string]$_.Exception.Message
    $errorCode = 'LocalOrNetworkError'
    if ($rawCode -eq 'MissingLocalKey') { $errorCode = 'MissingLocalKey' }
    elseif ($rawCode -eq 'InvalidLocalKey') { $errorCode = 'InvalidLocalKey' }
    elseif ($rawCode -match '^YuanbaoAPI:([A-Za-z0-9_.-]+)$') { $errorCode = $Matches[1] }
    elseif ($rawCode -match '^CurlExit:([0-9]+)$') { $errorCode = 'CurlExit' + $Matches[1] }
    elseif ($null -ne $httpCode) { $errorCode = 'HTTP' + $httpCode }
    $reason = switch -Regex ($errorCode) {
        'MissingLocalKey' { 'Save your service API Key with Setup-YuanbaoSearch.ps1 first.'; break }
        'InvalidLocalKey' { 'The local key contains invalid line breaks.'; break }
        'HTTP401|HTTP403|AuthFailure|Unauthorized' { 'Authentication or permission failed. Check the service API Key and service permissions.'; break }
        'ResourceNotFound' { 'The search service may not be enabled for this account.'; break }
        'HTTP429|RequestLimitExceeded|LimitExceeded' { 'Rate or quota limit reached. Check the console and retry later.'; break }
        'ResourceUnavailable' { 'Service unavailable. Check account status in the console.'; break }
        'CurlExit(35|60)' { 'TLS or certificate validation failed. Check the local network; certificate checks remain enabled.'; break }
        'CurlExit28' { 'Search request timed out after 45 seconds.'; break }
        default { 'Check network access and service status. No credentials are printed.' }
    }
    $exceptionTypes = @()
    $webStatus = $null
    $failureException = $caughtFailure.Exception
    while ($null -ne $failureException) {
        $exceptionTypes += $failureException.GetType().FullName
        if ($failureException -is [Net.WebException]) { $webStatus = [string]$failureException.Status }
        $failureException = $failureException.InnerException
    }
    $resultStatus = [ordered]@{ success = $false; errorCode = $errorCode; reason = $reason; stage = $stage; exceptionTypes = $exceptionTypes; webStatus = $webStatus; httpStatus = $httpCode; elapsedMs = $timer.ElapsedMilliseconds; checkedAt = [DateTimeOffset]::Now.ToString('o') }
    Write-Host ('Search failed: ' + $errorCode)
    Write-Host ('Stage: ' + $stage + '. Exception types: ' + ($exceptionTypes -join ' -> '))
    if ($null -ne $webStatus) { Write-Host ('Network status: ' + $webStatus) }
    Write-Host $reason
} finally {
    $keyValue = $null
    $headers = $null
    if ($WriteStatus -and $null -ne $resultStatus) {
        # Only diagnostic metadata is saved, not search result contents or secrets.
        [IO.File]::WriteAllText($statusPath, ($resultStatus | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
    }
}
if (-not $resultStatus.success) { exit 1 }

