# TIA Openness 只读巡检（V20）— 附加到已打开的 TIA，反射导出 CPU/OPC UA 配置
# 运行: powershell -ExecutionPolicy Bypass -File deployment/tia_inventory.ps1
# 注意: 首次执行 TIA 会弹出"允许外部应用访问"对话框——需人工点击允许。

$ErrorActionPreference = "Continue"
$apiDir = "D:\TIA V20\Portal V20\PublicAPI\V20"
$binDir = "D:\TIA V20\Portal V20\Bin"
$env:PATH = "$apiDir;$binDir;$env:PATH"
[System.IO.Directory]::SetCurrentDirectory($apiDir)
[void][System.Reflection.Assembly]::LoadFrom("$apiDir\Siemens.Engineering.dll")

Write-Host "正在附加到 TIA 进程（如 TIA 弹出权限对话框请点击允许）..."
$procs = [Siemens.Engineering.TiaPortal]::GetProcesses()
if (-not $procs -or $procs.Count -eq 0) { Write-Error "未发现 TIA 进程"; exit 1 }
$tia = $null
foreach ($p in $procs) {
    try {
        $pc = $p.Projects.Count
        Write-Host "  TIA 进程 #$($p.GetHashCode()): 项目数=$pc"
        if ($pc -gt 0 -and -not $tia) { $tia = $p }
    } catch { Write-Host "  进程不可访问: $_" }
}
if (-not $tia) { Write-Error "所有 TIA 进程均无打开项目"; exit 1 }
$proj = $tia.Projects[0]
Write-Host "项目: $($proj.Name)  (TIA 进程数: $($procs.Count))"

$out = [System.Collections.Generic.List[string]]::new()
$out.Add("PROJECT: $($proj.Name)")

function Dump-ScalarProps($obj, $label) {
    $out.Add("=== $label :: $($obj.GetType().FullName)")
    foreach ($prop in $obj.GetType().GetProperties()) {
        if ($prop.GetIndexParameters().Count -gt 0) { continue }
        try {
            $val = $prop.GetValue($obj, $null)
            if ($null -eq $val) { $out.Add("  $($prop.Name) = <null>"); continue }
            $t = $val.GetType()
            if ($t.IsPrimitive -or $t -eq [string] -or $t.IsEnum) {
                $out.Add("  $($prop.Name) = $val")
            } elseif ($val -is [Siemens.Engineering.IEngineeringObject]) {
                $out.Add("  $($prop.Name) = <$($t.Name)>")
            }
        } catch { $out.Add("  $($prop.Name) = <error>") }
    }
}

# 设备树 + 找 CPU
$cpuItem = $null
foreach ($dev in $proj.Devices) {
    foreach ($di in $dev.DeviceItems) {
        $out.Add("DEVICEITEM: $($di.Name) :: $($di.GetType().Name)")
        if ($di -is [Siemens.Engineering.SW.ControllerS7CPU]) { $cpuItem = $di }
    }
}
if (-not $cpuItem) { Write-Error "未找到 S7-1500 CPU"; $out | Out-File deployment/tia_inventory.txt -Encoding UTF8; exit 1 }

Dump-ScalarProps $cpuItem "CPU"
# OPC UA 相关成员（属性名含 Opc/Ua 的深挖一层）
foreach ($prop in $cpuItem.GetType().GetProperties()) {
    if ($prop.Name -notmatch 'Opc|Ua' ) { continue }
    try {
        $val = $prop.GetValue($cpuItem, $null)
        if ($null -ne $val -and $val -is [Siemens.Engineering.IEngineeringObject]) {
            Dump-ScalarProps $val "CPU.$($prop.Name)"
        }
    } catch {}
}

$out | Out-File "$PSScriptRoot\tia_inventory.txt" -Encoding UTF8
Write-Host "已导出 deployment/tia_inventory.txt（$($out.Count) 行）"
Get-Content "$PSScriptRoot\tia_inventory.txt" -TotalCount 60
