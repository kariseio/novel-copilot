<#
.SYNOPSIS
  GitHub 공개용 브랜치(github-public)를 만든다 — 코드와 사용 설명서만, 내부 문서는 제외.

.DESCRIPTION
  현재 작업 트리(HEAD 추적 파일 + 무시되지 않은 새 파일)를 임시 인덱스에 스테이징한 뒤 $Exclude 를 빼고
  $Reinclude 를 다시 넣어 커밋을 만들고 -Branch 에 붙인다. 작업 트리·현재 브랜치·기존 인덱스는 건드리지 않는다.
  브랜치가 이미 있으면 그 위에 커밋을 쌓고(트리가 같으면 건너뜀), 없으면 부모 없는 첫 커밋을 만든다.
  커밋 전에 API 키 패턴이 트리에 있으면 중단한다.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\export_github.ps1
  git push origin github-public:main
  (처음 만들 때 기존 원격 이력을 이으려면 먼저: git branch -f github-public origin/main)
#>
param(
    [string]$Branch = "github-public",
    [string]$Message = ""
)
$ErrorActionPreference = "Continue"

$root = (git rev-parse --show-toplevel)
if ($LASTEXITCODE -ne 0 -or -not $root) { throw "git 저장소 안에서 실행하세요." }
Set-Location $root

# 제외: 내부 설계·연구 문서, 리뷰 기록, 발표 자료, 레퍼런스 원문, 스크래치 덤프, 실험 리포트, 에이전트 설정
$Exclude = @(
    "docs", "cross-review", "ppt", "reference",
    "*.xlsx", "_menu_dump.txt", "_spine_dump.txt",
    "app/cj.txt", "app/full.html",
    "app/tools/reports", "app/tools/_*.py",
    ".claude", ".codex", ".agents", "CLAUDE.md",
    # 공개 트리에 없는 자료(app/data 라이브 작품 JSON, 내부 설계 문서)를 읽는 테스트 — 공개 저장소에서는 항상 실패하므로 제외
    "app/tools/test_fi3_delete_artifacts.py", "app/tools/test_hm1a_humanize_detect.py",
    "app/tools/test_st10_kiwi_metrics.py", "app/tools/test_st11_span_rewrite.py", "app/tools/test_st1_lightness.py",
    "app/tools/test_xr14_consumer_classes.py", "app/tools/test_xr37_semantic_census.py", "app/tools/test_xr3_inventory_sync.py"
)
# 재포함: 코드가 런타임에 읽는 파일
$Reinclude = @(
    "docs/guide", "docs/PIPELINE.md", "docs/ARCHITECTURE.md",   # /manual.html 이 /api/docs/{name} 으로 읽음 (api/routes.py)
    "app/tools/reports/sp1_exemplars.json"                     # 문장 결 예시 데이터 (domain/skill.py, engine/prompts.py)
)

$tmpDir   = [System.IO.Path]::GetTempPath()
$tmpIndex = Join-Path $tmpDir ("novelcopilot-export-index-" + [guid]::NewGuid().ToString("N"))
$msgFile  = Join-Path $tmpDir ("novelcopilot-export-msg-"   + [guid]::NewGuid().ToString("N") + ".txt")
$env:GIT_INDEX_FILE = $tmpIndex
try {
    # 1) 임시 인덱스 = HEAD 추적 상태 + 작업 트리 내용(수정·삭제 반영, 새 파일 추가, .gitignore 존중)
    git read-tree HEAD
    if ($LASTEXITCODE -ne 0) { throw "git read-tree 실패" }
    git -c core.safecrlf=false add -A -- .
    if ($LASTEXITCODE -ne 0) { throw "git add 실패" }

    # 2) 제외 → 재포함(-f: 추적 중이지만 ignore 패턴에 걸리는 파일도 넣는다)
    git rm -r -q --cached --ignore-unmatch -- $Exclude
    if ($LASTEXITCODE -ne 0) { throw "git rm --cached 실패" }
    foreach ($p in $Reinclude) {
        if (Test-Path $p) {
            git -c core.safecrlf=false add -f -- $p
            if ($LASTEXITCODE -ne 0) { throw "git add 실패: $p" }
        } else {
            Write-Warning "재포함 대상 없음(건너뜀): $p"
        }
    }

    $tree = (git write-tree)
    if ($LASTEXITCODE -ne 0 -or -not $tree) { throw "git write-tree 실패" }

    # 3) 키 유출 검사 — 걸리면 브랜치를 만들지 않는다
    $leaks = git grep -I -l -E '(sk-ant-|sk-proj-|AKIA)[A-Za-z0-9_-]{10,}' $tree
    if ($leaks) {
        $leaks | ForEach-Object { Write-Host "  $_" }
        throw "API 키로 보이는 문자열이 있어 중단합니다. 위 파일을 확인하세요."
    }

    # 4) 커밋 — 기존 브랜치가 있으면 그 위에, 없으면 부모 없이
    $parent = git rev-parse -q --verify "refs/heads/$Branch"
    if ($LASTEXITCODE -ne 0) { $parent = $null }
    if ($parent) {
        $parentTree = git rev-parse "$parent^{tree}"
        if ($parentTree -eq $tree) {
            Write-Host "변경 없음 — $Branch 는 그대로입니다 ($parent)"
            return
        }
    }
    if (-not $Message) { $Message = "export: code + user guide ($(Get-Date -Format yyyy-MM-dd))" }
    [System.IO.File]::WriteAllText($msgFile, $Message, (New-Object System.Text.UTF8Encoding $false))
    if ($parent) { $commit = git commit-tree $tree -p $parent -F $msgFile }
    else         { $commit = git commit-tree $tree -F $msgFile }
    if ($LASTEXITCODE -ne 0 -or -not $commit) { throw "git commit-tree 실패" }
    git update-ref "refs/heads/$Branch" $commit
    if ($LASTEXITCODE -ne 0) { throw "git update-ref 실패" }

    # 5) 요약
    $count = (git ls-tree -r --name-only $commit | Measure-Object).Count
    Write-Host ""
    Write-Host "$Branch <- $commit  (파일 $count 개)"
    git ls-tree --name-only $commit | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "업로드:  git push origin ${Branch}:main"
}
finally {
    Remove-Item Env:GIT_INDEX_FILE -ErrorAction SilentlyContinue
    Remove-Item $tmpIndex, $msgFile -Force -ErrorAction SilentlyContinue
}
