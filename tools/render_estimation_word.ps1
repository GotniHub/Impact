$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$docPath = Join-Path $projectRoot 'outputs\Synthese_IMPACT_OpenAI_Ollama_4000_exposants.docx'
$pdfPath = Join-Path $projectRoot 'outputs\qa\synthese\Synthese_word.pdf'
New-Item -ItemType Directory -Path (Split-Path -Parent $pdfPath) -Force | Out-Null
$wordApplication = $null
$wordDocument = $null
$ownsApplication = $false
$oldAlerts = $null
$oldSecurity = $null
try {
    try { $wordApplication = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application') }
    catch {
        $wordApplication = New-Object -ComObject Word.Application
        $ownsApplication = $true
        $wordApplication.Visible = $false
    }
    $oldAlerts = $wordApplication.DisplayAlerts
    $oldSecurity = $wordApplication.AutomationSecurity
    $wordApplication.DisplayAlerts = 0
    $wordApplication.AutomationSecurity = 3
    Write-Output 'Word disponible'
    $wordDocument = $wordApplication.Documents.Open($docPath, $false, $true, $false)
    Write-Output 'Document ouvert'
    $wordDocument.ExportAsFixedFormat($pdfPath, 17)
    Write-Output 'PDF exporte'
    Write-Output $pdfPath
} finally {
    if ($wordDocument) { $wordDocument.Close(0); [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordDocument) }
    if ($wordApplication) {
        if ($null -ne $oldAlerts) { $wordApplication.DisplayAlerts = $oldAlerts }
        if ($null -ne $oldSecurity) { $wordApplication.AutomationSecurity = $oldSecurity }
        if ($ownsApplication) { $wordApplication.Quit(0) }
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($wordApplication)
    }
}
