---
name: workflow-obligation
description: >
  Disklavier-Auto リポジトリにおける毎回のタスク完了時の恒久義務規約。
  README.md の最新化、全自動テストと10回反復検証の通過、および git commit / git push の完全実施を強制する。
---

# Workflow Obligation Skill (完了時の恒久義務)

このリポジトリで変更作業を完了する際は、毎回必ず以下の3項目をすべて実行してから完了報告を行うこと。ローカルでの編集・ローカルコミットのみで終了してはならない。

## 1. README.md の最新化
- 機能追加・仕様変更・起動方法の変更・デモモード（`run_demo_showcase.bat`）と本番モード（`start_stage.bat`）の使い分け・トラブルシューティングを必ず `README.md` に正確に反映する。

## 2. 全自動テスト・反復検証の通過確認
- `run_self_check.bat`（`tests/test_stage_system.py`、`pytest tests/`、`validate_system_integrity.py`）がすべて PASS していることを実行結果で確認する。
- パイプラインやLLM・音響出力に関わる改修時は、`scripts/run_10x_iterative_refinement.py` による10回以上の反復テストもすべて PASS していることを確認する。

## 3. Git Commit & Push の完全実施
- 変更ファイルを漏れなくステージングして `git commit` を実行する。
- コミット後に必ず `git push` を実行し、`git status` およびリモートリポジトリ（GitHub `origin/main`）に反映済みであることを確認してから報告する。
