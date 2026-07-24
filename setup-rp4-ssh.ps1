[CmdletBinding()]
param(
    [string]$EnvFile = (Join-Path $PSScriptRoot ".env"),
    [string]$KeyPath = (Join-Path $env:USERPROFILE ".ssh\frontdoor_rp4")
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function ConvertFrom-SecureValue {
    param([Security.SecureString]$Value)

    return [System.Net.NetworkCredential]::new("", $Value).Password
}

function ConvertTo-DotEnvValue {
    param([string]$Value)

    if ($Value.Contains("`r") -or $Value.Contains("`n")) {
        throw "Environment values cannot contain newlines."
    }

    $escaped = $Value.Replace("\", "\\").Replace('"', '\"')
    return '"' + $escaped + '"'
}

function ConvertFrom-DotEnvValue {
    param([string]$Value)

    $value = $Value.Trim()
    if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
        $value = $value.Substring(1, $value.Length - 2)
        return $value.Replace('\"', '"').Replace("\\", "\")
    }

    if ($value.Length -ge 2 -and $value.StartsWith("'") -and $value.EndsWith("'")) {
        return $value.Substring(1, $value.Length - 2)
    }

    return $value
}

function Read-DotEnv {
    param([string]$Path)

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*#" -or [string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        if ($line -notmatch "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$") {
            throw "Invalid line in ${Path}: $line"
        }

        $values[$matches[1]] = ConvertFrom-DotEnvValue $matches[2]
    }

    return $values
}

function New-Rp4EnvFile {
    param([string]$Path)

    Write-Host "No environment file was found at $Path."
    Write-Host "Create the RP4 connection settings. The password will be stored in this Git-ignored file."

    $hostName = (Read-Host "RP4 host name or IP address").Trim()
    $userName = (Read-Host "RP4 user name").Trim()
    $password = ConvertFrom-SecureValue (Read-Host "Desired/current RP4 password" -AsSecureString)

    if (-not $hostName -or -not $userName -or -not $password) {
        throw "Host, user name, and password are required."
    }

    $parent = Split-Path -Parent $Path
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent | Out-Null
    }

    @(
        "# Local secrets. Do not commit this file."
        "ACCESS_CONTROL_CONFIG=configs/local.yaml"
        "RP4_HOST=$(ConvertTo-DotEnvValue $hostName)"
        "RP4_USER=$(ConvertTo-DotEnvValue $userName)"
        "RP4_PASSWORD=$(ConvertTo-DotEnvValue $password)"
    ) | Set-Content -LiteralPath $Path -Encoding utf8

    Write-Host "Created $Path."
}

foreach ($command in @("ssh", "ssh-keygen", "uv")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command '$command' was not found in PATH."
    }
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    New-Rp4EnvFile $EnvFile
}

$config = Read-DotEnv $EnvFile
foreach ($name in @("RP4_HOST", "RP4_USER", "RP4_PASSWORD")) {
    if (-not $config.ContainsKey($name) -or [string]::IsNullOrWhiteSpace($config[$name])) {
        throw "$name is missing or empty in $EnvFile."
    }
}

$keyDirectory = Split-Path -Parent $KeyPath
if (-not (Test-Path -LiteralPath $keyDirectory)) {
    New-Item -ItemType Directory -Path $keyDirectory | Out-Null
}

$publicKeyPath = "$KeyPath.pub"
$privateExists = Test-Path -LiteralPath $KeyPath
$publicExists = Test-Path -LiteralPath $publicKeyPath
if ($privateExists -xor $publicExists) {
    throw "Only one half of the SSH key pair exists at $KeyPath. Refusing to overwrite it."
}

if (-not $privateExists) {
    # Older Windows OpenSSH builds require a quoted empty argument for -N.
    & ssh-keygen -q -t ed25519 -f $KeyPath -N '""' -C "frontdoor-rp4"
    if ($LASTEXITCODE -ne 0) {
        throw "ssh-keygen failed."
    }
    Write-Host "Created SSH key $KeyPath."
}
else {
    Write-Host "Reusing SSH key $KeyPath."
}

$env:RP4_SETUP_HOST = $config["RP4_HOST"]
$env:RP4_SETUP_USER = $config["RP4_USER"]
$env:RP4_SETUP_PASSWORD = $config["RP4_PASSWORD"]
$env:RP4_SETUP_PUBLIC_KEY = (Get-Content -LiteralPath $publicKeyPath -Raw).Trim()
$env:RP4_SETUP_PRIVATE_KEY = $KeyPath

$python = @'
import base64
import getpass
import os
import sys

import paramiko


host = os.environ["RP4_SETUP_HOST"]
user = os.environ["RP4_SETUP_USER"]
desired_password = os.environ["RP4_SETUP_PASSWORD"]
public_key = os.environ["RP4_SETUP_PUBLIC_KEY"]
private_key_path = os.environ["RP4_SETUP_PRIVATE_KEY"]


def connect_with_password(password):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        host,
        username=user,
        password=password,
        look_for_keys=False,
        allow_agent=False,
        timeout=10,
        auth_timeout=10,
    )
    return client


try:
    client = connect_with_password(desired_password)
    print("Configured password authentication verified.")
except paramiko.AuthenticationException:
    current_password = getpass.getpass(
        "RP4_PASSWORD was not accepted. Enter the current remote password: "
    )
    client = connect_with_password(current_password)

    _, stdout, _ = client.exec_command("sudo -n true")
    password_line = f"{user}:{desired_password}\n"
    if stdout.channel.recv_exit_status() == 0:
        stdin, stdout, stderr = client.exec_command("sudo -n chpasswd")
        stdin.write(password_line)
    else:
        stdin, stdout, stderr = client.exec_command("sudo -S -p '' chpasswd")
        stdin.write(current_password + "\n")
        stdin.write(password_line)

    stdin.flush()
    stdin.channel.shutdown_write()
    status = stdout.channel.recv_exit_status()
    error = stderr.read().decode(errors="replace").strip()
    client.close()
    if status != 0:
        raise RuntimeError("Remote password change failed: " + error[:300])

    client = connect_with_password(desired_password)
    print("Remote password changed and verified.")

encoded_key = base64.b64encode((public_key + "\n").encode()).decode()
command = (
    "umask 077; "
    "mkdir -p ~/.ssh; "
    "touch ~/.ssh/authorized_keys; "
    f"grep -qxF '{public_key}' ~/.ssh/authorized_keys "
    f"|| echo {encoded_key} | base64 -d >> ~/.ssh/authorized_keys; "
    "chmod 700 ~/.ssh; "
    "chmod 600 ~/.ssh/authorized_keys"
)
_, stdout, stderr = client.exec_command(command)
status = stdout.channel.recv_exit_status()
error = stderr.read().decode(errors="replace").strip()
client.close()
if status != 0:
    raise RuntimeError("Public-key installation failed: " + error[:300])

key = paramiko.Ed25519Key.from_private_key_file(private_key_path)
key_client = paramiko.SSHClient()
key_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
key_client.connect(
    host,
    username=user,
    pkey=key,
    look_for_keys=False,
    allow_agent=False,
    timeout=10,
    auth_timeout=10,
)
_, stdout, _ = key_client.exec_command("printf key-auth-ok")
result = stdout.read().decode()
status = stdout.channel.recv_exit_status()
key_client.close()
if status != 0 or result != "key-auth-ok":
    raise RuntimeError("Paramiko key-authentication verification failed.")

print("Public key installed and key authentication verified.")
'@

try {
    $python | uv run --with paramiko python -
    if ($LASTEXITCODE -ne 0) {
        throw "RP4 SSH setup failed."
    }
}
finally {
    Remove-Item Env:RP4_SETUP_HOST -ErrorAction SilentlyContinue
    Remove-Item Env:RP4_SETUP_USER -ErrorAction SilentlyContinue
    Remove-Item Env:RP4_SETUP_PASSWORD -ErrorAction SilentlyContinue
    Remove-Item Env:RP4_SETUP_PUBLIC_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:RP4_SETUP_PRIVATE_KEY -ErrorAction SilentlyContinue
}

$target = "$($config["RP4_USER"])@$($config["RP4_HOST"])"
& ssh `
    -i $KeyPath `
    -o BatchMode=yes `
    -o StrictHostKeyChecking=accept-new `
    -o ConnectTimeout=10 `
    $target `
    "printf powershell-key-auth-ok"
if ($LASTEXITCODE -ne 0) {
    throw "Native PowerShell/OpenSSH key-authentication test failed."
}

Write-Host ""
Write-Host "RP4 SSH setup completed successfully."
Write-Host "Connect with:"
Write-Host "  ssh -i `"$KeyPath`" $target"
