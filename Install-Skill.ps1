#requires -Version 5.1
<#
.SYNOPSIS
Copies the bundled endnote-gpt-reader skill into a Codex skills directory.
.DESCRIPTION
The default destination is $env:CODEX_HOME\skills, or
$env:USERPROFILE\.codex\skills when CODEX_HOME is not set.
An existing installation is never overwritten. Use -WhatIf to validate and
preview the operation. A failed copy is retained for diagnosis.
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string] $DestinationRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$skillName = 'endnote-gpt-reader'

function Get-AbsoluteFileSystemPath {
    param([Parameter(Mandatory = $true)][string] $Path)

    $provider = $null
    $drive = $null
    $providerPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath(
        $Path, [ref] $provider, [ref] $drive
    )
    if ($provider.Name -ne 'FileSystem') {
        throw "Only filesystem paths are supported: $Path"
    }
    $absolute = [System.IO.Path]::GetFullPath($providerPath)
    $pathRoot = [System.IO.Path]::GetPathRoot($absolute)
    if ($absolute.Length -gt $pathRoot.Length) {
        return $absolute.TrimEnd([char[]] @('\', '/'))
    }
    return $absolute
}

function Test-SameOrNestedPath {
    param(
        [Parameter(Mandatory = $true)][string] $Candidate,
        [Parameter(Mandatory = $true)][string] $Parent
    )

    if ($Candidate.Equals($Parent, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $Parent.TrimEnd([char[]] @('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    return $Candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
}

function Assert-NoReparseAncestor {
    param([Parameter(Mandatory = $true)][string] $Path)

    $candidate = $Path
    while (-not [string]::IsNullOrEmpty($candidate)) {
        if (Test-Path -LiteralPath $candidate) {
            $item = Get-Item -LiteralPath $candidate -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing a junction, symbolic link, or other reparse point in the path: $candidate"
            }
        }
        $parentDirectory = [System.IO.Directory]::GetParent($candidate)
        if ($null -eq $parentDirectory) {
            break
        }
        $candidate = $parentDirectory.FullName
    }
}

$sourcePath = Get-AbsoluteFileSystemPath (Join-Path -Path $PSScriptRoot -ChildPath $skillName)
if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
    throw "The bundled skill directory was not found: $sourcePath"
}
Assert-NoReparseAncestor $sourcePath
if (-not (Test-Path -LiteralPath (Join-Path $sourcePath 'SKILL.md') -PathType Leaf)) {
    throw "The bundled skill must contain SKILL.md: $sourcePath"
}

if (-not $PSBoundParameters.ContainsKey('DestinationRoot')) {
    if (-not [string]::IsNullOrWhiteSpace($env:CODEX_HOME)) {
        $DestinationRoot = Join-Path -Path $env:CODEX_HOME -ChildPath 'skills'
    }
    elseif (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        $DestinationRoot = Join-Path -Path $env:USERPROFILE -ChildPath '.codex\skills'
    }
    else {
        throw 'CODEX_HOME and USERPROFILE are not set. Supply -DestinationRoot with a skills directory.'
    }
}
if ([string]::IsNullOrWhiteSpace($DestinationRoot)) {
    throw 'DestinationRoot must be a nonempty filesystem path.'
}
$destinationRootPath = Get-AbsoluteFileSystemPath $DestinationRoot
$destinationPath = Get-AbsoluteFileSystemPath (Join-Path -Path $destinationRootPath -ChildPath $skillName)

if ((Test-SameOrNestedPath $destinationPath $sourcePath) -or
    (Test-SameOrNestedPath $sourcePath $destinationPath)) {
    throw "Source and destination must be separate, non-nested directories. Source: $sourcePath; destination: $destinationPath"
}
Assert-NoReparseAncestor $destinationPath
if (Test-Path -LiteralPath $destinationRootPath) {
    if (-not (Test-Path -LiteralPath $destinationRootPath -PathType Container)) {
        throw "The destination skills root is not a directory: $destinationRootPath"
    }
}
if (Test-Path -LiteralPath $destinationPath) {
    throw "The destination already exists; nothing was overwritten: $destinationPath"
}

# Inspect each directory explicitly so that linked source directories are never traversed.
$sourceDirectories = New-Object 'System.Collections.Generic.List[string]'
$sourceFiles = New-Object 'System.Collections.Generic.List[string]'
$pendingDirectories = New-Object 'System.Collections.Generic.Queue[string]'
$pendingDirectories.Enqueue($sourcePath)
while ($pendingDirectories.Count -gt 0) {
    $currentDirectory = $pendingDirectories.Dequeue()
    foreach ($entry in Get-ChildItem -LiteralPath $currentDirectory -Force) {
        if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing a junction, symbolic link, or other reparse point in the bundled skill: $($entry.FullName)"
        }
        if ($entry.PSIsContainer) {
            $sourceDirectories.Add($entry.FullName)
            $pendingDirectories.Enqueue($entry.FullName)
        }
        else {
            $sourceFiles.Add($entry.FullName)
        }
    }
}

if (-not $PSCmdlet.ShouldProcess($destinationPath, "Copy the bundled skill from '$sourcePath'")) {
    return
}

try {
    if (-not (Test-Path -LiteralPath $destinationRootPath -PathType Container)) {
        New-Item -ItemType Directory -Path $destinationRootPath -Force -WhatIf:$false | Out-Null
    }
    Assert-NoReparseAncestor $destinationPath
    # Omit -Force here: a destination created since validation must also be refused.
    New-Item -ItemType Directory -Path $destinationPath -WhatIf:$false | Out-Null
    foreach ($directoryPath in $sourceDirectories) {
        $relativePath = $directoryPath.Substring($sourcePath.Length).TrimStart([char[]] @('\', '/'))
        $copyDirectory = Join-Path -Path $destinationPath -ChildPath $relativePath
        New-Item -ItemType Directory -Path $copyDirectory -WhatIf:$false | Out-Null
    }
    foreach ($filePath in $sourceFiles) {
        $relativePath = $filePath.Substring($sourcePath.Length).TrimStart([char[]] @('\', '/'))
        $copyPath = Join-Path -Path $destinationPath -ChildPath $relativePath
        [System.IO.File]::Copy($filePath, $copyPath, $false)
    }
}
catch {
    throw "Skill installation failed. Source: $sourcePath; destination: $destinationPath. Any created directories and partial files were retained for diagnosis. Original error: $($_.Exception.Message)"
}

Write-Output "Installed skill: $destinationPath"
