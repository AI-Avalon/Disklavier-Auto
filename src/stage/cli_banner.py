"""
UTF-8 Safe Console Banner Helper for Windows .bat Launchers.

Printing multibyte Japanese directly inside Windows cmd.exe batch files with `chcp 65001`
causes character doubling ("起起動動直直後後..."). All .bat files stay pure ASCII and
delegate Japanese banner output to this module.
"""

from __future__ import annotations

import sys


def _safe_print(text: str) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print(text, flush=True)


BANNERS = {
    "setup_header": (
        "============================================================================\n"
        " Disklavier-Auto 初回セットアップ ＆ 環境診断 (Windows 11 / RTX 3070)\n"
        "============================================================================"
    ),
    "setup_step1": "[1/4] Python 仮想環境 (.venv) を確認・作成しています...",
    "setup_step2": "[2/4] 必須パッケージをインストールしています...",
    "setup_step3": "[3/4] MIDI / オーディオ / Ollama / クラシックMIDIコーパスを自動スキャンしています...",
    "setup_step4": "[4/4] セットアップ完了。config.local.yaml を生成しました。",
    "stage_header": (
        "============================================================================\n"
        " Disklavier-Auto 本番・リハーサル実機モード (--production)\n"
        " 仮想奏者・PCモニターシンセを自動OFFにし、実機DisklavierとUR22に直結します\n"
        "============================================================================"
    ),
    "stage_open": "[2/3] 本番舞台投影Web HUD (http://localhost:8000) を開きます (F10でUI完全格納)...",
    "stage_run": "[3/3] 本番ステージサーバを起動しています (終了時は Ctrl+C)...",
    "demo_header": (
        "============================================================================\n"
        " Disklavier-Auto 自己実演・舞台デモショーケース (--demo --monitor-audio)\n"
        " 起動直後からPCスピーカーで「問いかけ → 自律作曲推敲 → 両ペダル実演」が鳴ります\n"
        "============================================================================"
    ),
    "demo_open": "[2/3] デモ用舞台HUD (http://localhost:8000/?demo=1) を開きます (Python倍音シンセ + WebAudio ON)...",
    "demo_run": "[3/3] 自己実演デモサーバを起動中 (終了時は Ctrl+C)...",
    "check_header": (
        "============================================================================\n"
        " Disklavier-Auto 自動検証 ＆ 安全ゲートチェック (Self-Check)\n"
        "============================================================================"
    ),
    "check_pass": "結果: ALL CHECKS PASSED - すべての安全・音楽性・音響・統合検証に合格しました",
    "check_fail": "結果: CHECKS FAILED - 上記のエラー出力を確認してください",
}


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "demo_header"
    _safe_print(BANNERS.get(key, key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
