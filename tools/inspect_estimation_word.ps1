$ErrorActionPreference = 'Stop'
$activeWord = $null
try {
    $activeWord = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
    $activeWord.Documents | Select-Object Name, FullName
} finally {
    if ($activeWord) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($activeWord) }
}
