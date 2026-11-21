"""
UTF-8 Safe Console Banner Helper for Windows .bat Launchers.

Fixes Windows conhost.exe CP65001 DBCS character doubling ("起起動動直直後後...") by:
1. Never changing the console codepage (`chcp 65001` is removed from all .bat launchers).
2. Writing UTF-16LE directly to the Windows console via Win32 `WriteConsoleW` when attached to a TTY.
3. Writing raw UTF-8 bytes when redirected/piped.
4. Keeping all Japanese banner lines <= 72 display columns to prevent 80-col conhost wrap glitches.
"""

from __future__ import annotations

import ctypes
import sys
import unicodedata


def display_width(line: str) -> int:
    """Returns terminal column width using East Asian Width rules."""
    total = 0
    for ch in line:
        w = unicodedata.east_asian_width(ch)
        total += 2 if w in ("F", "W", "A") else 1
    return total


def _write_windows_console(text: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        if not sys.stdout.isatty():
            return False
        kernel32 = ctypes.windll.kernel32
        std_out_handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        if not std_out_handle or std_out_handle == -1:
            return False
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(std_out_handle, ctypes.byref(mode)):
            return False
        msg = text + "\n"
        written = ctypes.c_uint32()
        ok = kernel32.WriteConsoleW(
            std_out_handle,
            ctypes.c_wchar_p(msg),
            len(msg),
            ctypes.byref(written),
            None,
        )
        return bool(ok)
    except Exception:
        return False


def _safe_print(text: str) -> None:
    if _write_windows_console(text):
        return
    raw = (text + "\n").encode("utf-8", errors="replace")
    buf = getattr(sys.stdout, "buffer", None)
    if buf is not None:
        buf.write(raw)
        buf.flush()
        return
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(text, flush=True)


BANNERS = {
    "setup_header": (
        "========================================================================\n"
        " Disklavier-Auto 初回セットアップ ＆ 環境診断 (Win11 / RTX 3070)\n"
        "========================================================================"
    ),
    "setup_step1": "[1/4] Python 仮想環境 (.venv) を確認・作成しています...",
    "setup_step2": "[2/4] 必須パッケージをインストールしています...",
    "setup_step3": "[3/4] MIDI / オーディオ / Ollama / コーパスをスキャン中...",
    "setup_step4": "[4/4] セットアップ完了。config.local.yaml を生成しました。",
    "stage_header": (
        "========================================================================\n"
        " Disklavier-Auto 本番・リハーサル実機モード (--production)\n"
        " 仮想奏者とPCモニターをOFFにし、実機Disklavier/UR22へ直結します\n"
        "========================================================================"
    ),
    "stage_open": "[2/3] 本番舞台HUD (http://localhost:8000/stage) を開きます (F10切替)",
    "stage_run": "[3/3] 本番ステージサーバを起動しています (終了時は Ctrl+C)...",
    "demo_header": (
        "========================================================================\n"
        " Disklavier-Auto 自己実演・作曲スタジオ (--demo --monitor-audio)\n"
        " 起動直後からPCスピーカーで「問いかけ -> 推敲 -> 両ペダル実演」\n"
        " 100曲ライブラリとリアルタイム和声X線モニターを起動します\n"
        "========================================================================"
    ),
    "demo_open": "[2/3] スタジオUI (http://localhost:8000/studio?demo=1) を開きます",
    "demo_run": "[3/3] 自己実演デモサーバを起動中 (終了時は Ctrl+C)...",
    "check_header": (
        "========================================================================\n"
        " Disklavier-Auto 自動検証 ＆ 安全ゲートチェック (Self-Check)\n"
        "========================================================================"
    ),
    "check_pass": "結果: ALL CHECKS PASSED - 全安全・和声・音響・ブラウザ検証に合格",
    "check_fail": "結果: CHECKS FAILED - 上記のエラー出力を確認してください",
}


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "demo_header"
    _safe_print(BANNERS.get(key, key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
