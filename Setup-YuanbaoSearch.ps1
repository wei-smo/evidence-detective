param([switch]$Console)

$ErrorActionPreference = 'Stop'
$configPath = Join-Path $PSScriptRoot '.env.local'
$statusPath = Join-Path $PSScriptRoot '.yuanbao-test-status.json'
$utf8 = New-Object Text.UTF8Encoding($false)

function Save-SearchKey([string]$Value) {
    $Value = $Value.Trim()
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value -match '\s') {
        throw 'Enter the service API Key without spaces or line breaks.'
    }
    $otherLines = @()
    if (Test-Path -LiteralPath $configPath) {
        $otherLines = @([IO.File]::ReadAllLines($configPath) | Where-Object { $_ -notmatch '^\s*YUANBAO_SEARCH_API_KEY\s*=' })
    }
    $configLines = @($otherLines) + @('YUANBAO_SEARCH_API_KEY=' + $Value)
    [IO.File]::WriteAllLines($configPath, [string[]]$configLines, $utf8)
}

if ($Console) {
    Write-Host 'Use Yuanbao Search console -> Service API KEY method.'
    Write-Host 'Do NOT enter Tencent Cloud SecretId/SecretKey or the event invitation code.'
    $protectedValue = Read-Host 'Paste Yuanbao Search SERVICE API Key (input hidden)' -AsSecureString
    $temporaryCredential = New-Object Management.Automation.PSCredential('yuanbao', $protectedValue)
    try { Save-SearchKey $temporaryCredential.GetNetworkCredential().Password } finally { $temporaryCredential = $null; $protectedValue = $null }
    & (Join-Path $PSScriptRoot 'Test-YuanbaoSearch.ps1') -WriteStatus
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object Windows.Forms.Form
$form.Text = 'Yuanbao Search - local configuration'
$form.Size = New-Object Drawing.Size(660, 355)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.TopMost = $true

$label = New-Object Windows.Forms.Label
$label.Location = New-Object Drawing.Point(20, 18)
$label.Size = New-Object Drawing.Size(610, 75)
$label.Text = "Paste your Yuanbao Search SERVICE API Key below.`r`nThe key is saved only to E:\1024\.env.local (excluded from Git).`r`nSaving starts one search request to Tencent Cloud to verify the key."
$inputBox = New-Object Windows.Forms.TextBox
$inputBox.Location = New-Object Drawing.Point(20, 100)
$inputBox.Size = New-Object Drawing.Size(605, 28)
$inputBox.UseSystemPasswordChar = $true
$resultLabel = New-Object Windows.Forms.Label
$resultLabel.Location = New-Object Drawing.Point(20, 145)
$resultLabel.Size = New-Object Drawing.Size(605, 85)
$resultLabel.Text = 'Do not paste Tencent Cloud SecretId/SecretKey. Use the search service API Key.'
$saveButton = New-Object Windows.Forms.Button
$saveButton.Location = New-Object Drawing.Point(390, 245)
$saveButton.Size = New-Object Drawing.Size(135, 35)
$saveButton.Text = 'Save and test'
$closeButton = New-Object Windows.Forms.Button
$closeButton.Location = New-Object Drawing.Point(535, 245)
$closeButton.Size = New-Object Drawing.Size(90, 35)
$closeButton.Text = 'Close'
$closeButton.Add_Click({ $form.Close() })
$saveButton.Add_Click({
    try {
        Save-SearchKey $inputBox.Text
        $inputBox.Clear()
        $saveButton.Enabled = $false
        $resultLabel.Text = 'Key saved locally. Testing search; please wait (up to 45 seconds)...'
        [Windows.Forms.Application]::DoEvents()
        $testPath = Join-Path $PSScriptRoot 'Test-YuanbaoSearch.ps1'
        $processInfo = New-Object Diagnostics.ProcessStartInfo
        $processInfo.FileName = Join-Path $PSHOME 'powershell.exe'
        $processInfo.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $testPath + '" -WriteStatus'
        $processInfo.UseShellExecute = $false
        $processInfo.CreateNoWindow = $true
        $processInfo.RedirectStandardOutput = $true
        $processInfo.RedirectStandardError = $true
        $testProcess = [Diagnostics.Process]::Start($processInfo)
        # Read both streams asynchronously; never show raw exceptions or headers.
        $outTask = $testProcess.StandardOutput.ReadToEndAsync()
        $errTask = $testProcess.StandardError.ReadToEndAsync()
        while (-not $testProcess.HasExited) { [Windows.Forms.Application]::DoEvents(); [Threading.Thread]::Sleep(100) }
        $testProcess.WaitForExit()
        $null = $outTask.Result
        $null = $errTask.Result
        if (Test-Path -LiteralPath $statusPath) {
            $check = [IO.File]::ReadAllText($statusPath) | ConvertFrom-Json
            if ($check.success) {
                $resultLabel.Text = 'SUCCESS: search returned ' + $check.count + ' results in ' + $check.elapsedMs + ' ms. You can close this window.'
            } else {
                $resultLabel.Text = 'Saved locally. Search check failed: ' + $check.errorCode + '. ' + $check.reason
            }
        } else { $resultLabel.Text = 'Key saved. Test did not finish correctly; run Test-YuanbaoSearch.ps1 for diagnostics.' }
    } catch {
        $resultLabel.Text = 'Could not save or test. Check your key and local file permissions. No key is shown in logs.'
    } finally { $saveButton.Enabled = $true }
})
$form.Controls.AddRange(@($label, $inputBox, $resultLabel, $saveButton, $closeButton))
$form.AcceptButton = $saveButton
$null = $form.ShowDialog()
$inputBox.Clear()
$form.Dispose()

