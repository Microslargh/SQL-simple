# 验证构建后的文件是否包含正确的环境变量
Write-Host "Verifying build output..." -ForegroundColor Cyan

$distPath = "dist"
if (-not (Test-Path $distPath)) {
    Write-Host "Error: dist directory does not exist. Please run 'npm run build' first." -ForegroundColor Red
    exit 1
}

# 查找所有 JavaScript 文件
$jsFiles = Get-ChildItem -Path $distPath -Recurse -Filter "*.js" | Where-Object { $_.Name -match "index-.*\.js" }

if ($jsFiles.Count -eq 0) {
    Write-Host "Warning: No index-*.js files found in dist directory." -ForegroundColor Yellow
    exit 0
}

$productionUrl = "10.125.33.145:9038"
$localhostUrl = "localhost:8000"

Write-Host "`nChecking for production URL: $productionUrl" -ForegroundColor Green
Write-Host "Checking for localhost URL: $localhostUrl" -ForegroundColor Yellow

$hasProductionUrl = $false
$hasLocalhostUrl = $false

foreach ($file in $jsFiles) {
    Write-Host "`nChecking file: $($file.Name)" -ForegroundColor Cyan
    $content = Get-Content $file.FullName -Raw -Encoding UTF8
    
    $productionMatches = [regex]::Matches($content, [regex]::Escape($productionUrl))
    $localhostMatches = [regex]::Matches($content, [regex]::Escape($localhostUrl))
    
    if ($productionMatches.Count -gt 0) {
        Write-Host "  ✓ Found production URL: $($productionMatches.Count) times" -ForegroundColor Green
        $hasProductionUrl = $true
    } else {
        Write-Host "  ✗ Production URL not found" -ForegroundColor Red
    }
    
    if ($localhostMatches.Count -gt 0) {
        Write-Host "  ⚠ Found localhost URL: $($localhostMatches.Count) times" -ForegroundColor Yellow
        $hasLocalhostUrl = $true
        
        # 显示包含 localhost 的上下文
        $lines = Get-Content $file.FullName -Encoding UTF8
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match [regex]::Escape($localhostUrl)) {
                $start = [Math]::Max(0, $i - 2)
                $end = [Math]::Min($lines.Count - 1, $i + 2)
                Write-Host "  Context (line $($i+1)):" -ForegroundColor Yellow
                for ($j = $start; $j -le $end; $j++) {
                    $prefix = if ($j -eq $i) { ">>> " } else { "    " }
                    Write-Host "$prefix$($lines[$j])" -ForegroundColor Gray
                }
                break
            }
        }
    } else {
        Write-Host "  ✓ No localhost URL found" -ForegroundColor Green
    }
}

Write-Host "`n" + "="*60 -ForegroundColor Cyan
if ($hasProductionUrl -and -not $hasLocalhostUrl) {
    Write-Host "✓ Build verification PASSED: Production URL found, no localhost URL detected." -ForegroundColor Green
    Write-Host "`nIf you still see localhost errors in the browser:" -ForegroundColor Yellow
    Write-Host "  1. Clear browser cache (Ctrl+Shift+Delete)" -ForegroundColor Yellow
    Write-Host "  2. Hard refresh the page (Ctrl+Shift+R or Ctrl+F5)" -ForegroundColor Yellow
    Write-Host "  3. Try incognito/private browsing mode" -ForegroundColor Yellow
    exit 0
} elseif ($hasProductionUrl -and $hasLocalhostUrl) {
    Write-Host "⚠ Build verification WARNING: Both production and localhost URLs found." -ForegroundColor Yellow
    Write-Host "  This might be expected if localhost is used as a fallback in the code." -ForegroundColor Yellow
    Write-Host "  Check the context above to see where localhost is used." -ForegroundColor Yellow
    exit 0
} else {
    Write-Host "✗ Build verification FAILED: Production URL not found or localhost URL detected." -ForegroundColor Red
    Write-Host "  Please check:" -ForegroundColor Yellow
    Write-Host "  1. .env.production file exists and contains VITE_API_BASE_URL" -ForegroundColor Yellow
    Write-Host "  2. Run 'npm run build' with production mode" -ForegroundColor Yellow
    exit 1
}

