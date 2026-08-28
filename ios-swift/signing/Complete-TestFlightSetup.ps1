param(
    [Parameter(Mandatory = $true)]
    [string]$CertificatePath,

    [Parameter(Mandatory = $true)]
    [string]$PrivateKeyPath,

    [Parameter(Mandatory = $true)]
    [string]$ProvisioningProfilePath,

    [Parameter(Mandatory = $true)]
    [string]$ShareProvisioningProfilePath,

    [Parameter(Mandatory = $true)]
    [string]$AppStoreConnectKeyPath,

    [Parameter(Mandatory = $true)]
    [string]$AppStoreConnectKeyId,

    [Parameter(Mandatory = $true)]
    [string]$AppStoreConnectIssuerId,

    [Parameter(Mandatory = $true)]
    [string]$AppleTeamId,

    [string]$BundleId = "de.mausbaeren.rezepte"
)

$ErrorActionPreference = "Stop"

function Resolve-RequiredFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label wurde nicht gefunden: $Path"
    }
    return (Resolve-Path -LiteralPath $Path).Path
}

$certificate = Resolve-RequiredFile $CertificatePath "Apple-Zertifikat"
$privateKey = Resolve-RequiredFile $PrivateKeyPath "Privater Schlüssel"
$profile = Resolve-RequiredFile $ProvisioningProfilePath "Provisioning Profile"
$shareProfile = Resolve-RequiredFile $ShareProvisioningProfilePath "Share-Extension Provisioning Profile"
$ascKey = Resolve-RequiredFile $AppStoreConnectKeyPath "App-Store-Connect-Schlüssel"

$opensslCandidates = @(
    "C:\Program Files\Git\usr\bin\openssl.exe",
    "C:\Program Files\Git\mingw64\bin\openssl.exe"
)
$openssl = $opensslCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $openssl) {
    throw "OpenSSL wurde nicht gefunden. Git for Windows muss installiert sein."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI (gh) wurde nicht gefunden."
}

$tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
$workDirectory = Join-Path $tempRoot ("rezepte-ios-signing-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $workDirectory | Out-Null

try {
    $certificatePem = Join-Path $workDirectory "distribution.pem"
    $p12Path = Join-Path $workDirectory "distribution.p12"
    $p12Password = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    $keychainPassword = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")

    & $openssl x509 -inform DER -in $certificate -out $certificatePem
    if ($LASTEXITCODE -ne 0) { throw "Apple-Zertifikat konnte nicht konvertiert werden." }

    & $openssl pkcs12 -export -out $p12Path -inkey $privateKey -in $certificatePem -passout "pass:$p12Password"
    if ($LASTEXITCODE -ne 0) { throw "P12-Datei konnte nicht erstellt werden." }

    $p12Base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($p12Path))
    $profileBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($profile))
    $shareProfileBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($shareProfile))
    $ascKeyBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($ascKey))

    function Set-GitHubSecret([string]$Name, [string]$Value) {
        $Value | gh secret set $Name --repo oliverzimmermann1986-debug/Rezepte
        if ($LASTEXITCODE -ne 0) { throw "GitHub-Secret $Name konnte nicht gespeichert werden." }
    }

    Set-GitHubSecret "IOS_DISTRIBUTION_P12_BASE64" $p12Base64
    Set-GitHubSecret "IOS_P12_PASSWORD" $p12Password
    Set-GitHubSecret "IOS_KEYCHAIN_PASSWORD" $keychainPassword
    Set-GitHubSecret "IOS_APPSTORE_PROFILE_BASE64" $profileBase64
    Set-GitHubSecret "IOS_SHARE_PROFILE_BASE64" $shareProfileBase64
    Set-GitHubSecret "ASC_PRIVATE_KEY_BASE64" $ascKeyBase64
    Set-GitHubSecret "ASC_KEY_ID" $AppStoreConnectKeyId
    Set-GitHubSecret "ASC_ISSUER_ID" $AppStoreConnectIssuerId
    Set-GitHubSecret "APPLE_TEAM_ID" $AppleTeamId
    gh variable set IOS_BUNDLE_ID --body $BundleId --repo oliverzimmermann1986-debug/Rezepte
    if ($LASTEXITCODE -ne 0) { throw "GitHub-Variable IOS_BUNDLE_ID konnte nicht gespeichert werden." }

    Write-Host "TestFlight-Secrets wurden verschlüsselt bei GitHub hinterlegt."
    Write-Host "Die geheimen Werte wurden nicht im Repository gespeichert."
}
finally {
    if (Test-Path -LiteralPath $workDirectory) {
        $resolvedWorkDirectory = [System.IO.Path]::GetFullPath($workDirectory)
        if (-not $resolvedWorkDirectory.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
            -not ([System.IO.Path]::GetFileName($resolvedWorkDirectory)).StartsWith("rezepte-ios-signing-")) {
            throw "Unsicherer temporärer Löschpfad wurde verweigert: $resolvedWorkDirectory"
        }
        Remove-Item -LiteralPath $resolvedWorkDirectory -Recurse -Force
    }
}
