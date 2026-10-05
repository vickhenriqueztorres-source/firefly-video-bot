param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [int]$TimeoutSeconds = 15
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class ForegroundWindowTitle {
    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    public static string Read() {
        var buffer = new StringBuilder(512);
        GetWindowText(GetForegroundWindow(), buffer, buffer.Capacity);
        return buffer.ToString();
    }
}
"@

$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
while ([DateTime]::UtcNow -lt $deadline) {
    $title = [ForegroundWindowTitle]::Read()
    if ($title -match '(^|\s)(Abrir|Open)(\s|$)') {
        $escaped = $FilePath.Replace('{', '{{}').Replace('}', '{}}')
        [System.Windows.Forms.SendKeys]::SendWait('%n')
        Start-Sleep -Milliseconds 150
        [System.Windows.Forms.SendKeys]::SendWait($escaped)
        Start-Sleep -Milliseconds 150
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        exit 0
    }
    Start-Sleep -Milliseconds 100
}

Write-Error "Native file chooser did not become the foreground window."
exit 2
