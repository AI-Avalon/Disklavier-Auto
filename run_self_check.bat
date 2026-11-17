@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================================
echo  Disklavier-Auto 自動検証 ＆ 安全ゲートチェック (Self-Check)
echo ============================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv が見つかりません。先に setup_environment.bat を実行してください。
    pause
    exit /b 1
)

call "%~dp0.venv\Scripts\activate.bat"
set "PYTHONPATH=%~dp0"
set "FAIL_FLAG=0"

echo.
echo [1/4] 舞台システム 5大安全・知覚・表現テスト (tests/test_stage_system.py)...
pytest -v tests/test_stage_system.py
if errorlevel 1 (
    echo [FAIL] tests/test_stage_system.py に失敗しました。
    set "FAIL_FLAG=1"
) else (
    echo [PASS] tests/test_stage_system.py 全項目合格。
)

echo.
echo [2/4] 既存全テストスイート (pytest tests/)...
pytest -v tests/
if errorlevel 1 (
    echo [FAIL] pytest tests/ に失敗しました。
    set "FAIL_FLAG=1"
) else (
    echo [PASS] pytest tests/ 全テスト合格。
)

echo.
echo [3/4] 統合整合性検証 (validate_system_integrity.py)...
python validate_system_integrity.py
if errorlevel 1 (
    echo [FAIL] validate_system_integrity.py に失敗しました。
    set "FAIL_FLAG=1"
) else (
    echo [PASS] validate_system_integrity.py 全7スイート合格。
)

echo.
echo [4/4] 実ローカルLLM・発音・10回反復ループ検証 (scripts/run_10x_iterative_refinement.py)...
python scripts/run_10x_iterative_refinement.py --iterations 10 --no-device-audio
if errorlevel 1 (
    echo [FAIL] run_10x_iterative_refinement.py に失敗しました。
    set "FAIL_FLAG=1"
) else (
    echo [PASS] 10回反復検証 全項目合格。
)

echo.
echo ============================================================================
if "%FAIL_FLAG%"=="0" (
    echo  結果: ALL CHECKS PASSED - すべての安全・品質検証に合格しました
) else (
    echo  結果: CHECKS FAILED - 上記のエラー出力を確認してください
)
echo ============================================================================
if not defined CI_NO_PAUSE pause
exit /b %FAIL_FLAG%
