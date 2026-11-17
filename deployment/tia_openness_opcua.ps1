# TIA Openness 只读巡检脚本 — OPC UA 配置状态导出
# 前置: TIA Portal V20 已打开目标项目; 首次运行需在 TIA 弹窗中人工允许访问。
# 运行: powershell -File deployment/tia_openness_opcua.ps1 -TiaProject "Line03MCPBoZhonSiemens20260401"
#
# 说明: Openness 不做无人值守修改(证书导入仍需 GUI 确认), 本脚本仅巡检导出。

param(
    [string]$TiaProject = "Line03MCPBoZhonSiemens20260401",
    [string]$OutFile = "opcua_inventory.json"
)

Add-Type -AssemblyName "Siemens.Engineering, Version=20.0.0.0, Culture=neutral, PublicKeyToken=d29ec89bac048f84"
Add-Type -AssemblyName "Siemens.Engineering.Hmi, Version=20.0.0.0, Culture=neutral, PublicKeyToken=d29ec89bac048f84"

$tia = [Siemens.Engineering.TiaPortal]::GetProcesses() | Where-Object { $_.ProjectDirectory -like "*$TiaProject*" } | Select-Object -First 1
if (-not $tia) { $tia = [Siemens.Engineering.TiaPortal]::GetProcesses() | Select-Object -First 1 }
if (-not $tia) { Write-Error "未找到已打开的 TIA 项目进程（请先打开项目，并在弹窗中允许外部访问）"; exit 1 }

$project = $tia.Projects[0]
$report = @{ project = $project.Name; cpus = @() }

foreach ($dev in $project.Devices) {
    foreach ($item in $dev.DeviceItems) {
        $cpu = $item as [Siemens.Engineering.SW.ControllerS7CPU]
        if ($cpu) {
            $cpuReport = @{ name = $item.Name; opcua = @{} }
            # OPC UA 属性随 TIA 版本不同有所差异，逐项尝试读取，缺失记 null
            foreach ($prop in @("OpcUaServerActivated", "OpcUaRuntimeLicense", "OpcUaEndpoints")) {
                try { $cpuReport.opcua[$prop] = $cpu.GetType().GetProperty($prop).GetValue($cpu, $null) } catch { $cpuReport.opcua[$prop] = $null }
            }
            $report.cpus += $cpuReport
        }
    }
}

$report | ConvertTo-Json -Depth 5 | Out-File -Encoding UTF8 $OutFile
Write-Host "已导出: $OutFile"
